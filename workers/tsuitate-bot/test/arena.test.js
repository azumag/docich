import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { hostname } from "node:os";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { BRAIN_VERSION, LINEAR_PROFILE } from "../src/brain/index.js";
import { BetaSession } from "../src/arena/beta-session.js";
import { ArenaStore } from "../src/arena/store.js";
import { parsePublicResult, fetchPublicResult } from "../src/adapters/beta-results.js";
import { report } from "../src/training/index.js";

class Socket {
  constructor() { this.handlers = new Map(); this.sent = []; this.connected = false; }
  on(event, handler) { const items = this.handlers.get(event) ?? []; items.push(handler); this.handlers.set(event, items); return this; }
  off(event, handler) { this.handlers.set(event, (this.handlers.get(event) ?? []).filter((item) => item !== handler)); return this; }
  server(event, ...args) { for (const handler of [...(this.handlers.get(event) ?? [])]) handler(...args); }
  connect() { this.connected = true; this.server("connect"); }
  disconnect() { if (this.connected) { this.connected = false; this.server("disconnect", "transport close", {}); } }
  timeout() { return this; }
  emit(event, ...args) {
    const callback = typeof args.at(-1) === "function" ? args.pop() : null;
    const packet = { event, payload: args[0], callback };
    this.sent.push(packet);
    this.onSend?.(packet);
    return this;
  }
  packets(event) { return this.sent.filter((packet) => packet.event === event); }
  ack(event, value, index = -1, error = null) { const packet = this.packets(event).at(index); assert.ok(packet?.callback, event); packet.callback(error, value); }
}

class Store {
  constructor() { this.saves = []; this.records = []; }
  async save(value) { this.saves.push(structuredClone(value)); }
  async finish(value) { this.records.push(structuredClone(value)); }
}

function view(overrides = {}) {
  return { gameId: "test-game", yourColor: "sente", yourPieces: [{ square: "5i", role: "king" },
    { square: "5g", role: "pawn" }, { square: "2h", role: "rook" }], yourHand: {},
  turn: "sente", moveNumber: 1, clocks: { senteMs: 300000, goteMs: 300000, running: "sente", serverTime: 100 },
  fouls: { you: 0, opponent: 0 }, youInCheck: false, opponentInCheck: false, status: "playing", ...overrides };
}

async function flush(session) {
  for (let index = 0; index < 8; index += 1) { await session.tail; await Promise.resolve(); }
}

function setup(t, options = {}) {
  const socket = new Socket();
  const store = new Store();
  const events = [];
  const session = new BetaSession({ socket, store, profile: LINEAR_PROFILE, resolveResult: async () => null,
    log: (event) => events.push(event), queueWaitMs: 60000, pollMs: 60000, retryMs: 60000, queueSettleMs: 10, ...options });
  t.after(() => session.close());
  session.start();
  return { socket, store, session, events };
}

async function begin(context) {
  const { socket, session } = context;
  await flush(session);
  socket.ack("queue:join", { ok: true });
  socket.server("match:found", { gameId: "test-game", yourColor: "sente" });
  await flush(session);
  socket.ack("game:sync", { state: view() });
  await flush(session);
  assert.equal(socket.packets("game:move").length, 1);
}

function advancedView(socket, number = 3) {
  const moved = view();
  const usi = socket.packets("game:move")[0].payload.usi;
  const piece = moved.yourPieces.find((item) => item.square === usi.slice(0, 2));
  piece.square = usi.slice(2, 4);
  moved.moveNumber = number;
  return moved;
}

test("runner persists the pending move before emitting, and duplicate views do not send twice", async (t) => {
  const context = setup(t);
  context.socket.onSend = (packet) => {
    if (packet.event === "game:move") {
      assert.equal(context.store.saves.at(-1).active.gate.pending.usi, packet.payload.usi);
      assert.equal(context.store.saves.at(-1).active.record.decisions.at(-1).feedback, "unknown");
    }
  };
  await begin(context);
  context.socket.server("game:state", { ...view(), opponentPieces: [{ square: "1a", role: "king" }], token: "tsb_private" });
  await flush(context.session);
  assert.equal(context.socket.packets("game:move").length, 1);
  assert.equal(JSON.stringify(context.store.saves).includes("opponentPieces"), false);
  assert.equal(JSON.stringify(context.store.saves).includes("tsb_private"), false);
});

