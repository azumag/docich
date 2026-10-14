import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const workerDirectory = dirname(dirname(fileURLToPath(import.meta.url)));
const secret = "test-only-not-a-deployable-secret";
const botId = "fixture-bot-id";
const initialFixture = JSON.parse(await readFile(join(workerDirectory, "test/fixtures/initial-request.json"), "utf8"));
const incrementalFixture = JSON.parse(await readFile(join(workerDirectory, "test/fixtures/incremental-request.json"), "utf8"));
const gameEndFixture = JSON.parse(await readFile(join(workerDirectory, "test/fixtures/game-end-request.json"), "utf8"));
const runtimeConfig = await readFile(join(workerDirectory, "wrangler.runtime.toml"), "utf8");
assert.match(runtimeConfig, /^compatibility_date = "2026-09-08"$/m);

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function availablePort() {
  const server = createServer();
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const { port } = server.address();
  server.close();
  await once(server, "close");
  return port;
}

async function stopProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, "exit");
  child.kill("SIGTERM");
  await Promise.race([exited, sleep(2500)]);
  if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
}

async function startRuntime() {
  const tempDirectory = await mkdtemp(join(tmpdir(), "tsuitate-workerd-"));
  const port = await availablePort();
  const args = [
    "dev", "--config", "wrangler.runtime.toml", "--local",
    "--ip", "127.0.0.1", "--port", String(port), "--inspector-port", "0",
    "--persist-to", join(tempDirectory, "state"),
    "--var", `WEBHOOK_SECRET:${secret}`,
    "--show-interactive-dev-session=false", "--log-level", "error",
  ];
  const child = spawn("wrangler", args, {
    cwd: workerDirectory,
    env: {
      ...process.env,
      WRANGLER_LOG_PATH: join(tempDirectory, "wrangler.log"),
      WRANGLER_WRITE_LOGS: "false",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.setEncoding("utf8").on("data", (chunk) => { output += chunk; });
  child.stderr.setEncoding("utf8").on("data", (chunk) => { output += chunk; });

  const baseUrl = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 20000;
  try {
    while (Date.now() < deadline) {
      if (child.exitCode !== null || child.signalCode !== null) {
        throw new Error(`Wrangler exited before the local Worker became ready.\n${output}`);
      }
      try {
        const health = await fetch(`${baseUrl}/__runtime_test/health`, { signal: AbortSignal.timeout(500) });
        if (health.status === 200 && await health.text() === "ok") {
          return { child, baseUrl, tempDirectory, output: () => output };
        }
      } catch {
        // Wrangler may need several seconds to start the local workerd runtime.
      }
      await sleep(100);
    }
    throw new Error(`Timed out waiting for local Wrangler.\n${output}`);
  } catch (error) {
    await stopProcess(child);
    await rm(tempDirectory, { recursive: true, force: true });
    throw error;
  }
}

async function signedPost(baseUrl, payload, { path = "/webhook", botId: requestBotId = botId } = {}) {
  const rawBody = JSON.stringify(payload);
  const body = Buffer.from(rawBody, "utf8");
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = createHmac("sha256", secret).update(`${timestamp}.`).update(body).digest("hex");
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Tsuitate-Bot-Id": requestBotId,
      "X-Tsuitate-Timestamp": timestamp,
      "X-Tsuitate-Signature": `sha256=${signature}`,
      "x-amz-content-sha256": createHash("sha256").update(body).digest("hex"),
    },
    body,
  });
  return { status: response.status, text: await response.text() };
}

async function testConcurrentSameRequest(baseUrl) {
  const payload = structuredClone(initialFixture);
  payload.gameId = "workerd-concurrent-same";
  payload.requestId = "workerd-concurrent-same:b:0";
  const [first, second] = await Promise.all([signedPost(baseUrl, payload), signedPost(baseUrl, payload)]);
  assert.deepEqual([first.status, second.status], [200, 200]);
  assert.equal(first.text, second.text);
  console.log("PASS same request ID/body concurrently returns the same committed response");
}

async function testConcurrentChangedBody(baseUrl) {
  const firstPayload = structuredClone(initialFixture);
  firstPayload.gameId = "workerd-concurrent-changed";
  firstPayload.requestId = "workerd-concurrent-changed:b:0";
  firstPayload.positions["0"].times = { b: 299, w: 300 };
  const changedPayload = structuredClone(firstPayload);
  changedPayload.positions["0"].times.b = 298;
  const results = await Promise.all([
    signedPost(baseUrl, firstPayload),
    signedPost(baseUrl, changedPayload),
  ]);
  assert.deepEqual(results.map((result) => result.status).sort((a, b) => a - b), [200, 409]);
  const conflict = results.find((result) => result.status === 409);
  assert.deepEqual(JSON.parse(conflict.text), { error: "request_id_reused" });
  console.log("PASS concurrent same request ID with different raw bodies commits one and rejects the other");
}

