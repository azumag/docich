import assert from "node:assert/strict";
import { test } from "node:test";
import { BRAIN_VERSION, LINEAR_PROFILE } from "../src/brain/index.js";
import { BetaSession } from "../src/arena/beta-session.js";
import { DurableArenaController, QUEUE_WAIT_MS } from "../src/arena/durable-controller.js";
import { DurableArenaStore, META_KEY, CHECKPOINT_KEY, RECORD_KEY, runKey, recordKey } from "../src/arena/durable-store.js";

class Storage {
  constructor() { this.data = new Map(); this.alarm = null; this.tail = Promise.resolve(); this.failPut = false; }
  async get(key) { return structuredClone(this.data.get(key)); }
  async put(key, value) { if (this.failPut) throw new Error("fixture_storage_failure"); this.data.set(key, structuredClone(value)); }
  async delete(key) { this.data.delete(key); }
  async setAlarm(value) { this.alarm = value; }
  async deleteAlarm() { this.alarm = null; }
  transaction(operation) {
    const result = this.tail.then(async () => {
      const tx = new Storage(); tx.data = structuredClone(this.data); tx.alarm = this.alarm; tx.failPut = this.failPut;
      const value = await operation(tx);
      this.data = tx.data; this.alarm = tx.alarm;
      return value;
    });
    this.tail = result.catch(() => {});
    return result;
  }
}

class Socket {
  constructor() { this.handlers = new Map(); this.sent = []; this.connected = false; }
  on(event, handler) { this.handlers.set(event, [...(this.handlers.get(event) ?? []), handler]); return this; }
  off(event, handler) { this.handlers.set(event, (this.handlers.get(event) ?? []).filter((item) => item !== handler)); return this; }
  server(event, data) { for (const handler of this.handlers.get(event) ?? []) handler(data); }
  connect() { this.connected = true; this.server("connect"); }
  disconnect() { this.connected = false; this.server("disconnect"); }
  timeout() { return this; }
  emit(event, ...args) { const callback = typeof args.at(-1) === "function" ? args.pop() : null;
    this.sent.push({ event, payload: args[0], callback }); return this; }
  packets(event) { return this.sent.filter((item) => item.event === event); }
  ack(event, value) { this.packets(event).at(-1).callback(null, value); }
}

function view(overrides = {}) {
  return { gameId: "local-game", yourColor: "sente", yourPieces: [{ square: "5i", role: "king" },
    { square: "5g", role: "pawn" }, { square: "2h", role: "rook" }], yourHand: {},
  turn: "sente", moveNumber: 1, clocks: { senteMs: 300000, goteMs: 300000, running: "sente", serverTime: 100 },
  fouls: { you: 0, opponent: 0 }, youInCheck: false, opponentInCheck: false, status: "playing", ...overrides };
}

function terminal() {
  return { schemaVersion: 1, kind: "tsuitate_game", site: "beta.tsuitate.info", ruleset: "tsuitate-9x9",
    rulesKey: "beta-300+3-f10", gameId: "local-game", color: "b", startedAt: "2026-10-03T00:00:00.000Z",
    endedAt: "2026-10-03T00:05:00.000Z", brainVersion: BRAIN_VERSION, profile: LINEAR_PROFILE,
    decisions: [], completed: true, historyComplete: true, outcome: "win", reason: "checkmate" };
}

function setup(t, overrides = {}) {
  const sockets = [], events = [], scheduled = [];
  const storage = overrides.storage ?? new Storage();
  const controller = new DurableArenaController({ storage,
    env: { TSUITATE_BOT_TOKEN: "fixture-only-not-a-credential" },
    makeSocket: () => { const socket = new Socket(); sockets.push(socket); return socket; },
    makeSession: (options) => {
      const session = new BetaSession({ ...options, queueSettleMs: 1, pollMs: 60000 });
      session.later = (operation, milliseconds) => { scheduled.push({ operation, milliseconds }); };
      return session;
    }, resolveResult: async () => null, log: (event) => events.push(event), ...overrides });
  t.after(() => controller.session?.close());
  return { controller, storage, sockets, events, scheduled };
}

async function flush(controller) {
  for (let i = 0; i < 10; i++) { await controller.session?.tail; await controller.tail; await Promise.resolve(); }
}

