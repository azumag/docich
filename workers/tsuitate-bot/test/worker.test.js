import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import worker, { GameState, handleWebhook } from "../src/index.js";
import { chooseObservedMove, parseVisibleSfen } from "../src/bot.js";

const SECRET = "test-only-not-a-deployable-secret";
const BOT_ID = "fixture-bot-id";
const FIXTURE_DIR = new URL("./fixtures/", import.meta.url);
const initialFixture = JSON.parse(readFileSync(new URL("initial-request.json", FIXTURE_DIR), "utf8"));
const incrementalFixture = JSON.parse(readFileSync(new URL("incremental-request.json", FIXTURE_DIR), "utf8"));
const foulFixture = JSON.parse(readFileSync(new URL("foul-request.json", FIXTURE_DIR), "utf8"));
const encoder = new TextEncoder();

class MemoryStorage {
  constructor() {
    this.values = new Map();
    this.tail = Promise.resolve();
  }

  async transaction(callback) {
    let unlock;
    const previous = this.tail;
    this.tail = new Promise((resolve) => { unlock = resolve; });
    await previous;
    const staged = new Map(this.values);
    const tx = {
      get: async (key) => staged.get(key),
      put: async (key, value) => { staged.set(key, structuredClone(value)); },
      delete: async (key) => staged.delete(key),
    };
    try {
      const result = await callback(tx);
      this.values = staged;
      return result;
    } finally {
      unlock();
    }
  }
}

function stateBinding() {
  const objects = new Map();
  return {
    objects,
    idFromName: (name) => name,
    get: (id) => {
      if (!objects.has(id)) objects.set(id, new GameState({ storage: new MemoryStorage() }));
      return { fetch: (request) => objects.get(id).fetch(request) };
    },
  };
}

function env(binding = stateBinding()) {
  return { BOT_ID, WEBHOOK_SECRET: SECRET, GAME_STATE: binding };
}

async function sha256Hex(bytes) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function signedRequest(bodyText, options = {}) {
  const timestamp = String(options.timestamp ?? 1_000_000);
  const secret = options.secret ?? SECRET;
  const bytes = encoder.encode(bodyText);
  const bodyHash = await sha256Hex(bytes);
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const signedData = encoder.encode(`${timestamp}.${bodyText}`);
  const signature = [...new Uint8Array(await crypto.subtle.sign("HMAC", key, signedData))]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
  const headers = new Headers({
    "content-type": "application/json",
    "X-Tsuitate-Bot-Id": options.botId ?? BOT_ID,
    "X-Tsuitate-Timestamp": timestamp,
    "X-Tsuitate-Signature": options.signature ?? `sha256=${signature}`,
    "x-amz-content-sha256": options.bodyHash ?? bodyHash,
  });
  return new Request("https://worker.test/webhook", { method: "POST", headers, body: bodyText });
}

async function post(payload, options = {}) {
  const raw = options.raw ?? JSON.stringify(payload);
  const request = await signedRequest(raw, options);
  return handleWebhook(request, options.env ?? env(options.binding), {
    nowSeconds: options.nowSeconds ?? 1_000_000,
    rpcBudgetMs: options.rpcBudgetMs,
  });
}

async function responseJson(response) {
  return response.json();
}

function incremental(overrides = {}) {
  return structuredClone({ ...incrementalFixture, ...overrides });
}

test("Worker exports a webhook fetch handler and Cloudflare class", () => {
  assert.equal(typeof worker.fetch, "function");
  assert.equal(typeof GameState, "function");
});

test("initial request fixture returns a deterministic CSA-shaped move", async () => {
  const response = await post(initialFixture);
  assert.equal(response.status, 200);
  const { move } = await responseJson(response);
  assert.match(move, /^\+[1-9]{4}(?:FU|KY|KE|GI|KI|KA|HI|TO|NY|NK|NG|UM|RY)$/);
});

test("incremental fixture appends every expected position and answers", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  const response = await post(incrementalFixture, { binding });
  assert.equal(response.status, 200);
  assert.match((await responseJson(response)).move, /^[+-][1-9]{4}(?:FU|KY|KE|GI|KI|KA|HI|TO|NY|NK|NG|UM|RY)$/);
  const object = [...binding.objects.values()][0];
  assert.equal(await object.state.storage.values.get("session:b:0").lastPly, 2);
  assert.equal(object.state.storage.values.has("position:b:0:1"), true);
  assert.equal(object.state.storage.values.has("position:b:0:2"), true);
  assert.equal(object.state.storage.values.get("position:b:0:2").lastCapture, "FU");
  assert.equal(object.state.storage.values.get("position:b:0:2").wasPromotion, false);
});

