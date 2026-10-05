import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CloudflareSttError,
  CloudflareWhisperSTT,
  loadCloudflareSttConfig,
  pcmToWav,
} from './cloudflare-stt.mjs';
import { PCM } from './runtime.mjs';

const baseEnv = () => ({
  DOCICH_DISCORD_VOICE_CF_ACCOUNT_ID: '0123456789abcdef0123456789abcdef',
  DOCICH_DISCORD_VOICE_CF_API_TOKEN: 'x'.repeat(48),
});

test('Cloudflare STT config is explicit and fixed to whisper-large-v3-turbo', () => {
  const config = loadCloudflareSttConfig(baseEnv());
  assert.equal(config.model, '@cf/openai/whisper-large-v3-turbo');
  assert.equal(
    config.endpoint,
    'https://api.cloudflare.com/client/v4/accounts/0123456789abcdef0123456789abcdef/ai/run/@cf/openai/whisper-large-v3-turbo',
  );
});

test('Cloudflare STT config rejects malformed account/token without leaking secret', () => {
  const secret = 'super-private-workers-ai-token-value';
  assert.throws(
    () =>
      loadCloudflareSttConfig({
        ...baseEnv(),
        DOCICH_DISCORD_VOICE_CF_ACCOUNT_ID: 'bad',
        DOCICH_DISCORD_VOICE_CF_API_TOKEN: secret,
      }),
    (error) =>
      error instanceof CloudflareSttError &&
      error.code === 'invalid_stt_config' &&
      !error.message.includes(secret),
  );
});

test('pcmToWav emits bounded 48kHz mono PCM16 RIFF', () => {
  const pcm = new Int16Array(PCM.samples).fill(1234);
  const wav = pcmToWav(pcm, PCM);
  const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);
  assert.equal(new TextDecoder().decode(wav.subarray(0, 4)), 'RIFF');
  assert.equal(new TextDecoder().decode(wav.subarray(8, 12)), 'WAVE');
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 48_000);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), pcm.byteLength);
  assert.equal(view.getInt16(44, true), 1234);
});

test('Cloudflare STT sends bounded Japanese transcription request and returns only text', async () => {
  const seen = [];
  const responseBytes = new TextEncoder().encode(
    JSON.stringify({ success: true, result: { text: 'こんにちは、テストです。' } }),
  );
  const stt = new CloudflareWhisperSTT({
    env: baseEnv(),
    request: async (request) => {
      seen.push(request);
      return { status: 200, body: new Uint8Array(responseBytes) };
    },
  });

  const controller = new AbortController();
  const pcm = new Int16Array(PCM.samples * 2).fill(2000);
  const text = await stt.transcribe(pcm, { format: PCM, signal: controller.signal });
  assert.equal(text, 'こんにちは、テストです。');
  assert.equal(seen.length, 1);
  assert.equal(seen[0].method, 'POST');
  assert.equal(seen[0].headers['Content-Type'], 'application/json');
  assert.match(seen[0].headers.Authorization, /^Bearer /);

  const body = JSON.parse(seen[0].body);
  assert.equal(body.task, 'transcribe');
  assert.equal(body.language, 'ja');
  assert.equal(body.vad_filter, true);
  assert.equal(body.condition_on_previous_text, false);
  assert.ok(Array.isArray(body.audio));
  assert.equal(body.audio.length, 44 + pcm.byteLength);
});

test('Cloudflare STT turns provider details into fixed error codes', async () => {
  const stt = new CloudflareWhisperSTT({
    env: baseEnv(),
    request: async () => ({
      status: 500,
      body: new TextEncoder().encode('private provider failure details'),
    }),
  });

  const controller = new AbortController();
  await assert.rejects(
    stt.transcribe(new Int16Array(PCM.samples).fill(1000), {
      format: PCM,
      signal: controller.signal,
    }),
    (error) =>
      error instanceof CloudflareSttError &&
      error.code === 'stt_failed' &&
      !error.message.includes('private provider failure details'),
  );
});

test('Cloudflare STT cancellation wins over late provider completion', async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const stt = new CloudflareWhisperSTT({
    env: baseEnv(),
    request: async () => {
      await gate;
      return {
        status: 200,
        body: new TextEncoder().encode(
          JSON.stringify({ success: true, result: { text: 'late transcript' } }),
        ),
      };
    },
  });

  const controller = new AbortController();
  const pending = stt.transcribe(new Int16Array(PCM.samples).fill(1000), {
    format: PCM,
    signal: controller.signal,
  });
  controller.abort();
  release();

  await assert.rejects(
    pending,
    (error) => error instanceof CloudflareSttError && error.code === 'stt_cancelled',
  );
});
