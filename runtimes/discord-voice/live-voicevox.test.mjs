import assert from 'node:assert/strict';
import test from 'node:test';

import {
  LiveVoicevoxError,
  createLiveVoicevoxTTS,
} from './live-voicevox.mjs';

const remoteEnv = () => ({
  VOICEVOX_URLS: 'https://voicevox.example:50021',
});

test('live VOICEVOX keeps loopback denied by default and allows it only explicitly', () => {
  assert.throws(
    () => createLiveVoicevoxTTS({
      VOICEVOX_URLS: 'http://127.0.0.1:50021',
    }, {
      request: async () => ({ status: 500, body: new Uint8Array() }),
    }),
    (error) =>
      error instanceof LiveVoicevoxError &&
      error.code === 'invalid_live_voicevox_config',
  );

  const adapter = createLiveVoicevoxTTS({
    VOICEVOX_URLS: 'http://127.0.0.1:50021',
  }, {
    allowLoopback: true,
    request: async () => ({ status: 500, body: new Uint8Array() }),
  });
  assert.equal(adapter.kind, 'voicevox-injected');
});

test('live VOICEVOX accepts an explicit remote endpoint without loopback opt-in', () => {
  const adapter = createLiveVoicevoxTTS(remoteEnv(), {
    request: async () => ({ status: 500, body: new Uint8Array() }),
  });
  assert.equal(adapter.kind, 'voicevox-injected');
});
