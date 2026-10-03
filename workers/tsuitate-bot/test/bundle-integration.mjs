import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const require = createRequire(import.meta.url);
// Use the reader and local runtime shipped with the pinned Cf CLI itself.
const cfRequire = createRequire(require.resolve("cf/package.json"));
const { readBuildOutput } = await import(pathToFileURL(cfRequire.resolve("@cloudflare/build-output-utils")));
const { Miniflare } = cfRequire("miniflare");
const output = await readBuildOutput(root); // Missing/malformed build output fails; never build from src here.
const built = output.workers.default;
const { config, bundleDir } = built;
assert.equal(config.name, "docich-tsuitate-bot");
assert.equal(config.compatibilityDate, "2026-09-21");
assert.deepEqual(config.exports.GameState, { type: "durable-object", storage: "sqlite" });
assert.deepEqual(config.env.GAME_STATE, {
  type: "durable-object", worker: config.name, exportName: "GameState",
});
assert.equal(config.env.WEBHOOK_SECRET.type, "secret");
assert.equal(Object.hasOwn(config.env.WEBHOOK_SECRET, "value"), false);
assert.ok(bundleDir);
assert.ok(config.manifest?.mainModule);
const entrypoint = join(bundleDir, config.manifest.mainModule);
const bundle = await import(pathToFileURL(entrypoint));
assert.equal(typeof bundle.GameState, "function");
assert.equal(typeof bundle.default.fetch, "function");
console.log("PASS actual Cf manifest, GameState export, SQLite storage and GAME_STATE self-binding");

const modules = await Promise.all(Object.entries(config.manifest.modules).filter(([, v]) => v.type !== "sourcemap")
  .map(async ([name, info]) => {
    assert.equal(info.type, "esm", "this prototype expects only emitted ES modules");
    return { type: "ESModule", path: join(bundleDir, name), contents: await readFile(join(bundleDir, name), "utf8") };
  }));
assert.ok(modules.some((module) => module.path === entrypoint));
const fixture = async (name) => JSON.parse(await readFile(join(root, `test/fixtures/${name}-request.json`), "utf8"));
const initial = await fixture("initial");
const delta = await fixture("incremental");
const foul = await fixture("foul");
const secret = "test-only-not-a-deployable-secret"; // Existing fixture value, never uploaded.
const botId = "fixture-bot-id";
const temp = await mkdtemp(join(tmpdir(), "tsuitate-built-bundle-"));
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const digest = (value) => createHash("sha256").update(value).digest("hex");
const payload = (game, id = game) => ({ ...structuredClone(initial), gameId: game, requestId: id });
const runtimes = new Set();

async function start(faults = false) {
  let runtimeModules = modules;
  if (faults) {
    const wrapper = (await readFile(join(root, "test/bundle-fault-worker.js"), "utf8"))
      .replace("__CF_ENTRYPOINT__", config.manifest.mainModule);
    runtimeModules = [{ type: "ESModule", path: join(bundleDir, "__bundle_test_wrapper.js"), contents: wrapper }, ...modules];
  } else {
    runtimeModules = [...modules].sort((a, b) => (a.path === entrypoint ? -1 : b.path === entrypoint ? 1 : 0));
  }
  const runtime = new Miniflare({
    name: config.name,
    modules: runtimeModules,
    modulesRoot: bundleDir,
    compatibilityDate: config.compatibilityDate,
    host: "127.0.0.1", port: 0, cf: false,
    bindings: { BOT_ID: botId, WEBHOOK_SECRET: secret },
    durableObjects: {
      GAME_STATE: { className: config.env.GAME_STATE.exportName,
        scriptName: config.env.GAME_STATE.worker,
        useSQLite: config.exports.GameState.storage === "sqlite" },
    },
    durableObjectsPersist: join(temp, faults ? "fault-state" : "plain-state"),
    outboundService() { throw new Error("Network access is forbidden in bundle tests"); },
  });
  runtimes.add(runtime);
  await runtime.ready;
  return runtime;
}

async function post(runtime, value, options = {}) {
  const raw = options.raw ?? JSON.stringify(value);
  const timestamp = String(options.timestamp ?? Math.floor(Date.now() / 1000));
  const signature = createHmac("sha256", secret).update(`${timestamp}.`).update(options.signedRaw ?? raw).digest("hex");
  const response = await runtime.dispatchFetch("http://localhost/webhook", {
    method: "POST", body: raw,
    headers: { "content-type": "application/json", "X-Tsuitate-Bot-Id": botId,
      "X-Tsuitate-Timestamp": timestamp, "X-Tsuitate-Signature": `sha256=${signature}`,
      "x-amz-content-sha256": digest(raw), ...options.headers },
  });
  return { status: response.status, text: await response.text() };
}

async function inspect(runtime, gameId) {
  const namespace = await runtime.getDurableObjectNamespace("GAME_STATE");
  const response = await namespace.get(namespace.idFromName(gameId)).fetch("http://do/__bundle_test/inspect");
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.sqlite, 1, "real SQLite SQL must be available");
  return result.entries;
}

