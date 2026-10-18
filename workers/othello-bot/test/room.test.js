import { strict as assert } from "node:assert";
import test from "node:test";

import {
  OthelloRoom, ROOM_ID_PATTERN, spectatorSnapshot, STATE_KEY, VIEWER_NAME_PATTERN,
} from "../src/room.js";
import { legalMoves, VIEWER_PLAYER } from "../src/rules.js";

function memoryStorage() {
  const map = new Map();
  return {
    async get(key) { return map.get(key); },
    async put(key, value) { map.set(key, structuredClone(value)); },
    async delete(key) { map.delete(key); },
    async transaction(operation) {
      const tx = {
        get: (key) => map.get(key),
        put: (key, value) => { map.set(key, structuredClone(value)); },
        delete: (key) => { map.delete(key); },
      };
      return operation(tx);
    },
    _map: map,
  };
}

function makeRoom() {
  const storage = memoryStorage();
  const room = new OthelloRoom({ storage }, {});
  return { room, storage };
}

function dispatch(room, route, payload) {
  const result = room.dispatch({ route, payload });
  room.tail.catch(() => {});
  return result;
}

test("lifecycle: idle -> open -> playing -> viewer/AI exchange -> turn stays with viewer", async () => {
  const { room } = makeRoom();
  const opened = await dispatch(room, "openRoom", { roomId: "room-1", viewer: "comrade" });
  assert.equal(opened.phase, "ROOM_OPEN");
  const started = await dispatch(room, "startMatch", {});
  assert.equal(started.phase, "PLAYING");
  assert.equal(started.turn, "black");
  assert.deepEqual(started.scores, { black: 2, white: 2 });

  // Viewer d3 + the AI's reply commit in one dispatch; the turn stays black.
  const after = await dispatch(room, "viewerMove", { row: 2, col: 3 });
  assert.equal(after.movesPlayed, 2);
  assert.equal(after.turn, "black");
  assert.equal(after.phase, "PLAYING");
  // Both sides flipped d4/d5 territory: the opening settles to 3+3.
  assert.deepEqual(after.scores, { black: 3, white: 3 });
});

test("double open is refused and the first room state survives", async () => {
  const { room } = makeRoom();
  await dispatch(room, "openRoom", { roomId: "room-1", viewer: "comrade" });
  await assert.rejects(
    () => dispatch(room, "openRoom", { roomId: "room-2", viewer: "second" }),
    (error) => error.message === "room_not_idle",
  );
  const snap = await dispatch(room, "snapshot", {});
  assert.equal(snap.roomId, "room-1");
});

test("moves before start, out-of-turn and illegal moves are refused", async () => {
  const { room } = makeRoom();
  await assert.rejects(() => dispatch(room, "viewerMove", { row: 2, col: 3 }), /not_playing/);
  await dispatch(room, "openRoom", { roomId: "room-1", viewer: "comrade" });
  await assert.rejects(() => dispatch(room, "viewerMove", { row: 2, col: 3 }), /not_playing/);
  await dispatch(room, "startMatch", {});
  await assert.rejects(() => dispatch(room, "viewerMove", { row: 3, col: 3 }), /illegal_move/);
  await assert.rejects(() => dispatch(room, "viewerMove", { row: 99, col: 0 }), /invalid_move/);
  await assert.rejects(() => dispatch(room, "viewerMove", { row: 0.5, col: 3 }), /invalid_move/);
});

test("fail and timeout close the room fail-closed with no invented result", async () => {
  const { room } = makeRoom();
  await dispatch(room, "openRoom", { roomId: "room-1", viewer: "comrade" });
  await dispatch(room, "startMatch", {});
  const failed = await dispatch(room, "fail", { reason: "disc sync lost" });
  assert.equal(failed.phase, "FAILED");
  assert.equal(failed.outcome, "aborted");
  const { room: room2 } = makeRoom();
  await dispatch(room2, "openRoom", { roomId: "room-2", viewer: "comrade" });
  await dispatch(room2, "startMatch", {});
  const timedOut = await dispatch(room2, "timeout", {});
  assert.equal(timedOut.phase, "TIMEOUT");
  assert.equal(timedOut.outcome, "timeout");
  const resnap = await dispatch(room, "snapshot", {});
  assert.equal(resnap.phase, "FAILED");
  assert.equal(resnap.outcome, "aborted");
});

test("unknown dispatch route is refused and never poisons the tail", async () => {
  const { room } = makeRoom();
  await assert.rejects(() => room.dispatch({ route: "encrypt", payload: {} }), /dispatch_unknown/);
  const snap = await dispatch(room, "snapshot", {});
  assert.equal(snap.phase, "IDLE");
});

