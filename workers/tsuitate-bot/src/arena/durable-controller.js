import { BRAIN_VERSION, LINEAR_PROFILE } from "../brain/index.js";
import { BetaSession } from "./beta-session.js";
import { DurableArenaStore, META_KEY, CHECKPOINT_KEY } from "./durable-store.js";

export const SINGLETON_NAME = "beta:DoCiAI";
export const QUEUE_WAIT_MS = 60000;
export const ALARM_INTERVAL_MS = 20000;
const RUN_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const ACTIVE = new Set(["queued", "playing", "draining"]);
const LOG_EVENTS = new Set(["connected", "queued", "matched", "disconnected", "connection_retry",
  "sync_unconfirmed", "invalid_observation", "conflicting_observation", "move_ack_unknown",
  "invalid_move_ack", "resigning_no_candidate", "draining_current_game", "game_finished", "paused", "invalid_match_shape"]);

function initial() { return { version: 1, state: "stopped", runId: null, generation: 0,
  gameId: null, completedGames: 0, reservedGames: 0, stopRequested: false, errorCode: null }; }

/** Internal control only. No HTTP authentication or production route is added. */
export class DurableArenaController {
  constructor({ storage, env, waitUntil = () => {}, makeSocket,
    makeSession = (options) => new BetaSession(options), resolveResult,
    log = () => {}, now = Date.now }) {
    Object.assign(this, { storage, env, waitUntil, makeSocket, makeSession, resolveResult, log, now });
    this.session = null;
    this.tail = Promise.resolve();
  }

  serialize(operation) {
    const result = this.tail.then(operation);
    this.tail = result.catch(() => {});
    return result;
  }

  async read() { return await this.storage.get(META_KEY) ?? initial(); }

  async status() {
    const meta = await this.read();
    return { state: meta.state, runId: meta.runId, gameId: meta.gameId,
      completedGames: meta.completedGames, reservedGames: meta.reservedGames,
      maxGames: 1, queueWaitSeconds: 60, stopRequested: meta.stopRequested,
      errorCode: meta.errorCode, brainVersion: meta.brainVersion ?? BRAIN_VERSION };
  }

  ready() {
    if (this.env.BETA_ARENA_ENABLED !== "true") throw new Error("arena_disabled");
    if (typeof this.env.TSUITATE_BOT_TOKEN !== "string" || !this.env.TSUITATE_BOT_TOKEN.trim()
        || this.env.TSUITATE_BOT_TOKEN.length > 4096) throw new Error("token_not_configured");
  }

  start(options) {
    return this.serialize(async () => {
      if (!options || typeof options !== "object" || Array.isArray(options)
          || typeof options.runId !== "string" || !RUN_ID.test(options.runId)
          || Object.keys(options).some((key) => key !== "runId")) {
        throw new Error("invalid_start_options");
      }
      const existing = await this.read();
      if (existing.runId) {
        if (existing.runId !== options.runId) throw new Error("run_locked");
        return this.status(); // Same request can never queue a second game.
      }
      this.ready();
      const meta = { ...initial(), runId: options.runId, reservedGames: 1, generation: 1,
        state: "queued", brainVersion: BRAIN_VERSION, profile: LINEAR_PROFILE,
        queueDeadlineAt: this.now() + QUEUE_WAIT_MS };
      // Commit reservation before creating the socket or joining the queue.
      await this.storage.transaction(async (tx) => {
        if ((await tx.get(META_KEY))?.runId) throw new Error("run_locked");
        await tx.put(META_KEY, meta);
        await tx.setAlarm(this.now() + ALARM_INTERVAL_MS);
      });
      await this.launch(meta, null);
      return this.status();
    });
  }

  safeLog(event) {
    // The shared runner logs moves; this transport only logs fixed event names
    // and booleans. Do not expose observations, tokens, URLs or raw errors.
    if (LOG_EVENTS.has(event?.event)) this.log({ event: `beta_${event.event}` });
  }