test("a failed durable write prevents the network move", async (t) => {
  const context = setup(t);
  const save = context.store.save.bind(context.store);
  context.store.save = async (value) => {
    if (value.active?.gate.pending) throw new Error("disk unavailable");
    await save(value);
  };
  await flush(context.session);
  context.socket.server("match:found", { gameId: "test-game", yourColor: "sente" });
  await flush(context.session);
  context.socket.ack("game:sync", { state: view() });
  await flush(context.session);
  assert.equal(context.socket.packets("game:move").length, 0);
  assert.equal((await context.session.done).status, "paused");
});

test("success ACK does not move the board; an advanced view permits the next move", async (t) => {
  const context = setup(t);
  await begin(context);
  context.socket.ack("game:move", { ok: true });
  await flush(context.session);
  context.socket.ack("game:sync", { state: view() });
  await flush(context.session);
  assert.equal(context.socket.packets("game:move").length, 1);
  context.socket.server("game:state", advancedView(context.socket));
  await flush(context.session);
  assert.equal(context.socket.packets("game:move").length, 2);
});

test("view-before-ACK cannot clear or repeat the next pending move", async (t) => {
  const context = setup(t);
  await begin(context);
  context.socket.server("game:state", advancedView(context.socket));
  await flush(context.session);
  const pending = context.session.gate.pending;
  context.socket.ack("game:move", { ok: true }, 0);
  await flush(context.session);
  assert.equal(context.session.gate.pending, pending);
  assert.equal(context.socket.packets("game:move").length, 2);
});

test("foul confirmation allows a different move, keeping previous attempts excluded", async (t) => {
  const context = setup(t);
  await begin(context);
  context.socket.ack("game:move", { ok: false, reason: "foul", foulCount: 1 });
  await flush(context.session);
  context.socket.ack("game:sync", { state: view({ fouls: { you: 1, opponent: 0 } }) });
  await flush(context.session);
  const moves = context.socket.packets("game:move");
  assert.equal(moves.length, 2);
  assert.notEqual(moves[0].payload.usi, moves[1].payload.usi);
  assert.equal(context.session.record.decisions[0].feedback, "foul");
});

test("late foul ACK cannot overwrite acceptance proved by an advanced view", async (t) => {
  const context = setup(t);
  await begin(context);
  context.socket.server("game:state", advancedView(context.socket));
  await flush(context.session);
  const pending = context.session.gate.pending;
  assert.equal(context.session.record.decisions[0].feedback, "accepted");
  context.socket.ack("game:move", { ok: false, reason: "foul", foulCount: 1 }, 0);
  await flush(context.session);
  assert.equal(context.session.record.decisions[0].feedback, "accepted");
  assert.equal(context.store.saves.at(-1).active.record.decisions[0].feedback, "accepted");
  assert.equal(context.session.gate.pending, pending);
  assert.equal(context.socket.packets("game:move").length, 2);
});

test("duplicate ACK cannot overwrite confirmed foul feedback", async (t) => {
  const context = setup(t);
  await begin(context);
  context.socket.ack("game:move", { ok: false, reason: "foul", foulCount: 1 });
  await flush(context.session);
  context.socket.ack("game:move", { ok: true });
  await flush(context.session);
  assert.equal(context.session.record.decisions[0].feedback, "foul");
  assert.equal(context.store.saves.at(-1).active.record.decisions[0].feedback, "foul");
  assert.equal(context.socket.packets("game:move").length, 1);
});

test("a timed-out move remains unresolved after same-view sync, reconnect and process restore", async (t) => {
  const context = setup(t);
  await begin(context);
  context.socket.ack("game:move", undefined, -1, new Error("timeout"));
  await flush(context.session);
  context.socket.ack("game:sync", { state: view() });
  await flush(context.session);
  context.socket.disconnect();
  await flush(context.session);
  context.socket.connect();
  await flush(context.session);
  context.socket.ack("game:sync", { state: view() });
  await flush(context.session);
  assert.equal(context.socket.packets("game:move").length, 1);
  const restored = setup(t, { checkpoint: context.store.saves.at(-1).active });
  await flush(restored.session);
  assert.equal(restored.socket.packets("queue:join").length, 0);
  restored.socket.ack("game:sync", { state: view() });
  await flush(restored.session);
  assert.equal(restored.socket.packets("game:move").length, 0);
});

test("a null sync never manufactures a loss or joins another game", async (t) => {
  const context = setup(t);
  await begin(context);
  context.socket.server("game:end", { result: "gote_win", gameId: "previous-game", token: "private" });
  await flush(context.session);
  context.socket.ack("game:sync", { state: null });
  await flush(context.session);
  assert.equal(context.store.records.length, 0);
  assert.equal(context.socket.packets("queue:join").length, 1);
  assert.equal(context.session.closed, false);
});

