import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, test } from "node:test";
import worker, { GameState, handleWebhook } from "../src/index.js";
import { chooseObservedMove, parseVisibleSfen } from "../src/bot.js";
import { BRAIN_VERSION, LEGACY_PROFILE, LINEAR_PROFILE } from "../src/brain/index.js";
import { profileHash as trainingProfileHash } from "../src/training/index.js";
import { MAX_BODY_BYTES } from "../src/protocol.js";

const SECRET = "test-only-not-a-deployable-secret";
const BOT_ID = "fixture-bot-id";
const FIXTURE_DIR = new URL("./fixtures/", import.meta.url);
const initialFixture = JSON.parse(readFileSync(new URL("initial-request.json", FIXTURE_DIR), "utf8"));
const incrementalFixture = JSON.parse(readFileSync(new URL("incremental-request.json", FIXTURE_DIR), "utf8"));
const foulFixture = JSON.parse(readFileSync(new URL("foul-request.json", FIXTURE_DIR), "utf8"));
const relayDropFixture = JSON.parse(readFileSync(new URL("relay-drop-request.json", FIXTURE_DIR), "utf8"));
const gameEndFixture = JSON.parse(readFileSync(new URL("game-end-request.json", FIXTURE_DIR), "utf8"));
const legacyStateFixture = JSON.parse(readFileSync(new URL("legacy-game-state-6874345.json", FIXTURE_DIR), "utf8"));
const encoder = new TextEncoder();
const originalConsoleLog = console.log;
console.log = () => {};
after(() => { console.log = originalConsoleLog; });

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

function stateBinding(stateEnv = {}, storageFactory = () => new MemoryStorage()) {
  const objects = new Map();
  return {
    objects,
    idFromName: (name) => name,
    get: (id) => {
      if (!objects.has(id)) objects.set(id, new GameState({ storage: storageFactory() }, stateEnv));
      return { fetch: (request) => objects.get(id).fetch(request) };
    },
  };
}

function env(binding = stateBinding()) {
  return {
    BOT_ID,
    WEBHOOK_SECRET: SECRET,
    GAME_STATE: binding,
    CF_VERSION_METADATA: { id: "version-fixture-123", tag: "test", timestamp: "2026-10-03T00:00:00.000Z" },
  };
}

async function captureDiagnosticLogs(callback) {
  const records = [];
  const original = console.log;
  console.log = (value) => records.push(value);
  try {
    return { result: await callback(), records };
  } finally {
    console.log = original;
  }
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
  return new Request(`https://worker.test${options.path ?? "/webhook"}`, { method: "POST", headers, body: bodyText });
}

