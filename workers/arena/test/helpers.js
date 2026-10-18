/** Test doubles for Durable Object state, storage and WebSockets.
 *
 * Mirrors the workerd contracts the room relies on: get/put/delete/list,
 * alarms, acceptWebSocket/getWebSockets, per-socket attachments, and the
 * WebSocketPair global. The pair's client side records every send so tests can
 * assert the exact wire protocol.
 */
import { ArenaLobby } from "../src/arena/lobby.js";
import { OthelloRoom } from "../src/arena/room.js";

export class Storage {
  constructor(initial) {
    this.data = new Map(initial ?? []);
    this.alarm = null;
  }

  async get(key) {
    const value = this.data.get(key);
    return value === undefined ? undefined : structuredClone(value);
  }

  async put(key, value) {
    this.data.set(key, structuredClone(value));
  }

  async delete(key) {
    this.data.delete(key);
  }

  async deleteAll() {
    this.data.clear();
  }

  async list(options = {}) {
    const out = new Map();
    for (const [key, value] of this.data) {
      if (!options.prefix || key.startsWith(options.prefix)) out.set(key, structuredClone(value));
    }
    return out;
  }

  async setAlarm(value) {
    this.alarm = value;
  }

  async getAlarm() {
    return this.alarm;
  }

  async deleteAlarm() {
    this.alarm = null;
  }
}

export class FakeWebSocketPair {
  constructor() {
    this.clientMessages = [];
    this.closed = null;
    this.closedClient = false;
    this.client = { kind: "fake-client-socket" };
    this.server = {
      pair: this,
      attachment: null,
      send: (data) => {
        if (this.closed !== null) throw new Error("send after close");
        this.clientMessages.push(data);
      },
      close: (code, reason) => {
        if (this.closed === null) this.closed = { code, reason };
      },
      serializeAttachment: (value) => {
        this.server.attachment = structuredClone(value);
      },
      deserializeAttachment: () => {
        return this.server.attachment === null ? null : structuredClone(this.server.attachment);
      },
    };
    // Mirror the workerd pair shape: Object.values(new WebSocketPair())
    // yields [client, server].
    this[0] = this.client;
    this[1] = this.server;
  }
}

export class FakeState {
  constructor() {
    this.storage = new Storage();
    this.sockets = [];
  }

  acceptWebSocket(socket) {
    this.sockets.push(socket);
  }

  getWebSockets() {
    return this.sockets.filter((socket) => socket.pair.closed === null);
  }
}

/** One stable env per test: rooms keyed by id name, one shared lobby.
 * The stub fetches mirror the real binding contract: (input, init) in,
 * Request out to the DO. */
export function makeEnv() {
  const rooms = new Map();
  const lobbyState = new FakeState();
  const lobby = new ArenaLobby(lobbyState, {});
  return {
    rooms,
    lobby,
    lobbyState,
    ARENA_LOBBY: {
      idFromName: () => "lobby",
      get: () => ({
        fetch: (input, init) => {
          const request = input instanceof Request ? input : new Request(input, init);
          return lobby.fetch(request);
        },
      }),
    },
    ARENA_ROOM: {
      idFromName: (name) => name,
      get: (id) => {
        if (!rooms.has(id)) rooms.set(id, new OthelloRoom(new FakeState(), {}));
        const room = rooms.get(id);
        return {
          fetch: (input, init) => {
            const request = input instanceof Request ? input : new Request(input, init);
            return room.fetch(request);
          },
        };
      },
    },
  };
}

/** Open a WebSocket against a room DO and return {socket, pair, messages}. */
export async function connectRoom(room, roomId) {
  const request = new Request(`http://arena.internal/ws?room=${roomId}`, {
    headers: { upgrade: "websocket" },
  });
  const response = await room.fetch(request);
  if (!response.webSocket) {
    throw new Error(`upgrade failed: ${response.status}`);
  }
  const socket = room.state.sockets.at(-1);
  const messages = () => socket.pair.clientMessages.map((raw) => JSON.parse(raw));
  return { socket, pair: socket.pair, messages };
}

export async function initRoom(env, roomId, level = "normal") {
  const response = await env.ARENA_ROOM.get(roomId).fetch("http://arena.internal/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roomId, level }),
  });
  return response;
}

// The room constructs `new WebSocketPair()` (a workerd global). Install the
// test double for the unit-test process; the workerd integration test uses the
// real one inside miniflare. Assigned after the class definitions above.
if (typeof globalThis.WebSocketPair === "undefined") {
  globalThis.WebSocketPair = FakeWebSocketPair;
}
