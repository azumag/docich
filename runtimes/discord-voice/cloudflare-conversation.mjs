import { randomUUID } from 'node:crypto';

export class CloudflareConversationError extends Error {
  constructor(code) {
    super(code);
    this.name = 'CloudflareConversationError';
    this.code = code;
  }
}

const fail = (code) => {
  throw new CloudflareConversationError(code);
};

const MAX_RESPONSE_BYTES = 65_536;
const MAX_REPLY_CHARS = 901;
const TURN_ID = /^[A-Za-z0-9._:-]{1,128}$/;

const validToken = (value) =>
  typeof value === 'string' &&
  value.length >= 32 &&
  value.length <= 4096 &&
  !/\s/.test(value);

const validSnowflake = (value) =>
  typeof value === 'string' &&
  /^[1-9][0-9]{0,19}$/.test(value) &&
  BigInt(value) < 2n ** 64n;

function erase(value) {
  if (!ArrayBuffer.isView(value)) return;
  try {
    Uint8Array.prototype.fill.call(value, 0);
  } catch {
    // Detached/foreign buffers remain owned by their creator.
  }
}

export function loadCloudflareConversationConfig(env = process.env) {
  const rawUrl = env.DOCICH_DISCORD_VOICE_CHAT_URL;
  const token = env.DOCICH_DISCORD_VOICE_CHAT_TOKEN;
  if (typeof rawUrl !== 'string' || !validToken(token)) fail('invalid_conversation_config');

  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    fail('invalid_conversation_config');
  }
  if (
    url.protocol !== 'https:' ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== '/voice/reply'
  ) {
    fail('invalid_conversation_config');
  }

  return Object.freeze({
    url: url.toString(),
    token,
  });
}

export async function conversationFetchRequest({
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
      if (!(value instanceof Uint8Array)) fail('conversation_failed');
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        fail('conversation_failed');
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
  } finally {
    for (const chunk of chunks) erase(chunk);
    reader.releaseLock?.();
  }
}

export class CloudflareConversationClient {
  get kind() {
    return 'cloudflare-discord-conversation';
  }

  #config;
  #request;
  #turnIdFactory;

  constructor({
    env = process.env,
    request = conversationFetchRequest,
    turnIdFactory = randomUUID,
  } = {}) {
    this.#config = loadCloudflareConversationConfig(env);
    if (typeof request !== 'function' || typeof turnIdFactory !== 'function') {
      fail('invalid_conversation_config');
    }
    this.#request = request;
    this.#turnIdFactory = turnIdFactory;
  }

  async reply(transcript, { guildId, channelId, userId, signal } = {}) {
    if (
      typeof transcript !== 'string' ||
      !transcript.trim() ||
      transcript.length > 2000 ||
      !validSnowflake(guildId) ||
      !validSnowflake(channelId) ||
      !validSnowflake(userId) ||
      !(signal instanceof AbortSignal)
    ) {
      fail('invalid_conversation_context');
    }

    let responseBytes;
    try {
      signal.throwIfAborted();
      const turnId = String(this.#turnIdFactory());
      if (!TURN_ID.test(turnId)) fail('conversation_failed');

      const response = await this.#request(Object.freeze({
        url: this.#config.url,
        method: 'POST',
        headers: Object.freeze({
          Authorization: `Bearer ${this.#config.token}`,
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({
          guildId,
          channelId,
          userId,
          turnId,
          transcript: transcript.trim(),
        }),
        signal,
        maxBytes: MAX_RESPONSE_BYTES,
      }));

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
        fail('conversation_failed');
      }

      const parsed = JSON.parse(
        new TextDecoder('utf-8', { fatal: true }).decode(responseBytes),
      );
      const reply = parsed?.reply;
      if (
        typeof reply !== 'string' ||
        !reply.trim() ||
        reply.length > MAX_REPLY_CHARS
      ) {
        fail('conversation_failed');
      }
      return reply.trim();
    } catch (error) {
      const code = signal.aborted
        ? 'conversation_cancelled'
        : error instanceof CloudflareConversationError
          ? error.code
          : 'conversation_failed';
      throw new CloudflareConversationError(code);
    } finally {
      erase(responseBytes);
    }
  }
}
