import { attemptBudgetFromWebhook, checksFromLastMove, chooseWebhookDecision } from "./adapters/webhook.js";
import {
  BRAIN_VERSION,
  LEGACY_PROFILE,
  validateProfile,
} from "./brain/index.js";
import {
  MAX_BODY_BYTES,
  POSITION_VALIDATION_STAGES,
  POSITION_VALUE_CLASSES,
  ProtocolFault,
  extractRequestIdentity,
  isRecord,
  parseCurrentTurn,
  validateGameEndPayload,
  validateOfflineReviewPayload,
  validateWebhookPayload,
} from "./protocol.js";

const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const RPC_BUDGET_MS = 2500;
const REQUEST_BUDGET_MS = 7000;
const SITE_ID = "tsuitateviewer.web.app";
const REVIEWABLE_BRAIN_VERSIONS = new Set(["tsuitate-brain-v1", "tsuitate-brain-v2", BRAIN_VERSION]);
const PROFILE_FEATURES = Object.freeze(["advance", "centrality", "promotion", "drop", "kingMove", "distance", "repeat"]);
const SAFE_ERROR_CODES = new Set([
  "not_found", "method_not_allowed", "content_type_required", "webhook_not_configured",
  "authentication_failed", "timestamp_out_of_range", "body_too_large", "request_timeout",
  "invalid_json", "invalid_identity", "invalid_request", "invalid_color", "invalid_ply",
  "invalid_positions", "invalid_game", "unsupported_game_type", "invalid_players", "invalid_seat",
  "invalid_base_ply", "invalid_ply_range", "incomplete_positions", "unexpected_position_key",
  "invalid_position", "invalid_sfen", "not_your_turn", "state_unavailable", "state_timeout",
  "internal_error", "invalid_internal_request", "request_id_reused", "session_already_initialized",
  "game_metadata_mismatch", "session_missing", "seat_mismatch", "base_ply_mismatch", "stale_ply",
  "no_observed_move", "state_failure", "invalid_brain_profile", "invalid_game_end",
  "invalid_offline_review_export", "offline_review_not_found", "game_end_conflict",
  "game_already_ended", "bot_identity_mismatch", "unknown_webhook_type", "brain_version_mismatch",
  "legacy_identity_unverified",
]);
const AUTH_FAILURE_STAGES = new Set([
  "bot_id_missing", "bot_id_format", "timestamp_missing", "timestamp_format", "timestamp_out_of_range",
  "body_hash_missing", "body_hash_format", "body_hash_mismatch",
  "signature_missing", "signature_format", "signature_mismatch",
]);
const POSITION_FIELD_TYPES = new Set(["undefined", "null", "array", "object", "string", "number", "boolean"]);
const CSA_MOVE = /^[+-](?:(?:[1-9]{4}(?:FU|KY|KE|GI|KI|KA|HI|OU|TO|NY|NK|NG|UM|RY))|(?:00[1-9]{2}(?:FU|KY|KE|GI|KI|KA|HI))|(?:0000TORYO))$/;
const MASKED_OPPONENT_MOVE = /^[+-](?:0000ZZ|00[1-9]{2}ZZ)$/;
const BOT_ID_FORMAT = /^[A-Za-z0-9:][A-Za-z0-9._:-]{0,63}$/;

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function emptyResponse(status) {
  return new Response(null, { status, headers: { "cache-control": "no-store" } });
}

function safeDiagnosticObservation(position, color) {
  if (!isRecord(position) || typeof position.sfen !== "string" || position.sfen.length > 160) return undefined;
  const observation = { sfen: position.sfen };
  if (typeof position.lastMove === "string") {
    const ownSign = color === "b" ? "+" : "-";
    const opponentSign = color === "b" ? "-" : "+";
    if (position.lastMove.startsWith(ownSign)
        || (position.lastMove.startsWith(opponentSign) && MASKED_OPPONENT_MOVE.test(position.lastMove))) {
      observation.lastMove = position.lastMove;
    }
  }
  if (Number.isInteger(position.lastInfo)) observation.lastInfo = position.lastInfo;
  if (typeof position.lastCapture === "string") observation.lastCapture = position.lastCapture;
  if (typeof position.wasPromotion === "boolean") observation.wasPromotion = position.wasPromotion;
  for (const field of ["fouls", "times", "byoyomiActive"]) {
    if (isRecord(position[field]) && Object.hasOwn(position[field], "b") && Object.hasOwn(position[field], "w")) {
      observation[field] = { b: position[field].b, w: position[field].w };
    }
  }
  return observation;
}