async function post(payload, options = {}) {
  const raw = options.raw ?? JSON.stringify(payload);
  const request = await signedRequest(raw, options);
  return handleWebhook(request, options.env ?? env(options.binding), {
    nowSeconds: options.nowSeconds ?? 1_000_000,
    receivedAt: options.receivedAt,
    rpcBudgetMs: options.rpcBudgetMs,
    requestBudgetMs: options.requestBudgetMs,
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

test("structured diagnostic captures only the initial masked position and emitted move", async () => {
  const { result: response, records } = await captureDiagnosticLogs(() => post(initialFixture));
  assert.equal(response.status, 200);
  assert.equal(records.length, 1);
  const [event] = records;
  const { move } = await responseJson(response);
  assert.equal(event.event, "tsuitate_webhook");
  assert.equal(event.status, 200);
  assert.equal(event.errorCode, null);
  assert.equal(event.gameId, initialFixture.gameId);
  assert.equal(event.color, "b");
  assert.equal(event.seat, 0);
  assert.equal(event.ply, 0);
  assert.equal(event.observation.sfen, initialFixture.positions["0"].sfen);
  assert.deepEqual(event.observation.fouls, initialFixture.positions["0"].fouls);
  assert.equal(event.issuedMove, move);
  assert.equal(event.strategyVersion, "observed-sfen-heuristic-v1");
  assert.equal(event.profileId, LEGACY_PROFILE.id);
  assert.equal(event.brainVersion, BRAIN_VERSION);
  assert.equal(event.codeVersion, "version-fixture-123");
  assert.equal(Number.isInteger(event.elapsedMs), true);
  assert.equal(Object.hasOwn(event, "positions"), false);
  assert.equal(Object.hasOwn(event, "headers"), false);
  assert.equal(Object.hasOwn(event, "ip"), false);
  assert.equal(JSON.stringify(event).includes(SECRET), false);
});

test("first white turn at ply 1 records the masked opponent opening only", async () => {
  const whiteFirstTurn = {
    requestId: "diagnostic-white-opening",
    gameId: "diagnostic-white-opening",
    color: "w",
    number: 0,
    ply: 1,
    positions: {
      "0": { sfen: "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/9/9/9 b - 1" },
      "1": {
        sfen: "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/9/9/9 w - 2",
        lastMove: "+0000ZZ",
        lastInfo: 0,
        fouls: { b: 0, w: 0 },
        times: { b: 300, w: 300 },
        byoyomiActive: { b: false, w: false },
      },
    },
    game: { type: "ついたて", requiredPlayers: { b: 1, w: 1 } },
  };
  const { result: response, records } = await captureDiagnosticLogs(() => post(whiteFirstTurn));
  assert.equal(response.status, 200);
  assert.equal(records.length, 1);
  assert.equal(records[0].ply, 1);
  assert.equal(records[0].color, "w");
  assert.equal(records[0].observation.sfen, whiteFirstTurn.positions["1"].sfen);
  assert.equal(records[0].observation.lastMove, "+0000ZZ");
  assert.match(records[0].issuedMove, /^-/);

  const unmasked = structuredClone(whiteFirstTurn);
  unmasked.requestId = "diagnostic-unmasked-opponent";
  unmasked.positions["1"].lastMove = "+7776FU";
  const maskedResult = await captureDiagnosticLogs(() => post(unmasked));
  assert.equal(maskedResult.result.status, 200);
  assert.equal(Object.hasOwn(maskedResult.records[0].observation, "lastMove"), false);
});

test("diagnostics distinguish authentication, configuration, validation, state timeout and request timeout", async () => {
  const authBody = JSON.stringify({ ...initialFixture, marker: "raw-body-marker" });
  const authRequest = await signedRequest(authBody, { signature: `sha256=${"0".repeat(64)}` });
  authRequest.headers.set("X-Diagnostic-Marker", "header-marker");
  authRequest.headers.set("CF-Connecting-IP", "203.0.113.88");
  const auth = await captureDiagnosticLogs(() => handleWebhook(authRequest, env(), { nowSeconds: 1_000_000 }));
  assert.equal(auth.result.status, 401);
  assert.equal(auth.records[0].errorCode, "authentication_failed");
  assert.equal(auth.records[0].authFailureStage, "signature_mismatch");
  const authLog = JSON.stringify(auth.records[0]);
  for (const marker of [SECRET, "raw-body-marker", "header-marker", "203.0.113.88", "X-Tsuitate-Signature"]) {
    assert.equal(authLog.includes(marker), false);
  }

  const missingSecretEnv = {
    GAME_STATE: stateBinding(),
    CF_VERSION_METADATA: { id: "version-fixture-config" },
  };
  const config = await captureDiagnosticLogs(() => post(initialFixture, { env: missingSecretEnv }));
  assert.equal(config.result.status, 503);
  assert.equal(config.records[0].errorCode, "webhook_not_configured");

  const unsupported = structuredClone(initialFixture);
  unsupported.game.type = "ついたて5五";
  const validation = await captureDiagnosticLogs(() => post(unsupported));
  assert.equal(validation.result.status, 422);
  assert.equal(validation.records[0].errorCode, "unsupported_game_type");
  assert.equal(validation.records[0].gameId, unsupported.gameId);

  const hangingBinding = {
    idFromName: (name) => name,
    get: () => ({ fetch: () => new Promise(() => {}) }),
  };
  const stateTimeout = await captureDiagnosticLogs(() => post(initialFixture, {
    binding: hangingBinding,
    rpcBudgetMs: 2,
  }));
  assert.equal(stateTimeout.result.status, 503);
  assert.equal(stateTimeout.records[0].errorCode, "state_timeout");
  assert.equal(stateTimeout.records[0].ply, 0);

  const stalledBody = new ReadableStream({ start() {} });
  const requestTimeoutRequest = new Request("https://worker.test/webhook", {
    method: "POST",
    duplex: "half",
    headers: {
      "content-type": "application/json",
      "X-Tsuitate-Bot-Id": BOT_ID,
      "X-Tsuitate-Timestamp": "1000000",
      "X-Tsuitate-Signature": `sha256=${"0".repeat(64)}`,
      "x-amz-content-sha256": "0".repeat(64),
    },
    body: stalledBody,
  });
  const requestTimeout = await captureDiagnosticLogs(() => handleWebhook(requestTimeoutRequest, env(), {
    nowSeconds: 1_000_000,
    requestBudgetMs: 2,
  }));
  assert.equal(requestTimeout.result.status, 503);
  assert.equal(requestTimeout.records[0].errorCode, "request_timeout");
});

test("invalid_position diagnostics expose only the fixed field, index and value type", async () => {
  const malformed = structuredClone(initialFixture);
  malformed.requestId = "validation-diagnostic-request";
  malformed.gameId = "validation-diagnostic-game";
  malformed.ply = 1;
  malformed.positions["1"] = {
    ...structuredClone(initialFixture.positions["0"]),
    lastInfo: "position-value-marker",
  };
  const rawBody = JSON.stringify(malformed);
  const { result, records } = await captureDiagnosticLogs(() => post(malformed));

  assert.equal(result.status, 400);
  const responseBody = await responseJson(result);
  assert.deepEqual(responseBody, { error: "invalid_position" });
  assert.equal(records.length, 1);
  const [event] = records;
  assert.equal(event.errorCode, "invalid_position");
  assert.equal(event.validationFailureStage, "last_info");
  assert.equal(event.positionIndex, 1);
  assert.equal(event.fieldType, "string");
  assert.equal(Object.hasOwn(responseBody, "validationFailureStage"), false);

  const serialized = JSON.stringify(event);
  for (const forbidden of [SECRET, BOT_ID, rawBody, "position-value-marker", "lastInfo"]) {
    assert.equal(serialized.includes(forbidden), false, "diagnostic included " + forbidden);
  }
});

test("authentication failures log only fixed auth stages and preserve the HTTP error contract", async (t) => {
  const rawBody = JSON.stringify({ ...initialFixture, marker: "raw-body-marker" });
  const cases = [
    {
      name: "missing Bot ID",
      makeRequest: async () => {
        const request = await signedRequest(rawBody);
        request.headers.delete("X-Tsuitate-Bot-Id");
        return request;
      },
      status: 401,
      errorCode: "authentication_failed",
      authFailureStage: "bot_id_missing",
    },
    {
      name: "malformed Bot ID",
      makeRequest: async () => signedRequest(rawBody, { botId: "bad/bot-marker" }),
      status: 401,
      errorCode: "authentication_failed",
      authFailureStage: "bot_id_format",
    },
    {
      name: "missing timestamp",
      makeRequest: async () => {
        const request = await signedRequest(rawBody);
        request.headers.delete("X-Tsuitate-Timestamp");
        return request;
      },
      status: 401,
      errorCode: "authentication_failed",
      authFailureStage: "timestamp_missing",
    },
    {
      name: "malformed timestamp",
      makeRequest: async () => {
        const request = await signedRequest(rawBody);
        request.headers.set("X-Tsuitate-Timestamp", "bad-timestamp-marker");
        return request;
      },
      status: 401,
      errorCode: "authentication_failed",
      authFailureStage: "timestamp_format",
    },
    {
      name: "stale timestamp",
      makeRequest: async () => signedRequest(rawBody, { timestamp: 1_000_300 }),
      status: 403,
      errorCode: "timestamp_out_of_range",
      authFailureStage: "timestamp_out_of_range",
    },
    {
      name: "missing body hash",
      makeRequest: async () => {
        const request = await signedRequest(rawBody);
        request.headers.delete("x-amz-content-sha256");
        return request;
      },
      status: 401,
      errorCode: "authentication_failed",
      authFailureStage: "body_hash_missing",
    },
    {
      name: "malformed body hash",
      makeRequest: async () => {
        const request = await signedRequest(rawBody);
        request.headers.set("x-amz-content-sha256", "bad-body-hash-marker");
        return request;
      },
      status: 401,
      errorCode: "authentication_failed",
      authFailureStage: "body_hash_format",
    },
    {
      name: "mismatched body hash",
      makeRequest: async () => {
        const correctHash = await sha256Hex(encoder.encode(rawBody));
        const wrongHash = `${correctHash[0] === "0" ? "1" : "0"}${correctHash.slice(1)}`;
        return signedRequest(rawBody, { bodyHash: wrongHash });
      },
      status: 401,
      errorCode: "authentication_failed",
      authFailureStage: "body_hash_mismatch",
    },
    {
      name: "missing signature",
      makeRequest: async () => {
        const request = await signedRequest(rawBody);
        request.headers.delete("X-Tsuitate-Signature");
        return request;
      },
      status: 401,
      errorCode: "authentication_failed",
      authFailureStage: "signature_missing",
    },
    {
      name: "malformed signature",
      makeRequest: async () => {
        const request = await signedRequest(rawBody);
        request.headers.set("X-Tsuitate-Signature", "bad-signature-marker");
        return request;
      },
      status: 401,
      errorCode: "authentication_failed",
      authFailureStage: "signature_format",
    },
    {
      name: "mismatched signature",
      makeRequest: async () => {
        const request = await signedRequest(rawBody);
        const signature = request.headers.get("X-Tsuitate-Signature").slice("sha256=".length);
        const wrongSignature = `${signature[0] === "0" ? "1" : "0"}${signature.slice(1)}`;
        request.headers.set("X-Tsuitate-Signature", `sha256=${wrongSignature}`);
        return request;
      },
      status: 401,
      errorCode: "authentication_failed",
      authFailureStage: "signature_mismatch",
    },
  ];

  for (const scenario of cases) {
    await t.test(scenario.name, async () => {
      const request = await scenario.makeRequest();
      request.headers.set("X-Diagnostic-Marker", "header-marker");
      request.headers.set("CF-Connecting-IP", "203.0.113.88");
      const { result, records } = await captureDiagnosticLogs(() => handleWebhook(request, env(), {
        nowSeconds: 1_000_000,
      }));
      assert.equal(result.status, scenario.status);
      assert.deepEqual(await result.json(), { error: scenario.errorCode });
      assert.equal(records.length, 1);
      const [event] = records;
      assert.equal(event.errorCode, scenario.errorCode);
      assert.equal(event.authFailureStage, scenario.authFailureStage);
      assert.deepEqual(Object.keys(event).sort(), [
        "authFailureStage", "codeVersion", "elapsedMs", "errorCode", "event", "status", "strategyVersion",
      ]);

      const serialized = JSON.stringify(event);
      const forbiddenValues = [
        SECRET,
        BOT_ID,
        rawBody,
        "raw-body-marker",
        "bad/bot-marker",
        "bad-timestamp-marker",
        "bad-body-hash-marker",
        "bad-signature-marker",
        "header-marker",
        "203.0.113.88",
        "X-Tsuitate-Bot-Id",
        "X-Tsuitate-Timestamp",
        "X-Tsuitate-Signature",
        "x-amz-content-sha256",
        request.headers.get("X-Tsuitate-Bot-Id"),
        request.headers.get("X-Tsuitate-Timestamp"),
        request.headers.get("X-Tsuitate-Signature"),
        request.headers.get("x-amz-content-sha256"),
      ].filter((value) => typeof value === "string" && value.length > 0);
      for (const forbidden of forbiddenValues) {
        assert.equal(serialized.includes(forbidden), false, `diagnostic included ${forbidden}`);
      }
    });
  }
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
  assert.equal(object.state.storage.values.get("position:b:0:2").lastCapture, "P");
  assert.equal(object.state.storage.values.get("position:b:0:2").wasPromotion, false);
});

test("lastCapture accepts CSA and USI codes and normalizes empty no-capture values", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  assert.equal((await post(incrementalFixture, { binding })).status, 200);
  assert.equal(binding.objects.get(incrementalFixture.gameId).state.storage.values
    .get("position:b:0:2").lastCapture, "P");

  const emptyInitial = structuredClone(initialFixture);
  emptyInitial.gameId = "empty-capture-initial";
  emptyInitial.requestId = "empty-capture-initial:0:b:0";
  emptyInitial.positions["0"].lastCapture = "";
  const { result: emptyInitialResult, records: emptyInitialRecords } =
    await captureDiagnosticLogs(() => post(emptyInitial, { binding }));
  assert.equal(emptyInitialResult.status, 200);
  const emptyInitialPosition = binding.objects.get(emptyInitial.gameId).state.storage.values
    .get("position:b:0:0");
  assert.equal(Object.hasOwn(emptyInitialPosition, "lastCapture"), false);
  assert.equal(Object.hasOwn(emptyInitialRecords[0].observation, "lastCapture"), false);

  const emptyDeltaInitial = structuredClone(initialFixture);
  emptyDeltaInitial.gameId = "empty-capture-delta";
  emptyDeltaInitial.requestId = "empty-capture-delta:0:b:0";
  assert.equal((await post(emptyDeltaInitial, { binding })).status, 200);
  const emptyDelta = structuredClone(incrementalFixture);
  emptyDelta.gameId = emptyDeltaInitial.gameId;
  emptyDelta.requestId = "empty-capture-delta:2:b:0";
  emptyDelta.positions["1"].lastCapture = "";
  assert.equal((await post(emptyDelta, { binding })).status, 200);
  const emptyDeltaStorage = binding.objects.get(emptyDelta.gameId).state.storage.values;
  assert.equal(Object.hasOwn(emptyDeltaStorage.get("position:b:0:1"), "lastCapture"), false);
  assert.equal(emptyDeltaStorage.get("position:b:0:2").lastCapture, "P");

  const csaInitial = structuredClone(initialFixture);
  csaInitial.gameId = "csa-capture-demo";
  csaInitial.requestId = "csa-capture-demo:0:b:0";
  assert.equal((await post(csaInitial, { binding })).status, 200);
  const csaCapture = structuredClone(incrementalFixture);
  csaCapture.gameId = csaInitial.gameId;
  csaCapture.requestId = "csa-capture-demo:2:b:0";
  csaCapture.positions["2"].lastCapture = "FU";
  assert.equal((await post(csaCapture, { binding })).status, 200);
  assert.equal(binding.objects.get(csaInitial.gameId).state.storage.values
    .get("position:b:0:2").lastCapture, "FU");

  for (const lastCapture of ["+P", null, "p", "fu", "FU!", "not-a-piece-marker"]) {
    const malformed = structuredClone(incrementalFixture);
    malformed.requestId = "invalid-capture:" + String(lastCapture);
    malformed.positions["2"].lastCapture = lastCapture;
    const response = await post(malformed, { binding });
    assert.equal(response.status, 400, String(lastCapture));
    assert.deepEqual(await responseJson(response), { error: "invalid_position" });
  }

  const noCapture = structuredClone(initialFixture);
  noCapture.gameId = "optional-capture-demo";
  noCapture.requestId = "optional-capture-demo:0:b:0";
  assert.equal((await post(noCapture, { binding })).status, 200);
  assert.equal(Object.hasOwn(binding.objects.get(noCapture.gameId).state.storage.values
    .get("position:b:0:0"), "lastCapture"), false);
});

test("rejected lastCapture strings log only fixed value classes", async () => {
  const cases = [
    ["fu", "lowercase_piece_code"],
    ["private-value-marker", "other_string"],
  ];
  for (const [value, valueClass] of cases) {
    const malformed = structuredClone(initialFixture);
    malformed.gameId = "capture-class-" + valueClass;
    malformed.requestId = "capture-class-" + valueClass + ":0:b:0";
    malformed.positions["0"].lastCapture = value;
    const rawBody = JSON.stringify(malformed);
    const { result, records } = await captureDiagnosticLogs(() => post(malformed));

    assert.equal(result.status, 400);
    const responseBody = await responseJson(result);
    assert.deepEqual(responseBody, { error: "invalid_position" });
    assert.equal(records.length, 1);
    const [event] = records;
    assert.equal(event.validationFailureStage, "last_capture");
    assert.equal(event.positionIndex, 0);
    assert.equal(event.fieldType, "string");
    assert.equal(event.validationFailureValueClass, valueClass);
    assert.equal(Object.hasOwn(responseBody, "validationFailureValueClass"), false);

    const serialized = JSON.stringify(event);
    for (const forbidden of [SECRET, BOT_ID, rawBody, value]) {
      if (forbidden) assert.equal(serialized.includes(forbidden), false, "diagnostic included a private value");
    }
  }
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

test("the configured brain is pinned for the game, retry receipt and recreated DO", async () => {
  const configured = { ...LINEAR_PROFILE, id: "trained-at-game-start", exploration: 0 };
  const stateEnv = { BRAIN_PROFILE_JSON: JSON.stringify(configured) };
  const binding = stateBinding(stateEnv);
  const first = await captureDiagnosticLogs(() => post(initialFixture, { binding }));
  assert.equal(first.result.status, 200);
  const firstBody = await first.result.json();
  const storage = binding.objects.get(initialFixture.gameId).state.storage;
  assert.deepEqual(storage.values.get("game").brainProfile, configured);
  assert.deepEqual(storage.values.get("session:b:0").brainProfile, configured);
  assert.equal(first.records[0].profileId, configured.id);

  // A rollout with an invalid new profile must not change an already-started game.
  stateEnv.BRAIN_PROFILE_JSON = "invalid-after-game-start";
  binding.objects.set(initialFixture.gameId, new GameState({ storage }, stateEnv));
  const retried = await captureDiagnosticLogs(() => post(initialFixture, { binding }));
  assert.deepEqual(await retried.result.json(), firstBody);
  assert.equal(retried.records[0].profileId, configured.id);
  assert.equal(retried.records[0].brainVersion, BRAIN_VERSION);
  const appended = await captureDiagnosticLogs(() => post(incrementalFixture, { binding }));
  assert.equal(appended.result.status, 200);
  assert.equal(appended.records[0].profileId, configured.id);
  assert.equal(appended.records[0].strategyVersion, configured.id);
  assert.equal(storage.values.get("session:b:0").lastDecision.profileId, configured.id);

  // A second bot seat in the same ordinary game also uses the game-level pin.
  const white = structuredClone(initialFixture);
  white.requestId = "same-game-white-seat";
  white.color = "w";
  white.positions["0"].sfen = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/9/9/9 w - 1";
  const otherSeat = await captureDiagnosticLogs(() => post(white, { binding }));
  assert.equal(otherSeat.result.status, 200);
  assert.equal(otherSeat.records[0].profileId, configured.id);
  assert.deepEqual(storage.values.get("session:w:0").brainProfile, configured);
});

test("receipt diagnostics preserve the brain version that actually chose the move", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  const storage = binding.objects.get(initialFixture.gameId).state.storage;
  const receipt = [...storage.values.entries()].find(([key]) => key.startsWith("request:"))[1];
  receipt.decision.brainVersion = "tsuitate-brain-prior-version";
  const retry = await captureDiagnosticLogs(() => post(initialFixture, { binding }));
  assert.equal(retry.result.status, 200);
  assert.equal(retry.records[0].brainVersion, "tsuitate-brain-prior-version");
  assert.equal(retry.records[0].profileId, LEGACY_PROFILE.id);
});

test("an opted-in linear profile can issue CSA drops through the existing webhook interface", async () => {
  const request = structuredClone(initialFixture);
  request.positions["0"].sfen = "9/9/9/9/9/9/9/9/4K4 b R 1";
  assert.equal((await post(request)).status, 422);
  const binding = stateBinding({ BRAIN_PROFILE_JSON: JSON.stringify(LINEAR_PROFILE) });
  const response = await post(request, { binding });
  assert.equal(response.status, 200);
  assert.match((await response.json()).move, /^\+00[1-9]{2}HI$/);
});

test("invalid configured profiles fail closed without logging configuration values", async () => {
  for (const raw of [
    "", "profile-secret-marker", null, {},
    JSON.stringify({ ...LINEAR_PROFILE, id: "unsafe/profile-secret-marker" }),
    JSON.stringify({ ...LINEAR_PROFILE, weights: { ...LINEAR_PROFILE.weights, drop: 11 } }),
    JSON.stringify({ ...LINEAR_PROFILE, policy: "unknown-policy-secret-marker" }),
  ]) {
    const binding = stateBinding({ BRAIN_PROFILE_JSON: raw });
    const rejected = await captureDiagnosticLogs(() => post(initialFixture, { binding }));
    assert.equal(rejected.result.status, 503);
    assert.deepEqual(await rejected.result.json(), { error: "invalid_brain_profile" });
    assert.equal(rejected.records[0].errorCode, "invalid_brain_profile");
    assert.equal(rejected.records[0].strategyVersion, null);
    assert.equal(JSON.stringify(rejected.records).includes("secret-marker"), false);
    assert.equal(binding.objects.get(initialFixture.gameId).state.storage.values.has("session:b:0"), false);
  }
});

test("repairing an invalid profile lets the exact same request recover without caching its 503", async () => {
  const stateEnv = { BRAIN_PROFILE_JSON: "invalid-profile" };
  const binding = stateBinding(stateEnv);
  assert.equal((await post(initialFixture, { binding })).status, 503);
  const storage = binding.objects.get(initialFixture.gameId).state.storage;
  assert.equal([...storage.values.keys()].some((key) => key.startsWith("request:")), false);
  stateEnv.BRAIN_PROFILE_JSON = JSON.stringify(LEGACY_PROFILE);
  const recovered = await post(initialFixture, { binding });
  assert.equal(recovered.status, 200);
  const body = await recovered.json();
  stateEnv.BRAIN_PROFILE_JSON = "invalid-again";
  assert.deepEqual(await (await post(initialFixture, { binding })).json(), body);
});

test("real 6874345 receipts fail closed without inferring a Bot ID or mutating persisted state", async () => {
  assert.equal(legacyStateFixture.sourceCommit, "68743456798c6088e66e9b343e7451037c1f55ef");
  for (const snapshot of [legacyStateFixture.afterInitial, legacyStateFixture.afterIncremental]) {
    const storage = new MemoryStorage();
    storage.values = new Map(structuredClone(snapshot.entries));
    const binding = stateBinding({}, () => storage);
    const payload = JSON.parse(snapshot.rawBody);
    assert.equal(snapshot.response.status, 200, "the real old Worker accepted this request");
    assert.equal(Object.hasOwn(storage.values.get("session:b:0"), "botIdHash"), false);
    for (const botId of [BOT_ID, "another-authenticated-bot"]) {
      const held = await captureDiagnosticLogs(() => post(payload, { binding, botId, raw: snapshot.rawBody }));
      assert.equal(held.result.status, 409);
      assert.deepEqual(await held.result.json(), { error: "legacy_identity_unverified" });
      assert.equal(held.records[0].errorCode, "legacy_identity_unverified");
      assert.deepEqual([...storage.values.entries()], snapshot.entries);
    }
    const changed = structuredClone(payload);
    changed.positions[String(payload.ply)].times.b -= 1;
    const conflict = await post(changed, { binding });
    assert.equal(conflict.status, 409);
    assert.deepEqual(await conflict.json(), { error: "request_id_reused" });
    assert.deepEqual([...storage.values.entries()], snapshot.entries);
  }
});

test("real 6874345 sessions refuse deltas and replacement initial requests without reserving their IDs", async () => {
  const snapshot = legacyStateFixture.afterInitial;
  const storage = new MemoryStorage();
  storage.values = new Map(structuredClone(snapshot.entries));
  const binding = stateBinding({}, () => storage);
  const replacement = { ...initialFixture, requestId: `${initialFixture.requestId}:replacement` };
  for (const payload of [incrementalFixture, replacement]) {
    for (const botId of [BOT_ID, "another-authenticated-bot"]) {
      const held = await post(payload, { binding, botId });
      assert.equal(held.status, 409);
      assert.deepEqual(await held.json(), { error: "legacy_identity_unverified" });
      assert.deepEqual([...storage.values.entries()], snapshot.entries);
    }
  }
});

test("legacy refusals allow the identical requests to recover after an explicitly verified local state repair", async () => {
  // The real bc0f1ac class already persisted an erroneous ownership refusal on this old state.
  const snapshot = legacyStateFixture.afterDeniedUpgrade;
  const storage = new MemoryStorage();
  storage.values = new Map(structuredClone(snapshot.entries));
  const binding = stateBinding({}, () => storage);
  const held = await post(incrementalFixture, { binding });
  assert.equal(held.status, 409);
  assert.deepEqual(await held.json(), { error: "legacy_identity_unverified" });
  assert.deepEqual([...storage.values.entries()], snapshot.entries);

  // Test-only trusted repair; the Worker must never derive ownership from the incoming header.
  const botIdHash = await sha256Hex(encoder.encode(BOT_ID));
  const receiptKey = `request:${await sha256Hex(encoder.encode(initialFixture.requestId))}`;
  const session = storage.values.get("session:b:0");
  const verifiedProfileHash = await trainingProfileHash(session.brainProfile);
  await storage.transaction(async (tx) => {
    await tx.put("session:b:0", {
      ...session, botIdHash, brainVersion: BRAIN_VERSION, brainVersions: [BRAIN_VERSION],
      profileHashes: [verifiedProfileHash], firstObservedPly: 0,
    });
    await tx.put(receiptKey, { ...await tx.get(receiptKey), botIdHash });
  });
  const original = legacyStateFixture.afterInitial;
  const replay = await post(initialFixture, { binding, raw: original.rawBody });
  assert.equal(replay.status, original.response.status);
  assert.deepEqual(await replay.json(), original.response.body);
  const recovered = await post(incrementalFixture, { binding });
  assert.equal(recovered.status, 200);
  const move = await recovered.json();
  assert.deepEqual(await (await post(incrementalFixture, { binding })).json(), move);
  assert.equal(storage.values.get("session:b:0").lastPly, incrementalFixture.ply);
  const wrongBotReplay = await post(initialFixture, { binding, botId: "another-authenticated-bot", raw: original.rawBody });
  assert.equal(wrongBotReplay.status, 409);
  assert.deepEqual(await wrongBotReplay.json(), { error: "request_id_reused" });
});

test("a wrong Bot ID cannot reserve a new request ID against its verified seat owner", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  const storage = binding.objects.get(initialFixture.gameId).state.storage;
  const before = structuredClone([...storage.values.entries()]);
  const crossed = await post(incrementalFixture, { binding, botId: "another-authenticated-bot" });
  assert.equal(crossed.status, 409);
  assert.deepEqual(await crossed.json(), { error: "bot_identity_mismatch" });
  assert.deepEqual([...storage.values.entries()], before);
  const legitimate = await post(incrementalFixture, { binding });
  assert.equal(legitimate.status, 200);
  assert.equal(storage.values.get("session:b:0").botIdHash, await sha256Hex(encoder.encode(BOT_ID)));
});

test("same-version sessions missing a profile stay on legacy after a new default", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  const storage = binding.objects.get(initialFixture.gameId).state.storage;
  const session = storage.values.get("session:b:0");
  delete session.brainProfile;
  delete session.lastDecision;
  delete storage.values.get("game").brainProfile;
  binding.objects.set(initialFixture.gameId, new GameState({ storage }, {
    BRAIN_PROFILE_JSON: JSON.stringify(LINEAR_PROFILE),
  }));
  const next = await captureDiagnosticLogs(() => post(incrementalFixture, { binding }));
  assert.equal(next.result.status, 200);
  assert.equal(next.records[0].profileId, LEGACY_PROFILE.id);
  assert.deepEqual(storage.values.get("session:b:0").brainProfile, LEGACY_PROFILE);
});

test("an active webhook game refuses a different or mixed brain version and can retry after restoration", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  const storage = binding.objects.get(initialFixture.gameId).state.storage;
  const session = storage.values.get("session:b:0");
  session.brainVersion = "tsuitate-brain-previous";
  session.brainVersions = ["tsuitate-brain-previous"];

  const held = await post(incrementalFixture, { binding });
  assert.equal(held.status, 409);
  assert.deepEqual(await held.json(), { error: "brain_version_mismatch" });
  assert.equal(storage.values.get("session:b:0").lastPly, 0);
  assert.equal(storage.values.has("position:b:0:2"), false);
  assert.equal([...storage.values.keys()].some((key) => key.startsWith("request:") && key.includes(incrementalFixture.requestId)), false);

  session.brainVersion = BRAIN_VERSION;
  session.brainVersions = [BRAIN_VERSION];
  const recovered = await post(incrementalFixture, { binding });
  assert.equal(recovered.status, 200);
  assert.equal(storage.values.get("session:b:0").lastPly, incrementalFixture.ply);

  storage.values.get("session:b:0").brainVersions = [BRAIN_VERSION, "tsuitate-brain-previous"];
  const mixed = structuredClone(incrementalFixture);
  mixed.requestId = `${incrementalFixture.requestId}:mixed-version`;
  mixed.basePly = incrementalFixture.ply;
  mixed.ply += 1;
  mixed.positions = { [String(mixed.ply)]: structuredClone(incrementalFixture.positions[String(incrementalFixture.ply)]) };
  const mixedHeld = await post(mixed, { binding });
  assert.equal(mixedHeld.status, 409);
  assert.deepEqual(await mixedHeld.json(), { error: "brain_version_mismatch" });
  assert.equal(storage.values.get("session:b:0").lastPly, incrementalFixture.ply);
});

