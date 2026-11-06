// Canonical multigame stage DOM callback, serialized by Playwright page.evaluate.
// Keep the callback self-contained: no Node, imports, closures or side effects at import.
export const DIRECT_GAME_STAGE_DOM_CONTRACT_VERSION = 1;

export function applyDirectGameStageDom({ stage, containerSelector, canvasSelector, drawBufferWidth, drawBufferHeight }) {
    document.body.style.margin = '0';
    document.body.style.padding = '0';
    document.body.style.overflow = 'hidden';
    document.body.style.background = '#050914';
    document.documentElement.style.background = '#050914';
    for (const selector of ['#unity-footer', '#unity-loading-bar']) {
      const element = document.querySelector(selector);
      if (element) element.style.display = 'none';
    }

    document.getElementById(stage.elementId)?.remove();
    const stageElement = document.createElement('div');
    stageElement.id = stage.elementId;
    stageElement.setAttribute('aria-hidden', 'true');
    Object.assign(stageElement.style, {
      position: 'fixed', inset: '0', width: `${stage.outputWidth}px`,
      height: `${stage.outputHeight}px`, overflow: 'hidden', pointerEvents: 'none',
      zIndex: '2147483620', background: '#050914',
    });
    const panel = (style) => {
      const element = document.createElement('div');
      element.className = 'soren-stage-placeholder';
      Object.assign(element.style, { position: 'absolute', boxSizing: 'border-box', ...style, opacity: '0' });
      stageElement.appendChild(element);
    };
    panel({
      left: `${stage.sidebarLeft}px`, top: '0', width: `${stage.sidebarWidth}px`,
      height: `${stage.outputHeight}px`,
      background: 'linear-gradient(180deg, #07111f 0%, #030914 100%)',
      borderLeft: '1px solid rgba(56,189,248,.28)',
    });
    panel({
      left: '0', top: '0', width: `${stage.gameWidth}px`, height: `${stage.topRailHeight}px`,
      background: 'linear-gradient(180deg, #07111f 0%, #050b15 100%)',
      borderBottom: '1px solid rgba(56,189,248,.22)',
    });
    panel({
      left: '0', top: `${stage.bottomRailTop}px`, width: `${stage.gameWidth}px`,
      height: `${stage.bottomRailHeight}px`,
      background: 'linear-gradient(180deg, #050b15 0%, #07111f 100%)',
      borderTop: '1px solid rgba(56,189,248,.22)',
    });
    document.body.appendChild(stageElement);

    const container = document.querySelector(containerSelector);
    const canvas = document.querySelector(canvasSelector);
    if (!container || !canvas) throw new Error('game stage container/canvas not found');
    Object.assign(container.style, {
      position: 'fixed', left: `${stage.gameLeft}px`, top: `${stage.gameTop}px`,
      width: `${stage.gameWidth}px`, height: `${stage.gameHeight}px`,
      transform: 'none', overflow: 'hidden', zIndex: '2147483621',
      boxShadow: '0 0 0 1px rgba(148, 205, 255, .28)',
    });
    if (drawBufferWidth > 0 && drawBufferHeight > 0) {
      canvas.width = drawBufferWidth;
      canvas.height = drawBufferHeight;
    }
    Object.assign(canvas.style, {
      width: `${stage.gameWidth}px`, height: `${stage.gameHeight}px`,
      display: 'block', imageRendering: 'pixelated',
    });
    return {
      stageMode: stage.mode,
      gameLeft: stage.gameLeft,
      gameTop: stage.gameTop,
      gameWidth: stage.gameWidth,
      gameHeight: stage.gameHeight,
      canvasWidth: canvas.width,
      canvasHeight: canvas.height,
    };
}
