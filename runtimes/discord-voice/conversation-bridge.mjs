export class ConversationBridgeError extends Error {
  constructor(code) {
    super(code);
    this.name = 'ConversationBridgeError';
    this.code = code;
  }
}

const fail = (code) => {
  throw new ConversationBridgeError(code);
};

const MAX_RESPONSE_BYTES = 16_384;
const REQUEST_TIMEOUT_MS = 10_000;

const validSnowflake = (value) =>
  typeof value === 'string' &&
  /^[1-9][0-9]{0,19}$/.test(value) &&
  BigInt(value) < 2n ** 64n;

const validTurnId = (value) =>
  typeof value === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(value);

const validToken = (value) =>
  typeof value === 'string' &&
  value.length >= 32 &&
  value.length <= 4096 &&
  !/\s/.test(value);

const erase = (value) => {
  if (!ArrayBuffer.isView(value)) return;
  try {
    Uint8Array.prototype.fill.call(value, 0);
  } catch {
    // Detached/foreign buffers remain owned by their creator.
  }
};

function workerOrigin(raw) {
  try {
    const url = new URL(raw);
    if (
      url.protocol !== 'https:' ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      !url.hostname ||
      (url.pathname !== '/' && url.pathname !== '')
    ) {
      fail('invalid_conversation_config');
    }
    return url.origin;
  } catch (error) {
    if (error instanceof ConversationBridgeError) throw error;
    fail('invalid_conversation_config');
  }
}

export function loadConversationBridgeConfig(env = process.env) {
  const origin = workerOrigin(env.DOCICH_DISCORD_VOICE_CORE_URL);
  const token = env.DOCICH_DISCORD_VOICE_CORE_TOKEN;
  if (!validToken(token)) fail('invalid_conversation_config');
  return Object.freeze({
    endpoint: origin + '/internal/voice/reply',
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

export class CloudflareConversationBridge {
  #config;
  #request;

  constructor({ env = process.env, request = conversationFetchRequest } = {}) {
    this.#config = loadConversationBridgeConfig(env);
    if (typeof request !== 'function') fail('invalid_conversation_config');
    this.#request = request;
  }

  async reply({ scope, turnId, transcript }, { signal } = {}) {
    if (
      !(signal instanceof AbortSignal) ||
      !scope ||
      !validSnowflake(scope.guildId) ||
      !validSnowflake(scope.channelId) ||
      !validSnowflake(scope.userId) ||
      !validTurnId(turnId) ||
      typeof transcript !== 'string' ||
      !transcript.trim() ||
      transcript.length > 2000
    ) {
      fail('invalid_conversation_request');
    }

    const controller = new AbortController();
    const onAbort = () => controller.abort();
    signal.addEventListener('abort', onAbort, { once: true });
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    timer.unref?.();

    let responseBytes;
    try {
      if (signal.aborted) controller.abort();
      controller.signal.throwIfAborted();

      const requestId = turnId;
      const body = JSON.stringify({
        version: 1,
        requestId,
        guildId: scope.guildId,
        channelId: scope.channelId,
        userId: scope.userId,
        transcript: transcript.trim(),
      });

      const response = await this.#request(Object.freeze({
        url: this.#config.endpoint,
        method: 'POST',
        headers: Object.freeze({
          Authorization: `Bearer ${this.#config.token}`,
          'Content-Type': 'application/json',
        }),
        body,
        signal: controller.signal,
        maxBytes: MAX_RESPONSE_BYTES,
      }));

      controller.signal.throwIfAborted();
      responseBytes = response?.body;
      if (!(responseBytes instanceof Uint8Array) || responseBytes.length > MAX_RESPONSE_BYTES) {
        fail('conversation_failed');
      }

      if (response.status === 409) fail('conversation_context_changed');
      if (response.status === 429) fail('conversation_busy');
      if (!Number.isInteger(response.status) || response.status < 200 || response.status >= 300) {
        fail('conversation_failed');
      }
      if (!responseBytes.length) fail('conversation_failed');

      const parsed = JSON.parse(
        new TextDecoder('utf-8', { fatal: true }).decode(responseBytes),
      );
      if (
        parsed?.version !== 1 ||
        parsed?.requestId !== requestId ||
        typeof parsed?.reply !== 'string' ||
        !parsed.reply.trim() ||
        parsed.reply.length > 900
      ) {
        fail('conversation_failed');
      }
      return parsed.reply.trim();
    } catch (error) {
      let code = 'conversation_failed';
      if (controller.signal.aborted || signal.aborted) code = 'conversation_cancelled';
      else if (error instanceof ConversationBridgeError) code = error.code;
      throw new ConversationBridgeError(code);
    } finally {
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      controller.abort();
      erase(responseBytes);
    }
  }
}
