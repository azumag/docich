import test from 'node:test';
import assert from 'node:assert/strict';
import {
  SHARED_OVERLAY_LAYOUT,
  sharedOverlayViewportReady,
} from '../overlays/shared_overlay_geometry.mjs';

function viewport(overrides = {}) {
  return {
    innerWidth: 1280, innerHeight: 720,
    outerWidth: 1280, outerHeight: 720,
    screenX: 0, screenY: 0, screenWidth: 1280, screenHeight: 720,
    stage: { left: 0, top: 0, width: 1280, height: 720 },
    ...overrides,
  };
}

test('docich owns the exact four-surface streaming geometry', () => {
  assert.deepEqual(SHARED_OVERLAY_LAYOUT.game, { left: 0, top: 90, width: 960, height: 540 });
  assert.deepEqual(SHARED_OVERLAY_LAYOUT.sidebar, { left: 960, top: 0, width: 320, height: 720 });
  assert.deepEqual(SHARED_OVERLAY_LAYOUT.topRail, { left: 0, top: 0, width: 960, height: 90 });
  assert.deepEqual(SHARED_OVERLAY_LAYOUT.bottomRail, { left: 0, top: 630, width: 960, height: 90 });
  assert.equal(Object.isFrozen(SHARED_OVERLAY_LAYOUT), true);
  assert.equal(Object.isFrozen(SHARED_OVERLAY_LAYOUT.game), true);
});

test('exact fullscreen browser and stage are required before readiness', () => {
  assert.equal(sharedOverlayViewportReady(viewport()), true);
  assert.equal(sharedOverlayViewportReady(viewport({ screenWidth: 1920, screenHeight: 1080 })), true);
  assert.equal(sharedOverlayViewportReady(viewport({ outerHeight: 805 })), false);
  assert.equal(sharedOverlayViewportReady(viewport({ screenX: 1 })), false);
  assert.equal(sharedOverlayViewportReady(viewport({ screenY: -1 })), false);
  assert.equal(sharedOverlayViewportReady(viewport({ screenWidth: 1279 })), false);
  assert.equal(sharedOverlayViewportReady(viewport({ stage: { left: 1, top: 0, width: 1280, height: 720 } })), false);
  assert.equal(sharedOverlayViewportReady(viewport({ stage: null })), false);
  assert.equal(sharedOverlayViewportReady(null), false);
});