test("terminal records use the result correlated to the requested game, not the raw end push", async (t) => {
  const endedAt = new Date(Date.now() + 1000).toISOString();
  const context = setup(t, { resolveResult: async (gameId) => ({ gameId, outcome: "win", reason: "checkmate", endedAt }) });
  await begin(context);
  context.socket.server("game:end", { gameId: "wrong-game", result: "gote_win", fullBoard: "secret" });
  await flush(context.session);
  const result = await context.session.done;
  assert.equal(result.status, "finished");
  assert.equal(context.store.records[0].outcome, "win");
  assert.equal(context.store.records[0].historyComplete, true);
  assert.equal(context.socket.packets("queue:join").length, 1);
  assert.equal(context.socket.connected, false);
  assert.equal(JSON.stringify(context.store.records).includes("fullBoard"), false);
  context.socket.ack("game:move", { ok: true });
  await flush(context.session);
  assert.equal(context.store.records.length, 1);
});

test("drain during a match keeps playing; a queued match/leave race also finishes the match", async (t) => {
  const context = setup(t);
  await begin(context);
  await context.session.enqueue(() => context.session.drain());
  context.socket.server("game:state", advancedView(context.socket));
  await flush(context.session);
  assert.equal(context.socket.packets("game:move").length, 2);
  assert.equal(context.socket.packets("queue:leave").length, 0);
  const raced = setup(t);
  await flush(raced.session);
  await raced.session.enqueue(() => raced.session.drain());
  raced.socket.server("match:found", { gameId: "test-game", yourColor: "sente" });
  raced.socket.ack("queue:leave", { ok: true });
  await flush(raced.session);
  raced.socket.ack("game:sync", { state: view() });
  await flush(raced.session);
  assert.equal(raced.socket.packets("game:move").length, 1);
  assert.equal(raced.session.closed, false);
  const ackFirst = setup(t);
  await flush(ackFirst.session);
  await ackFirst.session.enqueue(() => ackFirst.session.drain());
  ackFirst.socket.ack("queue:leave", { ok: true });
  ackFirst.socket.server("match:found", { gameId: "test-game", yourColor: "sente" });
  await flush(ackFirst.session);
  ackFirst.socket.ack("game:sync", { state: view() });
  await flush(ackFirst.session);
  assert.equal(ackFirst.socket.packets("game:move").length, 1);
  assert.equal(ackFirst.session.closed, false);
});

test("join rejection waits for active-game notification, and missing initial views pause explicitly", async (t) => {
  const context = setup(t);
  await flush(context.session);
  context.socket.ack("queue:join", { ok: false, error: "in game" });
  context.socket.server("game:active", { gameId: "test-game" });
  await flush(context.session);
  assert.equal(context.session.closed, false);
  for (let count = 0; count < 5; count += 1) {
    context.socket.ack("game:sync", { state: null });
    await flush(context.session);
    if (count < 4) context.session.sync();
  }
  const result = await context.session.done;
  assert.equal(result.code, "missing_player_view");
  assert.equal(context.store.records.length, 0);
  assert.equal(context.store.saves.at(-1).active.gameId, "test-game");
});

test("an idle active-game snapshot before the queue ACK does not discard an immediate match", async (t) => {
  const context = setup(t);
  await flush(context.session);
  context.socket.server("game:active", { gameId: null });
  await flush(context.session);
  assert.equal(context.session.closed, false);
  assert.equal(context.session.gameId, null);
  assert.equal(context.store.saves.length, 0);
  context.socket.server("match:found", { gameId: "test-game", yourColor: "gote" });
  context.socket.ack("queue:join", { ok: true });
  await flush(context.session);
  context.socket.ack("game:sync", { state: view({ yourColor: "gote", turn: "gote", moveNumber: 2,
    clocks: { ...view().clocks, running: "gote" } }) });
  await flush(context.session);
  assert.equal(context.socket.packets("queue:join").length, 1);
  assert.equal(context.socket.packets("game:move").length, 1);
  assert.equal(context.store.saves.at(-1).active.gameId, "test-game");
  assert.equal(context.store.saves.at(-1).active.record.color, "w");
});

