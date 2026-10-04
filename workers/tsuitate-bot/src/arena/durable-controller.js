import { BRAIN_VERSION, LINEAR_PROFILE } from "../brain/index.js";
import { BetaSession } from "./beta-session.js";
import { DurableArenaStore, META_KEY, CHECKPOINT_KEY, RECORD_KEY, runKey, recordKey } from "./durable-store.js";

export const SINGLETON_NAME = "beta:DoCiAI";
export const QUEUE_WAIT_MS = 60000;
export const ALARM_INTERVAL_MS = 20000;
const RUN_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const ACTIVE = new Set(["queued", "playing", "draining"]);
const LOG_EVENTS = new Set(["connected", "queued", "matched", "disconnected", "connection_retry",
  "sync_unconfirmed", "invalid_observation", "conflicting_observation", "move_ack_unknown",
  "invalid_move_ack", "resigning_no_candidate", "draining_current_game", "game_finished", "paused", "invalid_match_shape"]);

function initial() { return { version: 1, state: "stopped", runId: null, generation: 0,
  gameId: null, completedGames: 0, reservedGames: 0, stopRequested: false, errorCode: null, settled: true }; }

/** Fixed singleton operations; the Worker entrypoint authenticates its caller. */
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

  snapshot(meta) {
    return { state: meta.state, runId: meta.runId, gameId: meta.gameId,
      completedGames: meta.completedGames, reservedGames: meta.reservedGames,
      maxGames: 1, queueWaitSeconds: 60, stopRequested: meta.stopRequested,
      errorCode: meta.errorCode, brainVersion: meta.brainVersion ?? BRAIN_VERSION,
      readyForNextRun: meta.settled === true && ["stopped", "queue_timeout", "finished"].includes(meta.state) };
  }

  async status() { return this.snapshot(await this.read()); }

  async settle(tx, meta) {
    // Only after the socket has closed / no session was restored. A terminal
    // record alone is not sufficient to permit the next explicit run.
    await tx.deleteAlarm();
    await tx.put(CHECKPOINT_KEY, { version: 1, active: null });
    const settled = { ...meta, settled: true };
    await tx.put(META_KEY, settled);
    await tx.put(runKey(meta.runId), this.snapshot(settled));
  }

  ready() {
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
      if (existing.runId === options.runId) {
        return this.status(); // Same request can never queue a second game.
      }
      const previousReceipt = await this.storage.get(runKey(options.runId));
      if (previousReceipt) return previousReceipt; // Even after many later runs.
      if (!this.snapshot(existing).readyForNextRun || this.session) throw new Error("run_locked");
      this.ready();
      const meta = { ...initial(), runId: options.runId, reservedGames: 1, generation: existing.generation + 1, settled: false,
        state: "queued", brainVersion: BRAIN_VERSION, profile: LINEAR_PROFILE,
        queueDeadlineAt: this.now() + QUEUE_WAIT_MS };
      // Commit reservation before creating the socket or joining the queue.
      await this.storage.transaction(async (tx) => {
        const current = await tx.get(META_KEY) ?? initial();
        if (current.generation !== existing.generation || !this.snapshot(current).readyForNextRun) throw new Error("run_locked");
        // Preserve the v1 single-run record before clearing the latest alias.
        if (current.runId) {
          const record = await tx.get(RECORD_KEY);
          if (record) await tx.put(recordKey(current.runId), record);
          if (record) await tx.put(`beta:game:${record.gameId}`, current.runId);
          await tx.put(runKey(current.runId), this.snapshot(current));
        }
        await tx.delete(RECORD_KEY);
        await tx.put(CHECKPOINT_KEY, { version: 1, active: null });
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
          if (["finished", "stopped", "queue_timeout"].includes(current.state)) await this.settle(tx, current);
          else await tx.deleteAlarm();
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
      const code = ["token_not_configured", "brain_version_unavailable"].includes(error.message)
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

  stop(options = null) {
    return this.serialize(async () => {
      const meta = await this.read();
      if (options && options.runId !== meta.runId) {
        const receipt = await this.storage.get(runKey(options.runId));
        if (receipt) return receipt;
        throw new Error("run_mismatch");
      }
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
    if (this.session && meta.state === "finished") {
      await this.storage.setAlarm(this.now() + ALARM_INTERVAL_MS);
      return; // session.done still owns socket cleanup / final checkpoint writes.
    }
    if (!ACTIVE.has(meta.state)) {
      if (meta.state === "finished") {
        // Recover a crash between terminal commit and session completion.
        const record = await this.storage.get(RECORD_KEY);
        if (!record?.completed || record.gameId !== meta.gameId) return this.pause("terminal_record_failure");
        await this.storage.transaction((tx) => this.settle(tx, meta));
      } else await this.storage.deleteAlarm();
      return;
    }
    if (this.session) { await this.storage.setAlarm(this.now() + ALARM_INTERVAL_MS); return; }
    const saved = await this.storage.get(CHECKPOINT_KEY);
    if (saved?.finishedRecord) {
      try {
        await new DurableArenaStore(this.storage, meta.runId, meta.generation).finish(saved.finishedRecord);
        const completed = await this.read();
        await this.storage.transaction((tx) => this.settle(tx, completed));
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
