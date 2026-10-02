import { chooseObservedMove } from "./bot.js";
import {
  MAX_BODY_BYTES,
  ProtocolFault,
  extractRequestIdentity,
  isRecord,
  parseCurrentTurn,
  validateWebhookPayload,
} from "./protocol.js";

const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const RPC_BUDGET_MS = 2500;
const REQUEST_BUDGET_MS = 7000;

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
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
export async function authenticateRequest(request, env, nowSeconds = Math.floor(Date.now() / 1000), signal) {
  const contentType = request.headers.get("content-type") ?? "";
  if (!/^application\/json(?:\s*;|\s*$)/i.test(contentType)) throw new ProtocolFault(415, "content_type_required");
  if (typeof env.WEBHOOK_SECRET !== "string" || env.WEBHOOK_SECRET.length === 0
      || typeof env.BOT_ID !== "string" || env.BOT_ID.length === 0
      || env.BOT_ID === "replace-with-tsuitate-bot-id") {
    throw new ProtocolFault(503, "webhook_not_configured");
  }

  const botId = request.headers.get("X-Tsuitate-Bot-Id");
  if (!botId || botId !== env.BOT_ID) throw new ProtocolFault(401, "authentication_failed");

  const timestampText = request.headers.get("X-Tsuitate-Timestamp") ?? "";
  if (!/^\d{1,16}$/.test(timestampText)) throw new ProtocolFault(401, "authentication_failed");
  const timestamp = Number(timestampText);
  if (!Number.isSafeInteger(timestamp) || Math.abs(nowSeconds - timestamp) >= 300) {
    throw new ProtocolFault(403, "timestamp_out_of_range");
  }

  const rawBody = await readBoundedBody(request, signal);

  const expectedBodyHash = request.headers.get("x-amz-content-sha256") ?? "";
  const suppliedHash = /^[a-f0-9]{64}$/i.test(expectedBodyHash) ? hexToBytes(expectedBodyHash) : new Uint8Array();
  const actualHashHex = await digestHex(rawBody);
  if (!constantTimeBytesEqual(suppliedHash, hexToBytes(actualHashHex))) {
    throw new ProtocolFault(401, "authentication_failed");
  }

  const signatureHeader = request.headers.get("X-Tsuitate-Signature") ?? "";
  const signatureMatch = /^sha256=([a-f0-9]{64})$/i.exec(signatureHeader);
  if (!signatureMatch) throw new ProtocolFault(401, "authentication_failed");
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(env.WEBHOOK_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["verify"],
  );
  const prefix = encoder.encode(`${timestampText}.`);
  const signedBytes = new Uint8Array(prefix.byteLength + rawBody.byteLength);
  signedBytes.set(prefix);
  signedBytes.set(rawBody, prefix.byteLength);
  const valid = await crypto.subtle.verify("HMAC", key, hexToBytes(signatureMatch[1]), signedBytes);
  if (!valid) throw new ProtocolFault(401, "authentication_failed");

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
async function handleWebhookRequest(request, env, options, signal) {
  const url = new URL(request.url);
  if (url.pathname !== "/webhook") return jsonResponse(404, { error: "not_found" });
  if (request.method !== "POST") return jsonResponse(405, { error: "method_not_allowed" });

  try {
    const nowSeconds = options.nowSeconds ?? Math.floor(Date.now() / 1000);
    const { bodyText, bodyHash } = await authenticateRequest(request, env, nowSeconds, signal);
    let decoded;
    try {
      decoded = JSON.parse(bodyText);
    } catch {
      throw new ProtocolFault(400, "invalid_json");
    }
    const identity = extractRequestIdentity(decoded);

    if (!env.GAME_STATE || typeof env.GAME_STATE.idFromName !== "function") {
      throw new ProtocolFault(503, "state_unavailable");
    }
    const stateName = identity.gameId;
    const stub = env.GAME_STATE.get(env.GAME_STATE.idFromName(stateName));
    const internal = new Request("https://game-state.internal/process", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ payload: decoded, bodyHash }),
      signal,
    });
    const result = await withTimeout(
      stub.fetch(internal),
      options.rpcBudgetMs ?? RPC_BUDGET_MS,
    );
    if (result === null) return jsonResponse(503, { error: "state_timeout" });
    return new Response(await result.text(), {
      status: result.status,
      headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
    });
  } catch (error) {
    if (error instanceof ProtocolFault) return jsonResponse(error.status, { error: error.code });
    // Deliberately do not log request data, signatures, secrets, or raw errors.
    return jsonResponse(500, { error: "internal_error" });
  }
}

