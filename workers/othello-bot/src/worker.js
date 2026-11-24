/**
 * docich othello MVP Worker (Issue #699).
 *
 * HTTP surface is JSON over HTTPS with fixed allowlisted routes. There is no
 * browser HTML here: the broadcast view keeps the tsuitate-view contract
 * (a local Python viewer process owns the HTML page; this Worker is its
 * control plane). Every error is one fixed safe code; secrets, request bodies
 * and stack text never travel into a response.
 *
 * Routes:
 *   GET  /room/snapshot?id=<roomId>     spectator-safe projection
 *   POST /room/open-room                open a viewer-vs-AI room
 *   POST /room/start-match              ROOM_OPEN -> PLAYING
 *   POST /room/viewer-move              viewer move (AI replies in-transaction)
 *   POST /room/fail                     fail-closed close with a reason
 *   POST /room/timeout                  explicit timeout close
 *
 * Storage: the OthelloRoom Durable Object instance per requested room. Room
 * operations are serialized with a promise tail on the DO instance, and every
 * state transition is one storage transaction.
 */
import { OthelloRoom, OthelloRoomError } from "./room.js";

export { OthelloRoom };

const MAX_BODY_BYTES = 4096;
const DO_BUDGET_MS = 2500;
const ROOM_PATH_PREFIX = "/room/";

const SAFE_ERROR_CODES = new Set([
  "not_found", "method_not_allowed", "content_type_required", "body_too_large",
  "request_timeout", "invalid_json", "invalid_request", "invalid_room_id",
  "room_not_idle", "room_not_open", "not_playing", "not_your_turn",
  "invalid_move", "illegal_move", "invalid_viewer_name", "invalid_window",
  "invalid_reason", "not_open_or_playing", "invalid_state", "dispatch_unknown",
  "room_unavailable",
]);

const json = (status, body) => Response.json(body, { status, headers: { "cache-control": "no-store" } });
const errorJson = (status, code) => json(status, { error: code });

async function boundedJsonBody(request) {
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    throw new OthelloRoomError("content_type_required");
  }
  const reader = request.body?.getReader();
  if (!reader) throw new OthelloRoomError("invalid_request");
  const chunks = [];
  let length = 0, timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    void reader.cancel().catch(() => {});
  }, 1000);
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (timedOut) throw new OthelloRoomError("request_timeout");
      if (done) break;
      length += value.length;
      if (length > MAX_BODY_BYTES) throw new OthelloRoomError("body_too_large");
      chunks.push(value);
    }
    const raw = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.length; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
  } finally {
    clearTimeout(timer);
    void reader.cancel().catch(() => {});
  }
}

async function deadline(operation, ms) {
  let timer;
  try {
    return await Promise.race([operation, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new OthelloRoomError("request_timeout")), ms);
    })]);
  } finally { clearTimeout(timer); }
}

export default {
  async fetch(request, env) {
    if (typeof env?.OTHELLO_ROOM === "undefined") return errorJson(503, "room_unavailable");
    const url = new URL(request.url);
    if (!url.pathname.startsWith(ROOM_PATH_PREFIX)) return errorJson(404, "not_found");

    const isGet = request.method === "GET";
    let payload = { roomId: null };
    if (isGet) {
      if (url.pathname !== "/room/snapshot") return errorJson(404, "not_found");
      payload.roomId = url.searchParams.get("id") ?? "";
    } else if (["/room/open-room", "/room/start-match", "/room/viewer-move", "/room/fail", "/room/timeout"].includes(url.pathname)) {
      try {
        const body = await boundedJsonBody(request);
        if (!body || typeof body !== "object" || Array.isArray(body)) return errorJson(400, "invalid_request");
        payload = body;
      } catch (error) {
        const code = error?.message;
        if (SAFE_ERROR_CODES.has(code)) return errorJson(code === "body_too_large" ? 413 : 400, code);
        if (error instanceof SyntaxError || error instanceof TypeError) return errorJson(400, "invalid_json");
        return errorJson(500, "internal_error");
      }
    } else {
      return errorJson(404, "not_found");
    }
    if (typeof payload.roomId !== "string" || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(payload.roomId)) {
      return errorJson(400, "invalid_room_id");
    }
    if (isGet || !["openRoom", "startMatch", "viewerMove", "fail", "timeout"].includes(payload.route)) {
      payload.route = isGet ? "snapshot"
        : { "/room/open-room": "openRoom", "/room/start-match": "startMatch",
            "/room/viewer-move": "viewerMove", "/room/fail": "fail", "/room/timeout": "timeout" }[url.pathname];
    }

    const stub = env.OTHELLO_ROOM.get(env.OTHELLO_ROOM.idFromName(`othello:room:${payload.roomId}`));
    try {
      const body = await deadline(stub.dispatch({ route: payload.route, payload }), DO_BUDGET_MS);
      return json(200, body);
    } catch (error) {
      const code = error?.message;
      if (SAFE_ERROR_CODES.has(code)) return errorJson(409, code);
      return errorJson(503, "room_unavailable");
    }
  },
};
