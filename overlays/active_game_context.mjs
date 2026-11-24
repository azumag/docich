// Common stream game-switch context. No game launch, overlay service, or VM side effects.
// This is the docich-owned canonical implementation; Soviet can use it when installed.
import fs from 'node:fs';

export const ACTIVE_GAME_CONTEXT_CONTRACT_VERSION = 1;
export const DEFAULT_ACTIVE_GAME_CONTEXT_FILE = '/home/ubuntu/docich/run-soren-live/game_switch.json';

function sourceUpdatedAt(file) {
  try {
    return Math.floor(fs.statSync(file).mtimeMs / 1000);
  } catch {
    return 0;
  }
}

function readJsonObject(file) {
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
  } catch {
    // Missing or partly-written lifecycle state means "waiting".
  }
  return {};
}

function safeText(value, fallback = '') {
  const text = String(value ?? '')
    .replace(/[\u0000-\u001f\u007f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return text.slice(0, 80) || fallback;
}

function objectValue(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

/**
 * Preserve only the selected game's public name/phase and the file timestamp.
 * A legacy plain-string active field is accepted; unrelated keys are never exposed.
 */
export function normalizeActiveGameContext(raw, updatedAt = 0) {
  const document = objectValue(raw) || {};
  const active = document.active;
  const activeObject = objectValue(active);
  const explicitInactive = active === false || active === null;
  const gameRaw = activeObject?.game
    ?? (typeof active === 'string' ? active : undefined);
  const phaseRaw = activeObject?.phase ?? document.phase;
  const game = safeText(gameRaw);
  const phase = safeText(phaseRaw, game ? 'active' : 'waiting').toLowerCase();
  const activeGame = Boolean(game) && !explicitInactive;
  return {
    active: activeGame,
    game: activeGame ? game : '',
    phase: activeGame ? phase : 'waiting',
    updatedAt: Number.isSafeInteger(Number(updatedAt)) ? Number(updatedAt) : 0,
  };
}

/** This call is read-only and safe when the file is missing or being written. */
export function loadActiveGameContext(file = DEFAULT_ACTIVE_GAME_CONTEXT_FILE) {
  const updatedAt = sourceUpdatedAt(file);
  return normalizeActiveGameContext(readJsonObject(file), updatedAt);
}