test("a late idle active-game snapshot preserves the known match and pending move", async (t) => {
  const context = setup(t);
  await begin(context);
  const checkpoint = context.session.checkpoint();
  context.socket.server("game:active", { gameId: null });
  await flush(context.session);
  assert.equal(context.session.closed, false);
  assert.deepEqual(context.session.checkpoint(), checkpoint);
  assert.equal(context.socket.packets("queue:join").length, 1);
  assert.equal(context.socket.packets("game:move").length, 1);
});

test("invalid match diagnostics expose only source and field types", async (t) => {
  const context = setup(t);
  await flush(context.session);
  context.socket.server("match:found", { gameId: null, yourColor: "gote",
    token: "private-auth", opponent: { username: "private-player" } });
  await flush(context.session);
  assert.equal((await context.session.done).code, "invalid_match");
  assert.equal(context.socket.packets("game:move").length, 0);
  assert.equal(context.store.saves.length, 0);
  assert.deepEqual(context.events.find(event => event.event === "invalid_match_shape"), {
    event: "invalid_match_shape", source: "match:found", payloadType: "object",
    gameIdType: "null", yourColorType: "string", stage: "game_id",
  });
  assert.equal(JSON.stringify(context.events).includes("private-auth"), false);
  assert.equal(JSON.stringify(context.events).includes("private-player"), false);
});

test("a malformed active-game notification remains fail-closed", async (t) => {
  const context = setup(t);
  await flush(context.session);
  context.socket.server("game:active", {});
  await flush(context.session);
  assert.equal((await context.session.done).code, "invalid_match");
  assert.equal(context.socket.packets("game:move").length, 0);
  assert.equal(context.store.saves.length, 0);
  assert.equal(context.events.find(event => event.event === "invalid_match_shape")?.source, "game:active");
});

test("initial connection failure has a bounded lifetime and never logs raw errors", async (t) => {
  const socket = new Socket();
  socket.connect = () => socket.server("connect_error", new Error("tsb_do-not-log"));
  const store = new Store();
  const events = [];
  const session = new BetaSession({ socket, store, profile: LINEAR_PROFILE, connectDeadlineMs: 5,
    log: (event) => events.push(event) });
  t.after(() => session.close());
  const result = await session.start();
  assert.equal(result.code, "connection_unavailable");
  assert.equal(JSON.stringify(events).includes("tsb_do-not-log"), false);
});

test("an ended checkpoint with an unavailable public result becomes one unknown terminal record", async (t) => {
  const context = setup(t);
  await begin(context);
  context.session.gate.acceptView(view({ status: "ended", clocks: { ...view().clocks, running: null } }),
    context.session.gate.generation, { synchronized: true });
  context.session.terminalSeen = true;
  const restored = setup(t, { checkpoint: context.session.checkpoint() });
  const scheduled = [];
  restored.session.later = (operation) => { scheduled.push(operation); };
  await flush(restored.session);
  for (let count = 0; count < 5; count += 1) {
    restored.socket.ack("game:sync", { state: null });
    await flush(restored.session);
    if (count < 4) restored.session.sync();
  }
  while (!restored.session.closed) {
    const next = scheduled.shift(); assert.ok(next);
    await restored.session.enqueue(next); await flush(restored.session);
  }
  assert.equal((await restored.session.done).status, "finished");
  assert.equal(restored.store.records.length, 1);
  assert.equal(restored.store.records[0].completed, true);
  assert.equal(restored.store.records[0].outcome, "unknown");
});

test("exhausted null syncs wait for the bounded replay retries before pausing", async (t) => {
  let resolveFirst, attempts = 0;
  const context = setup(t, { resolveResult: async (gameId) => {
    attempts += 1;
    if (attempts === 1) return new Promise((resolve) => { resolveFirst = resolve; });
    if (attempts < 3) return null;
    return { gameId, outcome: "loss", reason: "foul_limit", source: "public_replay",
      endedAt: new Date(Date.now() + 1000).toISOString() };
  } });
  const scheduled = [];
  context.session.later = (operation, milliseconds) => { scheduled.push({ operation, milliseconds }); };
  await begin(context);
  context.socket.server("game:end", { gameId: "unrelated-game", token: "fixture-private" });
  await flush(context.session);
  for (let count = 0; count < 5; count += 1) {
    context.socket.ack("game:sync", { state: null });
    await flush(context.session);
    if (count < 4) context.session.sync();
  }
  assert.equal(attempts, 1);
  resolveFirst(null); await flush(context.session);
  assert.equal(context.session.closed, false);
  assert.equal(context.session.terminalSeen, false);
  assert.equal(context.store.records.length, 0);
  // Duplicate pushes cannot bypass the scheduled retry's backoff.
  context.socket.server("game:end", {}); await flush(context.session);
  assert.equal(attempts, 1);
  while (!context.session.closed) {
    const next = scheduled.shift(); assert.ok(next);
    await context.session.enqueue(next.operation); await flush(context.session);
  }
  assert.equal(attempts, 3);
  assert.equal((await context.session.done).status, "finished");
  assert.equal(context.store.records.length, 1);
  assert.equal(context.store.records[0].outcome, "loss");
  assert.equal(context.store.records[0].resultConfidence, "verified");
  assert.equal(context.socket.connected, false);
  assert.equal(context.socket.packets("queue:join").length, 1);
  assert.equal(JSON.stringify(context.store.saves).includes("fixture-private"), false);
});

