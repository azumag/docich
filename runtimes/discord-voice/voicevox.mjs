/** Injected HTTP boundary only. No fetch, environment reads, sockets or startup. */
import { PCM } from './runtime.mjs';

export class VoicevoxContractError extends Error {
  constructor(code) { super(code); this.name = 'VoicevoxContractError'; }
}
const fail = (code) => { throw new VoicevoxContractError(code); };
const MAX_WAV = 5_760_256; // 30s, 48kHz stereo PCM16 plus bounded RIFF headers
const validId = (id) => typeof id === 'string' && /^[1-9][0-9]{0,19}$/.test(id) && BigInt(id) < 2n ** 64n;
function erase(bytes) {
  if (!ArrayBuffer.isView(bytes)) return;
  try { Uint8Array.prototype.fill.call(bytes, 0); } catch { /* detached owner must erase destination */ }
}
function number(env, key, fallback, min, max, integer = false) {
  const raw = env[key];
  const value = raw === undefined || raw === '' ? fallback : Number(raw);
  if (!Number.isFinite(value) || value < min || value > max || (integer && !Number.isSafeInteger(value))) fail('invalid_config');
  return value;
}
function remoteURL(raw, skipLocal = false) {
  try {
    const url = new URL(raw);
    const host = url.hostname.toLowerCase();
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.search || url.hash || url.pathname !== '/') fail('invalid_config');
    if (host === 'localhost' || host.endsWith('.localhost') || host === '[::1]' || host === '[::]' ||
      /^\[::ffff:(7f[0-9a-f]{2}:|0:0\])/.test(host) || /^127\./.test(host) || host === '0.0.0.0') {
      if (skipLocal) return null;
      fail('invalid_config');
    }
    return url.origin;
  } catch { fail('invalid_config'); }
}

/** Explicit snapshot of existing VOICEVOX_* settings; never reads process.env/.env.
 * selectedURL must belong to VOICEVOX_URLS (shared chooser selection), or first wins.
 * No implicit VM-local defaults, persisted backoff, locks or synthesis retries.
 */
export function voicevoxConfig(env, selectedURL) {
  try {
    const urls = [...new Set(String(env.VOICEVOX_URLS ?? '').split(/[,\s]+/).filter(Boolean)
      .map((url) => remoteURL(url, true)).filter(Boolean))];
    if (!urls.length || urls.length > 8) fail('invalid_config');
    const endpoint = selectedURL === undefined ? urls[0] : remoteURL(selectedURL);
    if (!urls.includes(endpoint)) fail('invalid_config');
    return Object.freeze({ endpoint,
      speaker: number(env, 'VOICEVOX_SPEAKER', 3, 0, 2 ** 31 - 1, true),
      timeoutMs: number(env, 'VOICEVOX_TIMEOUT', 30, 0.001, 30) * 1000,
      maxChars: number(env, 'VOICEVOX_MAX_CHARS', 200, 1, 900, true),
      pitch: number(env, 'VOICEVOX_PITCH', 0, -0.15, 0.15),
      tempo: number(env, 'VOICEVOX_TEMPO', 1, 0.5, 2),
      intonation: number(env, 'VOICEVOX_INTONATION', 1, 0, 2),
    });
  } catch { fail('invalid_config'); }
}

/** Strict bounded RIFF/WAVE PCM16 reader; output is independently owned. */
export function wavToPCM(bytes) {
  try {
    if (!(bytes instanceof Uint8Array) || bytes.byteLength < 44 || bytes.byteLength > MAX_WAV) fail('invalid_wav');
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const tag = (p) => String.fromCharCode(...bytes.subarray(p, p + 4));
    if (tag(0) !== 'RIFF' || tag(8) !== 'WAVE' || view.getUint32(4, true) + 8 !== bytes.length) fail('invalid_wav');
    let format; let data;
    for (let p = 12; p < bytes.length;) {
      if (p + 8 > bytes.length) fail('invalid_wav');
      const size = view.getUint32(p + 4, true), start = p + 8, end = start + size;
      if (end + (size & 1) > bytes.length) fail('invalid_wav');
      if (tag(p) === 'fmt ') {
        if (format || size !== 16) fail('invalid_wav');
        format = { encoding: view.getUint16(start, true), channels: view.getUint16(start + 2, true),
          rate: view.getUint32(start + 4, true), byteRate: view.getUint32(start + 8, true),
          align: view.getUint16(start + 12, true), bits: view.getUint16(start + 14, true) };
      } else if (tag(p) === 'data') {
        if (data) fail('invalid_wav');
        data = { start, size };
      }
      p = end + (size & 1);
    }
    if (!format || !data || format.encoding !== 1 || format.bits !== 16 ||
      ![1, 2].includes(format.channels) || ![24000, 48000].includes(format.rate) ||
      format.align !== format.channels * 2 || format.byteRate !== format.rate * format.align ||
      !data.size || data.size % format.align) fail('invalid_wav');
    const frames = data.size / format.align;
    if (frames > format.rate * 30) fail('invalid_wav');
    const ratio = 48000 / format.rate, samples = frames * ratio;
    const out = new Int16Array(Math.ceil(samples / PCM.samples) * PCM.samples);
    const mono = (i) => {
      const pos = data.start + i * format.align;
      const left = view.getInt16(pos, true);
      return format.channels === 1 ? left : Math.round((left + view.getInt16(pos + 2, true)) / 2);
    };
    for (let i = 0; i < frames; i++) {
      const current = mono(i); out[i * ratio] = current;
      if (ratio === 2) out[i * 2 + 1] = Math.round((current + mono(Math.min(i + 1, frames - 1))) / 2);
    }
    return out;
  } catch { fail('invalid_wav'); }
}

