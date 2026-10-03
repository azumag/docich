import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { after, test } from "node:test";
import worker, { GameState, handleWebhook } from "../src/index.js";
import { chooseObservedMove, parseVisibleSfen } from "../src/bot.js";
import { MAX_BODY_BYTES } from "../src/protocol.js";

const SECRET = "test-only-not-a-deployable-secret";
const BOT_ID = "fixture-bot-id";
const FIXTURE_DIR = new URL("./fixtures/", import.meta.url);
const initialFixture = JSON.parse(readFileSync(new URL("initial-request.json", FIXTURE_DIR), "utf8"));
const incrementalFixture = JSON.parse(readFileSync(new URL("incremental-request.json", FIXTURE_DIR), "utf8"));
const foulFixture = JSON.parse(readFileSync(new URL("foul-request.json", FIXTURE_DIR), "utf8"));
const relayDropFixture = JSON.parse(readFileSync(new URL("relay-drop-request.json", FIXTURE_DIR), "utf8"));
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
  return new Request("https://worker.test/webhook", { method: "POST", headers, body: bodyText });
}

async function post(payload, options = {}) {
  const raw = options.raw ?? JSON.stringify(payload);
  const request = await signedRequest(raw, options);
  return handleWebhook(request, options.env ?? env(options.binding), {
    nowSeconds: options.nowSeconds ?? 1_000_000,
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

test("lastCapture is optional and uses an unpromoted USI piece-kind code", async () => {
  const binding = stateBinding();
  assert.equal((await post(initialFixture, { binding })).status, 200);
  assert.equal((await post(incrementalFixture, { binding })).status, 200);
  assert.equal(binding.objects.get(incrementalFixture.gameId).state.storage.values
    .get("position:b:0:2").lastCapture, "P");

  for (const lastCapture of ["FU", "+P", "", null, "p"]) {
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
