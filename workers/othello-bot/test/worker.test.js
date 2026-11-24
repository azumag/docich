import { strict as assert } from "node:assert";
import test from "node:test";

import worker from "../src/worker.js";
import * as workerModule from "../src/worker.js";
import { OthelloRoom } from "../src/room.js";

function memoryStorage() {
  const map = new Map();
  return {
    async get(key) { return map.get(key); },
    async put(key, value) { map.set(key, structuredClone(value)); },
    async transaction(op) {
      const tx = { get: (k) => map.get(k), put: (k, v) => { map.set(k, structuredClone(v)); } };
      return op(tx);
    },
  };
}

/** A stub DO namespace: real class instances behind the same getInstance shape. */
function fakeNamespace() {
  const instances = new Map();
  return {
    idFromName: (name) => name,
    get(id) {
      if (!instances.has(id)) {
        const room = new OthelloRoom({ storage: memoryStorage() }, {});
        instances.set(id, {
          dispatch: (payload) => {
            const result = room.dispatch(payload);
            room.tail.catch(() => {});
            return result;
          },
        });
      }
      return instances.get(id);
    },
  };
}

const env = { OTHELLO_ROOM: fakeNamespace() };
const POST = (path, body) => new Request(`https://w.test${path}`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: typeof body === "string" ? body : JSON.stringify(body),
});

const BASE = "https://w.test";

test("full HTTP lifecycle through the worker surface", async () => {
  let response = await worker.fetch(new Request(`${BASE}/room/snapshot?id=main`), env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    phase: "IDLE", roomId: null, board: null, turn: null, scores: null, movesPlayed: 0, outcome: null,
  });

  response = await worker.fetch(POST("/room/open-room", { roomId: "main", viewer: "comrade" }), env);
  assert.equal(response.status, 200);
  assert.equal((await response.json()).phase, "ROOM_OPEN");

  await worker.fetch(POST("/room/start-match", { roomId: "main" }), env);
  response = await worker.fetch(POST("/room/viewer-move", { roomId: "main", row: 2, col: 3 }), env);
  const after = await response.json();
  assert.equal(after.movesPlayed, 2);
  assert.equal(after.turn, "black");
});

test("unknown paths and methods are fixed 404s; snapshot GET only", async () => {
  assert.equal((await worker.fetch(new Request(`${BASE}/nope`), env)).status, 404);
  assert.equal((await worker.fetch(new Request(`${BASE}/room/nothing`), env)).status, 404);
  assert.equal((await worker.fetch(POST("/room/snapshot", { roomId: "x" }), env)).status, 404);
  // snapshot with a bad id is a fixed 400 code, never a stack trace
  const bad = await worker.fetch(new Request(`${BASE}/room/snapshot?id=BAD ID`), env);
  assert.equal(bad.status, 400);
  assert.deepEqual(await bad.json(), { error: "invalid_room_id" });
});

test("body and content-type guards map onto fixed codes", async () => {
  const noType = new Request(`${BASE}/room/open-room`, { method: "POST", body: "{}" });
  const response = await worker.fetch(noType, env);
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "content_type_required" });

  const tooBig = POST("/room/open-room", { roomId: "r", viewer: "v", blob: "x".repeat(6000) });
  const big = await worker.fetch(tooBig, env);
  assert.equal(big.status, 413);
  assert.deepEqual(await big.json(), { error: "body_too_large" });

  const notJson = new Request(`${BASE}/room/open-room`, {
    method: "POST", headers: { "content-type": "application/json" }, body: "{oops",
  });
  assert.equal((await worker.fetch(notJson, env)).status, 400);
});

test("room-level errors surface as one 409 safe code", async () => {
  await worker.fetch(POST("/room/open-room", { roomId: "r1", viewer: "comrade" }), env);
  const again = await worker.fetch(POST("/room/open-room", { roomId: "r1", viewer: "again" }), env);
  assert.equal(again.status, 409);
  assert.deepEqual(await again.json(), { error: "room_not_idle" });
});

test("worker re-exports OthelloRoom for the DO binding", () => {
  assert.equal(workerModule.OthelloRoom, OthelloRoom);
});

test("a missing DO binding degrades to a fixed 503", async () => {
  const response = await worker.fetch(new Request(`${BASE}/room/snapshot?id=x`), {});
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "room_unavailable" });
});
