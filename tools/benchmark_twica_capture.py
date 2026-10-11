#!/usr/bin/env python3
"""Bounded offline PNG benchmark. No live URLs, pages, events or image export.

Child CPU includes browser/driver startup and shutdown, not just capture.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.twica_renderer import ChromiumCapture, TRANSPARENT_PAGE_STYLE, decode_screenshot, browser_environment


async def measure(method, frames, scenario):
    from playwright.async_api import async_playwright
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    python_before = time.process_time()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, env=browser_environment(),
            executable_path=os.environ.get('SOREN_CHROME_EXECUTABLE_PATH') or None,
        )
        try:
            page = await browser.new_page(viewport={'width': 1280, 'height': 720}, device_scale_factor=1)
            card = '' if scenario == 'blank' else '''<div style="position:absolute;left:350px;top:50px;
              width:550px;height:300px;background:rgba(20,40,70,.8);color:white">
              <h1>Overlay fixture 123456</h1></div>'''
            await page.set_content('<style>html,body{margin:0;background:white}</style>' + card)
            await page.evaluate('document.fonts.ready')
            capture = await ChromiumCapture.create(page) if method == 'fast' else None
            times, byte_counts, digest = [], [], None
            for _ in range(frames + 3):
                started = time.monotonic()
                if capture:
                    png = await capture.capture(5000)
                else:
                    png = await page.screenshot(type='png', full_page=False, omit_background=True,
                        animations='allow', scale='css', style=TRANSPARENT_PAGE_STYLE, timeout=5000)
                rgba = decode_screenshot(png, 1280, 720)
                times.append(time.monotonic() - started)
                byte_counts.append(len(png))
                current = hashlib.sha256(rgba).hexdigest()
                if digest is not None and digest != current:
                    raise RuntimeError('fixture pixels changed')
                digest = current
        finally:
            await browser.close()
    child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    samples = sorted(times[3:])
    return {
        'median_ms': round(statistics.median(samples) * 1000, 2),
        'p95_ms': round(samples[min(len(samples) - 1, int(.95 * len(samples)))] * 1000, 2),
        'python_cpu_sec': round(time.process_time() - python_before, 4),
        'child_cpu_sec_including_startup': round(child_after.ru_utime + child_after.ru_stime
            - child_before.ru_utime - child_before.ru_stime, 4),
        'png_bytes_median': int(statistics.median(byte_counts[3:])),
        'pixel_sha256': digest,
    }


async def run(frames, scenario):
    results = {}
    for method in ('standard', 'fast'):
        results[method] = await measure(method, frames, scenario)
    equal = results['standard']['pixel_sha256'] == results['fast']['pixel_sha256']
    print(json.dumps({'scenario': scenario, 'frames_per_method': frames,
        'same_pixels': equal, 'methods': results}))
    return 0 if equal else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames', type=int, default=60)
    parser.add_argument('--scenario', choices=('blank', 'card'), default='blank')
    args = parser.parse_args()
    if not 10 <= args.frames <= 240:
        parser.error('--frames must be between 10 and 240')
    try:
        raise SystemExit(asyncio.run(run(args.frames, args.scenario)))
    except Exception:
        print('{"benchmark":"failed"}', file=sys.stderr)
        raise SystemExit(1)
