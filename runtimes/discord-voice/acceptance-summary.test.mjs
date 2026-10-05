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
    'not-a-valid-log-line',
  ]);

  assert.equal(result.passed, false);
  assert.equal(result.privacyOk, false);
  assert.equal(result.failureCount, 1);
  assert.equal(result.malformed, 1);
});