/** Bound the complete fetch, including streaming body reads and state RPC. */
export async function handleWebhook(request, env, options = {}) {
  const budgetMs = options.requestBudgetMs ?? REQUEST_BUDGET_MS;
  const controller = new AbortController();
  let timer;
  const task = handleWebhookRequest(request, env, options, controller.signal);
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve(jsonResponse(503, { error: "request_timeout" }));
    }, budgetMs);
  });
  try {
    return await Promise.race([task, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

function ownMovesFrom(positions, color) {
  const sign = color === "b" ? "+" : "-";
  return Object.values(positions)
    .map((position) => position.lastMove)
    .filter((move) => typeof move === "string" && move.startsWith(sign) && !move.endsWith("ZZ"))
    .slice(-64);
}

function result(status, body) {
  return { status, body };
}

function conflict(code) {
  return result(409, { error: code });
}

/** Cloudflare Durable Object. Per-seat serialization and one atomic transaction protect state. */
export class GameState {
  constructor(state) {
    this.state = state;
  }

  async fetch(request) {
    if (request.method !== "POST" || new URL(request.url).pathname !== "/process") {
      return jsonResponse(404, { error: "not_found" });
    }
    let input;
    try {
      input = await request.json();
    } catch {
      return jsonResponse(400, { error: "invalid_internal_request" });
    }
    if (!isRecord(input) || typeof input.bodyHash !== "string" || !/^[a-f0-9]{64}$/.test(input.bodyHash)) {
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
          return { status: oldReceipt.status, body: oldReceipt.body };
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
            status: outcome.status,
            body: outcome.body,
          });
          return outcome;
        }

        const sessionKey = `session:${payload.color}:${payload.number}`;
        const current = await tx.get(sessionKey);
        if (payload.game) {
          if (current) {
            outcome = conflict("session_already_initialized");
          } else {
            const game = await tx.get("game");
            if (game && (game.type !== payload.game.type
                || game.requiredPlayers.b !== payload.game.requiredPlayers.b
                || game.requiredPlayers.w !== payload.game.requiredPlayers.w)) {
              outcome = conflict("game_metadata_mismatch");
            } else {
              outcome = await this.#initialize(tx, payload, sessionKey, game === undefined);
            }
          }
        } else if (!current) {
          outcome = conflict("session_missing");
        } else if (current.gameId !== payload.gameId || current.color !== payload.color || current.number !== payload.number) {
          outcome = conflict("seat_mismatch");
        } else if (payload.basePly !== current.lastPly) {
          outcome = conflict("base_ply_mismatch");
        } else {
          outcome = await this.#append(tx, payload, current, sessionKey);
        }

        await tx.put(receiptKey, {
          requestId: identity.requestId,
          bodyHash: input.bodyHash,
          status: outcome.status,
          body: outcome.body,
        });
        return outcome;
      });
      return jsonResponse(stored.status, stored.body);
    } catch {
      // No raw exception is returned or logged; a transaction failure commits neither state nor receipt.
      return jsonResponse(500, { error: "state_failure" });
    }
  }

  async #initialize(tx, payload, sessionKey, writeGameMetadata) {
    const positions = payload.positions;
    const currentPosition = positions[String(payload.ply)];
    const recentOwnMoves = ownMovesFrom(positions, payload.color);
    const move = chooseObservedMove({
      sfen: currentPosition.sfen,
      color: payload.color,
      gameId: payload.gameId,
      ply: payload.ply,
      recentOwnMoves,
    });
    if (!move) return result(422, { error: "no_observed_move" });
    for (const [ply, position] of Object.entries(positions)) {
      await tx.put(`position:${payload.color}:${payload.number}:${ply}`, position);
    }
    if (writeGameMetadata) {
      await tx.put("game", { type: payload.game.type, requiredPlayers: payload.game.requiredPlayers });
    }
    await tx.put(sessionKey, {
      gameId: payload.gameId,
      color: payload.color,
      number: payload.number,
      gameType: payload.game.type,
      requiredPlayers: payload.game.requiredPlayers,
      lastPly: payload.ply,
      recentOwnMoves,
      lastIssuedMove: move,
    });
    return result(200, { move });
  }

  async #append(tx, payload, current, sessionKey) {
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
    const move = chooseObservedMove({
      sfen: currentPosition.sfen,
      color: payload.color,
      gameId: payload.gameId,
      ply: payload.ply,
      recentOwnMoves,
      forbiddenMoves: foulledOwnMove ? [foulledOwnMove] : [],
    });
    if (!move) return result(422, { error: "no_observed_move" });
    for (const [ply, position] of Object.entries(payload.positions)) {
      await tx.put(`position:${payload.color}:${payload.number}:${ply}`, position);
    }
    await tx.put(sessionKey, { ...current, lastPly: payload.ply, recentOwnMoves, lastIssuedMove: move });
    return result(200, { move });
  }
}

export default {
  fetch: handleWebhook,
};