try {
  let runtime = await start();
  const auth = payload("bundle-auth");
  const raw = JSON.stringify(auth, null, 2);
  assert.equal((await post(runtime, auth, { raw })).status, 200);
  assert.equal((await post(runtime, payload("bundle-tampered"), { signedRaw: "{}" })).status, 401);
  assert.equal((await post(runtime, payload("bundle-unsigned"), { headers: { "X-Tsuitate-Signature": "" } })).status, 401);
  assert.equal((await post(runtime, payload("bundle-bodyhash"), { headers: { "x-amz-content-sha256": "0".repeat(64) } })).status, 401);
  assert.equal((await post(runtime, payload("bundle-old"), { timestamp: Math.floor(Date.now() / 1000) - 300 })).status, 403);
  // Pin now to test both sides of the exact boundary using the emitted function.
  for (const offset of [-300, -299, 299, 300]) {
    const timestamp = String(1000 + offset);
    const signature = createHmac("sha256", secret).update(`${timestamp}.${raw}`).digest("hex");
    const request = new Request("http://localhost/webhook", { method: "POST", body: raw,
      headers: { "content-type": "application/json", "X-Tsuitate-Bot-Id": botId,
        "X-Tsuitate-Timestamp": timestamp, "X-Tsuitate-Signature": `sha256=${signature}`,
        "x-amz-content-sha256": digest(raw) } });
    const check = bundle.authenticateRequest(request, { BOT_ID: botId, WEBHOOK_SECRET: secret }, 1000);
    if (Math.abs(offset) === 300) await assert.rejects(check, (error) => error.status === 403);
    else assert.equal((await check).bodyText, raw);
  }
  console.log("PASS emitted bundle raw-byte HMAC, unsigned/tampered/digest rejection and ±300-second boundary");

  const replay = payload("bundle-replay");
  const first = await post(runtime, replay);
  assert.equal(first.status, 200);
  assert.match(JSON.parse(first.text).move, /^\+[0-9]{4}[A-Z]{2}$/);
  await runtime.dispose(); runtimes.delete(runtime);
  runtime = await start();
  assert.deepEqual(await post(runtime, replay), first);
  assert.equal((await post(runtime, replay, { raw: JSON.stringify(replay, null, 2) })).status, 409);
  console.log("PASS persisted receipt replay after runtime recreation and same-ID different raw body rejection");

  const same = payload("bundle-concurrent");
  const results = await Promise.all([post(runtime, same), post(runtime, same)]);
  assert.deepEqual(results[0], results[1]); assert.equal(results[0].status, 200);
  const race = payload("bundle-race");
  const changed = structuredClone(race); changed.positions["0"].times.b -= 1;
  assert.deepEqual((await Promise.all([post(runtime, race), post(runtime, changed)])).map(x => x.status).sort(), [200, 409]);
  console.log("PASS actual bundle concurrent duplicate and changed-body race");

  const game = "bundle-history";
  assert.equal((await post(runtime, payload(game))).status, 200);
  const next = { ...structuredClone(delta), gameId: game, requestId: "bundle-delta" };
  assert.equal((await post(runtime, next)).status, 200);
  assert.equal((await post(runtime, { ...next, requestId: "bundle-stale" })).status, 409);
  const foulGame = "bundle-foul";
  const before = await post(runtime, payload(foulGame));
  const after = await post(runtime, { ...structuredClone(foul), gameId: foulGame, requestId: "bundle-foul-delta" });
  assert.equal(after.status, 200);
  assert.notEqual(JSON.parse(after.text).move, JSON.parse(before.text).move);
  console.log("PASS emitted bundle delta/basePly rejection and foul move avoidance");

  const faultRuntime = await start(true);
  const rollback = payload("bundle-rollback", "bundle-rollback:once");
  const failed = await post(faultRuntime, rollback);
  assert.equal(failed.status, 500);
  assert.deepEqual(JSON.parse(failed.text), { error: "state_failure" });
  const rolledBack = await inspect(faultRuntime, rollback.gameId);
  assert.equal(Object.keys(rolledBack).filter(k => /^(game$|position:|session:|request:)/.test(k)).length, 0);
  assert.equal((await post(faultRuntime, rollback)).status, 200);
  console.log("PASS built GameState real SQLite application-transaction rollback and successful retry");

  const late = payload("bundle-timeout", "bundle-timeout:once");
  const startTime = Date.now();
  const timeout = await post(faultRuntime, late);
  assert.equal(timeout.status, 503);
  assert.deepEqual(JSON.parse(timeout.text), { error: "state_timeout" });
  assert.ok(Date.now() - startTime < 7000, "RPC timeout must leave margin within the 10-second response budget");
  const deadline = Date.now() + 5000;
  let receipt;
  do {
    receipt = (await inspect(faultRuntime, late.gameId))[`request:${digest(late.requestId)}`];
    if (!receipt) await delay(100);
  } while (!receipt && Date.now() < deadline);
  assert.equal(receipt?.status, 200);
  const retry = await post(faultRuntime, late);
  assert.equal(retry.status, 200); assert.deepEqual(JSON.parse(retry.text), receipt.body);
  console.log("PASS built GameState late commit after production RPC timeout and receipt replay");
  console.log("All Cf generated-bundle offline checks passed.");
} finally {
  await Promise.all([...runtimes].map((runtime) => runtime.dispose()));
  await rm(temp, { recursive: true, force: true });
}
