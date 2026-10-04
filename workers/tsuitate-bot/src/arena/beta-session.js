import { BRAIN_VERSION, chooseMove, validateProfile } from "../brain/index.js";
import { MoveGate, parsePlayerView, toBrainObservation } from "../adapters/beta.js";
import { fetchPublicResult, validBetaGameId } from "../adapters/beta-results.js";
import { normalizeGameRecord } from "../training/index.js";

const SITE = "beta.tsuitate.info";
const RULES_KEY = "beta-300+3-f10";
const isoNow = () => new Date().toISOString();

/** One socket lifetime owns one match, including reconnects, never a second match. */
export class BetaSession {
  constructor({ socket, store, profile, checkpoint = null, resolveResult = fetchPublicResult,
    log = () => {}, ackMs = 5000, queueWaitMs = 120000, retryMs = 1500, pollMs = 10000,
    queueSettleMs = 5000, connectDeadlineMs = 65000, disconnectDeadlineMs = 65000 }) {
    this.socket = socket;
    this.store = store;
    this.profile = validateProfile(profile);
    if (!this.profile) throw new Error("invalid_brain_profile");
    this.resolveResult = resolveResult;
    this.log = log;
    this.ackMs = ackMs;
    this.queueWaitMs = queueWaitMs;
    this.retryMs = retryMs;
    this.pollMs = pollMs;
    this.queueSettleMs = queueSettleMs;
    this.connectDeadlineMs = connectDeadlineMs;
    this.disconnectDeadlineMs = disconnectDeadlineMs;
    this.gate = new MoveGate();
    this.gameId = null;
    this.record = null;
    this.pendingIndex = null;
    this.terminalSeen = false;
    this.resigning = false;
    this.stopping = false;
    this.closed = false;
    this.syncPending = null;
    this.syncSequence = 0;
    this.resultBusy = false;
    this.resultAttempts = 0;
    this.nullSyncs = 0;
    this.failedSyncs = 0;
    this.syncExhausted = false;
    this.tail = Promise.resolve();
    this.timers = new Set();
    this.listeners = [];
    this.socketEpoch = 0;
    this.everConnected = false;
    this.resignSentEpoch = null;
    this.brainVersionMismatch = false;
    if (checkpoint) this.restore(checkpoint);
    this.done = new Promise((resolve) => { this.resolveDone = resolve; });
  }

  restore(saved) {
    if (saved?.version !== 1 || !validBetaGameId(saved.gameId)
        || typeof saved.terminalSeen !== "boolean" || typeof saved.resigning !== "boolean") {
      throw new Error("invalid_checkpoint");
    }
    const record = saved.record === null ? null : normalizeGameRecord(saved.record);
    if (saved.record !== null && (!record || record.gameId !== saved.gameId || record.site !== SITE || record.completed)) {
      throw new Error("invalid_checkpoint");
    }
    if (saved.pendingIndex !== null && (!Number.isSafeInteger(saved.pendingIndex)
        || !record || saved.pendingIndex < 0 || saved.pendingIndex >= record.decisions.length)) {
      throw new Error("invalid_checkpoint");
    }
    this.gate.restore(saved.gate);
    if (this.gate.view && this.gate.view.gameId !== saved.gameId) throw new Error("invalid_checkpoint");
    this.gameId = saved.gameId;
    this.record = record;
    if (record && record.brainVersion !== BRAIN_VERSION) {
      // Keep the original attribution and checkpoint. Without that implementation
      // we cannot continue this match or treat it as ordinary training evidence.
      this.brainVersionMismatch = true;
      record.historyComplete = false;
      record.reason = "interrupted";
    }
    this.profile = record?.profile ?? this.profile;
    this.pendingIndex = saved.pendingIndex;
    this.terminalSeen = saved.terminalSeen;
    this.resigning = saved.resigning;
    this.stopping = this.gate.quiescing;
  }