test("five unavailable or mismatched replay results preserve an unresolved checkpoint", async (t) => {
  for (const result of [null, { gameId: "unrelated-game", outcome: "loss", reason: "timeout" }]) {
    let attempts = 0;
    const context = setup(t, { resolveResult: async () => { attempts += 1; return result; } });
    const scheduled = [];
    context.session.later = (operation) => { scheduled.push(operation); };
    await begin(context);
    context.session.syncExhausted = true;
    context.socket.server("game:end", { result: "gote_win", gameId: "unrelated-game" });
    await flush(context.session);
    assert.equal(context.session.closed, false);
    while (!context.session.closed) {
      const next = scheduled.shift(); assert.ok(next);
      await context.session.enqueue(next); await flush(context.session);
    }
    assert.equal(attempts, 5);
    assert.equal((await context.session.done).code, "terminal_unconfirmed");
    assert.equal(context.store.records.length, 0);
    assert.equal(context.store.saves.at(-1).active.record.completed, false);
    assert.equal(context.socket.packets("queue:join").length, 1);
  }
});

test("a disconnect before the first PlayerView pauses with its game checkpoint intact", async (t) => {
  const context = setup(t, { disconnectDeadlineMs: 5 });
  await flush(context.session);
  context.socket.server("game:active", { gameId: "test-game" });
  await flush(context.session);
  context.socket.disconnect();
  const result = await context.session.done;
  assert.equal(result.code, "missing_player_view");
  assert.equal(context.store.records.length, 0);
  assert.equal(context.store.saves.at(-1).active.gameId, "test-game");
});

test("unavailable pinned brain preserves an aborted checkpoint without connecting or playing", { timeout: 1000 }, async (t) => {
  const context = setup(t);
  await begin(context);
  const checkpoint = structuredClone(context.store.saves.at(-1).active);
  checkpoint.record.brainVersion = "previous-brain";
  const restored = setup(t, { checkpoint });
  const result = await restored.session.done;
  assert.notEqual(BRAIN_VERSION, "previous-brain");
  assert.equal(result.status, "paused");
  assert.equal(result.code, "brain_version_unavailable");
  assert.equal(restored.socket.sent.length, 0);
  assert.equal(restored.session.everConnected, false);
  assert.equal(restored.store.records.length, 0);
  assert.equal(restored.session.record.historyComplete, false);
  assert.equal(restored.session.record.brainVersion, "previous-brain");
  const saved = restored.store.saves.at(-1).active;
  assert.equal(saved.record.reason, "interrupted");
  assert.equal(saved.record.completed, false);
  assert.equal(saved.record.outcome, "unknown");
  assert.deepEqual(saved.gate.pending, checkpoint.gate.pending);
  assert.deepEqual(saved.record.decisions, checkpoint.record.decisions);
  assert.equal(report([saved.record]).totals.aborted, 1);
  // Restarting the runner again cannot clear the interrupted match and join a new one.
  const again = setup(t, { checkpoint: saved });
  assert.equal((await again.session.done).code, "brain_version_unavailable");
  assert.equal(again.socket.sent.length, 0);
});

test("same brain revision resumes with its pinned profile", async (t) => {
  const context = setup(t);
  await begin(context);
  const checkpoint = structuredClone(context.store.saves.at(-1).active);
  const alternative = { ...LINEAR_PROFILE, id: "next-game-profile", exploration: 0.5 };
  const restored = setup(t, { checkpoint, profile: alternative });
  await flush(restored.session);
  assert.equal(restored.session.everConnected, true);
  assert.equal(restored.session.closed, false);
  assert.deepEqual(restored.session.profile, checkpoint.record.profile);
  assert.equal(restored.socket.packets("queue:join").length, 0);
  assert.equal(restored.socket.packets("game:sync").length, 1);
});