/** Isolated adapter, deliberately rejected by fake-only VoiceRuntime. */
export class InjectedVoicevoxTTS {
  get kind() { return 'voicevox-injected'; }
  #config; #request;
  constructor({ env, selectedURL, request }) {
    this.#config = voicevoxConfig(env, selectedURL);
    if (typeof request !== 'function') fail('invalid_config');
    this.#request = request;
  }
  async synthesize(reply, context) {
    let timer; let parent; let onAbort; let output;
    const controller = new AbortController();
    try {
      const { format, scope, signal } = context;
      if (!scope || ![scope.guildId, scope.channelId, scope.userId].every(validId) ||
        !(signal instanceof AbortSignal) ||
        !format || Object.keys(PCM).some((key) => format[key] !== PCM[key])) fail('invalid_context');
      if (typeof reply !== 'string' || !reply.trim() || reply.length > this.#config.maxChars) fail('invalid_text');
      parent = signal;
      const deadline = performance.now() + this.#config.timeoutMs;
      const aborted = new Promise((_, reject) => {
        controller.signal.addEventListener('abort', () => reject(new VoicevoxContractError('tts_cancelled')), { once: true });
      });
      // Register the race before honoring an already aborted parent.
      const check = () => { if (controller.signal.aborted || performance.now() >= deadline) fail('tts_cancelled'); };
      onAbort = () => controller.abort(); parent.addEventListener('abort', onAbort, { once: true });
      timer = setTimeout(onAbort, this.#config.timeoutMs);
      const operation = async () => {
        const post = async (url, body, maxBytes, consume) => {
          check();
          const response = await this.#request(Object.freeze({ url, method: 'POST',
            headers: Object.freeze({ 'Content-Type': 'application/json' }), body,
            signal: controller.signal, maxBytes }));
          let bytes;
          try {
            bytes = response?.body;
            check();
            if (!(bytes instanceof Uint8Array) || !bytes.length || bytes.length > maxBytes ||
              !Number.isInteger(response.status) || response.status < 200 || response.status >= 300) fail('tts_failed');
            return consume(bytes);
          } finally { erase(bytes); }
        };
        const { endpoint, speaker, pitch, tempo, intonation } = this.#config;
        const query = await post(`${endpoint}/audio_query?${new URLSearchParams({ text: reply, speaker: String(speaker) })}`,
          '', 65536, (bytes) => {
            const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
            if (!value || typeof value !== 'object' || Array.isArray(value) || 'detail' in value ||
              !Array.isArray(value.accent_phrases) || !Number.isFinite(value.speedScale)) fail('tts_failed');
            if (!Number.isFinite(value.pitchScale)) fail('tts_failed');
            return { ...value, pitchScale: value.pitchScale + pitch, speedScale: tempo, intonationScale: intonation,
              outputSamplingRate: PCM.sampleRate, outputStereo: false };
          });
        check();
        output = await post(`${endpoint}/synthesis?speaker=${speaker}`, JSON.stringify(query), MAX_WAV, wavToPCM);
        check(); return output;
      };
      const pending = Promise.resolve().then(operation).finally(() => {
        if (controller.signal.aborted) erase(output);
      });
      const result = Promise.race([pending, aborted]);
      if (parent.aborted) controller.abort();
      return await result;
    } catch (error) {
      erase(output);
      // No provider message, request text, URL, scope or cause leaves this boundary.
      let code = 'tts_failed';
      try {
        if (error instanceof VoicevoxContractError &&
          ['invalid_context', 'invalid_text', 'invalid_wav', 'tts_cancelled', 'tts_failed'].includes(error.message)) code = error.message;
      } catch { /* hostile exception getters remain private */ }
      throw new VoicevoxContractError(code);
    } finally {
      clearTimeout(timer); parent?.removeEventListener('abort', onAbort); controller.abort();
    }
  }
}