async function begin(context) {
  await context.controller.start({ runId: "fixture-run" }); await flush(context.controller);
  const socket = context.sockets.at(-1);
  socket.ack("queue:join", { ok: true });
  socket.server("game:active", { gameId: null });
  socket.server("match:found", { gameId: "local-game", yourColor: "sente" });
  await flush(context.controller);
  socket.ack("game:sync", { state: view() });
  await flush(context.controller);
  return socket;
}

test("fresh arena is stopped; missing or invalid token cannot reserve/connect", async (t) => {
  for (const env of [{}, { TSUITATE_BOT_TOKEN: "" }, { TSUITATE_BOT_TOKEN: " " },
    { TSUITATE_BOT_TOKEN: 123 }, { TSUITATE_BOT_TOKEN: "x".repeat(4097) }]) {
    const c = setup(t, { env });
    assert.equal((await c.controller.status()).state, "stopped");
    await c.controller.stop(); await c.controller.alarm();
    await assert.rejects(c.controller.start({ runId: "one" }), /token_not_configured/);
    assert.equal(c.sockets.length, 0); assert.equal(c.storage.data.size, 0); assert.equal(c.storage.alarm, null);
  }
});

test("stale false enable setting does not block an explicit run", async (t) => {
  const c = setup(t, { env: { BETA_ARENA_ENABLED: "false", TSUITATE_BOT_TOKEN: "fixture-only-not-a-credential" } });
  assert.equal((await c.controller.status()).state, "stopped");
  await c.controller.alarm(); assert.equal(c.sockets.length, 0);
  await c.controller.start({ runId: "one" }); await flush(c.controller);
  assert.equal((await c.controller.status()).state, "queued");
  assert.equal(c.sockets.length, 1); assert.equal(c.sockets[0].packets("queue:join").length, 1);
  await assert.rejects(c.controller.start({ runId: "two" }), /run_locked/);
});

test("concurrent/repeated starts reserve before connecting and never open another run", async (t) => {
  const c = setup(t);
  c.controller.makeSocket = () => { assert.equal(c.storage.data.get(META_KEY).reservedGames, 1);
    const socket = new Socket(); c.sockets.push(socket); return socket; };
  const results = await Promise.all([c.controller.start({ runId: "one" }), c.controller.start({ runId: "one" })]);
  await flush(c.controller);
  assert.equal(results.length, 2); assert.equal(c.sockets.length, 1);
  assert.equal(c.sockets[0].packets("queue:join").length, 1);
  await assert.rejects(c.controller.start({ runId: "two" }), /run_locked/);
  await assert.rejects(c.controller.start({ runId: "one", maxGames: 2 }), /invalid_start_options/);
  for (const options of [{}, { runId: 1 }, [], "one"]) {
    await assert.rejects(c.controller.start(options), /invalid_start_options/);
  }
  await c.controller.alarm(); await c.controller.alarm(); assert.equal(c.sockets.length, 1);
});

test("reservation storage failure rolls back and prevents network access", async (t) => {
  const c = setup(t); c.storage.failPut = true;
  await assert.rejects(c.controller.start({ runId: "one" }), /fixture_storage_failure/);
  assert.equal(c.sockets.length, 0); assert.equal(c.storage.data.size, 0); assert.equal(c.storage.alarm, null);
});

test("queue lifetime is 60 seconds and leave settles without another match", async (t) => {
  const c = setup(t); await c.controller.start({ runId: "one" }); await flush(c.controller);
  assert.equal(QUEUE_WAIT_MS, 60000);
  const timeout = c.scheduled.filter((item) => item.milliseconds > 59000 && item.milliseconds <= 60000).at(-1);
  assert.ok(timeout); await timeout.operation(); await flush(c.controller);
  assert.equal(c.sockets[0].packets("queue:leave").length, 1);
  c.sockets[0].ack("queue:leave", { ok: true }); await flush(c.controller);
  await c.scheduled.find((item) => item.milliseconds === 1).operation(); await flush(c.controller);
  assert.equal((await c.controller.status()).state, "queue_timeout");
  assert.equal(c.storage.alarm, null);
  await c.controller.start({ runId: "one" }); await c.controller.alarm(); assert.equal(c.sockets.length, 1);
});

