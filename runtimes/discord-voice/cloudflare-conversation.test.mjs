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
  assert.equal(config.replyUrl, 'https://discord-chat.example/voice/reply');
  assert.equal(config.commitUrl, 'https://discord-chat.example/voice/commit');
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

test('generate exposes turn id and commit posts only after caller acknowledgement', async () => {
  const seen = [];
  const client = new CloudflareConversationClient({
    env: env(),
    turnIdFactory: () => 'fixture-turn-commit',
    request: async (request) => {
      seen.push(request);
      if (request.url.endsWith('/voice/reply')) {
        return {
          status: 200,
          body: new TextEncoder().encode(JSON.stringify({ reply: '音声で返す本文です。' })),
        };
      }
      return {
        status: 200,
        body: new TextEncoder().encode(JSON.stringify({ status: 'committed' })),
      };
    },
  });

  const controller = new AbortController();
  const generated = await client.generate(' こんにちは ', {
    ...scope(),
    signal: controller.signal,
  });
  assert.deepEqual(generated, {
    turnId: 'fixture-turn-commit',
    reply: '音声で返す本文です。',
  });
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, 'https://discord-chat.example/voice/reply');

  const status = await client.commit({
    turnId: generated.turnId,
    transcript: ' こんにちは ',
    reply: generated.reply,
  }, {
    ...scope(),
    signal: controller.signal,
  });

  assert.equal(status, 'committed');
  assert.equal(seen.length, 2);
  assert.equal(seen[1].url, 'https://discord-chat.example/voice/commit');
  assert.deepEqual(JSON.parse(seen[1].body), {
    ...scope(),
    turnId: 'fixture-turn-commit',
    transcript: 'こんにちは',
    reply: '音声で返す本文です。',
  });
});

test('commit treats duplicate acknowledgement as success and sanitizes commit failures', async () => {
  let mode = 'duplicate';
  const client = new CloudflareConversationClient({
    env: env(),
    request: async (request) => {
      if (request.url.endsWith('/voice/commit')) {
        if (mode === 'duplicate') {
          return {
            status: 200,
            body: new TextEncoder().encode(JSON.stringify({ status: 'already_committed' })),
          };
        }
        return {
          status: 409,
          body: new TextEncoder().encode('EXAMPLE_PRIVATE_COMMIT_CONFLICT'),
        };
      }
      throw new Error('unexpected request');
    },
  });
  const controller = new AbortController();
  const turn = {
    turnId: 'fixture-duplicate',
    transcript: 'こんにちは',
    reply: '返答です',
  };

  assert.equal(
    await client.commit(turn, { ...scope(), signal: controller.signal }),
    'already_committed',
  );

  mode = 'conflict';
  await assert.rejects(
    client.commit(turn, { ...scope(), signal: controller.signal }),
    (error) =>
      error instanceof CloudflareConversationError &&
      error.code === 'conversation_commit_failed' &&
      !error.message.includes('EXAMPLE_PRIVATE_COMMIT_CONFLICT'),
  );
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