test("disconnect and reconnect remain excluded even when the final timeout or checkmate is verified", async (t) => {
  for (const reason of ["timeout", "checkmate"]) {
    const raw = replay("test-game", reason === "timeout" ? "gote_win" : "sente_win");
    raw.nodes[0].data[4] = reason;
    const result = parsePublicResult(raw, "test-game", "b");
    const context = setup(t, { resolveResult: async () => result });
    await begin(context);
    context.socket.ack("game:move", { ok: true });
    await flush(context.session);
    context.socket.disconnect();
    await flush(context.session);
    const checkpoint = context.store.saves.at(-1).active;
    assert.equal(checkpoint.record.communicationInterrupted, true);
    assert.equal(checkpoint.record.validForTraining, false);
    context.socket.connect();
    await flush(context.session);
    context.socket.ack("game:sync", { state: advancedView(context.socket) });
    await flush(context.session);
    context.socket.server("game:end", {});
    await flush(context.session);
    const finished = (await context.session.done).record;
    assert.equal(finished.outcome, result.outcome);
    assert.equal(finished.reason, reason);
    assert.equal(finished.historyComplete, true);
    assert.equal(finished.resultSource, "public_replay");
    assert.equal(finished.resultConfidence, "verified");
    assert.equal(finished.communicationInterrupted, true);
    assert.equal(finished.validForTraining, false);
    assert.equal(report([finished]).totals.aborted, 1);
    assert.equal(report([finished]).totals.completed, 0);
  }
});

test("an uninterrupted verified result is eligible; restoring its active checkpoint is not", async (t) => {
  const result = parsePublicResult(replay(), "test-game", "b");
  const context = setup(t, { resolveResult: async () => result });
  await begin(context);
  context.socket.ack("game:move", { ok: true });
  await flush(context.session);
  const checkpoint = context.store.saves.at(-1).active;
  const restored = setup(t, { checkpoint, resolveResult: async () => result });
  await flush(restored.session);
  restored.socket.server("game:end", {});
  await flush(restored.session);
  assert.equal((await restored.session.done).record.validForTraining, false);
  context.socket.server("game:end", {});
  await flush(context.session);
  const finished = (await context.session.done).record;
  assert.equal(finished.validForTraining, true);
  assert.equal(report([finished]).totals.completed, 1);
});

test("a reconnect cannot erase the disconnect fact while the session queue is blocked", async (t) => {
  const result = parsePublicResult(replay(), "test-game", "b");
  const context = setup(t, { resolveResult: async () => result });
  await begin(context);
  let unblock;
  context.session.enqueue(() => new Promise((resolve) => { unblock = resolve; }));
  await Promise.resolve();
  context.socket.disconnect();
  context.socket.connect();
  assert.equal(context.session.record.communicationInterrupted, true);
  unblock();
  await flush(context.session);
  assert.equal(context.events.filter((event) => event.event === "disconnected").length, 0);
  assert.equal(context.events.filter((event) => event.event === "connected").length, 2);
  assert.equal(context.store.saves.at(-1).active.record.communicationInterrupted, true);
  context.socket.server("game:end", {});
  await flush(context.session);
  const finished = (await context.session.done).record;
  assert.equal(finished.outcome, result.outcome);
  assert.equal(finished.reason, result.reason);
  assert.equal(finished.communicationInterrupted, true);
  assert.equal(finished.validForTraining, false);
  assert.equal(report([finished]).totals.completed, 0);
});

test("old queue timeout cannot drain a reconnected socket", async (t) => {
  const context = setup(t);
  const scheduled = [];
  context.session.later = (operation, delay) => { scheduled.push({ operation, delay }); };
  await flush(context.session);
  const first = scheduled.find((item) => item.delay === context.session.queueWaitMs);
  assert.ok(first);
  context.socket.disconnect();
  await flush(context.session);
  await first.operation();
  assert.equal(context.session.stopping, false);
  context.socket.connect();
  await flush(context.session);
  await first.operation();
  assert.equal(context.session.stopping, false);
  assert.equal(context.socket.packets("queue:leave").length, 0);
  await scheduled.filter((item) => item.delay === context.session.queueWaitMs).at(-1).operation();
  assert.equal(context.session.stopping, true);
  assert.equal(context.socket.packets("queue:leave").length, 1);
});

