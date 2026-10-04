import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";
import { CONTROL_PREFIX, handleBetaControl } from "../src/arena/control.js";

const secret = "fixture-only-control-capability-not-credential";
const now = 1791100000000;
function request(payload, options = {}) {
  const raw = options.raw ?? JSON.stringify(payload);
  const timestamp = String(options.timestamp ?? now / 1000);
  const signature = createHmac("sha256", options.secret ?? secret).update(CONTROL_PREFIX + timestamp + ".").update(raw).digest("hex");
  return new Request("https://local-only.test/beta-control", { method: "POST", body: raw,
    headers: { "content-type": "application/json", "X-Beta-Control-Timestamp": timestamp,
      "X-Beta-Control-Signature": `sha256=${signature}`, ...options.headers } });
}
function setup() {
  const calls = [];
  const actor = Object.fromEntries(["status", "start", "stop"].map((action) => [action, async (options) => {
    calls.push({ action, options }); return { state: "stopped" };
  }]));
  return { calls, actor, env: { BETA_CONTROL_SECRET: secret, BETA_ARENA: {
    idFromName(name) { assert.equal(name, "beta:DoCiAI"); return name; }, get() { return actor; } } } };
}

test("only dedicated raw-body HMAC admits fixed singleton operations", async () => {
  const c = setup();
  for (const action of ["status", "start", "stop"]) {
    const payload = action === "status" ? { action } : { action, runId: "fixture-run" };
    assert.equal((await handleBetaControl(request(payload), c.env, now)).status, 200);
  }
  assert.deepEqual(c.calls.map((x) => x.action), ["status", "start", "stop"]);
  assert.deepEqual(c.calls[2].options, { runId: "fixture-run" });
  const raw = ' { "action" : "status" } ';
  assert.equal((await handleBetaControl(request(null, { raw }), c.env, now)).status, 200);
});

test("missing/reused capability, browser token and Webhook signature cannot authorize control", async () => {
  const c = setup();
  for (const env of [{}, { BETA_CONTROL_SECRET: "short" },
    { ...c.env, WEBHOOK_SECRET: secret }, { ...c.env, TSUITATE_BOT_TOKEN: secret }]) {
    assert.equal((await handleBetaControl(request({ action: "status" }), env, now)).status, 503);
  }
  for (const headers of [{ "X-Beta-Control-Signature": "" }, { "X-Beta-Control-Signature": "sha256=" + "0".repeat(64), Authorization: "Bearer fixture-operator" },
    { "X-Beta-Control-Timestamp": "NaN" }]) {
    assert.equal((await handleBetaControl(request({ action: "status" }, { headers }), c.env, now)).status, 401);
  }
  assert.equal(c.calls.length, 0);
});

test("clock boundary, tampering and arbitrary commands fail before any actor operation", async () => {
  const c = setup();
  for (const offset of [-299, 299]) assert.equal((await handleBetaControl(request({ action: "status" }, { timestamp: now / 1000 + offset }), c.env, now)).status, 200);
  c.calls.length = 0;
  for (const offset of [-300, 300]) assert.equal((await handleBetaControl(request({ action: "status" }, { timestamp: now / 1000 + offset }), c.env, now)).status, 401);
  for (const payload of [{ action: "deploy" }, { action: "start" }, { action: "stop", runId: 1 },
    { action: "start", runId: "one", maxGames: 2 }, { action: "status", runId: "one" }]) {
    assert.equal((await handleBetaControl(request(payload), c.env, now)).status, 400);
  }
  const valid = request({ action: "status" });
  const changed = new Request(valid.url, { method: "POST", headers: valid.headers, body: '{"action":"start","runId":"injected"}' });
  assert.equal((await handleBetaControl(changed, c.env, now)).status, 401); assert.equal(c.calls.length, 0);
});

test("bounded body and fixed error codes exclude private exception details", async () => {
  const c = setup();
  assert.equal((await handleBetaControl(request(null, { raw: "x".repeat(4097) }), c.env, now)).status, 413);
  c.actor.start = async () => { throw new Error("private-token=fixture-private"); };
  const response = await handleBetaControl(request({ action: "start", runId: "one" }), c.env, now);
  assert.equal(response.status, 503); assert.deepEqual(await response.json(), { error: "control_unavailable" });
});

test("a stalled signed body is cancelled within the control budget", async () => {
  const c = setup(); let cancelled = false;
  const reference = request({ action: "status" });
  const stalled = new Request(reference.url, { method: "POST", headers: reference.headers,
    body: new ReadableStream({ cancel() { cancelled = true; } }), duplex: "half" });
  assert.equal((await handleBetaControl(stalled, c.env, now)).status, 504); assert.equal(cancelled, true);
  assert.equal(c.calls.length, 0);
});
