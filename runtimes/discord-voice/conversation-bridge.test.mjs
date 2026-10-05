import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CloudflareConversationBridge,
  ConversationBridgeError,
  loadConversationBridgeConfig,
} from './conversation-bridge.mjs';

const ENV = Object.freeze({
  DOCICH_DISCORD_VOICE_CORE_URL: 'https://voice-core.example',
  DOCICH_DISCORD_VOICE_CORE_TOKEN: 'k'.repeat(64),
});
const SCOPE = Object.freeze({
  guildId: '123456789012345678',
  channelId: '223456789012345678',
  userId: '323456789012345678',
});

test('conversation bridge config requires an HTTPS origin and private token', () => {
  const config = loadConversationBridgeConfig(ENV);
  assert.equal(
    config.endpoint,
    'https://voice-core.example/internal/voice/reply',
  );

  for (const url of [
    'http://voice-core.example',
    'https://voice-core.example/path',
    'https://user:pass@voice-core.example',
    'https://voice-core.example/?x=1',
  ]) {
    assert.throws(
      () => loadConversationBridgeConfig({
        ...ENV,
        DOCICH_DISCORD_VOICE_CORE_URL: url,
      }),
      (error) =>
        error instanceof ConversationBridgeError &&
        error.code === 'invalid_conversation_config',
    );
  }
});

test('conversation bridge sends scoped transcript and validates correlated reply', async () => {
  const seen = [];
  const bridge = new CloudflareConversationBridge({
    env: ENV,
    request: async (request) => {
      seen.push(request);
      return {
        status: 200,
        body: new TextEncoder().encode(JSON.stringify({
          version: 1,
          requestId: 'turn_1',
          reply: '丁寧な返答です。',
        })),
      };
    },
  });
  const controller = new AbortController();
  const reply = await bridge.reply({
    scope: SCOPE,
    turnId: 'turn_1',
    transcript: '  音声の質問です。  ',
  }, { signal: controller.signal });

  assert.equal(reply, '丁寧な返答です。');
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, 'https://voice-core.example/internal/voice/reply');
  assert.equal(seen[0].headers['Content-Type'], 'application/json');
  assert.equal(seen[0].headers.Authorization, 'Bearer ' + ENV.DOCICH_DISCORD_VOICE_CORE_TOKEN);

  const body = JSON.parse(seen[0].body);
  assert.deepEqual(body, {
    version: 1,
    requestId: 'turn_1',
    ...SCOPE,
    transcript: '音声の質問です。',
  });
});

test('conversation bridge rejects mismatched correlation and provider details', async () => {
  const secretDetail = 'private upstream diagnostic';
  const mismatch = new CloudflareConversationBridge({
    env: ENV,
    request: async () => ({
      status: 200,
      body: new TextEncoder().encode(JSON.stringify({
        version: 1,
        requestId: 'other_turn',
        reply: secretDetail,
      })),
    }),
  });
  const controller = new AbortController();

  await assert.rejects(
    mismatch.reply({
      scope: SCOPE,
      turnId: 'turn_1',
      transcript: 'テスト',
    }, { signal: controller.signal }),
    (error) =>
      error instanceof ConversationBridgeError &&
      error.code === 'conversation_failed' &&
      !error.message.includes(secretDetail),
  );
});

test('conversation bridge maps context change and busy without exposing response body', async () => {
  for (const [status, code] of [
    [409, 'conversation_context_changed'],
    [429, 'conversation_busy'],
  ]) {
    const bridge = new CloudflareConversationBridge({
      env: ENV,
      request: async () => ({
        status,
        body: new TextEncoder().encode('private response body'),
      }),
    });
    const controller = new AbortController();
    await assert.rejects(
      bridge.reply({
        scope: SCOPE,
        turnId: 'turn_1',
        transcript: 'テスト',
      }, { signal: controller.signal }),
      (error) =>
        error instanceof ConversationBridgeError &&
        error.code === code &&
        !error.message.includes('private response body'),
    );
  }
});

test('conversation bridge cancellation wins over a late response', async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const bridge = new CloudflareConversationBridge({
    env: ENV,
    request: async () => {
      await gate;
      return {
        status: 200,
        body: new TextEncoder().encode(JSON.stringify({
          version: 1,
          requestId: 'turn_1',
          reply: 'late',
        })),
      };
    },
  });
  const controller = new AbortController();
  const pending = bridge.reply({
    scope: SCOPE,
    turnId: 'turn_1',
    transcript: 'テスト',
  }, { signal: controller.signal });

  controller.abort();
  release();

  await assert.rejects(
    pending,
    (error) =>
      error instanceof ConversationBridgeError &&
      error.code === 'conversation_cancelled',
  );
});
