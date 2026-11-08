import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DIRECT_STAGE_CONTRACT_VERSION,
  DIRECT_STAGE_ELEMENT_ID,
  loadDirectStageConfig,
} from '../overlays/direct_stage_config.mjs';

const base = {
  SOREN_STREAM_BACKEND: 'ffmpeg',
  SOREN_DIRECT_OVERLAY_ENABLED: '1',
};

test('docich independently owns the versioned multigame stage contract', () => {
  assert.equal(DIRECT_STAGE_CONTRACT_VERSION, 1);
  assert.equal(DIRECT_STAGE_ELEMENT_ID, 'soren-direct-stream-stage');
  assert.deepEqual(loadDirectStageConfig({}, 'linux'), {
    enabled: false, mode: 'fullscreen', elementId: DIRECT_STAGE_ELEMENT_ID,
  });
  assert.deepEqual(loadDirectStageConfig(base, 'darwin'), {
    enabled: false, mode: 'fullscreen', elementId: DIRECT_STAGE_ELEMENT_ID,
  });
  assert.deepEqual(loadDirectStageConfig({...base, SOREN_DIRECT_STAGE_LAYOUT: 'fullscreen'}, 'linux'), {
    enabled: false, mode: 'fullscreen', elementId: DIRECT_STAGE_ELEMENT_ID,
  });
});

test('dashboard reserves 960x540 game, 320 sidebar, and exact top/bottom rails', () => {
  assert.deepEqual(loadDirectStageConfig(base, 'linux'), {
    enabled: true, mode: 'dashboard', elementId: DIRECT_STAGE_ELEMENT_ID,
    outputWidth: 1280, outputHeight: 720,
    gameLeft: 0, gameTop: 90, gameWidth: 960, gameHeight: 540,
    sidebarLeft: 960, sidebarWidth: 320,
    topRailHeight: 90, bottomRailTop: 630, bottomRailHeight: 90,
  });
  const hd = loadDirectStageConfig({...base, SOREN_DIRECT_STREAM_SIZE: '1920x1080', SOREN_DIRECT_GAME_DISPLAY_SIZE: '1600x900'}, 'linux');
  assert.equal(hd.sidebarLeft, 1600);
  assert.equal(hd.sidebarWidth, 320);
  assert.equal(hd.gameTop, 90);
  assert.equal(hd.bottomRailTop, 990);
  assert.equal(hd.bottomRailHeight, 90);
});

test('reject misconfigured or overlapping game layouts', () => {
  const invalid = [
    [{SOREN_DIRECT_STAGE_LAYOUT: 'invalid'}, /dashboard or fullscreen/],
    [{SOREN_DIRECT_STREAM_SIZE: 'invalid'}, /WIDTHxHEIGHT/],
    [{SOREN_DIRECT_GAME_DISPLAY_SIZE: '960x541'}, /supported even dimensions/],
    [{SOREN_DIRECT_GAME_DISPLAY_SIZE: '1000x540'}, /16:9 aspect ratio/],
    [{SOREN_DIRECT_GAME_DISPLAY_SIZE: '1056x594'}, /at least 256px/],
    [{SOREN_DIRECT_GAME_DISPLAY_SIZE: '1280x720'}, /leave room/],
  ];
  for (const [env, error] of invalid) {
    assert.throws(() => loadDirectStageConfig({...base, ...env}, 'linux'), error);
  }
});