function captureValidatedDiagnosticContext(value, diagnostics) {
  try {
    const payload = validateWebhookPayload(value);
    diagnostics.color = payload.color;
    diagnostics.seat = payload.number;
    diagnostics.ply = payload.ply;
    diagnostics.gameType = payload.game?.type;
    diagnostics.observation = safeDiagnosticObservation(payload.positions[String(payload.ply)], payload.color);
  } catch (error) {
    if (error instanceof ProtocolFault && error.code === "invalid_position"
        && POSITION_VALIDATION_STAGES.includes(error.validationFailureStage)
        && Number.isSafeInteger(error.positionIndex)
        && error.positionIndex >= 0 && error.positionIndex <= 10000
        && POSITION_FIELD_TYPES.has(error.fieldType)) {
      diagnostics.validationFailureStage = error.validationFailureStage;
      diagnostics.positionIndex = error.positionIndex;
      diagnostics.fieldType = error.fieldType;
      if (error.validationFailureStage === "last_capture"
          && error.fieldType === "string"
          && POSITION_VALUE_CLASSES.includes(error.validationFailureValueClass)) {
        diagnostics.validationFailureValueClass = error.validationFailureValueClass;
      }
    }
  }
}

function recordWebhookDiagnostic(env, diagnostics, status, elapsedMs) {
  const versionId = env?.CF_VERSION_METADATA?.id;
  const event = {
    event: diagnostics.eventName ?? "tsuitate_webhook",
    status,
    errorCode: diagnostics.errorCode ?? null,
    elapsedMs: Math.max(0, Math.round(elapsedMs)),
    strategyVersion: diagnostics.profileId ?? null,
    codeVersion: typeof versionId === "string" && versionId.length <= 64 ? versionId : "local",
  };
  if (diagnostics.kind === "move") {
    for (const key of ["gameId", "color", "seat", "ply", "gameType", "observation", "issuedMove", "brainVersion", "profileId"]) {
      if (diagnostics[key] !== undefined) event[key] = diagnostics[key];
    }
  }
  if (AUTH_FAILURE_STAGES.has(diagnostics.authFailureStage)) {
    event.authFailureStage = diagnostics.authFailureStage;
  }
  if (diagnostics.errorCode === "invalid_position"
      && POSITION_VALIDATION_STAGES.includes(diagnostics.validationFailureStage)
      && Number.isSafeInteger(diagnostics.positionIndex)
      && diagnostics.positionIndex >= 0 && diagnostics.positionIndex <= 10000
      && POSITION_FIELD_TYPES.has(diagnostics.fieldType)) {
    event.validationFailureStage = diagnostics.validationFailureStage;
    event.positionIndex = diagnostics.positionIndex;
    event.fieldType = diagnostics.fieldType;
    if (diagnostics.validationFailureStage === "last_capture"
        && diagnostics.fieldType === "string"
        && POSITION_VALUE_CLASSES.includes(diagnostics.validationFailureValueClass)) {
      event.validationFailureValueClass = diagnostics.validationFailureValueClass;
    }
  }
  try {
    // Only this fixed, allowlisted object is persisted by Workers Logs. Never pass request/env/error objects.
    console.log(event);
  } catch {
    // Diagnostic output must not change the webhook response.
  }
}

function hexToBytes(hex) {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i += 1) bytes[i] = Number.parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return bytes;
}

function bytesToHex(bytes) {
  return [...new Uint8Array(bytes)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function constantTimeBytesEqual(left, right) {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let i = 0; i < left.length; i += 1) difference |= left[i] ^ right[i];
  return difference === 0;
}

async function digestHex(bytes) {
  return bytesToHex(await crypto.subtle.digest("SHA-256", bytes));
}

async function profileHash(profile) {
  const validated = validateProfile(profile);
  if (!validated) return null;
  const payload = validated.policy === "legacy-v1"
    ? JSON.stringify({ schemaVersion: validated.schemaVersion, policy: validated.policy })
    : JSON.stringify({
      schemaVersion: validated.schemaVersion,
      policy: validated.policy,
      weights: Object.fromEntries(PROFILE_FEATURES.map((name) => [name, validated.weights[name]])),
      exploration: validated.exploration,
    });
  return digestHex(encoder.encode(payload));
}

async function readBoundedBody(request, signal) {
  const contentLength = request.headers.get("content-length");
  if (contentLength && /^\d+$/.test(contentLength) && Number(contentLength) > MAX_BODY_BYTES) {
    throw new ProtocolFault(413, "body_too_large");
  }
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks = [];
  let total = 0;
  const cancelReader = () => { void reader.cancel().catch(() => {}); };
  if (signal?.aborted) throw new ProtocolFault(503, "request_timeout");
  signal?.addEventListener("abort", cancelReader, { once: true });
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (signal?.aborted) throw new ProtocolFault(503, "request_timeout");
      if (done) break;
      const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
      if (total + chunk.byteLength > MAX_BODY_BYTES) {
        cancelReader();
        throw new ProtocolFault(413, "body_too_large");
      }
      chunks.push(chunk);
      total += chunk.byteLength;
    }
  } catch (error) {
    if (signal?.aborted) throw new ProtocolFault(503, "request_timeout");
    throw error;
  } finally {
    signal?.removeEventListener("abort", cancelReader);
    try { reader.releaseLock(); } catch { /* cancellation may already have released it */ }
  }

  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

