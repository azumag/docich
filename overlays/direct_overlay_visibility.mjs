// Common visibility gate for broadcast surfaces, shared by game corners.
// The module is read-only and does not create a browser, process or service.
import fs from 'node:fs';
export const DIRECT_OVERLAY_VISIBILITY_CONTRACT_VERSION = 1;

function readState(file) {
  if (!file) return {};
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}


function wildcardActive(file) {
  const phase = String(readState(file).phase || '').toLowerCase();
  return ['generating', 'running'].includes(phase);
}


export function directOverlaySurfaceVisible(item) {
  if (!item || item.visibility === 'always') return true;
  if (item.visibility === 'dashboard') return !wildcardActive(item.suppressStateFile);
  if (item.visibility === 'wildcard') return wildcardActive(item.visibilityStateFile);
  if (item.visibility === 'improve') {
    if (wildcardActive(item.suppressStateFile)) return false;
    const state = readState(item.visibilityStateFile);
    return String(state.status || '').toLowerCase() === 'running';
  }
  return false;
}

