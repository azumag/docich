/** docich-arena: participatory game arena worker (Othello MVP).
 *
 * Public endpoints (no secrets in this PR; see design §5):
 * - GET  /                          landing page
 * - GET  /othello                   player client (create a new match)
 * - GET  /othello/<roomId>          player client (join an existing match)
 * - GET  /othello/view?room=<id>    spectator / broadcast view (960x540)
 * - POST /api/othello/rooms         create a room (ArenaLobby enforces the cap)
 * - GET  /api/othello/rooms/<id>    public snapshot (spectator contract, §4)
 * - GET  /api/othello/rooms/<id>/ws player+spectator WebSocket
 *
 * Fail-closed rules: room ids are 16 hex chars generated here, bodies are
 * size-capped, unknown paths return fixed JSON errors, and there is no room
 * listing anywhere.
 */
import { OthelloRoom } from "./arena/room.js";
import { ArenaLobby, LOBBY_SINGLETON } from "./arena/lobby.js";
import { landingPage, playerPage, viewPage } from "./pages.js";

export { OthelloRoom, ArenaLobby };

const ROOM_ID_RE = /^[0-9a-f]{16}$/;
const MAX_BODY_BYTES = 1024;
const AI_LEVELS = ["easy", "normal", "hard"];

function htmlResponse(markup) {
  return new Response(markup, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
}

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

/** 128-bit random room id (16 hex chars), unguessable and never listed. */
function newRoomId() {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  let text = "";
  for (const byte of bytes) text += byte.toString(16).padStart(2, "0");
  return text;
}

async function readJsonBody(request) {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return null;
  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return null;
  try {
    const parsed = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function lobbyStub(env) {
  const namespace = env.ARENA_LOBBY;
  const id = namespace.idFromName(LOBBY_SINGLETON);
  return namespace.get(id);
}

function roomStub(env, roomId) {
  const namespace = env.ARENA_ROOM;
  return namespace.get(namespace.idFromName(roomId));
}

export async function createRoom(env, level) {
  const roomId = newRoomId();
  const allocated = await lobbyStub(env).fetch("http://arena.internal/alloc", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roomId }),
  });
  if (!allocated.ok) {
    return { status: allocated.status, body: await allocated.json() };
  }
  const initialized = await roomStub(env, roomId).fetch("http://arena.internal/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roomId, level }),
  });
  const body = await initialized.json();
  if (!initialized.ok || !body.ok) {
    // Roll the lobby slot back so a failed init does not leak a record.
    await lobbyStub(env).fetch("http://arena.internal/release", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ roomId }),
    }).catch(() => {});
    return { status: initialized.status, body };
  }
  return {
    status: 200,
    body: {
      ok: true,
      roomId,
      joinPath: `/othello/${roomId}`,
      viewPath: `/othello/view?room=${roomId}`,
      snapshot: body.snapshot,
    },
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === "GET" && path === "/") return htmlResponse(landingPage());

    if (request.method === "GET" && path === "/othello") return htmlResponse(playerPage(null));

    if (request.method === "GET" && path.startsWith("/othello/")) {
      const rest = path.slice("/othello/".length);
      if (rest === "" || rest === "view") {
        const room = rest === "view" ? (url.searchParams.get("room") ?? "") : "";
        return htmlResponse(rest === "view" ? viewPage(ROOM_ID_RE.test(room) ? room : null) : playerPage(null));
      }
      if (!ROOM_ID_RE.test(rest)) {
        return jsonResponse(404, { ok: false, code: "unknown_room" });
      }
      return htmlResponse(playerPage(rest));
    }

    if (path === "/api/othello/rooms" && request.method === "POST") {
      const body = await readJsonBody(request);
      if (!body) return jsonResponse(400, { ok: false, code: "invalid_request" });
      const level = body.level ?? "normal";
      if (!AI_LEVELS.includes(level)) return jsonResponse(400, { ok: false, code: "invalid_level" });
      const result = await createRoom(env, level);
      return jsonResponse(result.status, result.body);
    }

    if (path.startsWith("/api/othello/rooms/")) {
      const rest = path.slice("/api/othello/rooms/".length);
      if (rest.endsWith("/ws")) {
        const roomId = rest.slice(0, -"/ws".length);
        if (!ROOM_ID_RE.test(roomId)) return jsonResponse(404, { ok: false, code: "unknown_room" });
        // Rewrite to the DO's internal path; the public URL is never seen by the room.
        return roomStub(env, roomId).fetch(new Request("https://arena.internal/ws", request));
      }
      if (!ROOM_ID_RE.test(rest)) return jsonResponse(404, { ok: false, code: "unknown_room" });
      if (request.method !== "GET") return jsonResponse(405, { ok: false, code: "method_not_allowed" });
      const response = await roomStub(env, rest).fetch("http://arena.internal/snapshot");
      return response;
    }

    return jsonResponse(404, { ok: false, code: "not_found" });
  },
};
