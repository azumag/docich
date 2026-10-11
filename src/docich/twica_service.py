"""One common TwiCa browser, outside all game/presenter lifecycles."""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
import signal
import sys
import time

from .twica_renderer import browser_environment, run_renderer, validate_url
from .twica_state import control, exclusive, frame_directory, fresh, heartbeat, read_json, state_directory


async def preflight_browser() -> None:
    """Prepare browser capability without opening a subscribing TwiCa page."""
    from PIL import Image  # noqa: F401
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, env=browser_environment(),
            executable_path=os.environ.get('SOREN_CHROME_EXECUTABLE_PATH') or None,
        )
        try:
            page = await browser.new_page()
            await page.set_content('<html><body></body></html>')
            await page.screenshot(omit_background=True, timeout=10000)
        finally:
            await browser.close()


async def serve(directory: Path, url: str, stop: asyncio.Event, *, runner=run_renderer,
                preflight=preflight_browser, tick_sec: float = 0.2) -> None:
    """Only ownership/pipeline changes can replace the page; never game changes."""
    url = validate_url(url)
    with exclusive(directory, 'service.lock'):
        await preflight()
        task = None
        renderer_stop = None
        generation = ''
        rendered_state = 'standby'
        retry_after = 0.0
        failures = 0
        last_active = 0.0
        def report(state):
            nonlocal rendered_state, last_active
            rendered_state = state
            if state == 'active':
                last_active = time.monotonic()
        try:
            while not stop.is_set():
                policy = control(directory)
                pipeline = read_json(directory / 'pipeline.json')
                enabled = (policy['owner'] == 'common' and fresh(pipeline)
                           and pipeline.get('ready') is True)
                dimensions_ok = (type(pipeline.get('width')) is int and type(pipeline.get('height')) is int
                                 and 320 <= pipeline['width'] <= 3840 and 180 <= pipeline['height'] <= 2160)
                enabled = enabled and dimensions_ok
                if task is not None and (not enabled or policy['generation'] != generation):
                    renderer_stop.set()
                    try:
                        await asyncio.wait_for(task, timeout=20)
                    except (Exception, asyncio.CancelledError):
                        pass
                    task, renderer_stop = None, None
                    rendered_state = 'standby'
                if task is not None and task.done():
                    try:
                        task.result()
                    except (Exception, asyncio.CancelledError):
                        pass
                    task, renderer_stop = None, None
                    failures += 1
                    retry_after = time.monotonic() + min(30, 2 ** min(failures, 5))
                    rendered_state = 'degraded'
                if enabled and task is None and time.monotonic() >= retry_after:
                    generation = policy['generation']
                    renderer_stop = asyncio.Event()
                    rendered_state = 'starting'
                    task = asyncio.create_task(runner(
                        url, frame_directory(), width=pipeline['width'], height=pipeline['height'],
                        fps=min(30, int(os.environ.get('DOCICH_TWICA_RENDER_FPS', '15'))),
                        audio_sink=os.environ.get('DOCICH_TWICA_AUDIO_SINK', 'soren_null'),
                        stop=renderer_stop, report=report, checkpoint_directory=directory,
                    ))
                if rendered_state == 'active' and time.monotonic() - last_active > 2:
                    rendered_state = 'degraded'
                # Do not reset backoff on a single successful screenshot.
                if rendered_state == 'active' and failures and time.monotonic() >= retry_after + 60:
                    failures = 0
                heartbeat(directory, 'renderer.json', state=rendered_state,
                          generation=policy['generation'], subscribed=task is not None,
                          ready=True, attempts=failures)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=tick_sec)
                except asyncio.TimeoutError:
                    pass
        finally:
            if task is not None:
                renderer_stop.set()
                try:
                    await asyncio.wait_for(task, timeout=20)
                except (Exception, asyncio.CancelledError):
                    pass
            heartbeat(directory, 'renderer.json', state='stopped', subscribed=False,
                      generation=control(directory)['generation'], ready=False)


def main() -> int:
    async def start():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await serve(state_directory(), os.environ.get('SOREN_DIRECT_TWICA_OVERLAY_URL', ''), stop)
    try:
        asyncio.run(start())
    except Exception:
        print('twica common: renderer service failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
