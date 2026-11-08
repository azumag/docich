// Shared broadcast overlay surface-selection policy.
// Legacy SOREN_DIRECT_* env names are preserved until VM rollout is accepted.
// This module is pure: no process, files, browser, HTTP, or game side effects.
export const DIRECT_OVERLAY_SURFACE_SELECTION_CONTRACT_VERSION = 1;

function enabledValue(raw, fallback = '1') {
  return !['0', 'false', 'no', 'off'].includes(String(raw ?? fallback).trim().toLowerCase());
}

export function selectDirectOverlaySurfaces({stage,env,broadcastSurfaces,twicaSurface,definitions}) {
  const flagNames = {
    broadcastSidebar: 'SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED',
    broadcastTop: 'SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED',
    broadcastBottom: 'SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED',
    broadcastGameGap: 'SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED',
    event: 'SOREN_DIRECT_EVENT_OVERLAY_ENABLED',
    stats: 'SOREN_DIRECT_STATS_OVERLAY_ENABLED',
    ops: 'SOREN_DIRECT_OPS_OVERLAY_ENABLED',
    improve: 'SOREN_DIRECT_IMPROVE_OVERLAY_ENABLED',
    wildcard: 'SOREN_DIRECT_WILDCARD_OVERLAY_ENABLED',
    avsync: 'SOREN_DIRECT_AV_SYNC_OVERLAY_ENABLED',
    twica: 'SOREN_DIRECT_TWICA_OVERLAY_ENABLED',
  };
  // dashboard配信では既存のevent/stats/ops HTMLを直接縮小表示しない。
  // それらはread-only data sourceとしてbroadcast専用面へ取り込み、
  // fullscreen互換時だけ従来の個別surfaceをそのまま使う。
  // improve も同様に broadcast 側へ統合するため、dashboard では全画面
  // surface を張らない (改善中もゲーム画面を覆わない)。
  const useBroadcastSurface = stage.enabled
    && enabledValue(env.SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED);
  const candidates = useBroadcastSurface
    ? [...broadcastSurfaces, twicaSurface, ...definitions.filter((item) => ['wildcard', 'avsync'].includes(item.key))]
    : [...definitions, twicaSurface];
  const surfaces = candidates.filter((item) => enabledValue(env[flagNames[item.key]]));
  const broadcastEnabled = surfaces.some((item) => item.region === 'sidebar');
  return { surfaces, broadcastEnabled };
}