  checkpoint() {
    return { version: 1, gameId: this.gameId, record: this.record, gate: this.gate.checkpoint(),
      pendingIndex: this.pendingIndex, terminalSeen: this.terminalSeen, resigning: this.resigning };
  }

  async persist() {
    if (this.gameId) await this.store.save({ version: 1, active: this.checkpoint() });
  }

  enqueue(operation) {
    this.tail = this.tail.then(async () => { if (!this.closed) await operation(); })
      .catch(() => this.pause("session_failure"));
    return this.tail;
  }

  later(operation, milliseconds) {
    const timer = setTimeout(() => { this.timers.delete(timer); void this.enqueue(operation); }, milliseconds);
    this.timers.add(timer);
    return timer;
  }

  listen(event, handler) {
    const listener = (...args) => {
      const epoch = this.socketEpoch;
      void this.enqueue(() => {
        if (epoch === this.socketEpoch) return handler(args[0], this.gate.generation);
      });
    };
    this.socket.on(event, listener);
    this.listeners.push([event, listener]);
  }

  start() {
    if (this.brainVersionMismatch) {
      void this.enqueue(async () => {
        await this.persist();
        await this.pause("brain_version_unavailable");
      });
      return this.done;
    }
    const connected = () => {
      const epoch = ++this.socketEpoch;
      void this.enqueue(() => { if (epoch === this.socketEpoch) return this.onConnect(); });
    };
    this.socket.on("connect", connected);
    this.listeners.push(["connect", connected]);
    this.listen("disconnect", (_reason, generation) => this.onDisconnect(generation));
    this.listen("connect_error", (error) => {
      // Never log transport errors; they can contain URLs or credential data.
      if (error?.message === "unauthorized") return this.pause("authentication_failed");
      this.log({ event: "connection_retry" });
    });
    this.listen("match:found", (payload, generation) => this.onMatch(payload, generation, true));
    this.listen("game:active", (payload, generation) => this.onMatch(payload, generation, false));
    this.listen("game:state", (raw, generation) => this.onView(raw, generation, false));
    for (const event of ["game:moveAccepted", "game:opponentMoved", "game:foul", "game:opponentFoul", "game:check"]) {
      this.listen(event, (_raw, generation) => this.sync(generation));
    }
    this.listen("game:end", (_raw, generation) => {
      if (generation !== this.gate.generation || !this.gameId) return;
      this.sync(generation);
      this.requestResult();
    });
    this.listen("queue:closed", () => { if (!this.gameId) return this.pause("queue_closed"); });
    this.later(() => this.poll(), this.pollMs);
    this.later(() => { if (!this.everConnected) return this.pause("connection_unavailable"); }, this.connectDeadlineMs);
    this.socket.connect();
    return this.done;
  }

  async onConnect() {
    this.everConnected = true;
    const generation = this.gate.newConnection();
    this.syncPending = null;
    this.log({ event: "connected", resuming: Boolean(this.gameId) });
    if (this.gameId) { this.sync(generation); return; }
    this.gate.acceptView(null, generation, { synchronized: true });
    if (this.stopping) { await this.leaveQueue(); return; }
    // The server enforces one active game. A rejected join is not evidence that
    // an existing match has ended; game:active or an on-disk checkpoint resumes it.
    this.emitAck("queue:join", null, (error, ack) => {
      if (this.gameId) return;
      if (error || ack?.ok !== true) {
        // game:active can arrive after a rejected join acknowledgement.
        this.settleQueue("queue_join_unconfirmed");
        return;
      }
      this.log({ event: "queued" });
    });
    this.later(async () => { if (!this.gameId) await this.drain(); }, this.queueWaitMs);
  }

