/** Offline voice turn coordinator. No sockets, providers, storage or auto-start.
 * Adapter contracts and ownership rules are documented in README.md.
 */
export class VoiceContractError extends Error {
  constructor(code) { super(code); this.name = 'VoiceContractError'; }
}

export const PCM = Object.freeze({ sampleRate: 48000, channels: 1, frameMs: 20, samples: 960 });
const DEFAULTS = Object.freeze({
  minSpeechMs: 100, silenceMs: 400, maxUtteranceMs: 10000,
  activationMs: 15000, maxCaptures: 4, maxQueue: 4, maxSeen: 256,
  speechThreshold: 500, stageTimeoutMs: 5000, maxPlaybackMs: 30000,
});
const CAPS = { minSpeechMs: 1000, silenceMs: 2000, maxUtteranceMs: 10000,
  activationMs: 30000, maxCaptures: 4, maxQueue: 4, maxSeen: 256,
  speechThreshold: 32767, stageTimeoutMs: 10000, maxPlaybackMs: 30000 };
const validId = (id) => typeof id === 'string' && /^[1-9][0-9]{0,19}$/.test(id) && BigInt(id) < 2n ** 64n;
function scopeOf(input) {
  if (![input?.guildId, input?.channelId, input?.userId].every(validId)) {
    throw new VoiceContractError('invalid_scope');
  }
  return Object.freeze({ guildId: input.guildId, channelId: input.channelId, userId: input.userId });
}
const keyOf = (scope) => JSON.stringify([scope.guildId, scope.channelId, scope.userId]);
function scrub(turn) {
  for (const frame of turn.frames ?? []) frame.fill(0);
  turn.frames = [];
  turn.pcm?.fill(0);
  turn.pcm = null;
}

export class VoiceRuntime {
  #adapters; #limits; #log; #now;
  #session = 0; #connected = false; #channel = null; #botId = null;
  #captures = new Map(); #seen = new Set(); #queue = []; #active = null;
  #worker = null; #connecting = null; #connectController = null; #disconnecting = null;

