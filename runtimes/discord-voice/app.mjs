import { DatabaseSync } from 'node:sqlite';
import { VoiceRuntime, PCM } from './runtime.mjs';
import { InjectedVoicevoxTTS } from './voicevox.mjs';
import { offlineFixtures } from './offline-fixtures.mjs';
import { generateConversationReply } from '../../workers/discord-chat/src/conversation.js';
import { beginConversation, failConversation, finishConversation, initializeMemory,
  markSending, validContext } from '../../workers/discord-chat/src/memory.js';

export class OfflineAppError extends Error {
  constructor(code) { super(code); this.name = 'OfflineAppError'; }
}
const fail = (code) => { throw new OfflineAppError(code); };
const validId = (id) => typeof id === 'string' && /^[1-9][0-9]{0,19}$/.test(id) && BigInt(id) < 2n ** 64n;
const FAILURE_EVENTS = new Set(['stt_failed', 'llm_failed', 'tts_failed', 'playback_failed', 'turn_cancelled']);
function sessionMemory() {
  const db = new DatabaseSync(':memory:');
  const sql = { exec(query, ...params) {
    if (query.includes('CREATE TABLE')) { db.exec(query); return { toArray: () => [] }; }
    const rows = db.prepare(query).all(...params);
    return { toArray: () => rows, one: () => rows[0] };
  } };
  try { initializeMemory(sql); } catch { db.close(); fail('session_failed'); }
  return { db, sql };
}

/** Offline composition, not a live connection switch. Only trusted synthetic
 * fixtures can be injected. Memory belongs to a single join/leave lifecycle.
 */