  async onDisconnect(generation) {
    this.gate.disconnect(generation);
    this.syncPending = null;
    await this.persist();
    this.log({ event: "disconnected", hasGame: Boolean(this.gameId) });
    const epoch = this.socketEpoch;
    // Do not keep a background process stuck forever after reconnect attempts fail.
    this.later(async () => {
      if (epoch === this.socketEpoch && !this.socket.connected) {
        if (this.gameId && this.record) this.requestResult();
        else await this.pause(this.gameId ? "missing_player_view" : "connection_unavailable");
      }
    }, this.disconnectDeadlineMs);
  }

  async onMatch(payload, generation, fromMatch) {
    if (generation !== this.gate.generation || !this.socket.connected) return;
    if (!validBetaGameId(payload?.gameId)) return this.pause("invalid_match");
    if (this.gameId && this.gameId !== payload.gameId) return this.pause("unexpected_game");
    const newGame = !this.gameId;
    this.gameId = payload.gameId;
    if (fromMatch && !["sente", "gote"].includes(payload.yourColor)) return this.pause("invalid_match");
    if (!this.record && fromMatch) this.newRecord(payload.yourColor, true);
    if (newGame) this.log({ event: "matched", gameId: this.gameId, profileId: this.profile.id });
    await this.persist();
    this.sync(generation);
  }

  newRecord(color, historyComplete) {
    this.record = {
      schemaVersion: 1, kind: "tsuitate_game", site: SITE, ruleset: "tsuitate-9x9", rulesKey: RULES_KEY,
      gameId: this.gameId, color: color === "sente" ? "b" : "w", startedAt: isoNow(), endedAt: isoNow(),
      brainVersion: BRAIN_VERSION, profile: this.profile, decisions: [], historyComplete,
      completed: false, outcome: "unknown", reason: "unknown",
    };
  }

  emitAck(event, payload, callback) {
    if (this.closed || !this.socket.connected) return false;
    const generation = this.gate.generation;
    const epoch = this.socketEpoch;
    const ack = (error, value) => {
      void this.enqueue(() => {
        if (epoch === this.socketEpoch && generation === this.gate.generation && this.socket.connected) return callback(error, value);
      });
    };
    if (payload === null) this.socket.timeout(this.ackMs).emit(event, ack);
    else this.socket.timeout(this.ackMs).emit(event, payload, ack);
    return true;
  }

  sync(generation = this.gate.generation) {
    if (!this.gameId || this.closed || generation !== this.gate.generation || !this.socket.connected || this.syncPending) return;
    const ticket = { generation, gameId: this.gameId, sequence: ++this.syncSequence };
    this.syncPending = ticket;
    this.emitAck("game:sync", { gameId: this.gameId }, async (error, ack) => {
      if (this.syncPending !== ticket) return;
      this.syncPending = null;
      if (error || !ack || !Object.hasOwn(ack, "state")) {
        this.failedSyncs += 1;
        this.log({ event: "sync_unconfirmed" });
        if (this.failedSyncs >= 5) return this.pause("synchronization_failed");
        this.later(() => this.sync(), this.retryMs);
        return;
      }
      this.failedSyncs = 0;
      if (ack.state === null) {
        // Null never manufactures a terminal game or clears an ambiguous move.
        this.nullSyncs += 1;
        if (this.nullSyncs >= 5) {
          if (!this.record) return this.pause("missing_player_view");
          // Let the bounded lookup settle before stopping. In particular an
          // already-ended checkpoint must become an unknown terminal record.
          this.syncExhausted = true;
          this.requestResult();
          return;
        }
        this.requestResult();
        this.later(() => this.sync(), this.retryMs);
        return;
      }
      this.nullSyncs = 0;
      this.syncExhausted = false;
      await this.onView(ack.state, generation, true);
    });
  }

