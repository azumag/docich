// Shared browser-side overlay frame installer for all game corners.
// No Node/Playwright dependencies or service launch: this function is serialized
// into each controlled page by addInitScript/evaluate.
export const BROWSER_OVERLAY_INSTALLER_CONTRACT_VERSION = 1;

export function overlayInstaller(surfaces) {
  if (window.top !== window) return;
  const idleHtml = '<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:transparent}</style>';
  const stripSelfRefresh = (html) => String(html ?? '')
    .replace(/<meta\b[^>]*http-equiv\s*=\s*["']?refresh["']?[^>]*>/gi, '')
    .replace(/<script>\s*setTimeout\(\(\)\s*=>\s*location[.]reload\(\)\s*,\s*1000\s*\)\s*;?\s*<\/script>/gi, '');
  const install = () => {
    if (!document.body) return;
    for (const item of surfaces) {
      if (document.getElementById(item.elementId)) continue;
      const makeFrame = (index) => {
        const frame = document.createElement('iframe');
        frame.id = index === 0 ? item.elementId : `${item.elementId}-buffer`;
        frame.title = index === 0 ? item.title : `${item.title} buffer`;
        frame.setAttribute('aria-hidden', 'true');
        if (item.region) frame.dataset.sorenOverlayRegion = item.region;
        frame.setAttribute('allowtransparency', 'true');
        frame.setAttribute('frameborder', '0');
        Object.assign(frame.style, {
          position: 'fixed',
          border: '0',
          margin: '0',
          padding: '0',
          background: 'transparent',
          pointerEvents: 'none',
          opacity: index === 0 ? '1' : '0',
          visibility: index === 0 ? 'visible' : 'hidden',
          ...item.style,
        });
        if (item.region === 'game-gap') frame.style.display = 'none';
        document.body.appendChild(frame);
        return frame;
      };
      // 外部URL（TwiCa 等）は二重バッファにすると片方の CSS ロード失敗で
      // 素の HTML（スタイルなし・左上配置）が visible 側に表示され得るため、
      // バッファは作らず1フレームのみで直接 src を設定する。
      if (item.srcUrl) {
        const frame = makeFrame(0);
        frame.dataset.sorenExternalOverlay = '1';
        frame.src = item.srcUrl;
        continue;
      }
      const frames = [0, 1].map(makeFrame);
      let activeIndex = 0;
      let lastHtml = '';
      let pendingHtml = '';
      let generation = 0;
      const refresh = async () => {
        // 外部URLオーバーレイはポーリング不要（iframe 自身がロード済み）。
        if (item.srcUrl) return;
        try {
          const response = await fetch(item.route, { cache: 'no-store' });
          if (!response.ok) return;
          const html = stripSelfRefresh(await response.text());
          if (html === lastHtml || html === pendingHtml) return;
          pendingHtml = html;
          const requestGeneration = ++generation;
          const incomingIndex = 1 - activeIndex;
          const incoming = frames[incomingIndex];
          const outgoing = frames[activeIndex];
          incoming.addEventListener('load', () => {
            if (requestGeneration !== generation) return;
            incoming.style.visibility = 'visible';
            incoming.style.opacity = '1';
            outgoing.style.opacity = '0';
            outgoing.style.visibility = 'hidden';
            if (item.region === 'game-gap') outgoing.style.display = 'none';
            activeIndex = incomingIndex;
            lastHtml = html;
            pendingHtml = '';
            outgoing.srcdoc = idleHtml;
            console.warn(`[DIRECT-OVERLAY-POLL] ${item.key} ${html.includes('const events=') ? 'active' : 'idle'}`);
          }, { once: true });
          incoming.srcdoc = html;
        } catch {
          pendingHtml = '';
          // Retry on the next poll; overlay IO must never block game rendering.
        }
      };
      void refresh();
      setInterval(refresh, Math.max(100, Number(item.pollMs) || 1000));
    }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, { once: true });
  } else {
    install();
  }
}