test("a parsed view outside brain hand limits resyncs without poisoning the current board", async (t) => {
  const context = setup(t);
  await flush(context.session);
  context.socket.server("match:found", { gameId: "test-game", yourColor: "sente" });
  await flush(context.session);
  context.socket.ack("game:sync", { state: view({ yourHand: { lance: 5 } }) });
  await flush(context.session);
  assert.equal(context.session.closed, false);
  assert.equal(context.session.gate.needsSync, true);
  assert.equal(context.session.gate.view, null);
  assert.equal(context.socket.packets("game:move").length, 0);
  context.socket.ack("game:sync", { state: view() });
  await flush(context.session);
  assert.equal(context.socket.packets("game:move").length, 1);
  // Also defend the decision boundary if an old checkpoint contains such a view.
  context.session.gate.pending = null;
  context.session.gate.view = view({ yourHand: { lance: 5 } });
  context.session.gate.needsSync = false;
  await context.session.maybeMove();
  assert.equal(context.session.gate.needsSync, true);
  assert.equal(context.socket.packets("game:move").length, 1);
});

function replay(gameId = "test-game", result = "sente_win") {
  return { type: "data", nodes: [{ type: "data", data: [
    { game: 1 }, { id: 2, result: 3, reason: 4, startedAt: 5, endedAt: 6, finalSfen: 7 },
    gameId, result, "checkmate", ["Date", "2026-10-03T00:00:00.000Z"],
    ["Date", "2026-10-03T00:05:00.000Z"], "entire-hidden-board",
  ] }] };
}

test("public result decoder validates game identity and terminal date without reviving the board", () => {
  const result = parsePublicResult(replay(), "test-game", "b");
  assert.equal(result.outcome, "win");
  assert.equal(parsePublicResult(replay(), "test-game", "w").outcome, "loss");
  assert.equal(parsePublicResult(replay("another-game"), "test-game", "b"), null);
  assert.equal(parsePublicResult(replay("test-game", "playing"), "test-game", "b"), null);
  const active = replay(); active.nodes[0].data[1].endedAt = -1;
  assert.equal(parsePublicResult(active, "test-game", "b"), null);
  assert.equal(JSON.stringify(result).includes("entire-hidden-board"), false);
  assert.equal(parsePublicResult(replay("test-game", "aborted"), "test-game", "b").reason, "interrupted");
});

test("public replay fetch never sends a token and rejects non-JSON, oversized, or changed data", async () => {
  let seen;
  const result = await fetchPublicResult("test-game", "b", { fetchImpl: async (url, options) => {
    seen = { url, options };
    return Response.json(replay());
  } });
  assert.equal(result.outcome, "win");
  assert.equal(seen.options.credentials, "omit");
  assert.equal(seen.options.redirect, "manual");
  assert.deepEqual(seen.options.headers, { accept: "application/json" });
  let redirects = 0;
  assert.equal(await fetchPublicResult("test-game", "b", { fetchImpl: async (_url, options) => {
    redirects += 1; assert.equal(options.redirect, "manual");
    return new Response(null, { status: 302, headers: { location: "https://untrusted.test/" } });
  } }), null);
  assert.equal(redirects, 1);
  assert.equal(await fetchPublicResult("test-game", "b", { fetchImpl: async () => new Response("<html>") }), null);
  assert.equal(await fetchPublicResult("test-game", "b", { fetchImpl: async () => Response.json({ changed: true }) }), null);
  assert.equal(await fetchPublicResult("test-game", "b", { fetchImpl: async () => new Response(" ".repeat(1048577), { headers: { "content-type": "application/json" } }) }), null);
});

test("durable store excludes concurrent writers, deduplicates terminal games and rebuilds JSONL", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "tsuitate-arena-"));
  const store = new ArenaStore(directory);
  await store.acquire();
  t.after(async () => { await store.release(); await rm(directory, { recursive: true, force: true }); });
  await assert.rejects(new ArenaStore(directory).acquire(), /runner_locked/);
  const record = { schemaVersion: 1, kind: "tsuitate_game", site: "beta.tsuitate.info", ruleset: "tsuitate-9x9",
    rulesKey: "beta-300+3-f10", gameId: "test-game", color: "b", startedAt: "2026-10-03T00:00:00.000Z",
    endedAt: "2026-10-03T00:05:00.000Z", brainVersion: BRAIN_VERSION, profile: LINEAR_PROFILE,
    decisions: [], completed: true, historyComplete: true, outcome: "win", reason: "checkmate" };
  await store.finish(record);
  await store.finish(record);
  await store.rebuildDataset();
  const lines = (await readFile(join(directory, "games.jsonl"), "utf8")).trim().split("\n");
  assert.equal(lines.length, 1);
  await assert.rejects(store.finish({ ...record, outcome: "loss" }), /conflicting_game_record/);
});