  async onView(raw, generation, synchronized) {
    if (generation !== this.gate.generation || !this.socket.connected) return;
    let view;
    try { view = parsePlayerView(raw); }
    catch {
      try { this.gate.acceptView(raw, generation, { synchronized }); } catch { /* gate blocks old view */ }
      this.log({ event: "invalid_observation" });
      this.sync(generation);
      return;
    }
    if (this.gameId && view.gameId !== this.gameId) return;
    if (!validBetaGameId(view.gameId)) return this.pause("invalid_match");
    if (!this.gameId) {
      // An authoritative view may precede match:found. Lacking the initial
      // lifecycle notification, do not claim a complete training history.
      this.gameId = view.gameId;
    }
    if (!this.record) this.newRecord(view.yourColor, false);
    if (this.record.color !== (view.yourColor === "sente" ? "b" : "w")) return this.pause("changed_color");
    const prior = this.gate.view;
    const intent = this.gate.pending;
    let accepted;
    try { accepted = this.gate.acceptView(view, generation, { synchronized }); }
    catch {
      this.log({ event: "conflicting_observation" });
      await this.persist();
      this.sync(generation);
      return;
    }
    if (!accepted) { this.sync(generation); return; }
    if (intent && prior && this.pendingIndex !== null) {
      const decision = this.record.decisions[this.pendingIndex];
      if (view.moveNumber > intent.moveNumber && view.fouls.you === prior.fouls.you) decision.feedback = "accepted";
      else if (view.moveNumber === intent.moveNumber && view.fouls.you > prior.fouls.you) decision.feedback = "foul";
    }
    if (view.status === "ended") {
      this.terminalSeen = true;
      await this.persist();
      this.requestResult();
      return;
    }
    await this.persist();
    await this.maybeMove();
  }

  async maybeMove() {
    if (this.closed || !this.socket.connected) return;
    if (this.resigning) {
      if (!this.gate.needsSync && this.gate.view?.status === "playing" && this.resignSentEpoch !== this.socketEpoch) {
        this.resignSentEpoch = this.socketEpoch;
        this.socket.emit("game:resign", { gameId: this.gameId });
      }
      return;
    }
    if (!this.gate.canMove) return;
    const epoch = this.socketEpoch;
    const observation = toBrainObservation(this.gate.view);
    const recentMoves = this.record.decisions.filter((decision) => decision.feedback === "accepted").map((decision) => decision.usi).slice(-64);
    const choice = chooseMove(observation, {
      profile: this.profile, seed: `${this.gameId}:${observation.moveNumber}`,
      recentMoves, forbiddenMoves: [...this.gate.attemptedMoves],
    });
    if (!choice) {
      this.resigning = true;
      await this.persist();
      if (this.socket.connected && epoch === this.socketEpoch) {
        this.resignSentEpoch = epoch;
        this.socket.emit("game:resign", { gameId: this.gameId });
      }
      this.log({ event: "resigning_no_candidate" });
      this.sync();
      return;
    }
    const intent = this.gate.prepareMove(choice.usi);
    this.pendingIndex = this.record.decisions.length;
    const decisionIndex = this.pendingIndex;
    this.record.decisions.push({ moveNumber: observation.moveNumber, observation, usi: choice.usi,
      features: choice.features, score: choice.score, feedback: "unknown" });
    // Persist before crossing the network boundary. A process crash cannot
    // erase the evidence that this exact move might already have been sent.
    await this.persist();
    const sent = epoch === this.socketEpoch && this.emitAck("game:move", intent.payload, async (error, ack) => {
      if (error) {
        this.gate.timeout(intent);
        this.log({ event: "move_ack_unknown" });
      } else {
        let valid = true;
        try { valid = this.gate.acknowledge(intent, ack); }
        catch { valid = false; this.gate.timeout(intent); this.log({ event: "invalid_move_ack" }); }
        if (valid && ack?.ok === true) this.record.decisions[decisionIndex].feedback = "accepted";
        else if (valid && ack?.ok === false && ack.reason === "foul" && Number.isInteger(ack.foulCount)
            && ack.foulCount > 0 && ack.foulCount <= 10) this.record.decisions[decisionIndex].feedback = "foul";
      }
      await this.persist();
      this.sync();
    });
    if (!sent) { this.gate.timeout(intent); await this.persist(); }
    else this.log({ event: "move_sent", gameId: this.gameId, moveNumber: observation.moveNumber,
      usi: choice.usi, profileId: this.profile.id });
  }

