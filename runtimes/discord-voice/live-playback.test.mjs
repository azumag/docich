import assert from 'node:assert/strict';
import test from 'node:test';

import {
  LivePlaybackError,
  createLivePlayback,
  monoPcm16ToStereoBuffer,
} from './live-playback.mjs';

test('mono PCM16 is duplicated into bounded stereo raw PCM', () => {
  const pcm = new Int16Array(960);
  pcm[0] = -1234;
  pcm[1] = 2345;
  const stereo = monoPcm16ToStereoBuffer(pcm);

  assert.equal(stereo.length, pcm.length * 4);
  assert.equal(stereo.readInt16LE(0), -1234);
  assert.equal(stereo.readInt16LE(2), -1234);
  assert.equal(stereo.readInt16LE(4), 2345);
  assert.equal(stereo.readInt16LE(6), 2345);
});

test('playback completes, scrubs transport buffer, and closes subscription', async () => {
  let played = null;
  let unsubscribed = 0;
  const player = {
    play(resource) {
      played = resource;
    },
    stop() {},
  };
  const playback = createLivePlayback({
    subscribe(receivedPlayer) {
      assert.equal(receivedPlayer, player);
      return {
        unsubscribe() {
          unsubscribed += 1;
        },
      };
    },
  }, {
    createPlayer: () => player,
    createResource: (buffer) => ({ buffer }),
    waitState: async () => {},
  });

  const pcm = new Int16Array(960).fill(1200);
  await playback.play(pcm, { signal: new AbortController().signal });

  assert.ok(played);
  assert.ok(played.buffer.every((value) => value === 0));
  assert.deepEqual(playback.status(), { closed: false, busy: false });

  playback.close();
  playback.close();
  assert.equal(unsubscribed, 1);
  assert.deepEqual(playback.status(), { closed: true, busy: false });
});

test('aborting playback stops the player and rejects as cancelled', async () => {
  let stopCalls = 0;
  let rejectIdle;
  const player = {
    play() {},
    stop() {
      stopCalls += 1;
      rejectIdle?.(new Error('fixture stopped'));
      return true;
    },
  };
  let waits = 0;
  const playback = createLivePlayback({
    subscribe() {
      return { unsubscribe() {} };
    },
  }, {
    createPlayer: () => player,
    createResource: () => ({}),
    waitState: async () => {
      waits += 1;
      if (waits === 1) return;
      return new Promise((_, reject) => {
        rejectIdle = reject;
      });
    },
  });

  const controller = new AbortController();
  const pending = playback.play(new Int16Array(960).fill(1000), {
    signal: controller.signal,
  });
  await new Promise((resolve) => setImmediate(resolve));
  controller.abort();

  await assert.rejects(
    pending,
    (error) =>
      error instanceof LivePlaybackError &&
      error.code === 'playback_cancelled',
  );
  assert.ok(stopCalls >= 1);
});
