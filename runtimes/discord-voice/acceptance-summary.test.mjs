import assert from 'node:assert/strict';
import test from 'node:test';

import { summarizeAcceptanceLines } from './acceptance-summary.mjs';

function line(second, event, extra = {}) {
  return `2026-10-06T01:00:${String(second).padStart(2, '0')}+09:00\t${JSON.stringify({ event, ...extra })}`;
}

test('successful one-turn acceptance passes without optional requirements', () => {
  const result = summarizeAcceptanceLines([
    line(0, 'voice_connected'),
    line(1, 'stt_completed'),
    line(2, 'llm_completed'),
    line(3, 'tts_completed'),
    line(4, 'playback_completed'),
    line(5, 'memory_commit_completed'),
  ]);

  assert.equal(result.passed, true);
  assert.equal(result.oneTurn, true);
  assert.equal(result.failureCount, 0);
  assert.equal(result.privacyOk, true);
});

test('one-turn acceptance requires one contiguous successful chain', () => {
  const result = summarizeAcceptanceLines([
    line(0, 'voice_connected'),
    line(1, 'utterance_started'),
    line(2, 'stt_completed'),
    line(3, 'llm_completed'),
    line(4, 'utterance_started'),
    line(5, 'tts_completed'),
    line(6, 'playback_completed'),
    line(7, 'memory_commit_completed'),
  ]);

  assert.equal(result.oneTurn, false);
  assert.equal(result.completedChains, 0);
  assert.equal(result.passed, false);
});

test('duration starts at voice_connected rather than earlier bootstrap events', () => {
  const result = summarizeAcceptanceLines([
    line(0, 'live_voice_check_ok'),
    line(2, 'voice_connected'),
    line(3, 'stt_completed'),
    line(4, 'llm_completed'),
    line(5, 'tts_completed'),
    line(6, 'playback_completed'),
    line(7, 'memory_commit_completed'),
    line(8, 'voice_stopped'),
  ], {
    minMinutes: 0.1,
  });

  assert.equal(result.durationSeconds, 6);
  assert.equal(result.durationOk, true);
  assert.equal(result.passed, true);
});

test('optional interruption, reconnect and duration gates are enforced', () => {
  const base = [
    line(0, 'voice_connected'),
    line(1, 'stt_completed'),
    line(2, 'llm_completed'),
    line(3, 'tts_completed'),
    line(4, 'playback_completed'),
    line(5, 'memory_commit_completed'),
  ];

  const failed = summarizeAcceptanceLines(base, {
    minMinutes: 0.1,
    requireInterrupt: true,
    requireReconnect: true,
  });
  assert.equal(failed.passed, false);
  assert.equal(failed.interruptOk, false);
  assert.equal(failed.reconnectOk, false);
  assert.equal(failed.durationOk, false);

  const passed = summarizeAcceptanceLines([
    ...base,
    line(7, 'playback_interrupted'),
    line(8, 'voice_rejoined'),
  ], {
    minMinutes: 0.1,
    requireInterrupt: true,
    requireReconnect: true,
  });
  assert.equal(passed.passed, true);
});

test('an unresolved voice disconnect fails acceptance while recovery clears it', () => {
  const base = [
    line(0, 'voice_connected'),
    line(1, 'stt_completed'),
    line(2, 'llm_completed'),
    line(3, 'tts_completed'),
    line(4, 'playback_completed'),
    line(5, 'memory_commit_completed'),
    line(6, 'voice_disconnected'),
  ];

  const failed = summarizeAcceptanceLines(base);
  assert.equal(failed.unresolvedDisconnect, true);
  assert.equal(failed.passed, false);

  const recovered = summarizeAcceptanceLines([
    ...base,
    line(7, 'voice_rejoined'),
  ]);
  assert.equal(recovered.unresolvedDisconnect, false);
  assert.equal(recovered.passed, true);
});

test('debug content, failures and malformed lines fail closed', () => {
  const result = summarizeAcceptanceLines([
    line(0, 'voice_connected'),
    line(1, 'stt_completed'),
    line(2, 'llm_completed'),
    line(3, 'tts_completed'),
    line(4, 'playback_completed'),
    line(5, 'memory_commit_completed'),
    line(6, 'llm_debug_reply', { reply: 'private' }),
    line(7, 'playback_failed'),
    line(8, 'voice_transport_error'),
    'not-a-valid-log-line',
  ]);

  assert.equal(result.passed, false);
  assert.equal(result.privacyOk, false);
  assert.equal(result.failureCount, 2);
  assert.equal(result.malformed, 1);
});