test("unknown cold queue state pauses and never reconnects or assumes no server game", async (t) => {
  const first = setup(t); await first.controller.start({ runId: "one" }); await flush(first.controller);
  first.controller.session.close();
  const cold = setup(t, { storage: first.storage });
  await cold.controller.alarm(); await cold.controller.alarm();
  assert.equal(cold.sockets.length, 0);
  assert.equal((await cold.controller.status()).errorCode, "unknown_match_state");
  assert.equal(cold.storage.alarm, null);
  await assert.rejects(cold.controller.start({ runId: "another" }), /run_locked/);
});

test("warm reconnect without known game leaves queue rather than joining again", async (t) => {
  const c = setup(t); await c.controller.start({ runId: "one" }); await flush(c.controller);
  c.sockets[0].disconnect(); await flush(c.controller); c.sockets[0].connect(); await flush(c.controller);
  assert.equal(c.sockets[0].packets("queue:join").length, 1);
  assert.equal(c.sockets[0].packets("queue:leave").length, 1);
});

test("expired queue deadline prevents the initial queue join", async (t) => {
  const c = setup(t, { now: () => Date.now() - 61000 });
  await c.controller.start({ runId: "one" }); await flush(c.controller);
  assert.equal(c.sockets[0].packets("queue:join").length, 0);
  assert.equal(c.sockets[0].packets("queue:leave").length, 1);
});

test("cold active restore syncs without replaying an unknown move or joining a new match", async (t) => {
  const first = setup(t); const socket = await begin(first);
  assert.equal(socket.packets("game:move").length, 1);
  const saved = await first.storage.get(CHECKPOINT_KEY); first.controller.session.close();
  const oldStore = new DurableArenaStore(first.storage, "fixture-run", 1);
  const cold = setup(t, { storage: first.storage }); await cold.controller.alarm(); await flush(cold.controller);
  assert.equal(cold.sockets.length, 1);
  assert.equal(cold.sockets[0].packets("queue:join").length, 0);
  cold.sockets[0].ack("game:sync", { state: view() }); await flush(cold.controller);
  assert.equal(cold.sockets[0].packets("game:move").length, 0);
  assert.equal((await cold.storage.get(CHECKPOINT_KEY)).active.record.communicationInterrupted, true);
  await assert.rejects(oldStore.save(saved), /stale_session/);
  await cold.controller.alarm(); assert.equal(cold.sockets.length, 1);
});

test("stop drains a current game while retaining the ability to move", async (t) => {
  const c = setup(t); const socket = await begin(c);
  await c.controller.stop(); await flush(c.controller);
  assert.equal((await c.controller.status()).state, "draining");
  assert.equal(socket.packets("queue:leave").length, 0);
  const advanced = view({ moveNumber: 3 }); const move = socket.packets("game:move")[0].payload.usi;
  advanced.yourPieces.find((piece) => piece.square === move.slice(0, 2)).square = move.slice(2, 4);
  socket.server("game:state", advanced); await flush(c.controller);
  assert.equal(socket.packets("game:move").length, 2);
  assert.equal((await c.storage.get(CHECKPOINT_KEY)).active.gate.quiescing, true);
});

test("stop before match leaves queue and no alarm revives the run", async (t) => {
  const c = setup(t); await c.controller.start({ runId: "one" }); await flush(c.controller);
  await c.controller.stop(); await flush(c.controller);
  c.sockets[0].ack("queue:leave", { ok: true }); await flush(c.controller);
  await c.scheduled.find((item) => item.milliseconds === 1).operation(); await flush(c.controller);
  assert.equal((await c.controller.status()).state, "stopped");
  await c.controller.alarm(); assert.equal(c.sockets.length, 1);
});

test("terminal crash window recovers the record locally once with no socket", async (t) => {
  const c = setup(t); await c.controller.start({ runId: "one" }); c.controller.session.close();
  const store = new DurableArenaStore(c.storage, "one", 1);
  await store.save({ version: 1, finishedRecord: terminal() });
  const cold = setup(t, { storage: c.storage }); await cold.controller.alarm(); await cold.controller.alarm();
  assert.equal(cold.sockets.length, 0);
  assert.equal((await cold.controller.status()).completedGames, 1);
  assert.equal((await cold.controller.status()).state, "finished");
  await store.finish(terminal());
  await assert.rejects(store.finish({ ...terminal(), outcome: "loss" }), /conflicting_game_record/);
  assert.equal((await cold.storage.get(RECORD_KEY)).outcome, "win");
  await cold.controller.start({ runId: "one" }); assert.equal(cold.sockets.length, 0);
});

