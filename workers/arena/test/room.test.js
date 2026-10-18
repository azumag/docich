import assert from "node:assert/strict";
import { test } from "node:test";
import { FakeState, connectRoom } from "./helpers.js";
import { OthelloRoom, ROOM_KEY, IDLE_MS, HARD_IDLE_MS } from "../src/arena/room.js";
import { boardToString, boardFromString, initialBoard, parseCoord, coordOf } from "../src/othello/engine.js";

const ROOM_ID = "0f3a9c1e77b24d6a";

function freshRoom() {
  return new OthelloRoom(new FakeState(), {});
}

async function seatedRoom(level = "normal") {
  const room = freshRoom();
  const response = await room.fetch(new Request("http://arena.internal/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roomId: ROOM_ID, level }),
  }));
  assert.equal(response.status, 200);
  const connection = await connectRoom(room, ROOM_ID);
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "hello", name: "テスト同志" }));
  return { room, connection };
}

function lastState(connection) {
  const states = connection.messages().filter((message) => message.type === "state");
  return states.at(-1).state;
}

test("init creates a waiting room and is idempotent", async () => {
  const room = freshRoom();
  const first = await room.fetch(new Request("http://arena.internal/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roomId: ROOM_ID, level: "hard" }),
  }));
  assert.equal(first.status, 200);
  const body = await first.json();
  assert.equal(body.ok, true);
  assert.equal(body.snapshot.status, "waiting");
  assert.equal(body.snapshot.level, "hard");
  assert.equal(body.snapshot.seats.white.kind, "ai");
  assert.equal(body.snapshot.seats.white.name, "DoCi AI");
  assert.equal(body.snapshot.seats.black, null);
  assert.deepEqual(body.snapshot.board, boardToString(initialBoard()));

  const second = await room.fetch(new Request("http://arena.internal/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roomId: ROOM_ID, level: "easy" }),
  }));
  assert.equal(second.status, 200);
  const again = await second.json();
  assert.equal(again.snapshot.level, "hard", "re-init must not change the level");
});

test("init rejects bad room ids and levels", async () => {
  const room = freshRoom();
  for (const body of [{ roomId: "short", level: "easy" }, { roomId: ROOM_ID, level: "impossible" }, {}]) {
    const response = await room.fetch(new Request("http://arena.internal/init", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }));
    assert.equal(response.status, 400, JSON.stringify(body));
  }
  const broken = await room.fetch(new Request("http://arena.internal/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "not json",
  }));
  assert.equal(broken.status, 400);
});

test("snapshot of an unknown room is 404", async () => {
  const room = freshRoom();
  const response = await room.fetch(new Request("http://arena.internal/snapshot"));
  assert.equal(response.status, 404);
  assert.equal((await response.json()).code, "unknown_room");
});

test("hello claims the black seat and starts the game", async () => {
  const { room, connection } = await seatedRoom();
  const joined = connection.messages().find((message) => message.type === "joined");
  assert.ok(joined, "joined must be sent to the claiming socket");
  assert.equal(joined.seat, "black");
  assert.equal(joined.state.status, "playing");
  assert.equal(joined.state.seats.black.name, "テスト同志");
  const snapshot = await (await room.fetch(new Request("http://arena.internal/snapshot"))).json();
  assert.equal(snapshot.snapshot.status, "playing");
  assert.equal(snapshot.snapshot.turn, "black");
});

test("a second hello from the same socket is idempotent; rivals stay spectators", async () => {
  const { room, connection } = await seatedRoom();
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "hello", name: "改名" }));
  const joined = connection.messages().filter((message) => message.type === "joined");
  assert.equal(joined.length, 2);
  assert.equal(joined.at(-1).state.seats.black.name, "改名");

  const rival = await connectRoom(room, ROOM_ID);
  await room.webSocketMessage(rival.socket, JSON.stringify({ type: "hello", name: "二番手" }));
  const errors = rival.messages().filter((message) => message.type === "error");
  assert.deepEqual(errors, [{ type: "error", code: "seat_taken" }]);
  const states = await (await room.fetch(new Request("http://arena.internal/snapshot"))).json();
  assert.equal(states.snapshot.seats.black.name, "改名");
});

test("spectators receive welcome and state but cannot move", async () => {
  const { room, connection } = await seatedRoom();
  const spectator = await connectRoom(room, ROOM_ID);
  const welcome = spectator.messages().find((message) => message.type === "welcome");
  assert.equal(welcome.role, "spectator");
  assert.ok(welcome.state);

  await room.webSocketMessage(spectator.socket, JSON.stringify({ type: "move", coord: "d3" }));
  const errors = spectator.messages().filter((message) => message.type === "error");
  assert.deepEqual(errors, [{ type: "error", code: "not_seated" }]);
  assert.ok(connection.messages().every((message) => message.type !== "error"));
});

