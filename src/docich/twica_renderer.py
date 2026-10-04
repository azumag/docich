"""Opt-in, game-independent TwiCa browser producing private RGBA snapshots.

No live activation is performed by importing this module or installing it.
One page owns the upstream queue and audio for its entire lifetime; game changes
are deliberately absent from this API. The output consumer enforces frame TTL.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import signal
import sys
import time
from urllib.parse import urlsplit

from .twica_overlay import SnapshotPublisher, frame_size
from .twica_checkpoint import read_checkpoint, save_checkpoint
from .twica_browser_health import BrowserHealth
from .twica_state import control

TRANSPARENT_PAGE_STYLE = (
    'html,body{background:transparent !important;'
    'background-color:transparent !important;}'
)

TRANSPARENT_PAGE_INIT = """(() => {
  const install = () => {
    let style = document.getElementById('docich-transparent-capture');
    const parent = document.head || document.documentElement;
    if (!style) {
      style = document.createElement('style');
      style.id = 'docich-transparent-capture';
      style.textContent = %s;
    }
    if (style.parentNode !== parent || parent.lastChild !== style) parent.appendChild(style);
  };
  const ready = () => {
    install();
    // Preserve the screenshot policy if the app replaces its head or adds CSS.
    new MutationObserver(install).observe(document.documentElement, {childList:true, subtree:true});
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ready, {once:true});
  } else ready();
})()""" % json.dumps(TRANSPARENT_PAGE_STYLE)


class ChromiumCapture:
    """Lossless viewport capture on the existing page; no new subscription.

    Playwright's general screenshot path inserts/removes style, waits for font
    readiness and compresses PNG each frame. This dedicated Chromium page has
    a fixed CSS viewport at device scale 1. Install its transparent background
    once, keep animations running, and prioritize PNG encoding speed.
    """
    def __init__(self, session):
        self.session = session

    @classmethod
    async def create(cls, page):
        session = await page.context.new_cdp_session(page)
        await page.add_init_script(TRANSPARENT_PAGE_INIT)
        await page.evaluate(TRANSPARENT_PAGE_INIT)
        await session.send('Emulation.setDefaultBackgroundColorOverride', {
            'color': {'r': 0, 'g': 0, 'b': 0, 'a': 0},
        })
        return cls(session)

    async def capture(self, timeout_ms):
        result = await asyncio.wait_for(self.session.send('Page.captureScreenshot', {
            'format': 'png', 'captureBeyondViewport': False, 'optimizeForSpeed': True,
        }), timeout=timeout_ms / 1000)
        return base64.b64decode(result['data'], validate=True)


def validate_url(value: str) -> str:
    """Use the existing configured URL; never put it in argv, status or logs."""
    try:
        parsed = urlsplit(value)
        allowed = parsed.scheme == 'https' or (
            parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', '::1', 'localhost'}
        )
        valid = (allowed and parsed.hostname and not parsed.username
                 and not parsed.password and parsed.path.startswith('/overlay/'))
        _ = parsed.port
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('invalid TwiCa overlay URL')
    return value


def decode_screenshot(png: bytes, width: int, height: int) -> bytes:
    # Optional dependencies are not imported in the existing streaming path.
    from PIL import Image
    size = frame_size(width, height)
    if len(png) > size + 65536:
        raise ValueError('oversized screenshot')
    with Image.open(io.BytesIO(png)) as image:
        if image.format != 'PNG' or image.size != (width, height):
            raise ValueError('unexpected screenshot format or size')
        return image.convert('RGBA').tobytes()


async def capture_once(page, publisher: SnapshotPublisher, *,
                       clock=time.monotonic_ns, ttl_ms: int = 1000,
                       capture: ChromiumCapture | None = None) -> bool:
    # Timestamp the START, not the end, of a possibly slow browser operation.
    started = clock()
    png = await capture.capture(ttl_ms) if capture else await page.screenshot(
        type='png', full_page=False, omit_background=True,
        animations='allow', scale='css', style=TRANSPARENT_PAGE_STYLE,
        timeout=ttl_ms,
    )
    rgba = decode_screenshot(png, publisher.width, publisher.height)
    if clock() - started >= ttl_ms * 1_000_000:
        publisher.clear()
        return False
    publisher.publish(rgba, started)
    return True


def browser_environment(audio_sink=None):
    allowed = {'HOME', 'PATH', 'DISPLAY', 'XDG_RUNTIME_DIR', 'LANG', 'LC_ALL',
               'TMPDIR', 'PULSE_SERVER', 'PULSE_COOKIE', 'PULSE_SINK'}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    if audio_sink:
        env['PULSE_SINK'] = audio_sink
    return env


async def run_renderer(url: str, directory: Path, *, width: int = 1280,
                       height: int = 720, fps: int = 15,
                       audio_sink: str | None = None, stop: asyncio.Event | None = None,
                       browser_factory=None, report=None, checkpoint_directory: Path | None = None) -> None:
    """Run one persistent subscription. The service owns restart/backoff/cutover."""
    url = validate_url(url)
    frame_size(width, height)
    if type(fps) is not int or not 1 <= fps <= 30:
        raise ValueError('invalid renderer framerate')
    stop = stop or asyncio.Event()
    if stop.is_set():
        return
    if browser_factory is None:
        from playwright.async_api import async_playwright
        browser_factory = async_playwright
    # Lock BEFORE opening a page: a second renderer must not subscribe or play audio.
    with SnapshotPublisher(directory, width, height) as publisher:
        async with browser_factory() as playwright:
            # The browser needs display/audio paths, not LLM/provider credentials.
            env = browser_environment(audio_sink)
            browser = await playwright.chromium.launch(
                headless=True, env=env,
                executable_path=os.environ.get('SOREN_CHROME_EXECUTABLE_PATH') or None,
                # Headless screenshots must not silently discard TwiCa's sounds.
                ignore_default_args=['--mute-audio'],
                args=['--autoplay-policy=no-user-gesture-required'],
            )
            capture_task = stop_task = health_task = None
            try:
                if checkpoint_directory is None:
                    page = await browser.new_page(
                        viewport={'width': width, 'height': height}, device_scale_factor=1,
                    )
                else:
                    checkpoint = read_checkpoint(checkpoint_directory, url)
                    context = await browser.new_context(
                        viewport={'width': width, 'height': height}, device_scale_factor=1,
                        storage_state=checkpoint.get('storage'),
                    )
                    session = json.dumps(checkpoint.get('session', {}))
                    await context.add_init_script(
                        '(() => { if (window.top !== window) return; '
                        'for (const [k,v] of Object.entries(' + session + ')) '
                        'sessionStorage.setItem(k,v); })()'
                    )
                    page = await context.new_page()

                generation = control(checkpoint_directory).get('generation', '') if checkpoint_directory else ''
                health = BrowserHealth(url, generation)
                if hasattr(page, 'on'):
                    health.attach(page)

                capture = await ChromiumCapture.create(page)

                async def health_loop():
                    # Separate from capture pacing; diagnostic latency must not
                    # starve fresh RGBA or reset a live subscription.
                    while not stop.is_set():
                        try:
                            await health.sample(page, checkpoint_directory)
                        except Exception:
                            pass
                        await asyncio.sleep(1)

                async def capture_loop():
                    # No game-specific URL, no navigation on game-switch, no new queue.
                    if stop.is_set():
                        return
                    response = await page.goto(url, wait_until='domcontentloaded', timeout=15000)
                    if response is None or not response.ok:
                        raise RuntimeError('overlay page unavailable')
                    loop = asyncio.get_running_loop()
                    failures = 0
                    checkpoint_due = loop.time() + 1
                    while not stop.is_set():
                        tick = loop.time()
                        # A recoverable request failure must not close this page.
                        # TwiCa's own controller owns HTTP/WS retries and its queue.
                        try:
                            captured = await capture_once(page, publisher, capture=capture)
                            failures = 0 if captured else failures + 1
                            if report:
                                report('active' if captured else 'degraded')
                        except Exception:
                            publisher.clear()
                            failures += 1
                            if report:
                                report('degraded')
                        if page.is_closed() or failures >= 3:
                            raise RuntimeError('renderer capture unavailable') from None
                        if checkpoint_directory is not None and loop.time() >= checkpoint_due:
                            try:
                                await asyncio.wait_for(save_checkpoint(checkpoint_directory, url, page), timeout=1)
                            except Exception:
                                pass
                            checkpoint_due = loop.time() + 1
                        await asyncio.sleep(max(0.01, 1 / fps - (loop.time() - tick)))

                capture_task = asyncio.create_task(capture_loop())
                stop_task = asyncio.create_task(stop.wait())
                if checkpoint_directory is not None:
                    health_task = asyncio.create_task(health_loop())
                done, _pending = await asyncio.wait(
                    [capture_task, stop_task], return_when=asyncio.FIRST_COMPLETED,
                )
                if capture_task in done:
                    await capture_task  # Propagate failure; do not claim ready.
            finally:
                for task in (capture_task, stop_task, health_task):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (capture_task, stop_task, health_task) if task is not None),
                    return_exceptions=True,
                )
                publisher.clear()
                if checkpoint_directory is not None:
                    try:
                        await asyncio.wait_for(save_checkpoint(checkpoint_directory, url, page), timeout=1)
                    except Exception:
                        pass
                await browser.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--fps', type=int, default=15)
    args = parser.parse_args(argv)
    # Environment-only: the URL may contain private overlay query parameters.
    url = os.environ.get('SOREN_DIRECT_TWICA_OVERLAY_URL', '')

    async def serve():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        try:
            await run_renderer(url, args.directory, width=args.width, height=args.height,
                               fps=args.fps, audio_sink=os.environ.get('PULSE_SINK'), stop=stop)
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)

    try:
        asyncio.run(serve())
    except Exception:
        # Playwright exceptions can contain URL/header/page text. Never print them.
        print(json.dumps({'component': 'twica_common', 'state': 'failed'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
