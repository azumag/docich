import { PCM } from './runtime.mjs';

export class CloudflareSttError extends Error {
  constructor(code) {
    super(code);
    this.name = 'CloudflareSttError';
    this.code = code;
  }
}

const fail = (code) => {
  throw new CloudflareSttError(code);
};

const MODEL = '@cf/openai/whisper-large-v3-turbo';
const MAX_AUDIO_MS = 10_000;
const MAX_RESPONSE_BYTES = 262_144;

const validToken = (value) =>
  typeof value === 'string' &&
  value.length >= 20 &&
  value.length <= 4096 &&
  !/\s/.test(value);

const validAccountId = (value) =>
  typeof value === 'string' && /^[0-9a-f]{32}$/i.test(value);

function erase(value) {
  if (!ArrayBuffer.isView(value)) return;
  try {
    Uint8Array.prototype.fill.call(value, 0);
  } catch {
    // Detached/foreign buffers are owned by their creator.
  }
}

export function loadCloudflareSttConfig(env = process.env) {
  const accountId = env.DOCICH_DISCORD_VOICE_CF_ACCOUNT_ID;
  const apiToken = env.DOCICH_DISCORD_VOICE_CF_API_TOKEN;
  if (!validAccountId(accountId) || !validToken(apiToken)) fail('invalid_stt_config');

  return Object.freeze({
    accountId,
    apiToken,
    model: MODEL,
    endpoint:
      `https://api.cloudflare.com/client/v4/accounts/${accountId}/ai/run/${MODEL}`,
  });
}

export function pcmToWav(pcm, format = PCM) {
  if (
    !(pcm instanceof Int16Array) ||
    !pcm.length ||
    pcm.length > (PCM.sampleRate * MAX_AUDIO_MS) / 1000 ||
    !format ||
    format.sampleRate !== PCM.sampleRate ||
    format.channels !== PCM.channels ||
    format.frameMs !== PCM.frameMs ||
    format.samples !== PCM.samples
  ) {
    fail('invalid_audio');
  }

  const dataBytes = pcm.length * Int16Array.BYTES_PER_ELEMENT;
  const wav = new Uint8Array(44 + dataBytes);
  const view = new DataView(wav.buffer);
  const tag = (offset, value) => wav.set(new TextEncoder().encode(value), offset);

  tag(0, 'RIFF');
  view.setUint32(4, wav.length - 8, true);
  tag(8, 'WAVE');
  tag(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, PCM.sampleRate, true);
  view.setUint32(28, PCM.sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  tag(36, 'data');
  view.setUint32(40, dataBytes, true);

  for (let i = 0; i < pcm.length; i += 1) {
    view.setInt16(44 + i * 2, pcm[i], true);
  }

  return wav;
}

export async function cloudflareFetchRequest({
  url,
  headers,
  body,
  signal,
  maxBytes = MAX_RESPONSE_BYTES,
}) {
  const response = await fetch(url, {
    method: 'POST',
    headers,
    body,
    signal,
    redirect: 'error',
  });

  if (!response.body) {
    return { status: response.status, body: new Uint8Array() };
  }

  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      signal.throwIfAborted();
      const { done, value } = await reader.read();
      if (done) break;
      if (!(value instanceof Uint8Array)) fail('stt_failed');
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        fail('stt_failed');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock?.();
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { status: response.status, body: bytes };
}

export class CloudflareWhisperSTT {
  get kind() {
    return 'cloudflare-whisper';
  }

  #config;
  #request;

  constructor({ env = process.env, request = cloudflareFetchRequest } = {}) {
    this.#config = loadCloudflareSttConfig(env);
    if (typeof request !== 'function') fail('invalid_stt_config');
    this.#request = request;
  }

  async transcribe(pcm, { format = PCM, signal } = {}) {
    if (!(signal instanceof AbortSignal)) fail('invalid_stt_context');

    let wav;
    let responseBytes;
    try {
      signal.throwIfAborted();
      wav = pcmToWav(pcm, format);
      const body = JSON.stringify({
        audio: Array.from(wav),
        task: 'transcribe',
        language: 'ja',
        vad_filter: true,
        condition_on_previous_text: false,
      });

      const response = await this.#request(
        Object.freeze({
          url: this.#config.endpoint,
          method: 'POST',
          headers: Object.freeze({
            Authorization: `Bearer ${this.#config.apiToken}`,
            'Content-Type': 'application/json',
          }),
          body,
          signal,
          maxBytes: MAX_RESPONSE_BYTES,
        }),
      );

      signal.throwIfAborted();
      responseBytes = response?.body;
      if (
        !Number.isInteger(response?.status) ||
        response.status < 200 ||
        response.status >= 300 ||
        !(responseBytes instanceof Uint8Array) ||
        !responseBytes.length ||
        responseBytes.length > MAX_RESPONSE_BYTES
      ) {
        fail('stt_failed');
      }

      const parsed = JSON.parse(
        new TextDecoder('utf-8', { fatal: true }).decode(responseBytes),
      );
      const text = parsed?.result?.text ?? parsed?.text;
      if (typeof text !== 'string' || !text.trim() || text.length > 2000) {
        fail('stt_failed');
      }
      return text.trim();
    } catch (error) {
      let code = 'stt_failed';
      if (signal.aborted) code = 'stt_cancelled';
      else if (error instanceof CloudflareSttError) code = error.code;
      throw new CloudflareSttError(code);
    } finally {
      erase(wav);
      erase(responseBytes);
    }
  }
}