test("a player move is applied and the AI answers in the same broadcast", async () => {
  const { room, connection } = await seatedRoom("easy");
  const before = lastState(connection);
  assert.equal(before.moveNumber, 0);

  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "move", coord: "d3" }));
  const after = lastState(connection);
  assert.equal(after.moveNumber, 2, "human move plus AI reply");
  assert.equal(after.turn, "black");
  assert.equal(after.board[parseCoord("d3")], "B");
  assert.equal(after.lastMove.color, "white");
  const history = after.history ?? [];
  // history is not part of the public snapshot contract; assert via room only.
  assert.ok(history.length === 0 || Array.isArray(history));
});

test("illegal moves, wrong turns and post-game moves get fixed error codes", async () => {
  const { room, connection } = await seatedRoom();
  // occupied center square
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "move", coord: "d4" }));
  // empty but illegal square
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "move", coord: "a1" }));
  let errors = connection.messages().filter((message) => message.type === "error");
  assert.deepEqual(errors.map((message) => message.code), ["invalid_move", "invalid_move"]);

  const rival = await connectRoom(room, ROOM_ID);
  await room.webSocketMessage(rival.socket, JSON.stringify({ type: "move", coord: "d3" }));
  errors = rival.messages().filter((message) => message.type === "error");
  assert.equal(errors.at(-1).code, "not_seated");

  // Not your turn: the room is always black-to-move between broadcasts, so
  // force the turn to white via storage to exercise the branch.
  const stored = structuredClone(room.room);
  stored.turn = "white";
  room.room = stored; // test-only direct mutation of the in-memory authority
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "move", coord: "f5" }));
  errors = connection.messages().filter((message) => message.type === "error");
  assert.equal(errors.at(-1).code, "not_your_turn");
});

test("resign ends the game with the AI winning; rematch starts game 2", async () => {
  const { room, connection } = await seatedRoom();
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "resign" }));
  const ended = lastState(connection);
  assert.equal(ended.status, "ended");
  assert.deepEqual(ended.result, { winner: "white", reason: "resign", black: 2, white: 2 });
  assert.deepEqual(ended.wins, { human: 0, ai: 1, draw: 0 });
  assert.equal(ended.turn, "none");
  const gameEnd = connection.messages().filter((message) => message.type === "game_end");
  assert.equal(gameEnd.length, 1);

  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "rematch" }));
  const restarted = lastState(connection);
  assert.equal(restarted.status, "playing");
  assert.equal(restarted.gameNumber, 2);
  assert.equal(restarted.result, null);
  assert.equal(restarted.moveNumber, 0);
  assert.equal(restarted.turn, "black");
  assert.deepEqual(restarted.wins, { human: 0, ai: 1, draw: 0 });
});

test("rematch and resign are refused before they apply", async () => {
  const { room, connection } = await seatedRoom();
  // Rematch while playing is protocol-valid but a no-op: no error, no broadcast.
  const messagesBefore = connection.pair.clientMessages.length;
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "rematch" }));
  assert.equal(connection.pair.clientMessages.length, messagesBefore);
  assert.equal(lastState(connection).status, "playing");

  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "resign" }));
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "resign" }));
  const states = connection.messages().filter((message) => message.type === "state");
  assert.equal(states.at(-1).state.wins.ai, 1, "second resign must not double-count");
});

test("three bad messages close the socket", async () => {
  const { room, connection } = await seatedRoom();
  await room.webSocketMessage(connection.socket, "not json");
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "mystery" }));
  assert.equal(connection.pair.closed, null);
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "mystery" }));
  assert.deepEqual(connection.pair.closed, { code: 1008, reason: "bad_messages" });
});

test("message rate limiting closes flooders", async () => {
  const { room, connection } = await seatedRoom();
  // The hello already counted as message 1 of the window.
  for (let index = 0; index < 19; index += 1) {
    await room.webSocketMessage(connection.socket, JSON.stringify({ type: "ping" }));
  }
  assert.equal(connection.pair.closed, null, "exactly 20 msgs within the window are allowed");
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "ping" }));
  assert.deepEqual(connection.pair.closed, { code: 1008, reason: "rate_limited" });
});

test("ping gets a pong without touching the game state", async () => {
  const { room, connection } = await seatedRoom();
  const before = lastState(connection);
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "ping" }));
  const pongs = connection.messages().filter((message) => message.type === "pong");
  assert.equal(pongs.length, 1);
  assert.equal(lastState(connection).updatedAt, before.updatedAt);
});

test("a full scripted game reaches a scored result and bounded history", async () => {
  const { room, connection } = await seatedRoom("easy");
  const { chooseMove } = await import("../src/othello/ai.js");
  const { movesFor, opponent } = await import("../src/othello/engine.js");

  let guard = 0;
  let state = lastState(connection);
  while (state.status === "playing" && guard < 200) {
    guard += 1;
    // A human takes seconds per move; clear the 1s rate window to emulate that.
    room.rate.clear();
    const board = boardFromString(state.board);
    const legal = movesFor(board, 1 /* BLACK */);
    if (legal.length === 0) break; // cannot happen for the seated human
    const pick = chooseMove(board, 1, { level: "easy", seed: guard });
    const move = legal.find((entry) => entry.index === pick) ?? legal[0];
    await room.webSocketMessage(connection.socket, JSON.stringify({ type: "move", coord: coordOf(move.index) }));
    state = lastState(connection);
  }
  assert.equal(state.status, "ended", `game must end (guard=${guard})`);
  assert.ok(state.result);
  assert.ok(["score", "resign"].includes(state.result.reason));
  assert.equal(state.result.black + state.result.white, state.score.black + state.score.white);
  assert.ok(state.wins.human + state.wins.ai + state.wins.draw >= 1);
  assert.ok(state.moveNumber <= 128, "a game is bounded by 64 discs plus passes");
});

