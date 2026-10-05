import assert from 'node:assert/strict';
import test from 'node:test';

import {
  LiveVoiceError,
  loadLiveVoiceConfig,
  makeStereoTestTone,
} from '../live-support.mjs';

const baseEnv = () => ({
  DOCICH_DISCORD_VOICE_ENABLED: '1',
  DOCICH_DISCORD_TOKEN: 'x'.repeat(64),
  DOCICH_DISCORD_VOICE_GUILD_ID: '123456789012345678',
  DOCICH_DISCORD_VOICE_CHANNEL_ID: '223456789012345678',
});

test('live config is explicit and test tone defaults off', () => {
  const config = loadLiveVoiceConfig(baseEnv());
  assert.equal(config.guildId, '123456789012345678');
  assert.equal(config.channelId, '223456789012345678');
  assert.equal(config.playTestTone, false);
});

test('live config accepts only explicit boolean test-tone flag', () => {
  assert.equal(loadLiveVoiceConfig({ ...baseEnv(), DOCICH_DISCORD_VOICE_TEST_TONE: '1' }).playTestTone, true);
  assert.throws(
    () => loadLiveVoiceConfig({ ...baseEnv(), DOCICH_DISCORD_VOICE_TEST_TONE: 'yes' }),
    (error) => error instanceof LiveVoiceError && error.code === 'invalid_config',
  );
});

test('live config rejects disabled mode and malformed identifiers without leaking token', () => {
  const secret = 'super-secret-token-value-that-must-not-appear';
  assert.throws(
    () => loadLiveVoiceConfig({ ...baseEnv(), DOCICH_DISCORD_VOICE_ENABLED: '0', DOCICH_DISCORD_TOKEN: secret }),
    (error) => error instanceof LiveVoiceError && error.message === 'live_disabled' && !error.message.includes(secret),
  );
  assert.throws(
    () => loadLiveVoiceConfig({ ...baseEnv(), DOCICH_DISCORD_VOICE_GUILD_ID: 'guild', DOCICH_DISCORD_TOKEN: secret }),
    (error) => error instanceof LiveVoiceError && error.message === 'invalid_config' && !error.message.includes(secret),
  );
});

test('test tone is bounded 48kHz signed PCM16 stereo with equal channels', () => {
  const pcm = makeStereoTestTone({ durationMs: 200, frequencyHz: 440 });
  assert.equal(pcm.length, 48_000 * 0.2 * 4);

  let nonZero = false;
  for (let offset = 0; offset < pcm.length; offset += 4) {
    const left = pcm.readInt16LE(offset);
    const right = pcm.readInt16LE(offset + 2);
    assert.equal(left, right);
    if (left !== 0) nonZero = true;
  }
  assert.equal(nonZero, true);
});

test('test tone rejects unsafe or accidental large settings', () => {
  assert.throws(
    () => makeStereoTestTone({ durationMs: 10_000 }),
    (error) => error instanceof LiveVoiceError && error.code === 'invalid_tone_config',
  );
});


test('live receive is fail-closed and requires an explicit target user', () => {
  const disabled = loadLiveVoiceConfig(baseEnv());
  assert.equal(disabled.receiveEnabled, false);
  assert.equal(disabled.receiveUserId, null);
  assert.equal(disabled.transcriptDebug, false);

  assert.throws(
    () => loadLiveVoiceConfig({
      ...baseEnv(),
      DOCICH_DISCORD_VOICE_RECEIVE_ENABLED: '1',
    }),
    (error) => error instanceof LiveVoiceError && error.code === 'invalid_config',
  );

  const enabled = loadLiveVoiceConfig({
    ...baseEnv(),
    DOCICH_DISCORD_VOICE_RECEIVE_ENABLED: '1',
    DOCICH_DISCORD_VOICE_RECEIVE_USER_ID: '323456789012345678',
  });
  assert.equal(enabled.receiveEnabled, true);
  assert.equal(enabled.receiveUserId, '323456789012345678');
  assert.equal(enabled.transcriptDebug, false);
});

test('live conversation is opt-in and requires receive mode', () => {
  const disabled = loadLiveVoiceConfig(baseEnv());
  assert.equal(disabled.conversationEnabled, false);
  assert.equal(disabled.replyDebug, false);

  assert.throws(
    () => loadLiveVoiceConfig({
      ...baseEnv(),
      DOCICH_DISCORD_VOICE_CONVERSATION_ENABLED: '1',
    }),
    (error) => error instanceof LiveVoiceError && error.code === 'invalid_config',
  );

  const enabled = loadLiveVoiceConfig({
    ...baseEnv(),
    DOCICH_DISCORD_VOICE_RECEIVE_ENABLED: '1',
    DOCICH_DISCORD_VOICE_RECEIVE_USER_ID: '323456789012345678',
    DOCICH_DISCORD_VOICE_CONVERSATION_ENABLED: '1',
    DOCICH_DISCORD_VOICE_REPLY_DEBUG: '1',
  });
  assert.equal(enabled.conversationEnabled, true);
  assert.equal(enabled.replyDebug, true);
});

test('transcript debug requires an explicit boolean flag', () => {
  const enabled = loadLiveVoiceConfig({
    ...baseEnv(),
    DOCICH_DISCORD_VOICE_RECEIVE_ENABLED: '1',
    DOCICH_DISCORD_VOICE_RECEIVE_USER_ID: '323456789012345678',
    DOCICH_DISCORD_VOICE_TRANSCRIPT_DEBUG: '1',
  });
  assert.equal(enabled.transcriptDebug, true);

  assert.throws(
    () => loadLiveVoiceConfig({
      ...baseEnv(),
      DOCICH_DISCORD_VOICE_TRANSCRIPT_DEBUG: 'yes',
    }),
    (error) => error instanceof LiveVoiceError && error.code === 'invalid_config',
  );
});