  async launch(meta, checkpoint) {
    try {
      this.ready();
      if (meta.brainVersion !== BRAIN_VERSION) throw new Error("brain_version_unavailable");
      const store = new DurableArenaStore(this.storage, meta.runId, meta.generation);
      const session = this.makeSession({ socket: this.makeSocket(this.env.TSUITATE_BOT_TOKEN), store,
        profile: meta.profile, checkpoint, resolveResult: this.resolveResult,
        queueWaitMs: QUEUE_WAIT_MS, queueDeadlineAt: meta.queueDeadlineAt,
        allowQueueRejoin: false, log: (event) => this.safeLog(event) });
      this.session = session;
      if (meta.stopRequested) {
        session.stopping = true;
        session.gate.requestQuiesce();
      }
      const running = session.start().then((result) => this.serialize(async () => {
        if (this.session !== session) return;
        this.session = null;
        await this.storage.transaction(async (tx) => {
          const current = await tx.get(META_KEY);
          if (current.runId !== meta.runId || current.generation !== meta.generation) return;
          // finish() commits the record/state before resolving session.done.
          if (current.state !== "finished") {
            current.state = result.status === "idle" ? (current.stopRequested ? "stopped" : "queue_timeout") : "paused";
            current.errorCode = result.status === "paused" ? result.code : null;
            await tx.put(META_KEY, current);
          }
          await tx.deleteAlarm();
        });
      })).catch(() => this.serialize(async () => {
        const current = await this.read();
        if (current.runId === meta.runId && current.generation === meta.generation) await this.pause("storage_failure");
      })).catch(() => {
        // If even the fail-closed status write fails, preserve the previous
        // checkpoint and emit no exception text or fabricated terminal state.
        this.safeLog({ event: "paused" });
      });
      this.waitUntil(running);
    } catch (error) {
      this.session?.close();
      this.session = null;
      const code = ["arena_disabled", "token_not_configured", "brain_version_unavailable"].includes(error.message)
        ? error.message : "session_failure";
      await this.pause(code);
    }
  }

  async pause(code) {
    this.session?.close();
    this.session = null;
    await this.storage.transaction(async (tx) => {
      const meta = await tx.get(META_KEY);
      if (meta && meta.state !== "finished") await tx.put(META_KEY, { ...meta, state: "paused", errorCode: code });
      await tx.deleteAlarm();
    });
  }

  stop() {
    return this.serialize(async () => {
      const meta = await this.read();
      if (!ACTIVE.has(meta.state)) return this.status();
      await this.storage.transaction(async (tx) => {
        const current = await tx.get(META_KEY);
        if (!ACTIVE.has(current.state)) return; // Terminal commit may win the stop race.
        await tx.put(META_KEY, { ...current, stopRequested: true, state: "draining" });
      });
      if (this.session) {
        const session = this.session;
        await session.enqueue(() => session.drain());
      }
      else await this.restore();
      return this.status();
    });
  }

  async restore() {
    const meta = await this.read();
    if (!ACTIVE.has(meta.state)) { await this.storage.deleteAlarm(); return; }
    if (this.session) { await this.storage.setAlarm(this.now() + ALARM_INTERVAL_MS); return; }
    const saved = await this.storage.get(CHECKPOINT_KEY);
    if (saved?.finishedRecord) {
      try {
        await new DurableArenaStore(this.storage, meta.runId, meta.generation).finish(saved.finishedRecord);
        await this.storage.deleteAlarm();
      } catch { await this.pause("terminal_record_failure"); }
      return;
    }
    // An actor may die after joining/matching but before the ID write. Never
    // infer "no game" from an absent checkpoint and never rejoin the queue.
    if (!saved?.active?.gameId || saved.active.gameId !== meta.gameId) {
      await this.pause("unknown_match_state");
      return;
    }
    const resumed = { ...meta, generation: meta.generation + 1 };
    await this.storage.transaction(async (tx) => {
      await tx.put(META_KEY, resumed);
      await tx.setAlarm(this.now() + ALARM_INTERVAL_MS);
    });
    await this.launch(resumed, saved.active); // BetaSession performs game:sync, never queue:join.
  }

  alarm() { return this.serialize(() => this.restore()); }
}