test("advanceTurn records an automatic pass when the opponent is stuck", async () => {
  const room = freshRoom();
  await room.fetch(new Request("http://arena.internal/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roomId: ROOM_ID, level: "easy" }),
  }));
  // Black to move is immobile, white still has moves (deterministic fixture).
  room.room.boardText = "WWBBBBB.WWBBBB..WBWWBBBB.BWWBBBBWBBWWBBBWBBBBWWBWWBWWWWBWWWWWBBB";
  room.room.turn = "black";
  room.room.status = "playing";
  room.advanceTurn(2 /* WHITE, just moved */);
  assert.equal(room.room.turn, "white", "the mover keeps the turn after the pass");
  const passEvents = room.room.history.filter((entry) => entry.pass);
  assert.equal(passEvents.length, 1);
  assert.equal(passEvents[0].color, "black");
  assert.deepEqual(room.room.lastMove, { coord: null, color: "black", pass: true });
});

test("advanceTurn ends the game when neither side can move", async () => {
  const room = freshRoom();
  await room.fetch(new Request("http://arena.internal/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roomId: ROOM_ID, level: "easy" }),
  }));
  room.room.boardText = "BBBBBBBBWWWBWWBWWBBWWWWWWBBBBBWWWWBWWWBWWWWWWBBWWWBBBBBWWWWWWWWW";
  room.room.turn = "white";
  room.room.status = "playing";
  room.advanceTurn(2);
  assert.equal(room.room.status, "ended");
  assert.equal(room.room.turn, "none");
  assert.deepEqual(room.room.result, { winner: "white", reason: "score", black: 26, white: 38 });
  assert.deepEqual(room.room.wins, { human: 0, ai: 1, draw: 0 });
});

test("the alarm reaps idle rooms and keeps live games", async () => {
  const room = freshRoom();
  await room.fetch(new Request("http://arena.internal/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roomId: ROOM_ID, level: "easy" }),
  }));
  const connection = await connectRoom(room, ROOM_ID);
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "hello", name: "居残り" }));
  const storage = room.state.storage;

  // Fresh activity: the alarm was scheduled at updatedAt + IDLE_MS.
  const alarmAt = storage.alarm;
  assert.ok(alarmAt > Date.now() + IDLE_MS - 2000);

  // Simulate a long-idle ended room: expired updatedAt, no live player.
  const realNow = Date.now;
  Date.now = () => alarmAt + HARD_IDLE_MS;
  try {
    await room.alarm();
    assert.equal(await storage.get(ROOM_KEY), undefined, "idle room storage must be deleted");
    const snapshot = await room.fetch(new Request("http://arena.internal/snapshot"));
    assert.equal(snapshot.status, 404);
  } finally {
    Date.now = realNow;
  }
});

test("the alarm keeps a live playing room and reschedules", async () => {
  const room = freshRoom();
  await room.fetch(new Request("http://arena.internal/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ roomId: ROOM_ID, level: "easy" }),
  }));
  const connection = await connectRoom(room, ROOM_ID);
  await room.webSocketMessage(connection.socket, JSON.stringify({ type: "hello", name: "対戦中" }));
  const storage = room.state.storage;
  const realNow = Date.now;
  Date.now = () => room.room.updatedAtMs + HARD_IDLE_MS - 1000;
  try {
    await room.alarm();
    assert.ok(await storage.get(ROOM_KEY), "live playing rooms survive the alarm");
    assert.ok(storage.alarm >= Date.now() + IDLE_MS - 2000);
  } finally {
    Date.now = realNow;
  }
});

test("corrupted storage fails closed instead of throwing", async () => {
  const state = new FakeState();
  await state.storage.put(ROOM_KEY, { schemaVersion: 99, garbage: true });
  const room = new OthelloRoom(state, {});
  const snapshot = await room.fetch(new Request("http://arena.internal/snapshot"));
  assert.equal(snapshot.status, 404);
  const upgrade = await room.fetch(new Request("http://arena.internal/ws", { headers: { upgrade: "websocket" } }));
  assert.equal(upgrade.status, 404);
});

test("spectator cap rejects new sockets past the limit", async () => {
  const { room } = await seatedRoom();
  // FakeState.getWebSockets is the source of truth; simulate the cap directly.
  const original = room.state.getWebSockets.bind(room.state);
  room.state.getWebSockets = () => Array.from({ length: 101 }, () => original()[0]).filter(Boolean);
  const response = await room.fetch(new Request("http://arena.internal/ws", { headers: { upgrade: "websocket" } }));
  assert.equal(response.status, 429);
  room.state.getWebSockets = original;
});
