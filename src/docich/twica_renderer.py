"""Single-page TwiCa renderer with native alpha and bounded private snapshots."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
from pathlib import Path
import signal
import sys
import time
from urllib.parse import urlsplit

from .twica_overlay import SnapshotPublisher, frame_size, private_directory

TRANSPARENT_PAGE_STYLE = (
    'html,body{background:transparent !important;'
    'background-color:transparent !important;}'
)


SESSION_CHECKPOINT = r"""(() => {
  if (window.top !== window) return;
  const key = '__docich_twica_session_v1';
  const set = Storage.prototype.setItem;
  const remove = Storage.prototype.removeItem;
  const clear = Storage.prototype.clear;
  const eligible = k => typeof k === 'string' && k.startsWith('twica-') && k.length < 200;
  try {
    const raw = localStorage.getItem(key);
    const values = raw && raw.length < 262144 ? JSON.parse(raw) : {};
    for (const [k,v] of Object.entries(values).slice(0,64)) {
      if (eligible(k) && typeof v === 'string' && v.length < 65536
          && sessionStorage.getItem(k) === null) set.call(sessionStorage,k,v);
    }
  } catch {}
  function checkpoint() {
    try {
      const values = {};
      for (let i=0; i<Math.min(sessionStorage.length,64);i++) {
        const k = sessionStorage.key(i), v = sessionStorage.getItem(k);
        if (eligible(k) && typeof v === 'string' && v.length < 65536) values[k]=v;
      }
      const raw = JSON.stringify(values);
      if (raw.length < 262144) set.call(localStorage,key,raw);
    } catch {}
  }
  Storage.prototype.setItem = function(k,v) {
    set.call(this,k,v); if (this === sessionStorage && eligible(String(k))) checkpoint();
  };
  Storage.prototype.removeItem = function(k) {
    remove.call(this,k); if (this === sessionStorage && eligible(String(k))) checkpoint();
  };
  Storage.prototype.clear = function() { clear.call(this); if (this === sessionStorage) checkpoint(); };
})();"""


def browser_environment(audio_sink=None) -> dict[str, str]:
    allowed = {'HOME', 'PATH', 'DISPLAY', 'XDG_RUNTIME_DIR', 'LANG', 'LC_ALL',
               'TMPDIR', 'PULSE_SERVER', 'PULSE_COOKIE', 'PULSE_SINK'}
    result = {k: v for k, v in os.environ.items() if k in allowed}
    if audio_sink:
        result['PULSE_SINK'] = audio_sink
    return result


def validate_url(value: str) -> str:
    """Use the existing configured URL; never put it in argv, status or logs."""
    if not isinstance(value, str) or any(c in value for c in '\r\n\x00'):
        raise ValueError('invalid TwiCa overlay URL')
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
    from PIL import Image
    size = frame_size(width, height)
    if len(png) > size + 65536:
        raise ValueError('oversized screenshot')
    with Image.open(io.BytesIO(png)) as image:
        if image.format != 'PNG' or image.size != (width, height):
            raise ValueError('unexpected screenshot format or size')
        return image.convert('RGBA').tobytes()


async def capture_once(page, publisher: SnapshotPublisher, *,
                       clock=time.monotonic_ns, ttl_ms: int = 1000) -> bool:
    started = clock()
    png = await page.screenshot(
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


async def run_renderer(url: str, directory: Path, *, width: int = 1280,
                       height: int = 720, fps: int = 15,
                       audio_sink: str | None = None, stop: asyncio.Event | None = None,
                       browser_factory=None, profile: Path | None = None) -> None:
    """Run one owned page. The common stream service supervises recovery."""
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
    with SnapshotPublisher(directory, width, height) as publisher:
        async with browser_factory() as playwright:
            env = browser_environment(audio_sink)
            launch = dict(
                headless=True, env=env,
                executable_path=os.environ.get('SOREN_CHROME_EXECUTABLE_PATH') or None,
                ignore_default_args=['--mute-audio'],
                args=['--autoplay-policy=no-user-gesture-required'],
            )
            if profile is None:
                browser = await playwright.chromium.launch(**launch)
            else:
                private_directory(profile)
                browser = await playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile), viewport={'width': width, 'height': height},
                    device_scale_factor=1, **launch)
                await browser.add_init_script(SESSION_CHECKPOINT)
            capture_task = stop_task = None
            try:
                if profile is None:
                    page = await browser.new_page(
                        viewport={'width': width, 'height': height}, device_scale_factor=1,
                    )
                else:
                    existing = list(browser.pages)
                    page = existing[0] if existing else await browser.new_page()
                    for extra in existing[1:]:
                        await extra.close()

                async def capture_loop():
                    if stop.is_set():
                        return
                    response = await page.goto(url, wait_until='domcontentloaded', timeout=15000)
                    if response is None or not response.ok:
                        raise RuntimeError('overlay page unavailable')
                    loop = asyncio.get_running_loop()
                    consecutive_failures = 0
                    while not stop.is_set():
                        tick = loop.time()
                        try:
                            if await capture_once(page, publisher):
                                consecutive_failures = 0
                            else:
                                consecutive_failures += 1
                        except Exception:
                            publisher.clear()
                            consecutive_failures += 1
                            if page.is_closed():
                                raise RuntimeError('renderer page closed') from None
                        if consecutive_failures >= 5:
                            raise RuntimeError('renderer capture failed')
                        await asyncio.sleep(max(0.01, 1 / fps - (loop.time() - tick)))

                capture_task = asyncio.create_task(capture_loop())
                stop_task = asyncio.create_task(stop.wait())
                done, _pending = await asyncio.wait(
                    [capture_task, stop_task], return_when=asyncio.FIRST_COMPLETED,
                )
                if capture_task in done:
                    await capture_task
            finally:
                for task in (capture_task, stop_task):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (capture_task, stop_task) if task is not None),
                    return_exceptions=True,
                )
                publisher.clear()
                await browser.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--fps', type=int, default=15)
    args = parser.parse_args(argv)
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
        print(json.dumps({'component': 'twica_common', 'state': 'failed'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