test("same request ID and exact body returns the persisted response after DO recreation", async () => {
  const binding = stateBinding();
  const first = await post(initialFixture, { binding });
  const firstBody = await responseJson(first);
  const stateName = [...binding.objects.keys()][0];
  const storage = binding.objects.get(stateName).state.storage;
  binding.objects.set(stateName, new GameState({ storage }));
  const retried = await post(initialFixture, { binding });
  assert.equal(retried.status, 200);
  assert.deepEqual(await responseJson(retried), firstBody);
});

test("same request ID with a different raw body is rejected without changing history", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  const changed = structuredClone(initialFixture);
  changed.positions["0"].times.b = 299;
  const response = await post(changed, { binding });
  assert.equal(response.status, 409);
  assert.deepEqual(await responseJson(response), { error: "request_id_reused" });
  const session = [...binding.objects.values()][0].state.storage.values.get("session:b:0");
  assert.equal(session.lastPly, 0);
});

test("foul observation avoids repeating the just-rejected move", async () => {
  const binding = stateBinding();
  const first = await post(initialFixture, { binding });
  const firstMove = (await responseJson(first)).move;
  const foul = await post(foulFixture, { binding });
  assert.equal(foul.status, 200);
  const nextMove = (await responseJson(foul)).move;
  assert.notEqual(nextMove, firstMove);
});

test("authenticated timestamps accept 299 seconds and reject the 300-second boundary", async () => {
  for (const delta of [-299, 299]) {
    const accepted = await post(initialFixture, { timestamp: 1_000_000 + delta, nowSeconds: 1_000_000 });
    assert.equal(accepted.status, 200);
  }
  for (const delta of [-300, 300]) {
    const rejected = await post(initialFixture, { timestamp: 1_000_000 + delta, nowSeconds: 1_000_000 });
    assert.equal(rejected.status, 403);
  }
});

test("invalid HMAC, body digest, Bot ID, and tampered raw body fail closed", async () => {
  const raw = JSON.stringify(initialFixture);
  const valid = await signedRequest(raw);
  const wrongSignature = new Request(valid.url, {
    method: "POST",
    headers: { ...Object.fromEntries(valid.headers), "X-Tsuitate-Signature": `sha256=${"00".repeat(32)}` },
    body: raw,
  });
  assert.equal((await handleWebhook(wrongSignature, env(), { nowSeconds: 1_000_000 })).status, 401);

  const wrongHash = await signedRequest(raw, { bodyHash: "0".repeat(64) });
  assert.equal((await handleWebhook(wrongHash, env(), { nowSeconds: 1_000_000 })).status, 401);

  const wrongBot = await signedRequest(raw, { botId: "other-bot" });
  assert.equal((await handleWebhook(wrongBot, env(), { nowSeconds: 1_000_000 })).status, 401);

  const tampered = await signedRequest(`${raw} `);
  const tamperedHeaders = new Headers(tampered.headers);
  tamperedHeaders.set("X-Tsuitate-Signature", (await signedRequest(raw)).headers.get("X-Tsuitate-Signature"));
  const request = new Request(tampered.url, { method: "POST", headers: tamperedHeaders, body: `${raw} ` });
  assert.equal((await handleWebhook(request, env(), { nowSeconds: 1_000_000 })).status, 401);
});

test("raw bytes, not reserialized JSON, are signed", async () => {
  const raw = ` {\n  "requestId": "raw:0:b:0", "gameId": "raw-game", "color": "b", "number": 0, "ply": 0,\n  "positions": {"0": {"sfen": "9/9/9/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"}},\n  "game": {"type": "ついたて", "requiredPlayers": {"b": 1, "w": 1}}\n}`;
  const response = await handleWebhook(await signedRequest(raw), env(), { nowSeconds: 1_000_000 });
  assert.equal(response.status, 200);
});

test("position gaps and basePly mismatches are rejected", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);

  const missing = incremental();
  delete missing.positions["1"];
  assert.equal((await post(missing, { binding })).status, 400);
  const correctedSameId = incremental();
  const changedBodyRetry = await post(correctedSameId, { binding });
  assert.equal(changedBodyRetry.status, 409);
  assert.deepEqual(await responseJson(changedBodyRetry), { error: "request_id_reused" });

  const wrongBase = {
    requestId: "game-demo:2:b:0:wrong-base",
    gameId: "game-demo",
    color: "b",
    number: 0,
    ply: 2,
    basePly: 1,
    positions: { "2": incrementalFixture.positions["2"] },
  };
  const response = await post(wrongBase, { binding });
  assert.equal(response.status, 409);
  assert.deepEqual(await responseJson(response), { error: "base_ply_mismatch" });
});