test("concurrent moves serialize: one viewer move plus one AI reply land together", async () => {
  const { room } = makeRoom();
  await dispatch(room, "openRoom", { roomId: "room-1", viewer: "comrade" });
  await dispatch(room, "startMatch", {});
  const [first, second] = await Promise.allSettled([
    dispatch(room, "viewerMove", { row: 2, col: 3 }),
    dispatch(room, "viewerMove", { row: 3, col: 2 }),
  ]);
  assert.equal(first.status, "fulfilled");
  // The second request waited on the tail, then applied on the fresh board.
  assert.equal(second.status, "fulfilled");
  const snap = await dispatch(room, "snapshot", {});
  assert.equal(snap.movesPlayed, 4);
  assert.equal(snap.turn, "black");
});

test("spectator snapshot hides nothing for othello but never leaks raw state", async () => {
  const { room, storage } = makeRoom();
  const idle = spectatorSnapshot({ schema: 1, phase: "IDLE" });
  assert.equal(idle.board, null);
  await dispatch(room, "openRoom", { roomId: "room-1", viewer: "comrade" });
  await dispatch(room, "startMatch", {});
  await dispatch(room, "viewerMove", { row: 2, col: 3 });
  const snap = spectatorSnapshot(await storage.get(STATE_KEY));
  // Playing room: yourTurn reflects the viewer's turn; terminal room: it is
  // gone entirely (never a stale boolean).
  assert.equal(snap.yourTurn, true);
  assert.ok(snap.board.every((row) => row.every((cell) => [0, 1, 2].includes(cell))));
  assert.ok(!("viewer" in snap));
  assert.ok(!("failureReason" in snap));
  const closed = spectatorSnapshot({ ...(await storage.get(STATE_KEY)), phase: "FAILED", turn: null });
  assert.strictEqual(closed.yourTurn, undefined);
});

test("room and viewer name patterns are strict", () => {
  assert.equal(ROOM_ID_PATTERN.test("room-1"), true);
  assert.equal(ROOM_ID_PATTERN.test("../etc"), false);
  assert.equal(ROOM_ID_PATTERN.test("Room"), false);
  assert.equal(VIEWER_NAME_PATTERN.test("comrade_01"), true);
  assert.equal(VIEWER_NAME_PATTERN.test(""), false);
  assert.equal(VIEWER_NAME_PATTERN.test(" dark knight "), false);
});

// ---------------------------------------------------------------------------
// pass regression (#2490 review): viewerMove used to commit a single AI reply.
// When the viewer had no legal move afterwards but the AI still did, the turn
// stuck on white and the room froze (not_your_turn forever, no alarm). Seeded
// self-play must always finish, must never observe turn=white between
// dispatches, and must exercise the pass chain (one commit applying 2+ AI
// moves) so the fixed path cannot quietly regress.
// ---------------------------------------------------------------------------

/** mulberry32: deterministic PRNG; the seed list below is pinned to it. */
function mulberry32(seed) {
  let state = seed >>> 0;
  return function next() {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

test("pass regression: seeded self-play never freezes and exercises the AI pass chain", async () => {
  // seed 11 is the originally-frozen game; 21/32 replay 3-move AI chains.
  for (const seed of [11, 18, 21, 32]) {
    const { room } = makeRoom();
    await dispatch(room, "openRoom", { roomId: `pass-${seed}`, viewer: "comrade" });
    await dispatch(room, "startMatch", {});
    const random = mulberry32(seed);
    let snapshot = await dispatch(room, "snapshot", {});
    let guard = 0;
    let maxStride = 0;
    let previousMoves = 0;
    while (snapshot.phase === "PLAYING" && guard < 400) {
      guard += 1;
      // Freeze signature: the AI seat never holds the turn between dispatches.
      assert.equal(snapshot.turn, "black", `seed ${seed}: turn stuck on ${snapshot.turn}`);
      const moves = legalMoves(snapshot.board, VIEWER_PLAYER);
      assert.ok(moves.length > 0, `seed ${seed}: viewer cannot be stuck while playing`);
      const pick = moves[Math.floor(random() * moves.length) % moves.length];
      snapshot = await dispatch(room, "viewerMove", { row: pick[0], col: pick[1] });
      maxStride = Math.max(maxStride, snapshot.movesPlayed - previousMoves);
      previousMoves = snapshot.movesPlayed;
    }
    assert.equal(snapshot.phase, "FINISHED", `seed ${seed}: game must finish`);
    // The pass chain must actually run: one commit applying 2+ AI moves. The
    // pre-fix code could never exceed a stride of 2.
    assert.ok(maxStride >= 3, `seed ${seed}: pass chain not exercised (max stride ${maxStride})`);
    // result projection regression: the finished snapshot carries the score.
    assert.ok(snapshot.result, `seed ${seed}: finished snapshot must carry result`);
    assert.equal(typeof snapshot.result.black, "number");
    assert.equal(typeof snapshot.result.white, "number");
  }
});
