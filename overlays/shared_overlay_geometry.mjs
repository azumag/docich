// Shared streaming layout and browser-visible readiness contract.
// Owned by docich; the Soviet game runtime may use this module read-only.
// No browser, service, game or encoder is started by importing this module.

export const SHARED_OVERLAY_LAYOUT = Object.freeze({
  width: 1280,
  height: 720,
  game: Object.freeze({ left: 0, top: 90, width: 960, height: 540 }),
  sidebar: Object.freeze({ left: 960, top: 0, width: 320, height: 720 }),
  topRail: Object.freeze({ left: 0, top: 0, width: 960, height: 90 }),
  bottomRail: Object.freeze({ left: 0, top: 630, width: 960, height: 90 }),
});


function rounded(value) {
  const number = Number(value);
  return Number.isFinite(number) && Math.round(number) === number ? number : null;
}


/**
 * Validate the browser's real visible window, not only its CSS viewport.
 *
 * Headed Chromium can report an inner 1280x720 viewport while a toolbar and
 * window-manager frame make the actual X11 window 1288x805.  That window
 * spills below a 1280x720 display even though DOM layout checks pass.  The
 * shared service must not publish ready until the outer window is exactly the
 * stream canvas, positioned at the screen origin, and fully contained by the
 * reported screen.
 */
export function sharedOverlayViewportReady(viewport) {
  if (!viewport || typeof viewport !== 'object') return false;
  const exact = (value, expected) => rounded(value) === expected;
  const innerReady = exact(viewport.innerWidth, SHARED_OVERLAY_LAYOUT.width)
    && exact(viewport.innerHeight, SHARED_OVERLAY_LAYOUT.height);
  const outerReady = exact(viewport.outerWidth, SHARED_OVERLAY_LAYOUT.width)
    && exact(viewport.outerHeight, SHARED_OVERLAY_LAYOUT.height);
  const screenX = rounded(viewport.screenX);
  const screenY = rounded(viewport.screenY);
  const screenWidth = rounded(viewport.screenWidth);
  const screenHeight = rounded(viewport.screenHeight);
  const screenReady = screenX === 0
    && screenY === 0
    && screenWidth !== null
    && screenHeight !== null
    && screenWidth >= SHARED_OVERLAY_LAYOUT.width
    && screenHeight >= SHARED_OVERLAY_LAYOUT.height
    && screenX + SHARED_OVERLAY_LAYOUT.width <= screenWidth
    && screenY + SHARED_OVERLAY_LAYOUT.height <= screenHeight;
  const stage = viewport.stage;
  const stageReady = stage && exact(stage.left, 0) && exact(stage.top, 0)
    && exact(stage.width, SHARED_OVERLAY_LAYOUT.width)
    && exact(stage.height, SHARED_OVERLAY_LAYOUT.height);
  return Boolean(innerReady && outerReady && screenReady && stageReady);
}