test("corrupted persisted profiles cannot silently fall back to another strategy", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  const storage = binding.objects.get(initialFixture.gameId).state.storage;
  storage.values.get("session:b:0").brainProfile = { ...LINEAR_PROFILE, weights: {} };
  const rejected = await post(incrementalFixture, { binding });
  assert.equal(rejected.status, 503);
  assert.deepEqual(await rejected.json(), { error: "invalid_brain_profile" });
  assert.equal(storage.values.get("session:b:0").lastPly, 0);
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

test("both webhook policies keep all consecutive rejected moves excluded", async () => {
  for (const profile of [LEGACY_PROFILE, { ...LINEAR_PROFILE, exploration: 0 }]) {
    const binding = stateBinding({ BRAIN_PROFILE_JSON: JSON.stringify(profile) });
    const initial = structuredClone(initialFixture);
    initial.positions["0"].sfen = "9/9/9/9/9/9/P3P4/9/9 b - 1";
    const first = await post(initial, { binding });
    const firstMove = (await first.json()).move;
    assert.ok(["+5756FU", "+9796FU"].includes(firstMove));
    const rejection = structuredClone(foulFixture);
    rejection.positions["1"].sfen = initial.positions["0"].sfen;
    const second = await post(rejection, { binding });
    assert.notEqual((await second.json()).move, firstMove);
    const exhausted = {
      ...rejection, requestId: "two-consecutive-fouls", basePly: 1, ply: 2,
      positions: { "2": rejection.positions["1"] },
    };
    const third = await post(exhausted, { binding });
    assert.equal(third.status, 422);
    assert.deepEqual(await third.json(), { error: "no_observed_move" });
  }
});

test("viewer check and consecutive foul feedback reach the shared brain", async () => {
  for (const color of ["b", "w"]) {
    const binding = stateBinding();
    const own = color === "b" ? "+" : "-";
    const initial = structuredClone(initialFixture);
    initial.color = color;
    initial.positions["0"] = {
      sfen: color === "b" ? "9/9/9/9/4K4/9/4P4/9/9 b - 1" : "9/9/9/9/4k4/9/4p4/9/9 w - 1",
      lastMove: `${own === "+" ? "-" : "+"}0000ZZ`, lastInfo: 3, fouls: { b: 9, w: 9 },
    };
    let reply = await post(initial, { binding });
    const attempts = [];
    for (let ply = 1; ply <= 8; ply += 1) {
      const move = (await reply.json()).move;
      assert.ok(move.endsWith("OU"));
      assert.ok(!attempts.includes(move));
      attempts.push(move);
      reply = await post({ ...initial, requestId: `check-${color}-${ply}`, basePly: ply - 1, ply,
        game: undefined, positions: { [ply]: { ...initial.positions["0"], lastMove: move, lastInfo: 2,
          fouls: { ...initial.positions["0"].fouls, [color]: 9 - ply } } } }, { binding });
      assert.equal(reply.status, 200);
    }
    assert.ok(!(await reply.json()).move.endsWith("OU"));
  }
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

test("invalid HMAC, body digest, and tampered raw body fail closed", async () => {
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

  const tampered = await signedRequest(`${raw} `);
  const tamperedHeaders = new Headers(tampered.headers);
  tamperedHeaders.set("X-Tsuitate-Signature", (await signedRequest(raw)).headers.get("X-Tsuitate-Signature"));
  const request = new Request(tampered.url, { method: "POST", headers: tamperedHeaders, body: `${raw} ` });
  assert.equal((await handleWebhook(request, env(), { nowSeconds: 1_000_000 })).status, 401);
});

test("valid request accepts the header Bot ID without a configured equality check", async () => {
  const binding = stateBinding();
  const response = await post(initialFixture, { botId: "DoCiAI", binding });
  assert.equal(response.status, 200);
  assert.deepEqual([...binding.objects.keys()], [initialFixture.gameId]);

  const noConfiguredId = { ...env(stateBinding()) };
  delete noConfiguredId.BOT_ID;
  const request = await signedRequest(JSON.stringify({ ...initialFixture, requestId: "no-env-id" }), {
    botId: ":DoCiAI",
  });
  assert.equal((await handleWebhook(request, noConfiguredId, { nowSeconds: 1_000_000 })).status, 200);
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

test("relay mode is rejected before a seat history can be created", async () => {
  const binding = stateBinding();
  const relayInitial = structuredClone(initialFixture);
  relayInitial.game.type = "ついたてリレー";
  relayInitial.game.requiredPlayers = { b: 2, w: 2 };
  const initial = await post(relayInitial, { binding });
  assert.equal(initial.status, 422);
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

test("CSA drops in received history are accepted and preserved", async () => {
  const binding = stateBinding();
  const response = await post(relayDropFixture, { binding });
  assert.equal(response.status, 422); // relay mode is outside the current tested scope
  assert.deepEqual(await responseJson(response), { error: "unsupported_game_type" });

  const ordinary = structuredClone(relayDropFixture);
  ordinary.gameId = "ordinary-drop-demo";
  ordinary.requestId = "ordinary-drop-demo:2:b:0";
  ordinary.game = { type: "ついたて", requiredPlayers: { b: 1, w: 1 } };
  ordinary.number = 0;
  const accepted = await post(ordinary, { binding });
  assert.equal(accepted.status, 200);
  const object = binding.objects.get("ordinary-drop-demo");
  assert.equal(object.state.storage.values.get("position:b:0:1").lastMove, "+0055FU");
});

test("reusing a request ID across rejected relay seats is still idempotency-protected", async () => {
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

test("only the ordinarily tested Tsuitate mode is supported", async () => {
  for (const [index, type] of ["ダーク", "ついたて5五", "ついたてリレー"].entries()) {
    const payload = structuredClone(initialFixture);
    payload.gameId = `type-${index}`;
    payload.requestId = `type-${index}:0:b:0`;
    payload.game.type = type;
    const response = await post(payload);
    assert.equal(response.status, 422, type);
    assert.deepEqual(await responseJson(response), { error: "unsupported_game_type" });
  }
  assert.equal((await post(initialFixture)).status, 200);
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

  for (const drop of ["+0055OU", "+0005FU"]) {
    const malformedDrop = structuredClone(initialFixture);
    malformedDrop.positions["0"].lastMove = drop;
    assert.equal((await post(malformedDrop)).status, 400, drop);
  }
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

test("all forbidden observed candidates return no move instead of repeating one", () => {
  const move = chooseObservedMove({
    sfen: "9/9/9/9/9/9/4P4/9/4K4 b - 1",
    color: "b",
    gameId: "forbidden-demo",
    ply: 1,
    forbiddenMoves: ["+5756FU"],
  });
  assert.equal(move, null);
});

test("oversized streaming request is cancelled as soon as the limit is crossed", async () => {
  let emittedBytes = 0;
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(MAX_BODY_BYTES + 1));
      emittedBytes = MAX_BODY_BYTES + 1;
    },
    pull() {
      return new Promise(() => {}); // never closes unless the reader cancels it
    },
    cancel() { cancelled = true; },
  });
  const request = new Request("https://worker.test/webhook", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Tsuitate-Bot-Id": BOT_ID,
      "X-Tsuitate-Timestamp": "1000000",
    },
    body,
    duplex: "half",
  });
  const response = await handleWebhook(request, env(), { nowSeconds: 1_000_000, requestBudgetMs: 100 });
  assert.equal(response.status, 413);
  assert.deepEqual(await responseJson(response), { error: "body_too_large" });
  assert.equal(emittedBytes, MAX_BODY_BYTES + 1);
  assert.equal(cancelled, true);
});

test("whole-request timeout covers a body stream that never produces bytes", async () => {
  let cancelled = false;
  const body = new ReadableStream({
    pull() { return new Promise(() => {}); },
    cancel() { cancelled = true; },
  });
  const request = new Request("https://worker.test/webhook", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Tsuitate-Bot-Id": BOT_ID,
      "X-Tsuitate-Timestamp": "1000000",
    },
    body,
    duplex: "half",
  });
  const started = performance.now();
  const response = await handleWebhook(request, env(), { nowSeconds: 1_000_000, requestBudgetMs: 20 });
  assert.equal(response.status, 503);
  assert.deepEqual(await responseJson(response), { error: "request_timeout" });
  assert.ok(performance.now() - started < 250);
  assert.equal(cancelled, true);
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

test("game_end is authenticated, durably archived, and acknowledged with a bodyless 204", async () => {
  const binding = stateBinding({ BRAIN_PROFILE_JSON: JSON.stringify(LINEAR_PROFILE) });
  assert.equal((await post(initialFixture, { binding })).status, 200);
  const receivedAt = "2026-10-04T12:34:56.789Z";
  const { result: response, records } = await captureDiagnosticLogs(() => post(gameEndFixture, {
    binding, receivedAt,
  }));
  assert.equal(response.status, 204);
  assert.equal(await response.text(), "");
  assert.equal(response.headers.get("content-type"), null);
  const botIdDigest = await sha256Hex(encoder.encode(BOT_ID));
  const archive = binding.objects.get(gameEndFixture.gameId).state.storage.values.get(`terminal:${botIdDigest}`);
  assert.equal(archive.kind, "tsuitate_terminal_review");
  assert.equal(archive.site, "tsuitateviewer.web.app");
  assert.equal(archive.selfColor, "b");
  assert.equal(archive.selfSeat, 0);
  assert.equal(archive.brainVersion, BRAIN_VERSION);
  assert.equal(archive.profileHash, trainingProfileHash(LINEAR_PROFILE));
  assert.equal(archive.workerVersion, "version-fixture-123");
  assert.equal(archive.result, gameEndFixture.result);
  assert.equal(archive.winner, gameEndFixture.winner);
  assert.equal(archive.receivedAt, receivedAt);
  assert.equal(archive.param, gameEndFixture.param);
  assert.deepEqual(archive.positions, {
    color: "b", seat: 0, fromPly: 0, throughPly: 0, expectedCount: 1, firstObservedPly: 0,
  });
  assert.equal(archive.reviewStatus, "offline_only_reviewable");
  assert.equal(archive.trainingEligible, false);
  assert.equal(records.length, 1);
  assert.equal(records[0].event, "tsuitate_game_end");
  assert.equal(JSON.stringify(records).includes(gameEndFixture.param), false);
  assert.equal(JSON.stringify(records).includes("Player%20One"), false);
  assert.equal(JSON.stringify(records).includes(BOT_ID), false);
});

test("game_end validates its exact schema but preserves opaque result, winner, and param values", async () => {
  const drawWithWinner = { ...gameEndFixture, result: "Draw", winner: "b" };
  const accepted = await post(drawWithWinner);
  assert.equal(accepted.status, 204);
  for (const malformed of [
    { ...gameEndFixture, unexpected: true },
    { ...gameEndFixture, result: "Timeout" },
    { ...gameEndFixture, winner: "draw" },
    { ...gameEndFixture, param: 12 },
  ]) {
    const rejected = await post(malformed);
    assert.equal(rejected.status, 400);
    assert.deepEqual(await rejected.json(), { error: "invalid_game_end" });
  }
  const unknownType = await post({ ...gameEndFixture, type: "other_event" });
  assert.equal(unknownType.status, 400);
  assert.deepEqual(await unknownType.json(), { error: "unknown_webhook_type" });
});

test("same game_end body is idempotent and a conflicting terminal event is classified", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  assert.equal((await post(gameEndFixture, { binding })).status, 204);
  assert.equal((await post(gameEndFixture, { binding })).status, 204);
  const differentEnd = { ...gameEndFixture, result: "Resign", winner: null };
  const conflict = await post(differentEnd, { binding });
  assert.equal(conflict.status, 409);
  assert.deepEqual(await conflict.json(), { error: "game_end_conflict" });
  const botIdDigest = await sha256Hex(encoder.encode(BOT_ID));
  const storage = binding.objects.get(gameEndFixture.gameId).state.storage.values;
  const archive = storage.get(`terminal:${botIdDigest}`);
  assert.equal(archive.duplicateCount, 1);
  assert.equal(archive.conflictCount, 1);
  assert.equal(archive.reviewStatus, "conflicting_terminal_event");
  assert.equal(storage.get(`terminal-conflict:${botIdDigest}:${await sha256Hex(encoder.encode(JSON.stringify(differentEnd)))}`)
    .result, "Resign");
  assert.equal((await post(incrementalFixture, { binding })).status, 409);
  assert.deepEqual(await (await post(incrementalFixture, { binding })).json(), { error: "game_already_ended" });
});

test("private offline review export is HMAC protected, read only, paginated, and excluded from training", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  assert.equal((await post(incrementalFixture, { binding })).status, 200);
  assert.equal((await post(gameEndFixture, { binding })).status, 204);
  const state = binding.objects.get(gameEndFixture.gameId).state.storage;
  const before = [...state.values.keys()].sort();
  const firstQuery = { type: "offline_review_export", gameId: gameEndFixture.gameId, fromPly: 0, limit: 1 };
  const { result: first, records } = await captureDiagnosticLogs(() => post(firstQuery, {
    binding, path: "/offline-review",
  }));
  assert.equal(first.status, 200);
  const pageOne = await first.json();
  assert.equal(pageOne.archive.param, gameEndFixture.param);
  assert.equal(pageOne.trainingEligible, false);
  assert.equal(pageOne.historyIntegrity, "complete");
  assert.deepEqual(pageOne.positions.map(({ ply }) => ply), [0]);
  assert.equal(pageOne.nextFromPly, 1);
  assert.equal(Object.hasOwn(pageOne.archive, "bodyHash"), false);
  assert.equal(Object.hasOwn(pageOne.archive, "botIdHash"), false);
  const second = await post({ ...firstQuery, fromPly: 1, limit: 2 }, { binding, path: "/offline-review" });
  assert.equal(second.status, 200);
  const pageTwo = await second.json();
  assert.deepEqual(pageTwo.positions.map(({ ply }) => ply), [1, 2]);
  assert.equal(pageTwo.nextFromPly, null);
  assert.deepEqual([...state.values.keys()].sort(), before);
  assert.equal(records[0].event, "tsuitate_offline_review_export");
  assert.equal(JSON.stringify(records).includes(gameEndFixture.param), false);

  const denied = await post(firstQuery, { binding, path: "/offline-review", botId: "other-bot" });
  assert.equal(denied.status, 404);
  assert.deepEqual(await denied.json(), { error: "offline_review_not_found" });
  const badSignature = await post(firstQuery, {
    binding, path: "/offline-review", signature: `sha256=${"0".repeat(64)}`,
  });
  assert.equal(badSignature.status, 401);
});

test("offline review classifies incomplete stored positions without making them training data", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  assert.equal((await post(incrementalFixture, { binding })).status, 200);
  const positions = binding.objects.get(gameEndFixture.gameId).state.storage.values;
  positions.delete("position:b:0:1");
  assert.equal((await post(gameEndFixture, { binding })).status, 204);
  const query = { type: "offline_review_export", gameId: gameEndFixture.gameId, fromPly: 0, limit: 3 };
  const response = await post(query, { binding, path: "/offline-review" });
  assert.equal(response.status, 200);
  const exported = await response.json();
  assert.equal(exported.historyIntegrity, "incomplete");
  assert.equal(exported.classification, "incomplete_history");
  assert.equal(exported.trainingEligible, false);
});

test("v1 sessions cannot change brain midgame but their terminal records remain reviewable", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  const values = binding.objects.get(gameEndFixture.gameId).state.storage.values;
  const session = values.get("session:b:0");
  session.brainVersion = "tsuitate-brain-v1";
  session.brainVersions = ["tsuitate-brain-v1"];
  const next = await post(incrementalFixture, { binding });
  assert.equal(next.status, 409);
  assert.deepEqual(await next.json(), { error: "brain_version_mismatch" });
  assert.equal((await post(gameEndFixture, { binding })).status, 204);
  const exported = await post({ type: "offline_review_export", gameId: gameEndFixture.gameId,
    fromPly: 0, limit: 3 }, { binding, path: "/offline-review" });
  assert.equal(exported.status, 200);
  const page = await exported.json();
  assert.equal(page.archive.brainVersion, "tsuitate-brain-v1");
  assert.equal(page.archive.reviewStatus, "offline_only_reviewable");
  assert.equal(page.trainingEligible, false);
});

test("unmatched, ambiguous, late, mismatched, and unknown-strategy ends stay outside training", async () => {
  const cases = [
    {
      name: "unmatched bot",
      setup: async (binding) => { assert.equal((await post(initialFixture, { binding })).status, 200); },
      botId: "different-bot",
      expected: "unmatched_bot",
    },
    {
      name: "ambiguous seat",
      setup: async (binding) => {
        assert.equal((await post(initialFixture, { binding })).status, 200);
        const white = structuredClone(initialFixture);
        white.color = "w";
        white.requestId = "game-demo:0:w:0";
        white.positions["0"].sfen = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/9/9/9 w - 1";
        assert.equal((await post(white, { binding })).status, 200);
      },
      expected: "ambiguous_self_seat",
    },
    {
      name: "partial history",
      setup: async (binding) => {
        assert.equal((await post(initialFixture, { binding })).status, 200);
        binding.objects.get(gameEndFixture.gameId).state.storage.values.get("session:b:0").firstObservedPly = 1;
      },
      expected: "partial_history",
    },
    {
      name: "mismatched game id",
      setup: async (binding) => {
        assert.equal((await post(initialFixture, { binding })).status, 200);
        binding.objects.get(gameEndFixture.gameId).state.storage.values.get("session:b:0").gameId = "other-game";
      },
      expected: "game_id_mismatch",
    },
    {
      name: "unknown brain history",
      setup: async (binding) => {
        assert.equal((await post(initialFixture, { binding })).status, 200);
        const session = binding.objects.get(gameEndFixture.gameId).state.storage.values.get("session:b:0");
        delete session.brainVersions;
        delete session.profileHashes;
      },
      expected: "unknown_strategy_version",
    },
  ];
  for (const entry of cases) {
    const binding = stateBinding();
    await entry.setup(binding);
    const ended = await post(gameEndFixture, { binding, botId: entry.botId ?? BOT_ID });
    assert.equal(ended.status, 204, entry.name);
    const botHash = await sha256Hex(encoder.encode(entry.botId ?? BOT_ID));
    const archive = binding.objects.get(gameEndFixture.gameId).state.storage.values.get(`terminal:${botHash}`);
    assert.equal(archive.reviewStatus, entry.expected, entry.name);
    assert.equal(archive.trainingEligible, false, entry.name);
  }
});

test("a different Bot ID cannot append to a seat or claim its terminal record", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  const crossed = await post(incrementalFixture, { binding, botId: "other-bot" });
  assert.equal(crossed.status, 409);
  assert.deepEqual(await crossed.json(), { error: "bot_identity_mismatch" });
  assert.equal((await post(gameEndFixture, { binding, botId: "other-bot" })).status, 204);
  const otherHash = await sha256Hex(encoder.encode("other-bot"));
  assert.equal(binding.objects.get(gameEndFixture.gameId).state.storage.values
    .get(`terminal:${otherHash}`).reviewStatus, "unmatched_bot");
});