export class OfflineVoiceApp {
  #persona; #fixtures; #log; #state = null; #closing = null; #stageMs;
  constructor({ persona, fixtures = offlineFixtures(), log = () => {}, stageTimeoutMs = 5000 } = {}) {
    if (typeof persona !== 'string' || !persona.trim() || Buffer.byteLength(persona) > 32768 ||
      fixtures?.kind !== 'fake' || !['connect', 'transcribe', 'model', 'request', 'play', 'stop', 'disconnect']
        .every((key) => typeof fixtures[key] === 'function') || typeof log !== 'function' ||
      !Number.isInteger(stageTimeoutMs) || stageTimeoutMs < 1 || stageTimeoutMs > 10000) fail('invalid_config');
    this.#persona = persona; this.#fixtures = fixtures; this.#log = log; this.#stageMs = stageTimeoutMs;
  }
  #failPending(state) {
    if (!state.closed && state.pending) failConversation(state.sql, state.pending.seq);
    state.pending = null;
  }
  #check(state, signal) {
    if (state.closed || this.#state !== state || signal.aborted) fail('session_cancelled');
  }
  async join({ guildId, channelId }) {
    if (this.#state || this.#closing) fail('session_busy');
    if (!validId(guildId) || !validId(channelId)) fail('invalid_command');
    const state = { ...sessionMemory(), guildId, channelId, closed: false, epoch: null,
      pending: null, turns: 0, plays: 0, sequence: 0 };
    this.#state = state;
    const fixtures = this.#fixtures;
    const tts = new InjectedVoicevoxTTS({ env: { VOICEVOX_URLS: 'http://windows.example:50021',
      VOICEVOX_TIMEOUT: String(this.#stageMs / 1000), VOICEVOX_MAX_CHARS: '200' },
      request: (request) => fixtures.request(request) });
    const conversation = { kind: 'fake', reply: async ({ scope, turnId, transcript }, { signal }) => {
      this.#check(state, signal); this.#failPending(state);
      if (state.turns >= 64) fail('session_turn_limit');
      const event = { id: JSON.stringify([state.epoch, scope.guildId, scope.channelId, scope.userId, turnId]),
        guildId: scope.guildId, channelId: scope.channelId, authorId: scope.userId,
        authorName: '合成話者', content: transcript, referenceId: null, createdAt: Date.now() / 1000 };
      const seq = beginConversation(state.sql, event);
      if (seq === null) fail('duplicate_turn');
      state.turns++; state.pending = { seq, context: null, reply: null };
      const generated = await generateConversationReply({ DOCICH_PERSONA: this.#persona,
        AI: { run: (model, input) => fixtures.model(model, input, { signal }) } }, state.sql, event, seq);
      this.#check(state, signal);
      if (!validContext(state.sql, seq, generated.context)) fail('session_cancelled');
      // Explicit application policy: no hidden truncation of the text core's 901-character contract.
      if (generated.reply.length > 200) fail('voice_reply_limit');
      state.pending = { seq, context: generated.context, reply: generated.reply };
      return generated.reply;
    } };
    const transport = { kind: 'fake', connect: (...args) => fixtures.connect(...args),
      play: async (pcm, context) => {
        this.#check(state, context.signal);
        const pending = state.pending;
        if (!pending?.context || !validContext(state.sql, pending.seq, pending.context) ||
          !markSending(state.sql, pending.seq)) fail('session_cancelled');
        await fixtures.play(pcm, context); this.#check(state, context.signal);
        if (!finishConversation(state.sql, pending.seq, 'offline-play-' + pending.seq, pending.reply)) fail('session_cancelled');
        state.plays++; state.pending = null;
      }, stop: () => fixtures.stop(), disconnect: () => fixtures.disconnect() };
    state.runtime = new VoiceRuntime({ transport, stt: { kind: 'fake', transcribe: (...args) => fixtures.transcribe(...args) },
      conversation, tts: { kind: 'fake', synthesize: (...args) => tts.synthesize(...args) },
      limits: { stageTimeoutMs: this.#stageMs }, log: (record) => {
        if (FAILURE_EVENTS.has(record.event)) this.#failPending(state);
        try { this.#log(record); } catch { /* display cannot change the lifecycle */ }
      } });
    try {
      state.epoch = await state.runtime.connect({ guildId, channelId });
      if (state.epoch === null || state.closed || this.#state !== state) fail('session_cancelled');
      return this.status();
    } catch { if (this.#state === state) await this.leave(); fail('session_failed'); }
  }
  #joined() {
    const state = this.#state;
    if (!state || state.closed || state.epoch === null) fail('not_joined');
    return state;
  }
  activate({ userId, turnId, receive = false, respond = false }) {
    const state = this.#joined();
    return state.runtime.activate({ guildId: state.guildId, channelId: state.channelId, userId, turnId,
      session: state.epoch, receive, respond });
  }
  receiveSynthetic({ userId, frames = 6, end = false }) {
    const state = this.#joined();
    if (!validId(userId) || !Number.isInteger(frames) || frames < 1 || frames > 500 || typeof end !== 'boolean') fail('invalid_command');
    const samples = new Int16Array(PCM.samples).fill(end ? 0 : 4000);
    let result;
    try {
      for (let i = 0; i < frames; i++) {
        const sequence = ++state.sequence;
        result = state.runtime.receive({ guildId: state.guildId, channelId: state.channelId, userId,
          session: state.epoch, isBot: false, sequence, samples });
        if (!['capturing', 'silence'].includes(result)) break;
      }
      return result;
    } finally { samples.fill(0); }
  }
  endSynthetic({ userId }) { return this.receiveSynthetic({ userId, frames: 20, end: true }); }
  cancel({ userId }) {
    const state = this.#joined();
    state.runtime.revoke({ guildId: state.guildId, channelId: state.channelId, userId });
  }
  async idle() { if (this.#state?.runtime) await this.#state.runtime.idle(); return this.status(); }
  leave() {
    if (this.#closing) return this.#closing;
    const state = this.#state;
    if (!state) return Promise.resolve();
    state.closed = true;
    const close = async () => {
      try { await state.runtime?.disconnect(); }
      finally {
        state.pending = null; state.db.close();
        if (this.#state === state) this.#state = null;
      }
    };
    this.#closing = close().finally(() => { this.#closing = null; });
    return this.#closing;
  }
  status() {
    const state = this.#state;
    const runtime = state?.runtime?.status();
    return Object.freeze({ mode: 'offline', joined: Boolean(state && !state.closed && state.epoch !== null),
      phase: runtime?.phase ?? 'idle', pending: runtime?.pending ?? 0, captures: runtime?.captures ?? 0,
      completed: state && !state.closed ? state.plays : 0,
      remembered: state && !state.closed ? Number(state.sql.exec("SELECT COUNT(*) AS count FROM conversations WHERE state='sent'").one().count) : 0,
      maxReplyChars: 200, stageTimeoutMs: this.#stageMs });
  }
}