test("finished record dominates a remaining active checkpoint", async (t) => {
  const c = setup(t); await begin(c); c.controller.session.close();
  await new DurableArenaStore(c.storage, "fixture-run", 1).finish(terminal());
  const cold = setup(t, { storage: c.storage }); await cold.controller.alarm();
  assert.equal(cold.sockets.length, 0); assert.equal((await cold.controller.status()).state, "finished");
});

test("record size and storage failures preserve the previous checkpoint", async (t) => {
  const c = setup(t); await begin(c);
  const prior = await c.storage.get(CHECKPOINT_KEY);
  const store = new DurableArenaStore(c.storage, "fixture-run", 1);
  await assert.rejects(store.save({ version: 1, active: prior.active, extra: "x".repeat(1048576) }), /checkpoint_too_large/);
  assert.deepEqual(await c.storage.get(CHECKPOINT_KEY), prior);
  c.storage.failPut = true;
  c.sockets[0].server("game:state", view({ moveNumber: 3 })); await flush(c.controller);
  assert.equal(c.sockets[0].packets("game:move").length, 1);
  assert.deepEqual(await c.storage.get(CHECKPOINT_KEY), prior);
});

test("status and structured logs exclude token, move, opponent and raw payload", async (t) => {
  const c = setup(t); await begin(c);
  c.controller.safeLog({ event: "move_sent", usi: "5g5f", token: "fixture-sensitive" });
  c.controller.safeLog({ event: "connected", token: "fixture-sensitive", opponentPieces: [1] });
  const publicData = JSON.stringify([await c.controller.status(), c.events]);
  for (const forbidden of ["fixture-only", "fixture-sensitive", "5g5f", "opponentPieces", "yourPieces"]) {
    assert.equal(publicData.includes(forbidden), false);
  }
});

test("explicit second run follows durable terminal/socket/alarm cleanup; old start/stop cannot affect it", async (t) => {
  const c = setup(t); await begin(c);
  const old = c.controller.session;
  await new DurableArenaStore(c.storage, "fixture-run", 1).finish(terminal());
  await assert.rejects(c.controller.start({ runId: "second" }), /run_locked/);
  await c.controller.alarm(); assert.equal((await c.controller.status()).readyForNextRun, false);
  // Closing the socket is necessary, but cleanup receipt must also commit.
  old.close(); old.resolveDone({ status: "finished" }); await flush(c.controller);
  assert.equal((await c.controller.status()).readyForNextRun, true); assert.equal(c.storage.alarm, null);
  await Promise.all([c.controller.start({ runId: "second" }), c.controller.start({ runId: "second" })]);
  await flush(c.controller);
  assert.equal(c.sockets.length, 2); assert.equal(c.sockets[1].packets("queue:join").length, 1);
  assert.equal((await c.storage.get(recordKey("fixture-run"))).completed, true);
  assert.equal((await c.storage.get(runKey("fixture-run"))).state, "finished");
  assert.equal((await c.controller.start({ runId: "fixture-run" })).state, "finished");
  assert.equal((await c.controller.stop({ runId: "fixture-run" })).state, "finished");
  assert.equal((await c.controller.status()).runId, "second");
  assert.equal(c.controller.session.stopping, false); assert.equal(c.sockets.length, 2);
  await assert.rejects(c.controller.start({ runId: "third" }), /run_locked/);
  // A completed game notification from the prior run cannot be played again.
  c.sockets[1].server("match:found", { gameId: "local-game", yourColor: "sente" }); await flush(c.controller);
  assert.equal((await c.controller.status()).state, "paused");
  assert.equal(c.sockets[1].packets("game:move").length, 0);
});

test("confirmed idle stop permits another explicit run; unknown pause does not", async (t) => {
  const c = setup(t); await c.controller.start({ runId: "one" }); await flush(c.controller);
  await c.controller.stop({ runId: "one" }); await flush(c.controller);
  c.sockets[0].ack("queue:leave", { ok: true }); await flush(c.controller);
  await c.scheduled.find((item) => item.milliseconds === 1).operation(); await flush(c.controller);
  await c.controller.start({ runId: "two" }); await flush(c.controller);
  assert.equal(c.sockets.length, 2);
  await c.controller.pause("unknown_match_state");
  await assert.rejects(c.controller.start({ runId: "three" }), /run_locked/);
});
