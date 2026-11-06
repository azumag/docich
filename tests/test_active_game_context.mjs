import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  ACTIVE_GAME_CONTEXT_CONTRACT_VERSION,
  normalizeActiveGameContext,
  loadActiveGameContext,
} from '../overlays/active_game_context.mjs';

test('context contract is explicit and stable', () => {
  assert.equal(ACTIVE_GAME_CONTEXT_CONTRACT_VERSION, 1);
  assert.deepEqual(normalizeActiveGameContext({}, 0), {
    active: false, game: '', phase: 'waiting', updatedAt: 0,
  });
});

test('supports legacy forms but never passes through unrelated keys', () => {
  assert.deepEqual(normalizeActiveGameContext({ active: { game: 'robots', phase: 'RUNNING', token: 'private' }, secret: 'private' }, 12), {
    active: true, game: 'robots', phase: 'running', updatedAt: 12,
  });
  assert.deepEqual(normalizeActiveGameContext({ active: 'sorengame', phase: 'active' }, 3), {
    active: true, game: 'sorengame', phase: 'active', updatedAt: 3,
  });
  assert.deepEqual(normalizeActiveGameContext({ game: 'sorengame', phase: 'running' }, 11), {
    active: false, game: '', phase: 'waiting', updatedAt: 11,
  });
});

test('invalid active, sanitized text, and timestamps use the legacy shape', () => {
  assert.deepEqual(normalizeActiveGameContext({ active: null, phase: 'running' }, 'no'), {
    active: false, game: '', phase: 'waiting', updatedAt: 0,
  });
  assert.deepEqual(normalizeActiveGameContext({ active: { game: '  Retro\n corner ', phase: ' Paused\t' } }, 9), {
    active: true, game: 'Retro corner', phase: 'paused', updatedAt: 9,
  });
  assert.equal(normalizeActiveGameContext({ active: { game: 'a'.repeat(200) } }).game.length, 80);
  assert.equal(normalizeActiveGameContext({ active: { game: 'robots' } }, 1.5).updatedAt, 0);
});

test('file loading survives missing, partial and non-object JSON and preserves mtime', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'docich-context-'));
  try {
    const file = path.join(dir, 'game_switch.json');
    assert.deepEqual(loadActiveGameContext(file), {
      active: false, game: '', phase: 'waiting', updatedAt: 0,
    });
    fs.writeFileSync(file, '{"active":');
    assert.equal(loadActiveGameContext(file).active, false);
    fs.writeFileSync(file, '[]');
    assert.equal(loadActiveGameContext(file).active, false);
    fs.writeFileSync(file, '{"active":{"game":"robots","phase":"running","token":"hidden"}}');
    const loaded = loadActiveGameContext(file);
    assert.deepEqual(loaded, {
      active: true, game: 'robots', phase: 'running',
      updatedAt: Math.floor(fs.statSync(file).mtimeMs / 1000),
    });
    assert.equal('token' in loaded, false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