test("dead owner lock is recovered once; living or ambiguous owners remain protected", { timeout: 5000 }, async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "tsuitate-lock-"));
  const moduleUrl = new URL("../src/arena/store.js", import.meta.url).href;
  const child = spawn(process.execPath, ["--input-type=module", "-e",
    `import {ArenaStore} from ${JSON.stringify(moduleUrl)}; const s=new ArenaStore(${JSON.stringify(directory)}); await s.acquire(); console.log('ready'); setInterval(()=>{},1000);`]);
  const [ready] = await once(child.stdout, "data");
  assert.equal(String(ready).trim(), "ready");
  t.after(async () => { if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL"); await rm(directory, { recursive: true, force: true }); });
  await assert.rejects(new ArenaStore(directory).acquire(), /runner_locked/);
  await writeFile(join(directory, "checkpoint.json"), '{"pending":"preserve"}');
  const exited = once(child, "exit"); child.kill("SIGKILL"); await exited;
  const stores = [new ArenaStore(directory), new ArenaStore(directory)];
  const results = await Promise.allSettled(stores.map((store) => store.acquire()));
  assert.equal(results.filter((result) => result.status === "fulfilled").length, 1);
  assert.equal(results.filter((result) => result.status === "rejected")[0].reason.message, "runner_locked");
  const winner = stores.find((store) => store.locked);
  assert.deepEqual(await winner.load(), { pending: "preserve" });
  await winner.release();
  for (const owner of [
    { pid: process.pid, hostname: hostname(), lockId: "reused-or-live-pid", createdAt: "1900-01-01" },
    { pid: child.pid, hostname: "another-host", lockId: "foreign-host" },
    { pid: child.pid, lockId: "legacy-lock" },
  ]) {
    const text = JSON.stringify(owner);
    await writeFile(join(directory, "runner.lock"), text);
    await assert.rejects(new ArenaStore(directory).acquire(), /runner_locked/);
    assert.equal(await readFile(join(directory, "runner.lock"), "utf8"), text);
  }
  await rm(join(directory, "runner.lock"));
  await writeFile(join(directory, "runner.lock.guard"), "");
  await assert.rejects(new ArenaStore(directory).acquire(), /runner_locked/);
});

test("later verified result upgrades only an unknown game and preserves its evidence", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "tsuitate-result-"));
  const store = new ArenaStore(directory); await store.acquire();
  t.after(async () => { await store.release(); await rm(directory, { recursive: true, force: true }); });
  const unknown = { schemaVersion: 1, kind: "tsuitate_game", site: "beta.tsuitate.info", ruleset: "tsuitate-9x9",
    rulesKey: "beta-300+3-f10", gameId: "test-game", color: "b", startedAt: "2026-10-03T00:00:00.000Z",
    endedAt: "2026-10-03T00:05:00.000Z", brainVersion: BRAIN_VERSION, profile: LINEAR_PROFILE,
    decisions: [], completed: true, historyComplete: false, outcome: "unknown", reason: "unknown" };
  await store.finish(unknown);
  for (const resolve of [async () => null, async () => { throw new Error("unavailable"); },
    async () => parsePublicResult(replay("another-game"), "another-game", "b"),
    async () => ({ ...parsePublicResult(replay(), "test-game", "b"), source: "unverified" })]) {
    assert.equal(await store.refreshUnknownResults(resolve), 0);
  }
  const resolve = async (id, color) => parsePublicResult(replay(id), id, color);
  const results = await Promise.all([store.refreshUnknownResults(resolve), store.refreshUnknownResults(resolve)]);
  assert.deepEqual(results, [1, 0]);
  const dataset = JSON.parse((await readFile(join(directory, "games.jsonl"), "utf8")).trim());
  assert.equal(dataset.outcome, "win");
  assert.equal(dataset.reason, "checkmate");
  assert.equal(dataset.historyComplete, false);
  assert.equal(dataset.brainVersion, unknown.brainVersion);
  assert.deepEqual(dataset.decisions, unknown.decisions);
  assert.equal(report([dataset]).totals.incompleteHistory, 1);
  assert.equal(await store.refreshUnknownResults(async () => parsePublicResult(replay(), "test-game", "w")), 0);
  await assert.rejects(store.finish({ ...dataset, outcome: "loss" }), /conflicting_game_record/);
});