test("game_end storage failures and RPC timeouts never return an acknowledgement", async () => {
  class FailArchiveStorage extends MemoryStorage {
    async transaction(callback) {
      return super.transaction((tx) => callback({
        get: tx.get,
        delete: tx.delete,
        put: async (key, value) => {
          if (key.startsWith("terminal:")) throw new Error("private storage failure");
          return tx.put(key, value);
        },
      }));
    }
  }
  const brokenBinding = stateBinding({}, () => new FailArchiveStorage());
  const failed = await post(gameEndFixture, { binding: brokenBinding });
  assert.equal(failed.status, 500);
  assert.deepEqual(await failed.json(), { error: "state_failure" });
  const botHash = await sha256Hex(encoder.encode(BOT_ID));
  assert.equal(brokenBinding.objects.get(gameEndFixture.gameId).state.storage.values.has(`terminal:${botHash}`), false);

  const backing = stateBinding();
  const hangingBinding = {
    idFromName: backing.idFromName,
    get(id) {
      const stub = backing.get(id);
      return { fetch(request) {
        if (new URL(request.url).pathname === "/game-end") return new Promise(() => {});
        return stub.fetch(request);
      } };
    },
  };
  const timedOut = await post(gameEndFixture, {
    binding: hangingBinding, rpcBudgetMs: 2, requestBudgetMs: 100,
  });
  assert.equal(timedOut.status, 503);
  assert.deepEqual(await timedOut.json(), { error: "state_timeout" });
  assert.notEqual(timedOut.status, 204);
});