  requestResult() {
    if (this.resultBusy || !this.record || this.closed) return;
    this.resultBusy = true;
    const gameId = this.gameId;
    const color = this.record.color;
    Promise.resolve(this.resolveResult(gameId, color)).then((result) => this.enqueue(async () => {
      this.resultBusy = false;
      if (this.gameId !== gameId) return;
      if (result?.gameId === gameId && ["win", "loss", "draw", "unknown"].includes(result.outcome)) {
        await this.finish(result);
        return;
      }
      this.resultAttempts += 1;
      if (this.syncExhausted) {
        if (this.terminalSeen) await this.finish({ outcome: "unknown", reason: "unknown", endedAt: isoNow() });
        else await this.pause("terminal_unconfirmed");
      } else if (this.resultAttempts < 5) this.later(() => this.requestResult(), this.retryMs);
      else if (this.terminalSeen) await this.finish({ outcome: "unknown", reason: "unknown", endedAt: isoNow() });
      else if (!this.socket.connected) await this.pause("terminal_unconfirmed");
      else { this.resultAttempts = 0; this.sync(); }
    })).catch(() => this.enqueue(() => { this.resultBusy = false; return this.pause("result_unavailable"); }));
  }

  async finish(result) {
    const record = normalizeGameRecord({ ...this.record, completed: true,
      outcome: result.outcome, reason: result.reason, endedAt: result.endedAt,
      startedAt: result.startedAt ?? this.record.startedAt });
    if (!record) throw new Error("invalid_game_record");
    await this.store.save({ version: 1, finishedRecord: record });
    await this.store.finish(record);
    await this.store.save({ version: 1, active: null });
    this.log({ event: "game_finished", gameId: record.gameId, outcome: record.outcome,
      reason: record.reason, profileId: record.profile.id, historyComplete: record.historyComplete });
    this.close();
    this.resolveDone({ status: "finished", record, stopping: this.stopping });
  }

  async drain() {
    this.stopping = true;
    this.gate.requestQuiesce();
    await this.persist();
    if (!this.gameId) await this.leaveQueue();
    else this.log({ event: "draining_current_game", gameId: this.gameId });
  }

  async leaveQueue() {
    if (!this.socket.connected) return this.pause("queue_leave_unconfirmed");
    this.emitAck("queue:leave", null, async (error, ack) => {
      if (this.gameId) return; // The match won the race; play it to completion.
      this.settleQueue(error || ack?.ok !== true ? "queue_leave_unconfirmed" : null);
    });
  }

  settleQueue(errorCode) {
    const epoch = this.socketEpoch;
    // The guide does not promise relative ACK/push ordering. Keep receiving
    // match:found/game:active through an ACK-sized settling window. This is not
    // a server-side guarantee against an arbitrarily delayed notification.
    this.later(async () => {
      if (this.gameId || epoch !== this.socketEpoch) return;
      if (errorCode) return this.pause(errorCode);
      this.close();
      this.resolveDone({ status: "idle", stopping: true });
    }, this.queueSettleMs);
  }

  poll() {
    if (this.gameId) this.sync();
    this.later(() => this.poll(), this.pollMs);
  }

  async pause(code) {
    if (this.closed) return;
    // Preserve the previous durable checkpoint on storage failure. No next
    // match is started and no unresolved game is reported as a finished loss.
    this.log({ event: "paused", code, hasGame: Boolean(this.gameId) });
    this.close();
    this.resolveDone({ status: "paused", code, gameId: this.gameId });
  }

  close() {
    this.closed = true;
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    for (const [event, listener] of this.listeners) this.socket.off(event, listener);
    this.listeners.length = 0;
    this.socket.disconnect();
  }
}
