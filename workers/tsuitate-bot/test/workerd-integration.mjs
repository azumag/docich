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

async function startRuntime(compatibilityDateOverride = null) {
  const tempDirectory = await mkdtemp(join(tmpdir(), "tsuitate-workerd-"));
  const port = await availablePort();
  const args = [
    "dev", "--config", "wrangler.runtime.toml", "--local",
    "--ip", "127.0.0.1", "--port", String(port), "--inspector-port", "0",
    "--persist-to", join(tempDirectory, "state"),
    "--var", `WEBHOOK_SECRET:${secret}`,
    "--show-interactive-dev-session=false", "--log-level", "error",
  ];
  if (compatibilityDateOverride) args.push("--compatibility-date", compatibilityDateOverride);

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
          return { child, baseUrl, tempDirectory, output: () => output, compatibilityDateOverride };
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

async function startWithAvailableCompatibilityDate() {
  try {
    const runtime = await startRuntime();
    console.log("workerd compatibility date: wrangler.runtime.toml default (2026-09-21)");
    return runtime;
  } catch (error) {
    const output = String(error?.message ?? error);
    const supported = /newest date supported by this server binary is [\"']?(\d{4}-\d{2}-\d{2})/.exec(output)?.[1];
    if (!supported) throw error;
    console.log(`local workerd does not support 2026-09-21; retrying with its reported latest date ${supported}`);
    const runtime = await startRuntime(supported);
    console.log(`workerd compatibility date override: ${supported}`);
    return runtime;
  }
}

async function signedPost(baseUrl, payload) {
  const rawBody = JSON.stringify(payload);
  const body = Buffer.from(rawBody, "utf8");
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = createHmac("sha256", secret).update(`${timestamp}.`).update(body).digest("hex");
  const response = await fetch(`${baseUrl}/webhook`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Tsuitate-Bot-Id": botId,
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

let runtime;
try {
  runtime = await startWithAvailableCompatibilityDate();
  await testConcurrentSameRequest(runtime.baseUrl);
  await testConcurrentChangedBody(runtime.baseUrl);
  await testStorageRollback(runtime.baseUrl);
  await testLateCommitRetry(runtime.baseUrl);
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
