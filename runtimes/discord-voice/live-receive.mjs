import { EndBehaviorType } from '@discordjs/voice';
import OpusScript from 'opusscript';

import { PCM } from './runtime.mjs';

const MAX_UTTERANCE_SAMPLES = PCM.sampleRate * 10;
const MIN_VOICED_SAMPLES = Math.round((PCM.sampleRate * 100) / 1000);
const SPEECH_THRESHOLD = 500;
const STT_TIMEOUT_MS = 10_000;
const CONVERSATION_TIMEOUT_MS = 10_000;
const MAX_PENDING_STT = 1;

const erase = (value) => {
  if (!ArrayBuffer.isView(value)) return;
  try {
    Uint8Array.prototype.fill.call(value, 0);
  } catch {
    // Detached/foreign buffers remain owned by their creator.
  }
};

function defaultDecoder() {
  const decoder = new OpusScript(
    PCM.sampleRate,
    2,
    OpusScript.Application.AUDIO,
  );
  return {
    decode(packet) {
      return decoder.decode(packet);
    },
    close() {
      try {
        decoder.delete?.();
      } catch {
        // Decoder cleanup must not change shutdown behavior.
      }
    },
  };
}

export function stereoPcm16ToMono(decoded) {
  if (!(decoded instanceof Uint8Array)) {
    throw new TypeError('invalid_decoded_audio');
  }
  if (!decoded.byteLength || decoded.byteLength % 4 !== 0) {
    throw new TypeError('invalid_decoded_audio');
  }

  const view = new DataView(
    decoded.buffer,
    decoded.byteOffset,
    decoded.byteLength,
  );
  const frames = decoded.byteLength / 4;
  const mono = new Int16Array(frames);
  for (let frame = 0; frame < frames; frame += 1) {
    const left = view.getInt16(frame * 4, true);
    const right = view.getInt16(frame * 4 + 2, true);
    mono[frame] = Math.round((left + right) / 2);
  }
  return mono;
}

function voicedSamples(samples) {
  let energy = 0;
  for (const sample of samples) energy += sample * sample;
  return energy / samples.length >= SPEECH_THRESHOLD ** 2
    ? samples.length
    : 0;
}