/** Validate all signed headers against the original request bytes. */
export async function authenticateRequest(request, env, nowSeconds = Math.floor(Date.now() / 1000), signal, diagnostics) {
  const rejectAuthentication = (stage, status = 401, code = "authentication_failed") => {
    if (diagnostics && AUTH_FAILURE_STAGES.has(stage)) diagnostics.authFailureStage = stage;
    throw new ProtocolFault(status, code);
  };
  const contentType = request.headers.get("content-type") ?? "";
  if (!/^application\/json(?:\s*;|\s*$)/i.test(contentType)) throw new ProtocolFault(415, "content_type_required");
  if (typeof env.WEBHOOK_SECRET !== "string" || env.WEBHOOK_SECRET.length === 0) {
    throw new ProtocolFault(503, "webhook_not_configured");
  }

  const botId = request.headers.get("X-Tsuitate-Bot-Id");
  if (!botId) rejectAuthentication("bot_id_missing");
  if (!BOT_ID_FORMAT.test(botId)) rejectAuthentication("bot_id_format");

  const timestampText = request.headers.get("X-Tsuitate-Timestamp") ?? "";
  if (!timestampText) rejectAuthentication("timestamp_missing");
  if (!/^\d{1,16}$/.test(timestampText)) rejectAuthentication("timestamp_format");
  const timestamp = Number(timestampText);
  if (!Number.isSafeInteger(timestamp) || Math.abs(nowSeconds - timestamp) >= 300) {
    rejectAuthentication("timestamp_out_of_range", 403, "timestamp_out_of_range");
  }

  const rawBody = await readBoundedBody(request, signal);

  const expectedBodyHash = request.headers.get("x-amz-content-sha256") ?? "";
  if (!expectedBodyHash) rejectAuthentication("body_hash_missing");
  if (!/^[a-f0-9]{64}$/i.test(expectedBodyHash)) rejectAuthentication("body_hash_format");
  const suppliedHash = hexToBytes(expectedBodyHash);
  const actualHashHex = await digestHex(rawBody);
  if (!constantTimeBytesEqual(suppliedHash, hexToBytes(actualHashHex))) {
    rejectAuthentication("body_hash_mismatch");
  }

  const signatureHeader = request.headers.get("X-Tsuitate-Signature") ?? "";
  if (!signatureHeader) rejectAuthentication("signature_missing");
  const signatureMatch = /^sha256=([a-f0-9]{64})$/i.exec(signatureHeader);
  if (!signatureMatch) rejectAuthentication("signature_format");
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(env.WEBHOOK_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["verify"],
  );
  const prefix = encoder.encode(`${timestampText}.`);
  const signedBytes = new Uint8Array(prefix.byteLength + rawBody.byteLength);
  signedBytes.set(prefix);
  signedBytes.set(rawBody, prefix.byteLength);
  const valid = await crypto.subtle.verify("HMAC", key, hexToBytes(signatureMatch[1]), signedBytes);
  if (!valid) rejectAuthentication("signature_mismatch");

  let bodyText;
  try {
    bodyText = decoder.decode(rawBody);
  } catch {
    throw new ProtocolFault(400, "invalid_json");
  }
  return { bodyText, bodyHash: actualHashHex };
}

