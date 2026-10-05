import { InjectedVoicevoxTTS } from './voicevox.mjs';

export class LiveVoicevoxError extends Error {
  constructor(code) {
    super(code);
    this.name = 'LiveVoicevoxError';
    this.code = code;
  }
}

const fail = (code) => {
  throw new LiveVoicevoxError(code);
};

function erase(value) {
  if (!ArrayBuffer.isView(value)) return;
  try {
    Uint8Array.prototype.fill.call(value, 0);
  } catch {
    // Detached/foreign buffers remain owned by their creator.
  }
}

export async function voicevoxFetchRequest({
  url,
  method,
  headers,
  body,
  signal,
  maxBytes,
}) {
  if (
    typeof url !== 'string' ||
    method !== 'POST' ||
    !headers ||
    typeof body !== 'string' ||
    !(signal instanceof AbortSignal) ||
    !Number.isInteger(maxBytes) ||
    maxBytes < 1 ||
    maxBytes > 5_760_256
  ) {
    fail('invalid_voicevox_request');
  }

  const response = await fetch(url, {
    method,
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
      if (!(value instanceof Uint8Array)) fail('voicevox_transport_failed');
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        fail('voicevox_transport_failed');
      }
      chunks.push(value);
    }

    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return { status: response.status, body: bytes };
  } catch (error) {
    if (signal.aborted) throw new LiveVoicevoxError('voicevox_cancelled');
    if (error instanceof LiveVoicevoxError) throw error;
    throw new LiveVoicevoxError('voicevox_transport_failed');
  } finally {
    for (const chunk of chunks) erase(chunk);
    reader.releaseLock?.();
  }
}

export function createLiveVoicevoxTTS(
  env = process.env,
  { allowLoopback = false, request = voicevoxFetchRequest } = {},
) {
  try {
    return new InjectedVoicevoxTTS({
      env,
      selectedURL: env.VOICEVOX_ACTIVE_URL || undefined,
      request,
      allowLocal: allowLoopback,
    });
  } catch {
    fail('invalid_live_voicevox_config');
  }
}
