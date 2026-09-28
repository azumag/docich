"""Supervised single common renderer; the stream runner owns this process group.

It never selects/stops a game. Inactive/failed ownership means no upstream page.
No secret-bearing browser exception or URL is written to logs/health records.
"""
from __future__ import annotations
import argparse
import asyncio
import os
from pathlib import Path
import signal
import time

from .twica_config import load_common_config
from .twica_overlay import private_directory
from .twica_renderer import run_renderer, validate_url, browser_environment
from .twica_state import (alive, component, fresh, heartbeat, lease, owner, retired,
                          proxy_inventory)


async def preflight(profile: Path, url: str) -> None:
    """Prove browser/screenshot dependencies without subscribing to TwiCa."""
    from PIL import Image
    from playwright.async_api import async_playwright
    private_directory(profile)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, env=browser_environment(),
            executable_path=os.environ.get('SOREN_CHROME_EXECUTABLE_PATH') or None)
        try:
            page = await browser.new_page(viewport={'width': 32, 'height': 32})
            # HEAD checks upstream availability without opening a subscribing page.
            response = await page.request.head(url, timeout=5000)
            if not response.ok:
                raise RuntimeError('overlay preflight unavailable')
            await page.set_content('<html><body></body></html>')
            await page.screenshot(type='png', omit_background=True, timeout=5000)
        finally:
            await browser.close()


async def serve(config, *, width: int, height: int, stop: asyncio.Event) -> None:
    # A second service cannot even preflight/start a browser for this stream.
    with lease(config.state, 'renderer-service'):
        profile = config.state / 'browser-profile'
        flag = os.environ.get('SOREN_DIRECT_TWICA_OVERLAY_ENABLED', '1').lower().strip()
        if flag in {'0', 'false', 'off', 'no'}:
            raise ValueError('TwiCa is intentionally disabled')
        url = validate_url(os.environ.get('SOREN_DIRECT_TWICA_OVERLAY_URL', ''))
        await preflight(profile, url)
        task = None
        render_stop = None
        try:
            while not stop.is_set():
                # A killed stream runner must not leave an orphan subscriber/lease.
                parent = os.environ.get('DOCICH_TWICA_PARENT_PID')
                if parent and not alive({'pid': int(parent),
                        'birth': os.environ.get('DOCICH_TWICA_PARENT_BIRTH'),
                        'boot': os.environ.get('DOCICH_TWICA_PARENT_BOOT')}):
                    break
                current = owner(config.state)
                compositor = component(config.state, 'compositor')
                running = (current['mode'] == 'common' and fresh(compositor)
                           and compositor.get('state') == 'running'
                           and retired(config.state, current['generation']))
                if running and task is None:
                    # Check only at a renderer start, not for every video frame.
                    # A not-yet-upgraded legacy host must not be mistaken for absent.
                    if await asyncio.to_thread(proxy_inventory, config.proxy_ports):
                        render_stop = asyncio.Event()
                        task = asyncio.create_task(run_renderer(
                            url, config.frames, width=width, height=height,
                            fps=config.fps, audio_sink=config.audio_sink,
                            profile=profile, stop=render_stop))
                elif not running and task is not None:
                    render_stop.set()
                    # A failed close exits nonzero; the owning runner tears down only
                    # this process group before creating another browser owner.
                    await asyncio.wait_for(task, timeout=8)
                    task = None
                if task is not None and task.done():
                    await task
                    raise RuntimeError('unexpected renderer completion')
                heartbeat(config.state, 'renderer',
                          state='active' if task else 'standby', browser_active=task is not None,
                          generation=current['generation'])
                try:
                    await asyncio.wait_for(stop.wait(), timeout=.25)
                except asyncio.TimeoutError:
                    pass
        finally:
            if task is not None:
                render_stop.set()
                await asyncio.wait_for(task, timeout=8)
            # This record is written only after browser/context cleanup returned.
            heartbeat(config.state, 'renderer', state='standby', browser_active=False,
                      generation=owner(config.state)['generation'])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--soren-root', type=Path, required=True)
    parser.add_argument('--width', type=int, required=True)
    parser.add_argument('--height', type=int, required=True)
    args = parser.parse_args(argv)
    async def run():
        config = load_common_config(args.soren_root)
        if not config.enabled:
            return
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        try:
            await serve(config, width=args.width, height=args.height, stop=stop)
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
    try:
        asyncio.run(run())
        return 0
    except Exception:
        # The runner reports only this fixed state and bounded restart counters.
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