function withTimeout(promise, timeoutMs) {
  let timer;
  return Promise.race([
    promise,
    new Promise((resolve) => {
      timer = setTimeout(() => resolve(null), timeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
}

/** Pure request handler exported for local fixture tests. */
async function handleWebhookRequest(request, env, options, signal, diagnostics) {
  const url = new URL(request.url);
  if (!["/webhook", "/offline-review"].includes(url.pathname)) return jsonResponse(404, { error: "not_found" });
  if (request.method !== "POST") {
    diagnostics.errorCode = "method_not_allowed";
    return jsonResponse(405, { error: diagnostics.errorCode });
  }

  try {
    const nowSeconds = options.nowSeconds ?? Math.floor(Date.now() / 1000);
    const { bodyText, bodyHash } = await authenticateRequest(request, env, nowSeconds, signal, diagnostics);
    let decoded;
    try {
      decoded = JSON.parse(bodyText);
    } catch {
      throw new ProtocolFault(400, "invalid_json");
    }

    const botId = request.headers.get("X-Tsuitate-Bot-Id");
    const botIdHash = await digestHex(encoder.encode(botId));
    let stateName;
    let internalPath;
    let internalInput;
    if (url.pathname === "/offline-review") {
      const query = validateOfflineReviewPayload(decoded);
      diagnostics.kind = "offline_review";
      diagnostics.eventName = "tsuitate_offline_review_export";
      stateName = query.gameId;
      internalPath = "/export";
      internalInput = { payload: query, botIdHash };
    } else if (isRecord(decoded) && decoded.type === "game_end") {
      const payload = validateGameEndPayload(decoded);
      diagnostics.kind = "game_end";
      diagnostics.eventName = "tsuitate_game_end";
      stateName = payload.gameId;
      internalPath = "/game-end";
      internalInput = {
        payload,
        bodyHash,
        botIdHash,
        receivedAt: options.receivedAt ?? new Date().toISOString(),
        workerVersion: typeof env?.CF_VERSION_METADATA?.id === "string"
          && env.CF_VERSION_METADATA.id.length <= 64 ? env.CF_VERSION_METADATA.id : "local",
      };
    } else {
      if (isRecord(decoded) && Object.hasOwn(decoded, "type")) {
        throw new ProtocolFault(400, "unknown_webhook_type");
      }
      const identity = extractRequestIdentity(decoded);
      diagnostics.kind = "move";
      diagnostics.gameId = identity.gameId;
      captureValidatedDiagnosticContext(decoded, diagnostics);
      stateName = identity.gameId;
      internalPath = "/process";
      internalInput = { payload: decoded, bodyHash, botIdHash };
    }

    if (!env.GAME_STATE || typeof env.GAME_STATE.idFromName !== "function") {
      throw new ProtocolFault(503, "state_unavailable");
    }
    const stub = env.GAME_STATE.get(env.GAME_STATE.idFromName(stateName));
    const internal = new Request(`https://game-state.internal${internalPath}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(internalInput),
      signal,
    });
    const result = await withTimeout(
      stub.fetch(internal),
      options.rpcBudgetMs ?? RPC_BUDGET_MS,
    );
    if (result === null) {
      diagnostics.errorCode = "state_timeout";
      return jsonResponse(503, { error: diagnostics.errorCode });
    }
    if (result.status === 204) return emptyResponse(204);
    const responseText = await result.text();
    let responseBody;
    try { responseBody = JSON.parse(responseText); } catch { /* The public response is preserved below. */ }
    if (internalPath === "/process" && result.status === 200
        && typeof responseBody?.move === "string" && CSA_MOVE.test(responseBody.move)) {
      diagnostics.issuedMove = responseBody.move;
      // Read the profile actually pinned by the DO, not the current deployment's env.
      const brainVersion = result.headers.get("x-tsuitate-brain-version");
      const profileId = result.headers.get("x-tsuitate-profile-id");
      if (typeof brainVersion === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(brainVersion)
          && typeof profileId === "string"
          && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(profileId)) {
        diagnostics.brainVersion = brainVersion;
        diagnostics.profileId = profileId;
      }
    } else if (responseBody && SAFE_ERROR_CODES.has(responseBody.error)) {
      diagnostics.errorCode = responseBody.error;
    } else if (result.status >= 400) {
      diagnostics.errorCode = "internal_error";
    }
    return new Response(responseText, {
      status: result.status,
      headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
    });
  } catch (error) {
    if (error instanceof ProtocolFault) {
      diagnostics.errorCode = SAFE_ERROR_CODES.has(error.code) ? error.code : "internal_error";
      return jsonResponse(error.status, { error: diagnostics.errorCode });
    }
    diagnostics.errorCode = "internal_error";
    return jsonResponse(500, { error: "internal_error" });
  }
}

/** Bound the complete fetch, including streaming body reads and state RPC. */
export async function handleWebhook(request, env, options = {}) {
  const startedAt = Date.now();
  const diagnostics = {};
  const budgetMs = options.requestBudgetMs ?? REQUEST_BUDGET_MS;
  const controller = new AbortController();
  let timer;
  const task = handleWebhookRequest(request, env, options, controller.signal, diagnostics);
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      diagnostics.errorCode = "request_timeout";
      resolve(jsonResponse(503, { error: "request_timeout" }));
    }, budgetMs);
  });
  let response;
  try {
    response = await Promise.race([task, timeout]);
    return response;
  } catch {
    diagnostics.errorCode = "internal_error";
    response = jsonResponse(500, { error: "internal_error" });
    return response;
  } finally {
    clearTimeout(timer);
    if (["/webhook", "/offline-review"].includes(new URL(request.url).pathname) && response) {
      recordWebhookDiagnostic(env, diagnostics, response.status, Date.now() - startedAt);
    }
  }
}

function ownMovesFrom(positions, color) {
  const sign = color === "b" ? "+" : "-";
  return Object.values(positions)
    .map((position) => position.lastMove)
    .filter((move) => typeof move === "string" && move.startsWith(sign) && !move.endsWith("ZZ"))
    .slice(-64);
}

function result(status, body, decision) {
  return decision ? { status, body, decision } : { status, body };
}

function conflict(code) {
  return result(409, { error: code });
}

/** Cloudflare Durable Object. Per-seat serialization and one atomic transaction protect state. */
export class GameState {
  constructor(state, env = {}) {
    this.state = state;
    this.env = env;
  }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (request.method !== "POST" || !["/process", "/game-end", "/export"].includes(path)) {
      return jsonResponse(404, { error: "not_found" });
    }
    let input;
    try {
      input = await request.json();
    } catch {
      return jsonResponse(400, { error: "invalid_internal_request" });
    }
    if (!isRecord(input)) {
      return jsonResponse(400, { error: "invalid_internal_request" });
    }
    if (path === "/game-end") {
      if (typeof input.bodyHash !== "string" || !/^[a-f0-9]{64}$/.test(input.bodyHash)
          || typeof input.botIdHash !== "string" || !/^[a-f0-9]{64}$/.test(input.botIdHash)
          || typeof input.receivedAt !== "string" || !Number.isFinite(Date.parse(input.receivedAt))
          || typeof input.workerVersion !== "string" || input.workerVersion.length > 64) {
        return jsonResponse(400, { error: "invalid_internal_request" });
      }
      return this.#recordGameEnd(input);
    }
    if (path === "/export") {
      if (typeof input.botIdHash !== "string" || !/^[a-f0-9]{64}$/.test(input.botIdHash)) {
        return jsonResponse(400, { error: "invalid_internal_request" });
      }
      let query;
      try { query = validateOfflineReviewPayload(input.payload); }
      catch (error) {
        if (error instanceof ProtocolFault) return jsonResponse(error.status, { error: error.code });
        return jsonResponse(400, { error: "invalid_internal_request" });
      }
      return this.#exportOfflineReview(query, input.botIdHash);
    }
    if (typeof input.bodyHash !== "string" || !/^[a-f0-9]{64}$/.test(input.bodyHash)
        || typeof input.botIdHash !== "string" || !/^[a-f0-9]{64}$/.test(input.botIdHash)) {
      return jsonResponse(400, { error: "invalid_internal_request" });
    }
    let identity;
    try {
      identity = extractRequestIdentity(input.payload);
    } catch (error) {
      if (error instanceof ProtocolFault) return jsonResponse(error.status, { error: error.code });
      return jsonResponse(400, { error: "invalid_internal_request" });
    }

    const requestKeyHash = await digestHex(encoder.encode(identity.requestId));
    const receiptKey = `request:${requestKeyHash}`;
    try {
      const stored = await this.state.storage.transaction(async (tx) => {
        const oldReceipt = await tx.get(receiptKey);
        if (oldReceipt) {
          if (oldReceipt.requestId !== identity.requestId || oldReceipt.bodyHash !== input.bodyHash) {
            return conflict("request_id_reused");
          }
          // bc0f1ac cached ownership refusals although they selected no move. Recheck the seat,
          // so a verified repair or the legitimate owner can retry the identical request.
          if (oldReceipt.status !== 409 || oldReceipt.body?.error !== "bot_identity_mismatch") {
            // Old receipts contain no authenticated owner. Preserve them until ownership is verified.
            if (typeof oldReceipt.botIdHash !== "string" || !/^[a-f0-9]{64}$/.test(oldReceipt.botIdHash)) {
              return conflict("legacy_identity_unverified");
            }
            if (oldReceipt.botIdHash !== input.botIdHash) return conflict("request_id_reused");
            return result(oldReceipt.status, oldReceipt.body, oldReceipt.decision);
          }
        }

        let payload;
        let outcome;
        try {
          payload = validateWebhookPayload(input.payload);
          parseCurrentTurn(payload);
        } catch (error) {
          if (error instanceof ProtocolFault) outcome = result(error.status, { error: error.code });
          else outcome = result(400, { error: "invalid_request" });
        }

        if (outcome) {
          await tx.put(receiptKey, {
            requestId: identity.requestId,
            bodyHash: input.bodyHash,
            botIdHash: input.botIdHash,
            status: outcome.status,
            body: outcome.body,
          });
          return outcome;
        }

        const sessionKey = `session:${payload.color}:${payload.number}`;
        const current = await tx.get(sessionKey);
        // Never let the first caller claim a legacy seat or reserve a rejected request ID.
        if (current && (typeof current.botIdHash !== "string" || !/^[a-f0-9]{64}$/.test(current.botIdHash))) {
          return conflict("legacy_identity_unverified");
        }
        if (await tx.get(`terminal:${input.botIdHash}`)) {
          outcome = conflict("game_already_ended");
        } else if (payload.game) {
          if (current) {
            outcome = conflict("session_already_initialized");
          } else {
            const game = await tx.get("game");
            if (game && (game.type !== payload.game.type
                || game.requiredPlayers.b !== payload.game.requiredPlayers.b
                || game.requiredPlayers.w !== payload.game.requiredPlayers.w)) {
              outcome = conflict("game_metadata_mismatch");
            } else {
              outcome = await this.#initialize(tx, payload, sessionKey, game, input.botIdHash);
            }
          }
        } else if (!current) {
          outcome = conflict("session_missing");
        } else if (current.gameId !== payload.gameId || current.color !== payload.color || current.number !== payload.number) {
          outcome = conflict("seat_mismatch");
        } else if (current.botIdHash !== input.botIdHash) {
          outcome = conflict("bot_identity_mismatch");
        } else if (payload.basePly !== current.lastPly) {
          outcome = conflict("base_ply_mismatch");
        } else {
          outcome = await this.#append(tx, payload, current, sessionKey, input.botIdHash);
        }

        // Configuration or ownership refusal has not selected a move or advanced state.
        // Preserve retries after verified repair, including the legitimate owner's same request ID.
        if ((outcome.status === 503 && outcome.body.error === "invalid_brain_profile")
            || outcome.body.error === "brain_version_mismatch"
            || outcome.body.error === "bot_identity_mismatch") return outcome;
        await tx.put(receiptKey, {
          requestId: identity.requestId,
          bodyHash: input.bodyHash,
          botIdHash: input.botIdHash,
          status: outcome.status,
          body: outcome.body,
          ...(outcome.decision ? { decision: outcome.decision } : {}),
        });
        return outcome;
      });
      const response = jsonResponse(stored.status, stored.body);
      if (stored.decision) {
        response.headers.set("x-tsuitate-brain-version", stored.decision.brainVersion);
        response.headers.set("x-tsuitate-profile-id", stored.decision.profileId);
      }
      return response;
    } catch {
      // No raw exception is returned or logged; a transaction failure commits neither state nor receipt.
      return jsonResponse(500, { error: "state_failure" });
    }
  }

  #configuredProfile() {
    if (this.env.BRAIN_PROFILE_JSON === undefined) return validateProfile(LEGACY_PROFILE);
    const raw = this.env.BRAIN_PROFILE_JSON;
    if (typeof raw !== "string" || raw.length > 8192) return null;
    try { return validateProfile(JSON.parse(raw)); } catch { return null; }
  }

  async #initialize(tx, payload, sessionKey, game, botIdHash) {
    // One profile for the whole game. Existing pre-profile games stay on legacy.
    const profile = game === undefined ? this.#configuredProfile()
      : validateProfile(Object.hasOwn(game, "brainProfile") ? game.brainProfile : LEGACY_PROFILE);
    if (!profile) return result(503, { error: "invalid_brain_profile" });
    const positions = payload.positions;
    const currentPosition = positions[String(payload.ply)];
    const recentOwnMoves = ownMovesFrom(positions, payload.color);
    const chosen = chooseWebhookDecision({
      sfen: currentPosition.sfen,
      ...checksFromLastMove(currentPosition, payload.color),
      attemptBudget: attemptBudgetFromWebhook(currentPosition, payload.color),
      color: payload.color,
      gameId: payload.gameId,
      ply: payload.ply,
      recentOwnMoves,
      profile,
    });
    if (!chosen) return result(422, { error: "no_observed_move" });
    const { move, decision } = chosen;
    const profileHashValue = await profileHash(profile);
    if (!profileHashValue) return result(503, { error: "invalid_brain_profile" });
    for (const [ply, position] of Object.entries(positions)) {
      await tx.put(`position:${payload.color}:${payload.number}:${ply}`, position);
    }
    if (game === undefined || !Object.hasOwn(game, "brainProfile")) {
      await tx.put("game", { type: payload.game.type, requiredPlayers: payload.game.requiredPlayers, brainProfile: profile });
    }
    await tx.put(sessionKey, {
      gameId: payload.gameId,
      color: payload.color,
      number: payload.number,
      gameType: payload.game.type,
      requiredPlayers: payload.game.requiredPlayers,
      lastPly: payload.ply,
      firstObservedPly: 0,
      botIdHash,
      brainVersion: decision.brainVersion,
      brainVersions: [decision.brainVersion],
      profileHashes: [profileHashValue],
      recentOwnMoves,
      lastIssuedMove: move,
      brainProfile: profile,
      lastDecision: decision,
      rejectedMoves: [],
    });
    return result(200, { move }, decision);
  }

  async #append(tx, payload, current, sessionKey, botIdHash) {
    const profile = validateProfile(Object.hasOwn(current, "brainProfile") ? current.brainProfile : LEGACY_PROFILE);
    if (!profile) return result(503, { error: "invalid_brain_profile" });
    const pinnedBrainVersion = current.brainVersion
      ?? (Array.isArray(current.brainVersions) && current.brainVersions.length === 1
        ? current.brainVersions[0] : current.lastDecision?.brainVersion);
    const recordedBrainVersions = Array.isArray(current.brainVersions) && current.brainVersions.length > 0
      ? [...new Set(current.brainVersions)] : [pinnedBrainVersion];
    if (pinnedBrainVersion !== BRAIN_VERSION || recordedBrainVersions.length !== 1
        || recordedBrainVersions[0] !== BRAIN_VERSION) {
      return conflict("brain_version_mismatch");
    }
    const currentPosition = payload.positions[String(payload.ply)];
    if (!currentPosition) return conflict("incomplete_positions");
    if (payload.ply <= current.lastPly) return conflict("stale_ply");
    const recentOwnMoves = [
      ...current.recentOwnMoves,
      ...ownMovesFrom(payload.positions, payload.color),
    ].slice(-64);
    const ownSign = payload.color === "b" ? "+" : "-";
    const foulledOwnMove = currentPosition.lastMove?.startsWith(ownSign)
      && (currentPosition.lastInfo === 1 || currentPosition.lastInfo === 2)
      ? current.lastIssuedMove
      : null;
    const rejectedMoves = foulledOwnMove
      ? [...new Set([...(current.rejectedMoves ?? []), foulledOwnMove])].slice(-64) : [];
    const chosen = chooseWebhookDecision({
      sfen: currentPosition.sfen,
      ...checksFromLastMove(currentPosition, payload.color),
      attemptBudget: attemptBudgetFromWebhook(currentPosition, payload.color),
      color: payload.color,
      gameId: payload.gameId,
      ply: payload.ply,
      recentOwnMoves,
      forbiddenMoves: rejectedMoves,
      profile,
    });
    if (!chosen) return result(422, { error: "no_observed_move" });
    const { move, decision } = chosen;
    const profileHashValue = await profileHash(profile);
    if (!profileHashValue) return result(503, { error: "invalid_brain_profile" });
    const brainVersions = Array.isArray(current.brainVersions)
      ? [...new Set([...current.brainVersions, decision.brainVersion])].slice(-8)
      : ["unknown"];
    const profileHashes = Array.isArray(current.profileHashes)
      ? [...new Set([...current.profileHashes, profileHashValue])].slice(-8)
      : [];
    for (const [ply, position] of Object.entries(payload.positions)) {
      await tx.put(`position:${payload.color}:${payload.number}:${ply}`, position);
    }
    await tx.put(sessionKey, {
      ...current,
      firstObservedPly: current.firstObservedPly ?? 0,
      botIdHash,
      brainVersion: pinnedBrainVersion,
      brainVersions,
      profileHashes,
      lastPly: payload.ply, recentOwnMoves, lastIssuedMove: move,
      brainProfile: profile, lastDecision: decision, rejectedMoves,
    });
    return result(200, { move }, decision);
  }

  async #recordGameEnd(input) {
    let payload;
    try { payload = validateGameEndPayload(input.payload); }
    catch (error) {
      if (error instanceof ProtocolFault) return jsonResponse(error.status, { error: error.code });
      return jsonResponse(400, { error: "invalid_internal_request" });
    }

    const archiveKey = `terminal:${input.botIdHash}`;
    try {
      const outcome = await this.state.storage.transaction(async (tx) => {
        const existing = await tx.get(archiveKey);
        if (existing) {
          if (existing.bodyHash === input.bodyHash && existing.gameId === payload.gameId) {
            await tx.put(archiveKey, {
              ...existing,
              duplicateCount: Math.min(1000000, (existing.duplicateCount ?? 0) + 1),
              lastDuplicateAt: input.receivedAt,
            });
            return { status: 204, body: null };
          }
          const conflictKey = `terminal-conflict:${input.botIdHash}:${input.bodyHash}`;
          if (!(await tx.get(conflictKey))) {
            await tx.put(conflictKey, {
              bodyHash: input.bodyHash,
              receivedAt: input.receivedAt,
              result: payload.result,
              winner: payload.winner,
            });
            const conflictHashes = Array.isArray(existing.conflictHashes) ? existing.conflictHashes : [];
            await tx.put(archiveKey, {
              ...existing,
              reviewStatus: "conflicting_terminal_event",
              conflictCount: Math.min(1000000, (existing.conflictCount ?? 0) + 1),
              conflictHashes: [...new Set([...conflictHashes, input.bodyHash])].slice(-16),
            });
          }
          return result(409, { error: "game_end_conflict" });
        }

        const game = await tx.get("game");
        const requiredPlayers = game?.requiredPlayers ?? { b: 1, w: 1 };
        const matches = [];
        for (const color of ["b", "w"]) {
          const rawCount = requiredPlayers[color];
          const count = Number.isSafeInteger(rawCount) ? Math.max(0, Math.min(rawCount, 16)) : 1;
          for (let number = 0; number < count; number += 1) {
            const session = await tx.get(`session:${color}:${number}`);
            if (session?.botIdHash === input.botIdHash) matches.push({ color, number, session });
          }
        }

        const match = matches.length === 1 && matches[0].session.gameId === payload.gameId
          ? matches[0] : null;
        const session = match?.session;
        const brainVersions = Array.isArray(session?.brainVersions) ? [...new Set(session.brainVersions)] : [];
        const profileHashes = Array.isArray(session?.profileHashes) ? [...new Set(session.profileHashes)] : [];
        const brainVersion = brainVersions.length === 1 ? brainVersions[0] : "unknown";
        const profileHashValue = profileHashes.length === 1 && /^[a-f0-9]{64}$/.test(profileHashes[0])
          ? profileHashes[0] : null;
        let reviewStatus;
        if (!payload.param.trim()) reviewStatus = "missing_kifu";
        else if (matches.length > 1) reviewStatus = "ambiguous_self_seat";
        else if (matches.length === 0) reviewStatus = "unmatched_bot";
        else if (!match) reviewStatus = "game_id_mismatch";
        else if (session.firstObservedPly !== 0) reviewStatus = "partial_history";
        else if (!REVIEWABLE_BRAIN_VERSIONS.has(brainVersion) || profileHashValue === null) {
          reviewStatus = "unknown_strategy_version";
        } else reviewStatus = "offline_only_reviewable";

        const positions = match ? {
          color: match.color,
          seat: match.number,
          fromPly: 0,
          throughPly: session.lastPly,
          expectedCount: session.lastPly + 1,
          firstObservedPly: Number.isSafeInteger(session.firstObservedPly) ? session.firstObservedPly : null,
        } : null;
        const archive = {
          schemaVersion: 1,
          kind: "tsuitate_terminal_review",
          gameId: payload.gameId,
          site: SITE_ID,
          selfColor: match?.color ?? null,
          selfSeat: match?.number ?? null,
          brainVersion,
          profileHash: profileHashValue,
          workerVersion: input.workerVersion,
          result: payload.result,
          winner: payload.winner,
          receivedAt: input.receivedAt,
          param: payload.param,
          positions,
          reviewStatus,
          trainingEligible: false,
          duplicateCount: 0,
          conflictCount: 0,
          bodyHash: input.bodyHash,
          botIdHash: input.botIdHash,
        };
        await tx.put(archiveKey, archive);
        return { status: 204, body: null };
      });
      return outcome.status === 204 ? emptyResponse(204) : jsonResponse(outcome.status, outcome.body);
    } catch {
      // A 204 is returned only after SQLite commits the terminal archive.
      return jsonResponse(500, { error: "state_failure" });
    }
  }

  async #exportOfflineReview(query, botIdHash) {
    const archiveKey = `terminal:${botIdHash}`;
    try {
      const exported = await this.state.storage.transaction(async (tx) => {
        const archive = await tx.get(archiveKey);
        if (!archive || archive.gameId !== query.gameId || archive.botIdHash !== botIdHash) {
          return result(404, { error: "offline_review_not_found" });
        }

        const positions = [];
        let historyIntegrity = archive.positions ? "complete" : "unavailable";
        let nextFromPly = null;
        if (archive.positions) {
          const { color, seat, throughPly, firstObservedPly } = archive.positions;
          if (firstObservedPly !== 0) historyIntegrity = "partial";
          const end = Math.min(throughPly, query.fromPly + query.limit - 1);
          if (query.fromPly <= throughPly) {
            for (let ply = query.fromPly; ply <= end; ply += 1) {
              const position = await tx.get(`position:${color}:${seat}:${ply}`);
              if (position === undefined) historyIntegrity = "incomplete";
              else positions.push({ ply, position });
            }
            if (end < throughPly) nextFromPly = end + 1;
          }
        }
        const { bodyHash, botIdHash: archivedBotIdHash, ...publicArchive } = archive;
        return result(200, {
          archive: publicArchive,
          positions,
          nextFromPly,
          historyIntegrity,
          classification: historyIntegrity === "incomplete" ? "incomplete_history" : archive.reviewStatus,
          trainingEligible: false,
        });
      });
      return jsonResponse(exported.status, exported.body);
    } catch {
      return jsonResponse(500, { error: "state_failure" });
    }
  }
}

export default {
  fetch: handleWebhook,
};