async function testStorageRollback(baseUrl) {
  const response = await fetch(`${baseUrl}/__runtime_test/rollback?gameId=workerd-rollback`, { method: "GET" });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    transactionThrew: true,
    storageWriteThrew: true,
    stable: "before",
    transient: null,
  });
  console.log("PASS real workerd SQLite transaction rolls back prior writes after storage rejects an invalid value");
}

async function testLateCommitRetry(baseUrl) {
  const payload = structuredClone(initialFixture);
  payload.gameId = "workerd-latecommit-game";
  payload.requestId = "workerd-latecommit:retry:b:0";

  const timedOut = await signedPost(baseUrl, payload);
  assert.equal(timedOut.status, 503);
  assert.deepEqual(JSON.parse(timedOut.text), { error: "state_timeout" });
  await sleep(900);

  const inspect = await fetch(
    `${baseUrl}/__runtime_test/receipt?gameId=${encodeURIComponent(payload.gameId)}&requestId=${encodeURIComponent(payload.requestId)}`,
    { method: "GET" },
  );
  assert.equal(inspect.status, 200);
  const { receipt } = await inspect.json();
  assert.equal(receipt?.requestId, payload.requestId, "the timed-out DO invocation should have committed its receipt late");
  assert.equal(receipt?.status, 200);

  const retry = await signedPost(baseUrl, payload);
  assert.equal(retry.status, 200);
  assert.deepEqual(JSON.parse(retry.text), receipt.body);
  console.log("PASS late Durable Object commit after webhook timeout is replayed from its receipt");
}

async function testTerminalArchiveAndPrivateExport(baseUrl) {
  const initial = structuredClone(initialFixture);
  initial.gameId = "workerd-game-end";
  initial.requestId = "workerd-game-end:0:b:0";
  const started = await signedPost(baseUrl, initial);
  assert.equal(started.status, 200);

  const delta = structuredClone(incrementalFixture);
  delta.gameId = initial.gameId;
  delta.requestId = "workerd-game-end:2:b:0";
  const advanced = await signedPost(baseUrl, delta);
  assert.equal(advanced.status, 200);

  const end = { ...gameEndFixture, gameId: initial.gameId };
  const acknowledged = await signedPost(baseUrl, end);
  assert.equal(acknowledged.status, 204);
  assert.equal(acknowledged.text, "");
  assert.equal((await signedPost(baseUrl, end)).status, 204, "an identical terminal event is idempotent");
  const conflicting = await signedPost(baseUrl, { ...end, result: "Resign", winner: null });
  assert.equal(conflicting.status, 409);
  assert.deepEqual(JSON.parse(conflicting.text), { error: "game_end_conflict" });

  const firstQuery = { type: "offline_review_export", gameId: initial.gameId, fromPly: 0, limit: 1 };
  const firstPage = await signedPost(baseUrl, firstQuery, { path: "/offline-review" });
  assert.equal(firstPage.status, 200);
  const first = JSON.parse(firstPage.text);
  assert.equal(first.archive.param, gameEndFixture.param);
  assert.equal(first.archive.result, gameEndFixture.result);
  assert.equal(first.archive.winner, gameEndFixture.winner);
  assert.equal(first.archive.reviewStatus, "conflicting_terminal_event");
  assert.equal(first.archive.duplicateCount, 1);
  assert.equal(first.archive.conflictCount, 1);
  assert.equal(first.trainingEligible, false);
  assert.equal(first.historyIntegrity, "complete");
  assert.deepEqual(first.positions.map(({ ply }) => ply), [0]);
  assert.equal(first.nextFromPly, 1);

  const secondPage = await signedPost(baseUrl, { ...firstQuery, fromPly: 1, limit: 2 }, { path: "/offline-review" });
  assert.equal(secondPage.status, 200);
  const second = JSON.parse(secondPage.text);
  assert.deepEqual(second.positions.map(({ ply }) => ply), [1, 2]);
  assert.equal(second.nextFromPly, null);

  const wrongIdentity = await signedPost(baseUrl, firstQuery, { path: "/offline-review", botId: "other-bot" });
  assert.equal(wrongIdentity.status, 404);
  console.log("PASS SQLite stores opaque game_end before 204; signed private export pages visible history without training eligibility");
}

let runtime;
try {
  runtime = await startRuntime();
  console.log("workerd compatibility date: wrangler.runtime.toml (2026-09-08), no CLI override");
  await testConcurrentSameRequest(runtime.baseUrl);
  await testConcurrentChangedBody(runtime.baseUrl);
  await testStorageRollback(runtime.baseUrl);
  await testLateCommitRetry(runtime.baseUrl);
  await testTerminalArchiveAndPrivateExport(runtime.baseUrl);
  console.log("All local workerd integration checks passed.");
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  if (runtime) {
    await stopProcess(runtime.child);
    await rm(runtime.tempDirectory, { recursive: true, force: true });
  }
}
