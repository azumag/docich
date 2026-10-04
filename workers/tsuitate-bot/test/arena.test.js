import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { BRAIN_VERSION, LINEAR_PROFILE } from "../src/brain/index.js";
import { BetaSession } from "../src/arena/beta-session.js";
import { ArenaStore } from "../src/arena/store.js";
import { parsePublicResult, fetchPublicResult } from "../src/adapters/beta-results.js";

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
  await flush(restored.session);
  for (let count = 0; count < 5; count += 1) {
    restored.socket.ack("game:sync", { state: null });
    await flush(restored.session);
    if (count < 4) restored.session.sync();
  }
  assert.equal((await restored.session.done).status, "finished");
  assert.equal(restored.store.records.length, 1);
  assert.equal(restored.store.records[0].completed, true);
  assert.equal(restored.store.records[0].outcome, "unknown");
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

test("resuming after a brain implementation change excludes the mixed-history match", async (t) => {
  const context = setup(t);
  await begin(context);
  const checkpoint = structuredClone(context.store.saves.at(-1).active);
  checkpoint.record.brainVersion = "previous-brain";
  const restored = setup(t, { checkpoint });
  assert.notEqual(BRAIN_VERSION, "previous-brain");
  assert.equal(restored.session.record.historyComplete, false);
  assert.equal(restored.session.record.brainVersion, "mixed-brain-revisions");
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
  assert.equal(seen.options.redirect, "error");
  assert.deepEqual(seen.options.headers, { accept: "application/json" });
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
