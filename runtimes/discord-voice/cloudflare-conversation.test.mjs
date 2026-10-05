import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CloudflareConversationClient,
  CloudflareConversationError,
  loadCloudflareConversationConfig,
} from './cloudflare-conversation.mjs';

const env = () => ({
  DOCICH_DISCORD_VOICE_CHAT_URL: 'https://discord-chat.example/voice/reply',
  DOCICH_DISCORD_VOICE_CHAT_TOKEN: 'x'.repeat(48),
});

const scope = () => ({
  guildId: '123456789012345678',
  channelId: '223456789012345678',
  userId: '323456789012345678',
});

test('conversation config requires HTTPS fixed voice reply path and secret token', () => {
  const config = loadCloudflareConversationConfig(env());
  assert.equal(config.url, 'https://discord-chat.example/voice/reply');
  assert.equal(config.token, 'x'.repeat(48));

  assert.throws(
    () => loadCloudflareConversationConfig({
      ...env(),
      DOCICH_DISCORD_VOICE_CHAT_URL: 'http://discord-chat.example/voice/reply',
    }),
    (error) =>
      error instanceof CloudflareConversationError &&
      error.code === 'invalid_conversation_config',
  );
  assert.throws(
    () => loadCloudflareConversationConfig({
      ...env(),
      DOCICH_DISCORD_VOICE_CHAT_URL: 'https://discord-chat.example/healthz',
    }),
    /invalid_conversation_config/,
  );
});

test('conversation client sends scoped transcript and returns only reply text', async () => {
  const seen = [];
  const client = new CloudflareConversationClient({
    env: env(),
    turnIdFactory: () => 'fixture-turn-1',
    request: async (request) => {
      seen.push(request);
      return {
        status: 200,
        body: new TextEncoder().encode(JSON.stringify({ reply: '丁寧な返答です。' })),
      };
    },
  });

  const controller = new AbortController();
  const reply = await client.reply(' こんにちは ', {
    ...scope(),
    signal: controller.signal,
  });

  assert.equal(reply, '丁寧な返答です。');
  assert.equal(seen.length, 1);
  assert.equal(seen[0].method, 'POST');
  assert.equal(seen[0].headers['Content-Type'], 'application/json');
  assert.match(seen[0].headers.Authorization, /^Bearer /);

  const body = JSON.parse(seen[0].body);
  assert.deepEqual(body, {
    ...scope(),
    turnId: 'fixture-turn-1',
    transcript: 'こんにちは',
  });
});

test('conversation client sanitizes provider errors and private response text', async () => {
  const privateText = 'EXAMPLE_PRIVATE_PROVIDER_BODY';
  const client = new CloudflareConversationClient({
    env: env(),
    request: async () => ({
      status: 503,
      body: new TextEncoder().encode(privateText),
    }),
  });
  const controller = new AbortController();

  await assert.rejects(
    client.reply('こんにちは', { ...scope(), signal: controller.signal }),
    (error) =>
      error instanceof CloudflareConversationError &&
      error.code === 'conversation_failed' &&
      !error.message.includes(privateText),
  );
});

test('conversation cancellation wins over late completion', async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const client = new CloudflareConversationClient({
    env: env(),
    request: async () => {
      await gate;
      return {
        status: 200,
        body: new TextEncoder().encode(JSON.stringify({ reply: 'late reply' })),
      };
    },
  });

  const controller = new AbortController();
  const pending = client.reply('こんにちは', {
    ...scope(),
    signal: controller.signal,
  });
  controller.abort();
  release();

  await assert.rejects(
    pending,
    (error) =>
      error instanceof CloudflareConversationError &&
      error.code === 'conversation_cancelled',
  );
});

test('invalid scope and oversized replies fail closed', async () => {
  const client = new CloudflareConversationClient({
    env: env(),
    request: async () => ({
      status: 200,
      body: new TextEncoder().encode(JSON.stringify({ reply: 'x'.repeat(902) })),
    }),
  });

  const controller = new AbortController();
  await assert.rejects(
    client.reply('こんにちは', {
      ...scope(),
      userId: 'bad',
      signal: controller.signal,
    }),
    /invalid_conversation_context/,
  );
  await assert.rejects(
    client.reply('こんにちは', { ...scope(), signal: controller.signal }),
    /conversation_failed/,
  );
});
