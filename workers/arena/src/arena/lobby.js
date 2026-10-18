/** ArenaLobby: cap on concurrently allocatable rooms (fail-closed).
 *
 * The lobby deliberately knows nothing about room internals: it only records
 * allocations and enforces a cap so a burst of POSTs cannot create unbounded
 * Durable Objects. Records are reaped by an alarm after ROOM_RECORD_TTL_MS
 * regardless of room liveness; rooms clean up their own storage on their own
 * alarm. The cap is therefore approximate (an anti-abuse bound), not exact
 * accounting, and there is no public room listing.
 *
 * No DO-to-DO calls in either direction: the worker reaches the lobby through
 * the ARENA_LOBBY binding, rooms never call back.
 */
export const LOBBY_SINGLETON = "lobby";
export const ROOM_CAP = 16;
export const ROOM_RECORD_TTL_MS = 30 * 60 * 1000;
export const REAP_INTERVAL_MS = 60 * 1000;

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

export class ArenaLobby {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.tail = Promise.resolve();
  }

  /** Serialize every operation: DO events may interleave, storage keys must not. */
  runExclusive(operation) {
    const result = this.tail.then(operation, operation);
    this.tail = result.catch(() => {});
    return result;
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/count") {
      const count = await this.runExclusive(() => this.countRecords());
      return jsonResponse(200, { ok: true, activeRooms: count, cap: ROOM_CAP });
    }
    if (request.method === "POST" && url.pathname === "/alloc") {
      let body;
      try {
        body = await request.json();
      } catch {
        return jsonResponse(400, { ok: false, code: "invalid_request" });
      }
      if (body === null || typeof body !== "object") {
        return jsonResponse(400, { ok: false, code: "invalid_request" });
      }
      const roomId = typeof body.roomId === "string" ? body.roomId : "";
      if (!/^[0-9a-f]{16}$/.test(roomId)) {
        return jsonResponse(400, { ok: false, code: "invalid_room_id" });
      }
      const allocated = await this.runExclusive(() => this.alloc(roomId));
      if (!allocated) return jsonResponse(429, { ok: false, code: "room_cap" });
      return jsonResponse(200, { ok: true });
    }
    if (request.method === "POST" && url.pathname === "/release") {
      let body;
      try {
        body = await request.json();
      } catch {
        return jsonResponse(400, { ok: false, code: "invalid_request" });
      }
      const roomId = typeof body?.roomId === "string" ? body.roomId : "";
      if (!/^[0-9a-f]{16}$/.test(roomId)) {
        return jsonResponse(400, { ok: false, code: "invalid_room_id" });
      }
      await this.runExclusive(() => this.state.storage.delete(`room:${roomId}`));
      return jsonResponse(200, { ok: true });
    }
    return jsonResponse(404, { ok: false, code: "not_found" });
  }

  async countRecords() {
    const entries = await this.state.storage.list({ prefix: "room:", limit: ROOM_CAP + 1 });
    return entries.size;
  }

  async alloc(roomId) {
    const entries = await this.state.storage.list({ prefix: "room:", limit: ROOM_CAP + 1 });
    if (entries.size >= ROOM_CAP) return false;
    if (entries.has(`room:${roomId}`)) return true; // idempotent re-alloc
    await this.state.storage.put(`room:${roomId}`, { createdAtMs: Date.now() });
    await this.ensureReapAlarm();
    return true;
  }

  async ensureReapAlarm() {
    const current = await this.state.storage.getAlarm();
    if (current === null) await this.state.storage.setAlarm(Date.now() + REAP_INTERVAL_MS);
  }

  async alarm() {
    await this.runExclusive(async () => {
      const now = Date.now();
      const entries = await this.state.storage.list({ prefix: "room:" });
      const remaining = [];
      for (const [key, record] of entries) {
        const createdAt = record && Number.isFinite(record.createdAtMs) ? record.createdAtMs : 0;
        if (now - createdAt > ROOM_RECORD_TTL_MS) await this.state.storage.delete(key);
        else remaining.push(record.createdAtMs);
      }
      if (remaining.length > 0) {
        await this.state.storage.setAlarm(Math.min(...remaining) + ROOM_RECORD_TTL_MS);
      }
    });
  }
}