export function attachLiveSttReceiver({
  connection,
  targetUserId,
  stt,
  emit = () => {},
  debugTranscript = false,
  onTranscript = null,
  createDecoder = defaultDecoder,
  subscribeOptions = Object.freeze({
    end: Object.freeze({
      behavior: EndBehaviorType.AfterSilence,
      duration: 700,
    }),
  }),
} = {}) {
  const receiver = connection?.receiver;
  if (
    !receiver?.speaking ||
    typeof receiver.speaking.on !== 'function' ||
    typeof receiver.speaking.off !== 'function' ||
    typeof receiver.subscribe !== 'function' ||
    typeof targetUserId !== 'string' ||
    !/^[1-9][0-9]{0,19}$/.test(targetUserId) ||
    typeof stt?.transcribe !== 'function' ||
    typeof emit !== 'function' ||
    (onTranscript !== null && typeof onTranscript !== 'function') ||
    typeof createDecoder !== 'function'
  ) {
    throw new TypeError('invalid_live_receive_config');
  }

  let stopped = false;
  let captureActive = null;
  let sttActive = null;
  const sttQueue = [];

  const safeEmit = (record) => {
    try {
      emit(Object.freeze(record));
    } catch {
      // Observability must not alter the audio lifecycle.
    }
  };

  const drainStt = () => {
    if (stopped || sttActive || sttQueue.length === 0) return;

    const item = sttQueue.shift();
    const controller = new AbortController();
    const state = { controller, pcm: item.pcm };
    sttActive = state;

    void (async () => {
      try {
        safeEmit({ event: 'stt_started' });
        let transcript;
        const sttTimeout = setTimeout(() => controller.abort(), STT_TIMEOUT_MS);
        sttTimeout.unref?.();
        try {
          transcript = await stt.transcribe(state.pcm, {
            format: PCM,
            signal: controller.signal,
          });
          if (controller.signal.aborted || stopped) return;
          safeEmit({ event: 'stt_completed' });
          if (debugTranscript) {
            safeEmit({ event: 'stt_debug_transcript', transcript });
          }
        } catch {
          safeEmit({
            event: controller.signal.aborted ? 'stt_cancelled' : 'stt_failed',
          });
          return;
        } finally {
          clearTimeout(sttTimeout);
        }

        if (onTranscript && !controller.signal.aborted && !stopped) {
          const conversationTimeout = setTimeout(
            () => controller.abort(),
            CONVERSATION_TIMEOUT_MS,
          );
          conversationTimeout.unref?.();
          try {
            await onTranscript(transcript, { signal: controller.signal });
          } catch {
            // The trusted transcript handler owns sanitized stage diagnostics.
          } finally {
            clearTimeout(conversationTimeout);
          }
        }
      } finally {
        erase(state.pcm);
        controller.abort();
        if (sttActive === state) sttActive = null;
        drainStt();
      }
    })();
  };

  const enqueueStt = (pcm) => {
    if (stopped) {
      erase(pcm);
      return;
    }
    if (sttActive && sttQueue.length >= MAX_PENDING_STT) {
      erase(pcm);
      safeEmit({ event: 'stt_queue_full' });
      return;
    }
    sttQueue.push({ pcm });
    drainStt();
  };

  const capture = async (userId) => {
    if (stopped || captureActive || userId !== targetUserId) return;

    const controller = new AbortController();
    let decoder;
    let stream;
    try {
      decoder = createDecoder();
      stream = receiver.subscribe(userId, subscribeOptions);
    } catch {
      try {
        decoder?.close?.();
      } catch {
        // Fixed cleanup path.
      }
      safeEmit({ event: 'voice_receive_failed' });
      return;
    }

    const state = {
      controller,
      decoder,
      stream,
      chunks: [],
      samples: 0,
      voiced: 0,
    };
    captureActive = state;
    safeEmit({ event: 'utterance_started' });

    try {
      for await (const packet of stream) {
        if (controller.signal.aborted || stopped) break;

        let decoded;
        let mono;
        try {
          decoded = decoder.decode(packet);
          mono = stereoPcm16ToMono(decoded);
        } finally {
          erase(decoded);
        }

        if (state.samples + mono.length > MAX_UTTERANCE_SAMPLES) {
          erase(mono);
          controller.abort();
          safeEmit({ event: 'utterance_too_long' });
          return;
        }

        state.samples += mono.length;
        state.voiced += voicedSamples(mono);
        state.chunks.push(mono);
      }

      if (controller.signal.aborted || stopped) return;
      if (state.samples === 0 || state.voiced < MIN_VOICED_SAMPLES) {
        safeEmit({ event: 'utterance_short' });
        return;
      }

      const pcm = new Int16Array(state.samples);
      let offset = 0;
      for (const chunk of state.chunks) {
        pcm.set(chunk, offset);
        offset += chunk.length;
        erase(chunk);
      }
      state.chunks = [];

      safeEmit({ event: 'utterance_finished' });
      enqueueStt(pcm);
    } catch {
      if (!controller.signal.aborted && !stopped) {
        safeEmit({ event: 'voice_receive_failed' });
      }
    } finally {
      controller.abort();
      try {
        stream.destroy?.();
      } catch {
        // Fixed cleanup path.
      }
      try {
        decoder.close?.();
      } catch {
        // Fixed cleanup path.
      }
      for (const chunk of state.chunks) erase(chunk);
      state.chunks = [];
      if (captureActive === state) captureActive = null;
    }
  };

  const onSpeakingStart = (userId) => {
    void capture(userId);
  };
  receiver.speaking.on('start', onSpeakingStart);

  return Object.freeze({
    stop() {
      if (stopped) return;
      stopped = true;
      receiver.speaking.off('start', onSpeakingStart);

      if (captureActive) {
        captureActive.controller.abort();
        try {
          captureActive.stream.destroy?.();
        } catch {
          // Fixed cleanup path.
        }
      }

      sttActive?.controller.abort();
      for (const item of sttQueue.splice(0)) erase(item.pcm);
    },
    status() {
      return Object.freeze({
        enabled: !stopped,
        active: Boolean(captureActive || sttActive),
        capturing: Boolean(captureActive),
        transcribing: Boolean(sttActive),
        queued: sttQueue.length,
      });
    },
  });
}