  constructor({ transport, stt, conversation, tts, limits = {}, log = () => {}, now = () => performance.now() }) {
    // This slice deliberately accepts fake adapters only. A live integration is a separate change.
    for (const [adapter, methods] of [[transport, ['connect', 'play', 'stop', 'disconnect']],
      [stt, ['transcribe']], [conversation, ['reply']], [tts, ['synthesize']]]) {
      if (adapter?.kind !== 'fake' || methods.some((name) => typeof adapter[name] !== 'function')) {
        throw new VoiceContractError('offline_adapters_required');
      }
    }
    if (Object.keys(limits).some((name) => !Object.hasOwn(DEFAULTS, name))) {
      throw new VoiceContractError('invalid_limits');
    }
    this.#limits = { ...DEFAULTS, ...limits };
    for (const [name, value] of Object.entries(this.#limits)) {
      if (!Number.isInteger(value) || value < 1 || value > CAPS[name]) throw new VoiceContractError('invalid_limits');
      if (name.endsWith('Ms') && name !== 'stageTimeoutMs' && value % PCM.frameMs !== 0) {
        throw new VoiceContractError('invalid_limits');
      }
    }
    if (this.#limits.minSpeechMs > this.#limits.maxUtteranceMs || this.#limits.silenceMs > this.#limits.maxUtteranceMs) {
      throw new VoiceContractError('invalid_limits');
    }
    this.#adapters = { transport, stt, conversation, tts };
    this.#log = log; this.#now = now;
  }

  #event(event) {
    try { this.#log(Object.freeze({ event })); } catch { /* Logging cannot change turn handling. */ }
  }

  async connect({ guildId, channelId }) {
    if (!validId(guildId) || !validId(channelId)) throw new VoiceContractError('invalid_scope');
    if (this.#connected || this.#connecting || this.#disconnecting) throw new VoiceContractError('session_already_open');
    const session = ++this.#session;
    const controller = new AbortController();
    this.#connectController = controller;
    const task = this.#step(() => this.#adapters.transport.connect(
      Object.freeze({ guildId, channelId }), { signal: controller.signal }), controller);
    this.#connecting = task;
    try {
      const result = await task;
      if (session !== this.#session || controller.signal.aborted) return null;
      if (!validId(result?.botUserId)) throw new VoiceContractError('invalid_transport');
      this.#channel = { guildId, channelId }; this.#botId = result.botUserId;
      this.#connected = true;
      this.#event('session_opened');
      return session; // Epoch must accompany activation and every decoded frame.
    } catch {
      this.#event('session_open_failed');
      return null;
    } finally {
      if (this.#connecting === task) this.#connecting = null;
    }
  }

  activate({ session, turnId, receive = false, respond = false, ...input }) {
    const scope = scopeOf(input);
    if (typeof receive !== 'boolean' || typeof respond !== 'boolean' ||
        typeof turnId !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(turnId)) {
      throw new VoiceContractError('invalid_activation');
    }
    if (!this.#connected || session !== this.#session || !this.#sameChannel(scope)) return 'ignored';
    if (!receive) return 'disabled';
    if (scope.userId === this.#botId) return 'ignored';
    const key = keyOf(scope), dedupe = JSON.stringify([key, turnId]);
    if (this.#seen.has(dedupe)) return 'duplicate';
    if (this.#captures.has(key) || this.#captures.size >= this.#limits.maxCaptures) return 'busy';
    this.#seen.add(dedupe);
    if (this.#seen.size > this.#limits.maxSeen) this.#seen.delete(this.#seen.values().next().value);
    const turn = { key, scope, turnId, respond, session, frames: [], voiced: 0, silent: 0,
      sequence: -1, started: false, expires: this.#now() + this.#limits.activationMs };
    turn.timer = setTimeout(() => this.#dropCapture(key, 'activation_expired'), this.#limits.activationMs);
    turn.timer.unref?.();
    this.#captures.set(key, turn);
    return 'armed';
  }

  #sameChannel(scope) {
    return scope.guildId === this.#channel?.guildId && scope.channelId === this.#channel?.channelId;
  }

  // Input is already decoded PCM: adapters must classify the authenticated speaker before handing it over.
  receive(frame) {
    if (!this.#connected || frame?.session !== this.#session || frame.isBot !== false) return 'ignored';
    let scope;
    try { scope = scopeOf(frame); } catch { return 'invalid_frame'; }
    if (!this.#sameChannel(scope) || scope.userId === this.#botId) return 'ignored';
    const turn = this.#captures.get(keyOf(scope));
    if (!turn) return 'disabled';
    if (this.#now() >= turn.expires) { this.#dropCapture(turn.key, 'activation_expired'); return 'expired'; }
    if (!(frame.samples instanceof Int16Array) || frame.samples.length !== PCM.samples ||
        !Number.isSafeInteger(frame.sequence) || frame.sequence < 0) return 'invalid_frame';
    if (frame.sequence <= turn.sequence) return 'duplicate';
    turn.sequence = frame.sequence;
    let energy = 0;
    for (const sample of frame.samples) energy += sample * sample;
    const speech = energy / PCM.samples >= this.#limits.speechThreshold ** 2;
    if (!speech && !turn.started) return 'silence';
    if (!turn.started) {
      turn.started = true;
      this.#event('utterance_started');
      if (this.#active?.phase === 'playback') {
        this.#active.controller.abort();
        this.#stopPlayback();
        this.#event('playback_interrupted');
      }
    }
    if ((turn.frames.length + 1) * PCM.frameMs > this.#limits.maxUtteranceMs) {
      this.#dropCapture(turn.key, 'utterance_too_long'); return 'too_long';
    }
    turn.frames.push(new Int16Array(frame.samples)); // Owned copy; never retain caller's buffer.
    if (speech) { turn.voiced += PCM.frameMs; turn.silent = 0; }
    else turn.silent += PCM.frameMs;
    if (turn.silent >= this.#limits.silenceMs) return this.#finish(turn);
    return 'capturing';
  }

  #dropCapture(key, event) {
    const turn = this.#captures.get(key);
    if (!turn) return;
    clearTimeout(turn.timer); scrub(turn); this.#captures.delete(key);
    this.#event(event);
  }

  #finish(turn) {
    clearTimeout(turn.timer); this.#captures.delete(turn.key);
    if (turn.voiced < this.#limits.minSpeechMs) { scrub(turn); this.#event('utterance_short'); return 'too_short'; }
    if (!turn.respond) { scrub(turn); this.#event('response_disabled'); return 'response_disabled'; }
    if (this.#queue.length + Number(this.#active !== null) >= this.#limits.maxQueue) {
      scrub(turn); this.#event('queue_full'); return 'busy';
    }
    turn.pcm = new Int16Array(turn.frames.length * PCM.samples);
    turn.frames.forEach((samples, index) => { turn.pcm.set(samples, index * PCM.samples); samples.fill(0); });
    turn.frames = [];
    this.#queue.push(turn); this.#event('utterance_finished');
    this.#startWorker();
    return 'queued';
  }

  #startWorker() {
    if (this.#worker) return;
    this.#worker = this.#drain().finally(() => {
      this.#worker = null;
      if (this.#queue.length && this.#connected) this.#startWorker();
    });
  }

  async #step(operation, controller) {
    const { signal } = controller;
    signal.throwIfAborted();
    let onAbort;
    const aborted = new Promise((_, reject) => {
      onAbort = () => reject(new VoiceContractError('turn_cancelled'));
      signal.addEventListener('abort', onAbort, { once: true });
    });
    const timer = setTimeout(() => controller.abort(), this.#limits.stageTimeoutMs);
    try {
      // Late adapter results are never allowed to continue the pipeline.
      const pending = Promise.resolve().then(() => { signal.throwIfAborted(); return operation(); }).then((result) => {
        if (signal.aborted) {
          if (result instanceof Int16Array) result.fill(0);
          throw new VoiceContractError('turn_cancelled');
        }
        return result;
      });
      return await Promise.race([pending, aborted]);
    } finally { clearTimeout(timer); signal.removeEventListener('abort', onAbort); }
  }

  async #drain() {
    while (this.#connected && this.#queue.length) {
      const turn = this.#queue.shift();
      const controller = new AbortController();
      const active = { ...turn, controller, phase: 'stt' }; this.#active = active;
      let output;
      try {
        this.#event('stt_started');
        let transcript;
        try {
          transcript = await this.#step(() => this.#adapters.stt.transcribe(turn.pcm,
            { format: PCM, scope: turn.scope, signal: controller.signal }), controller);
        } finally { scrub(turn); }
        if (typeof transcript !== 'string' || !transcript.trim() || transcript.length > 2000) {
          throw new VoiceContractError('invalid_transcript');
        }
        this.#event('stt_completed'); active.phase = 'llm'; this.#event('llm_started');
        const reply = await this.#step(() => this.#adapters.conversation.reply(
          { scope: turn.scope, turnId: turn.turnId, transcript }, { signal: controller.signal }), controller);
        if (typeof reply !== 'string' || !reply.trim() || reply.length > 900) throw new VoiceContractError('invalid_reply');
        this.#event('llm_completed'); active.phase = 'tts'; this.#event('tts_started');
        output = await this.#step(() => this.#adapters.tts.synthesize(reply,
          { format: PCM, signal: controller.signal }), controller);
        if (!(output instanceof Int16Array) || !output.length || output.length % PCM.samples ||
          output.length > this.#limits.maxPlaybackMs / PCM.frameMs * PCM.samples) throw new VoiceContractError('invalid_audio');
        this.#event('tts_completed');
        if (!this.#connected || turn.session !== this.#session || controller.signal.aborted) continue;
        active.phase = 'playback'; this.#event('playback_started');
        await this.#step(() => this.#adapters.transport.play(output,
          { format: PCM, signal: controller.signal }), controller);
        this.#event('playback_completed');
      } catch {
        this.#event(controller.signal.aborted ? 'turn_cancelled' : active.phase + '_failed');
      } finally {
        controller.abort(); scrub(turn); scrub(active); output?.fill?.(0);
        if (active.phase === 'playback') this.#stopPlayback();
        this.#active = null;
      }
    }
  }

  #stopPlayback() {
    try { this.#adapters.transport.stop(); } catch { this.#event('transport_failed'); }
  }

  revoke(input) {
    const key = keyOf(scopeOf(input));
    this.#dropCapture(key, 'activation_revoked');
    this.#queue = this.#queue.filter((turn) => {
      if (turn.key !== key) return true;
      scrub(turn); return false;
    });
    if (this.#active?.key === key) { this.#active.controller.abort(); this.#stopPlayback(); }
  }

  disconnect() {
    if (this.#disconnecting) return this.#disconnecting;
    const task = this.#close();
    this.#disconnecting = task.finally(() => { this.#disconnecting = null; });
    return this.#disconnecting;
  }

  async #close() {
    this.#connected = false; ++this.#session;
    this.#connectController?.abort();
    for (const key of this.#captures.keys()) this.#dropCapture(key, 'activation_revoked');
    for (const turn of this.#queue) scrub(turn);
    this.#queue = [];
    this.#active?.controller.abort();
    this.#stopPlayback();
    try { this.#adapters.transport.disconnect(); } catch { this.#event('transport_failed'); }
    await Promise.allSettled([this.#worker, this.#connecting]);
    this.#channel = null; this.#botId = null;
    this.#event('session_closed');
  }

  async idle() { await this.#worker; }

  status() {
    for (const [key, turn] of this.#captures) if (this.#now() >= turn.expires) this.#dropCapture(key, 'activation_expired');
    return Object.freeze({ mode: 'offline', connected: this.#connected, receiveEnabled: this.#captures.size > 0,
      captures: this.#captures.size, pending: this.#queue.length + Number(this.#active !== null),
      phase: this.#active?.phase ?? 'idle' });
  }
}
