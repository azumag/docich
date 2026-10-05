import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { runAcceptance } from './acceptance-runner.mjs';

test('acceptance runner timestamps injected live events and passes a one-turn run', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'docich-voice-accept-'));
  const log = join(dir, 'acceptance.jsonl');
  const signalSource = new EventEmitter();
  let tick = 0;

  const code = await runAcceptance({
    log,
    minMinutes: 0,
    requireInterrupt: false,
    requireReconnect: false,
    stopAfterMinutes: null,
  }, {
    signalSource,
    now: () => new Date(Date.UTC(2026, 9, 5, 16, 0, tick++)),
    runVoice: async (_env, ops) => {
      for (const event of [
        'voice_connected',
        'stt_completed',
        'llm_completed',
        'tts_completed',
        'playback_completed',
        'memory_commit_completed',
        'voice_stopped',
      ]) {
        ops.emit({ event });
      }
      return 0;
    },
  });

  assert.equal(code, 0);
  const content = readFileSync(log, 'utf8');
  assert.match(content, /voice_connected/);
  assert.match(content, /memory_commit_completed/);
  assert.doesNotMatch(content, /private transcript/);
});

test('runtime failure makes acceptance fail even with successful lifecycle events', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'docich-voice-accept-'));
  const log = join(dir, 'acceptance.jsonl');

  const code = await runAcceptance({
    log,
    minMinutes: 0,
    requireInterrupt: false,
    requireReconnect: false,
    stopAfterMinutes: null,
  }, {
    signalSource: new EventEmitter(),
    now: () => new Date('2026-10-05T16:00:00Z'),
    runVoice: async (_env, ops) => {
      for (const event of [
        'voice_connected',
        'stt_completed',
        'llm_completed',
        'tts_completed',
        'playback_completed',
        'memory_commit_completed',
      ]) {
        ops.emit({ event });
      }
      return 3;
    },
  });

  assert.equal(code, 1);
});
