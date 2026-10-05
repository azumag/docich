import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const KNOWN_EVENTS = new Set([
  'live_voice_check_ok',
  'voice_connected',
  'voice_disconnected',
  'voice_recovering',
  'voice_rejoin_attempt',
  'voice_rejoined',
  'voice_receive_enabled',
  'voice_conversation_enabled',
  'voice_tts_enabled',
  'utterance_started',
  'utterance_finished',
  'utterance_short',
  'utterance_too_long',
  'stt_started',
  'stt_completed',
  'stt_cancelled',
  'stt_failed',
  'stt_queue_full',
  'llm_started',
  'llm_completed',
  'llm_cancelled',
  'llm_failed',
  'tts_started',
  'tts_completed',
  'tts_cancelled',
  'tts_failed',
  'playback_started',
  'playback_completed',
  'playback_cancelled',
  'playback_interrupted',
  'playback_failed',
  'turn_interrupted',
  'memory_commit_started',
  'memory_commit_completed',
  'memory_commit_cancelled',
  'memory_commit_failed',
  'voice_stopped',
  'live_voice_error',
  'stt_debug_transcript',
  'llm_debug_reply',
]);

const FAILURE_EVENTS = new Set([
  'live_voice_error',
  'stt_failed',
  'llm_failed',
  'tts_failed',
  'playback_failed',
  'memory_commit_failed',
]);

function parseTimestamp(value) {
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

export function summarizeAcceptanceLines(lines, {
  minMinutes = 0,
  requireInterrupt = false,
  requireReconnect = false,
} = {}) {
  const counts = Object.create(null);
  let firstTimestamp = null;
  let lastTimestamp = null;
  let malformed = 0;
  let privateDebugEvents = 0;

  for (const rawLine of lines) {
    const line = String(rawLine ?? '').trimEnd();
    if (!line) continue;

    const split = line.indexOf('\t');
    if (split <= 0) {
      malformed += 1;
      continue;
    }

    const timestamp = parseTimestamp(line.slice(0, split));
    if (timestamp === null) {
      malformed += 1;
      continue;
    }

    let record;
    try {
      record = JSON.parse(line.slice(split + 1));
    } catch {
      malformed += 1;
      continue;
    }

    if (!record || typeof record.event !== 'string') {
      malformed += 1;
      continue;
    }

    if (firstTimestamp === null || timestamp < firstTimestamp) firstTimestamp = timestamp;
    if (lastTimestamp === null || timestamp > lastTimestamp) lastTimestamp = timestamp;

    if (KNOWN_EVENTS.has(record.event)) {
      counts[record.event] = (counts[record.event] ?? 0) + 1;
    }
    if (record.event === 'stt_debug_transcript' || record.event === 'llm_debug_reply') {
      privateDebugEvents += 1;
    }
  }

  const durationSeconds =
    firstTimestamp !== null && lastTimestamp !== null
      ? Math.max(0, (lastTimestamp - firstTimestamp) / 1000)
      : 0;

  const oneTurn =
    (counts.voice_connected ?? 0) >= 1 &&
    (counts.stt_completed ?? 0) >= 1 &&
    (counts.llm_completed ?? 0) >= 1 &&
    (counts.tts_completed ?? 0) >= 1 &&
    (counts.playback_completed ?? 0) >= 1 &&
    (counts.memory_commit_completed ?? 0) >= 1;

  const interruptOk =
    !requireInterrupt || (counts.playback_interrupted ?? 0) >= 1;
  const reconnectOk =
    !requireReconnect || (counts.voice_rejoined ?? 0) >= 1;
  const durationOk = durationSeconds >= Number(minMinutes) * 60;
  const failureCount = [...FAILURE_EVENTS].reduce(
    (total, event) => total + (counts[event] ?? 0),
    0,
  );
  const privacyOk = privateDebugEvents === 0;

  const passed =
    oneTurn &&
    interruptOk &&
    reconnectOk &&
    durationOk &&
    privacyOk &&
    failureCount === 0 &&
    malformed === 0;

  return Object.freeze({
    passed,
    oneTurn,
    interruptOk,
    reconnectOk,
    durationOk,
    privacyOk,
    failureCount,
    malformed,
    durationSeconds,
    counts: Object.freeze({ ...counts }),
  });
}

function parseArgs(args) {
  const options = {
    log: null,
    minMinutes: 0,
    requireInterrupt: false,
    requireReconnect: false,
  };
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === '--log') {
      options.log = args[++index] ?? null;
    } else if (arg === '--min-minutes') {
      options.minMinutes = Number(args[++index]);
    } else if (arg === '--require-interrupt') {
      options.requireInterrupt = true;
    } else if (arg === '--require-reconnect') {
      options.requireReconnect = true;
    } else {
      throw new Error('invalid_argument');
    }
  }
  if (
    typeof options.log !== 'string' ||
    !options.log ||
    !Number.isFinite(options.minMinutes) ||
    options.minMinutes < 0 ||
    options.minMinutes > 24 * 60
  ) {
    throw new Error('invalid_argument');
  }
  return options;
}

export async function main(args = process.argv.slice(2)) {
  try {
    const options = parseArgs(args);
    const input = await readFile(options.log, 'utf8');
    const result = summarizeAcceptanceLines(input.split(/\r?\n/), options);
    process.stdout.write(JSON.stringify({
      event: 'voice_acceptance_summary',
      passed: result.passed,
      oneTurn: result.oneTurn,
      interruptOk: result.interruptOk,
      reconnectOk: result.reconnectOk,
      durationOk: result.durationOk,
      privacyOk: result.privacyOk,
      failureCount: result.failureCount,
      malformed: result.malformed,
      durationSeconds: Math.round(result.durationSeconds),
      counts: result.counts,
    }) + '\n');
    return result.passed ? 0 : 1;
  } catch {
    process.stderr.write(JSON.stringify({
      event: 'voice_acceptance_summary_error',
      code: 'invalid_acceptance_log',
    }) + '\n');
    return 2;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main();
}
