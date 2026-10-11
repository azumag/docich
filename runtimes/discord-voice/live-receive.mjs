import { EndBehaviorType } from '@discordjs/voice';
import OpusScript from 'opusscript';

import { PCM } from './runtime.mjs';

const MAX_UTTERANCE_SAMPLES = PCM.sampleRate * 10;
const MIN_VOICED_SAMPLES = Math.round((PCM.sampleRate * 100) / 1000);
const SPEECH_THRESHOLD = 500;
const STT_TIMEOUT_MS = 10_000;
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

function voicedSamples(samples, threshold) {
  let energy = 0;
  for (const sample of samples) energy += sample * sample;
  return energy / samples.length >= threshold ** 2
    ? samples.length
    : 0;
}

export function attachLiveSttReceiver({
  connection,
  targetUserId,
  allowSpeaker = null,
  stt,
  emit = () => {},
  debugTranscript = false,
  onTranscript = null,
  onTargetSpeechStart = null,
  createDecoder = defaultDecoder,
  speechThreshold = SPEECH_THRESHOLD,
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
    (allowSpeaker === null
      ? (typeof targetUserId !== 'string' || !/^[1-9][0-9]{0,19}$/.test(targetUserId))
      : typeof allowSpeaker !== 'function') ||
    typeof stt?.transcribe !== 'function' ||
    typeof emit !== 'function' ||
    (onTranscript !== null && typeof onTranscript !== 'function') ||
    (onTargetSpeechStart !== null && typeof onTargetSpeechStart !== 'function') ||
    typeof createDecoder !== 'function' ||
    !Number.isFinite(speechThreshold) || speechThreshold < 50 || speechThreshold > 2000
  ) {
    throw new TypeError('invalid_live_receive_config');
  }

  let stopped = false;
  const captures = new Map();
  const admitted = (id) => {
    try { return !stopped && (allowSpeaker ? allowSpeaker(id) === true : id === targetUserId); }
    catch { return false; }
  };
  const maxCaptures = allowSpeaker ? 4 : 1;
  const maxPending = allowSpeaker ? 4 : MAX_PENDING_STT;
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
    const state = { controller, pcm: item.pcm, userId:item.userId };
    sttActive = state;

    void (async () => {
      try {
        if (!admitted(state.userId)) return;
        const sttStarted = performance.now();
        safeEmit({ event: 'stt_started', queueWaitMs: Math.max(0, Math.round(sttStarted - item.queuedAt)) });
        let transcript;
        const sttTimeout = setTimeout(() => controller.abort(), STT_TIMEOUT_MS);
        sttTimeout.unref?.();
        try {
          transcript = await stt.transcribe(state.pcm, {
            format: PCM,
            signal: controller.signal,
          });
          if (controller.signal.aborted || !admitted(state.userId)) return;
          safeEmit({ event: 'stt_completed', elapsedMs: Math.max(0, Math.round(performance.now() - sttStarted)) });
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
          try {
            await onTranscript(transcript, { signal: controller.signal, userId:state.userId });
          } catch {
            // The trusted transcript handler owns sanitized stage diagnostics.
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

  const enqueueStt = (pcm, userId) => {
    if (stopped) {
      erase(pcm);
      return;
    }
    const last = sttQueue.at(-1);
    const gap = PCM.sampleRate / 5; // 200ms separator; never merge different humans.
    if (allowSpeaker && last?.userId === userId &&
        last.pcm.length + gap + pcm.length <= MAX_UTTERANCE_SAMPLES) {
      const joined = new Int16Array(last.pcm.length + gap + pcm.length);
      joined.set(last.pcm); joined.set(pcm, last.pcm.length + gap);
      erase(last.pcm); erase(pcm); last.pcm = joined;
      return;
    }
    if (sttActive && sttQueue.length >= maxPending) {
      erase(pcm);
      safeEmit({ event: 'stt_queue_full' });
      return;
    }
    sttQueue.push({ pcm, userId, queuedAt: performance.now() });
    drainStt();
  };

  const capture = async (userId) => {
    if (!admitted(userId) || captures.has(userId) || captures.size >= maxCaptures) return;

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
    captures.set(userId, state);
    safeEmit({ event: 'utterance_started' });

    try {
      for await (const packet of stream) {
        if (controller.signal.aborted || !admitted(userId)) { controller.abort(); break; }

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
        state.voiced += voicedSamples(mono, speechThreshold);
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
      enqueueStt(pcm, userId);
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
      if (captures.get(userId) === state) captures.delete(userId);
    }
  };

  const onSpeakingStart = (userId) => {
    if (admitted(userId) && onTargetSpeechStart) {
      try {
        onTargetSpeechStart();
      } catch {
        // Barge-in notification must not prevent capture.
      }
    }
    void capture(userId);
  };
  receiver.speaking.on('start', onSpeakingStart);

  return Object.freeze({
    stop() {
      if (stopped) return;
      stopped = true;
      receiver.speaking.off('start', onSpeakingStart);

      for (const capture of captures.values()) {
        capture.controller.abort();
        try {
          capture.stream.destroy?.();
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
        active: Boolean(captures.size || sttActive),
        capturing: Boolean(captures.size),
        transcribing: Boolean(sttActive),
        queued: sttQueue.length,
      });
    },
  });
}
