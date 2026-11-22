// Common broadcast stage layout contract, independent of the Soviet game.
// Importing this module does not start Playwright, a game, FFmpeg, or a service.
export const DIRECT_STAGE_CONTRACT_VERSION = 1;
export const DIRECT_STAGE_ELEMENT_ID = 'soren-direct-stream-stage';

function enabledValue(raw, fallback = '1') {
  return !['0', 'false', 'no', 'off'].includes(String(raw ?? fallback).trim().toLowerCase());
}


function parseStageSize(raw, fallback, label) {
  const value = String(raw || fallback).trim().toLowerCase();
  const match = value.match(/^(\d{2,5})x(\d{2,5})$/);
  if (!match) throw new Error(`${label} must use WIDTHxHEIGHT`);
  const width = Number(match[1]);
  const height = Number(match[2]);
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height)
      || width < 320 || height < 180 || width > 3840 || height > 2160
      || width % 2 || height % 2) {
    throw new Error(`${label} must use supported even dimensions`);
  }
  return { width, height };
}


export function loadDirectStageConfig(env = process.env, platform = process.platform) {
  const backend = String(env.SOREN_STREAM_BACKEND || 'obs').trim().toLowerCase();
  const eligible = platform === 'linux'
    && backend === 'ffmpeg'
    && enabledValue(env.SOREN_DIRECT_OVERLAY_ENABLED);
  if (!eligible) return { enabled: false, mode: 'fullscreen', elementId: DIRECT_STAGE_ELEMENT_ID };

  const mode = String(env.SOREN_DIRECT_STAGE_LAYOUT || 'dashboard').trim().toLowerCase();
  if (!['dashboard', 'fullscreen'].includes(mode)) {
    throw new Error('SOREN_DIRECT_STAGE_LAYOUT must be dashboard or fullscreen');
  }
  if (mode === 'fullscreen') return { enabled: false, mode, elementId: DIRECT_STAGE_ELEMENT_ID };

  const output = parseStageSize(
    env.SOREN_DIRECT_STREAM_SIZE,
    '1280x720',
    'SOREN_DIRECT_STREAM_SIZE',
  );
  const game = parseStageSize(
    env.SOREN_DIRECT_GAME_DISPLAY_SIZE,
    '960x540',
    'SOREN_DIRECT_GAME_DISPLAY_SIZE',
  );
  if (game.width * 9 !== game.height * 16) {
    throw new Error('SOREN_DIRECT_GAME_DISPLAY_SIZE must use a 16:9 aspect ratio');
  }
  if (game.width >= output.width || game.height > output.height) {
    throw new Error('SOREN_DIRECT_GAME_DISPLAY_SIZE must leave room inside the stream output');
  }
  const sidebarWidth = output.width - game.width;
  if (sidebarWidth < 256) {
    throw new Error('SOREN_DIRECT_GAME_DISPLAY_SIZE must leave at least 256px for dashboard data');
  }
  const gameTop = Math.floor((output.height - game.height) / 2);
  return {
    enabled: true,
    mode,
    elementId: DIRECT_STAGE_ELEMENT_ID,
    outputWidth: output.width,
    outputHeight: output.height,
    gameLeft: 0,
    gameTop,
    gameWidth: game.width,
    gameHeight: game.height,
    sidebarLeft: game.width,
    sidebarWidth,
    topRailHeight: gameTop,
    bottomRailTop: gameTop + game.height,
    bottomRailHeight: output.height - gameTop - game.height,
  };
}