test("concurrent duplicate requests are single-flight and concurrent deltas cannot fork history", async () => {
  const duplicateBinding = stateBinding();
  const duplicateBody = JSON.stringify(initialFixture);
  const [one, two] = await Promise.all([
    post(initialFixture, { binding: duplicateBinding, raw: duplicateBody }),
    post(initialFixture, { binding: duplicateBinding, raw: duplicateBody }),
  ]);
  assert.deepEqual([one.status, two.status], [200, 200]);
  assert.deepEqual(await one.json(), await two.json());

  const deltaBinding = stateBinding();
  await post(initialFixture, { binding: deltaBinding });
  const results = await Promise.all([
    post(incrementalFixture, { binding: deltaBinding }),
    post({ ...incrementalFixture, requestId: "game-demo:2:b:0:concurrent" }, { binding: deltaBinding }),
  ]);
  assert.deepEqual(results.map((response) => response.status).sort(), [200, 409]);
  assert.equal([...deltaBinding.objects.values()][0].state.storage.values.get("session:b:0").lastPly, 2);
});

test("relay seat histories are isolated by color and player number", async () => {
  const binding = stateBinding();
  const relayInitial = structuredClone(initialFixture);
  relayInitial.game.type = "ついたてリレー";
  relayInitial.game.requiredPlayers = { b: 2, w: 2 };
  await post(relayInitial, { binding });
  const seatOne = {
    requestId: "game-demo:2:b:1",
    gameId: "game-demo",
    color: "b",
    number: 1,
    ply: 2,
    basePly: 0,
    positions: { "1": incrementalFixture.positions["1"], "2": incrementalFixture.positions["2"] },
  };
  const response = await post(seatOne, { binding });
  assert.equal(response.status, 409);
  assert.deepEqual(await responseJson(response), { error: "session_missing" });
  assert.equal(binding.objects.size, 1);
});

test("reusing a request ID across relay seats is rejected before touching a second seat", async () => {
  const binding = stateBinding();
  const relayInitial = structuredClone(initialFixture);
  relayInitial.game.type = "ついたてリレー";
  relayInitial.game.requiredPlayers = { b: 2, w: 2 };
  await post(relayInitial, { binding });
  const otherSeat = structuredClone(relayInitial);
  otherSeat.number = 1;
  const response = await post(otherSeat, { binding });
  assert.equal(response.status, 409);
  assert.deepEqual(await responseJson(response), { error: "request_id_reused" });
  assert.equal(binding.objects.size, 1);
});

test("all four documented game types are explicitly supported", async () => {
  for (const [index, type] of ["ダーク", "ついたて", "ついたて5五", "ついたてリレー"].entries()) {
    const payload = structuredClone(initialFixture);
    payload.gameId = `type-${index}`;
    payload.requestId = `type-${index}:0:b:0`;
    payload.game.type = type;
    if (type === "ついたてリレー") payload.game.requiredPlayers = { b: 2, w: 2 };
    const response = await post(payload);
    assert.equal(response.status, 200, type);
  }
});

test("unsupported game types and malformed positions are rejected", async () => {
  const unsupported = structuredClone(initialFixture);
  unsupported.game.type = "unknown";
  assert.equal((await post(unsupported)).status, 422);

  const malformed = structuredClone(initialFixture);
  malformed.positions["0"].lastInfo = 5;
  assert.equal((await post(malformed)).status, 400);

  const malformedByoyomi = structuredClone(initialFixture);
  malformedByoyomi.positions["0"].byoyomiActive.b = 0;
  assert.equal((await post(malformedByoyomi)).status, 400);
});

test("internal state timeout returns before the site's 10-second forfeiture limit", async () => {
  const slowBinding = {
    idFromName: (name) => name,
    get: () => ({ fetch: () => new Promise((resolve) => setTimeout(() => resolve(new Response("{}")), 100)) }),
  };
  const started = performance.now();
  const response = await post(initialFixture, { binding: slowBinding, rpcBudgetMs: 8 });
  assert.equal(response.status, 503);
  assert.ok(performance.now() - started < 90);
});

test("only an observed own piece is selected and king movement is not guessed", () => {
  const position = parseVisibleSfen(initialFixture.positions["0"].sfen);
  assert.equal(position.turn, "b");
  const move = chooseObservedMove({
    sfen: initialFixture.positions["0"].sfen,
    color: "b",
    gameId: "game-demo",
    ply: 0,
  });
  assert.match(move, /^\+[1-9]{4}(?:FU|KY|KE|GI|KI|KA|HI|TO|NY|NK|NG|UM|RY)$/);
  const source = position.board.get(move.slice(1, 3));
  const destination = position.board.get(move.slice(3, 5));
  assert.equal(source.owner, "b");
  assert.notEqual(source.type, "K");
  assert.notEqual(destination?.owner, "b");
});
