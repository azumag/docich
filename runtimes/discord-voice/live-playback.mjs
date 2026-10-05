import { Readable } from 'node:stream';

import {
  AudioPlayerStatus,
  StreamType,
  createAudioPlayer,
  createAudioResource,
  entersState,
} from '@discordjs/voice';

import { PCM } from './runtime.mjs';

export class LivePlaybackError extends Error {
  constructor(code) {
    super(code);
    this.name = 'LivePlaybackError';
    this.code = code;
  }
}

const fail = (code) => {
  throw new LivePlaybackError(code);
};

function erase(value) {
  if (!ArrayBuffer.isView(value)) return;
  try {
    Uint8Array.prototype.fill.call(value, 0);
  } catch {
    // Detached/foreign buffers remain owned by their creator.
  }
}

export function monoPcm16ToStereoBuffer(pcm) {
  if (
    !(pcm instanceof Int16Array) ||
    !pcm.length ||
    pcm.length > PCM.sampleRate * 30 ||
    pcm.length % PCM.samples !== 0
  ) {
    fail('invalid_playback_pcm');
  }

  const output = Buffer.alloc(
    pcm.length * 2 * Int16Array.BYTES_PER_ELEMENT,
  );
  for (let index = 0; index < pcm.length; index += 1) {
    const offset = index * 4;
    output.writeInt16LE(pcm[index], offset);
    output.writeInt16LE(pcm[index], offset + 2);
  }
  return output;
}

export function createLivePlayback(
  connection,
  {
    createPlayer = createAudioPlayer,
    createResource = (buffer) =>
      createAudioResource(Readable.from([buffer]), { inputType: StreamType.Raw }),
    waitState = entersState,
  } = {},
) {
  if (
    !connection ||
    typeof connection.subscribe !== 'function' ||
    typeof createPlayer !== 'function' ||
    typeof createResource !== 'function' ||
    typeof waitState !== 'function'
  ) {
    fail('invalid_playback_config');
  }

  const player = createPlayer();
  const subscription = connection.subscribe(player);
  if (!subscription) fail('voice_subscription_failed');

  let closed = false;
  let busy = false;

  return Object.freeze({
    async play(pcm, { signal } = {}) {
      if (closed) fail('playback_closed');
      if (busy) fail('playback_busy');
      if (!(signal instanceof AbortSignal)) fail('invalid_playback_context');

      let stereo;
      const onAbort = () => {
        try {
          player.stop(true);
        } catch {
          // Fixed cancellation path.
        }
      };
      busy = true;
      try {
        signal.throwIfAborted();
        stereo = monoPcm16ToStereoBuffer(pcm);
        const resource = createResource(stereo);
        signal.addEventListener('abort', onAbort, { once: true });

        player.play(resource);
        await waitState(player, AudioPlayerStatus.Playing, 5_000);
        signal.throwIfAborted();
        await waitState(player, AudioPlayerStatus.Idle, 35_000);
        signal.throwIfAborted();
      } catch {
        try {
          player.stop(true);
        } catch {
          // Fixed failure path.
        }
        if (signal.aborted) throw new LivePlaybackError('playback_cancelled');
        throw new LivePlaybackError('playback_failed');
      } finally {
        signal.removeEventListener('abort', onAbort);
        erase(stereo);
        busy = false;
      }
    },

    stop() {
      try {
        player.stop(true);
      } catch {
        // Idempotent best-effort stop.
      }
    },

    close() {
      if (closed) return;
      closed = true;
      try {
        player.stop(true);
      } catch {
        // Idempotent shutdown.
      }
      try {
        subscription.unsubscribe?.();
      } catch {
        // Subscription cleanup must not change shutdown.
      }
    },

    status() {
      return Object.freeze({ closed, busy });
    },
  });
}
