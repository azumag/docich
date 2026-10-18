import assert from "node:assert/strict";
import { test } from "node:test";
import worker, { createRoom } from "../src/worker.js";
import { makeEnv } from "./helpers.js";

const JAPANESE_PAGE_MARKERS = ['lang="ja"', 'translate="no"', 'name="google" content="notranslate"'];

test("landing and client pages carry the Japanese page markers", async () => {
  for (const path of ["/", "/othello", "/othello/view?room=0f3a9c1e77b24d6a"]) {
    const response = await worker.fetch(new Request(`https://arena.test${path}`), makeEnv());
    assert.equal(response.status, 200, path);
    assert.match(response.headers.get("content-type"), /text\/html; charset=utf-8/);
    assert.equal(response.headers.get("cache-control"), "no-store");
    const html = await response.text();
    for (const marker of JAPANESE_PAGE_MARKERS) {
      assert.ok(html.includes(marker), `${path} must contain ${marker}`);
    }
  }
});

test("the player page embeds the room id and no room id means the setup form", async () => {
  const env = makeEnv();
  const plain = await (await worker.fetch(new Request("https://arena.test/othello"), env)).text();
  assert.ok(plain.includes("AIと対戦を始める"));
  assert.ok(plain.includes("var ROOM_ID=null"));
  const joined = await (await worker.fetch(new Request("https://arena.test/othello/0f3a9c1e77b24d6a"), env)).text();
  assert.ok(joined.includes('var ROOM_ID="0f3a9c1e77b24d6a"'));
  assert.ok(joined.includes('byId("setup").classList.add("hidden")'), "join page hides the setup form");
});

test("unknown paths and malformed room ids return fixed JSON errors", async () => {
  const env = makeEnv();
  for (const [method, path, expected] of [
    ["GET", "/nowhere", 404],
    ["GET", "/othello/nothex", 404],
    ["GET", "/othello/0f3a9c1e77b24d6g", 404],
    ["GET", "/api/othello/rooms/0f3a9c1e77b24d6a", 404],
    ["GET", "/api/othello/rooms/short/ws", 404],
    ["POST", "/api/othello/rooms/0f3a9c1e77b24d6a", 405],
    ["GET", "/api/nothing", 404],
  ]) {
    const response = await worker.fetch(new Request(`https://arena.test${path}`, { method }), env);
    assert.equal(response.status, expected, `${method} ${path}`);
    const body = await response.json();
    assert.equal(body.ok, false);
  }
});

test("room creation validates the level and body size", async () => {
  const env = makeEnv();
  const bad = await worker.fetch(new Request("https://arena.test/api/othello/rooms", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ level: "impossible" }),
  }), env);
  assert.equal(bad.status, 400);
  assert.equal((await bad.json()).code, "invalid_level");

  const garbage = await worker.fetch(new Request("https://arena.test/api/othello/rooms", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "not json",
  }), env);
  assert.equal(garbage.status, 400);

  const huge = await worker.fetch(new Request("https://arena.test/api/othello/rooms", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ level: "normal", pad: "x".repeat(2000) }),
  }), env);
  assert.equal(huge.status, 400);
});

test("room creation returns the snapshot contract and the room answers", async () => {
  const env = makeEnv();
  const created = await worker.fetch(new Request("https://arena.test/api/othello/rooms", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ level: "hard" }),
  }), env);
  assert.equal(created.status, 200);
  const body = await created.json();
  assert.equal(body.ok, true);
  assert.match(body.roomId, /^[0-9a-f]{16}$/);
  assert.equal(body.joinPath, `/othello/${body.roomId}`);
  assert.equal(body.viewPath, `/othello/view?room=${body.roomId}`);
  assert.equal(body.snapshot.kind, "arena_othello");
  assert.equal(body.snapshot.level, "hard");
  assert.equal(body.snapshot.schemaVersion, 1);

  const snapshot = await worker.fetch(new Request(`https://arena.test/api/othello/rooms/${body.roomId}`), env);
  assert.equal(snapshot.status, 200);
  const data = await snapshot.json();
  assert.equal(data.ok, true);
  assert.equal(data.snapshot.roomId, body.roomId);
  assert.equal(data.snapshot.status, "waiting");
});

test("createRoom helper matches the endpoint and rolls back failed init", async (t) => {
  const env = makeEnv();
  const result = await createRoom(env, "easy");
  assert.equal(result.status, 200);
  assert.ok(env.rooms.has(result.body.roomId));

  // Force the room init to fail: make the room DO refuse /init.
  const failing = makeEnv();
  failing.ARENA_ROOM = {
    idFromName: (name) => name,
    get: () => ({ fetch: async () => new Response(JSON.stringify({ ok: false, code: "boom" }), { status: 500 }) }),
  };
  const failed = await createRoom(failing, "normal");
  assert.equal(failed.status, 500);
  const count = await failing.ARENA_LOBBY.get("x").fetch(new Request("http://arena.internal/count"));
  const countBody = await count.json();
  assert.equal(countBody.activeRooms, 0, "a failed init must not leak a lobby slot");
});

test("the lobby cap turns excess room creation into 429", async () => {
  const env = makeEnv();
  let created = 0;
  for (let index = 0; index < 20; index += 1) {
    const response = await worker.fetch(new Request("https://arena.test/api/othello/rooms", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ level: "easy" }),
    }), env);
    if (response.status === 200) created += 1;
    else {
      assert.equal(response.status, 429);
      assert.equal((await response.json()).code, "room_cap");
    }
  }
  assert.equal(created, 16);
});
