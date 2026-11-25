"""Browser contract for the hanjuku-speedrun split timer in the top rail.

The state route is stubbed with the exact timer.json projection produced by
overlays/direct_broadcast_overlay_state.mjs; the test only exercises the HTML
renderer.  Chromium is optional: the module skips when Playwright or its
browser is unavailable (the normal full-suite environment does not install it).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright.async_api")
from playwright.async_api import async_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "overlays" / "direct_broadcast_overlay.html"
STATE_ROUTE = "**/__soren_overlay/broadcast/state"
HARNESS_PATH = "/__srt_harness.html"

# Count rAF callbacks per frame. The speedrun clock is the only continuous
# animation in these tests (the monitor loop needs a monitor panel).
RAF_COUNTER = """
(() => {
  const original = window.requestAnimationFrame.bind(window);
  window.__srtRafCount = 0;
  window.requestAnimationFrame = (callback) => original((timestamp) => {
    window.__srtRafCount += 1;
    callback(timestamp);
  });
})();
"""

GOLD = "rgb(250, 204, 21)"
BLUE = "rgb(96, 165, 250)"
RED = "rgb(248, 113, 113)"
WHITE = "rgb(229, 231, 235)"
GRAY = "rgb(100, 116, 139)"


def _harness_html(mode):
    region = "bottom" if mode == "region" else "full"
    style = "visibility:hidden;" if mode == "hidden" else ""
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>srt harness</title></head>
<body><iframe id="srt" data-soren-overlay-region="{region}"
 src="/{OVERLAY.name}" style="width:1280px;height:720px;{style}"></iframe></body></html>
"""


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802 - SimpleHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == HARNESS_PATH:
            mode = parse_qs(parsed.query).get("mode", ["region"])[0]
            body = _harness_html(mode).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


@pytest.fixture(scope="module")
def overlay_server():
    handler = partial(_QuietHandler, directory=str(OVERLAY.parent))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture(scope="module")
def overlay_url(overlay_server):
    return f"{overlay_server}/{OVERLAY.name}"


def _summary_feed(now_sec):
    return {
        "showStatusG": {
            "text": "SOREN/CORNER: 半熟英雄\nRecent30: 12\nStrategy: A\nLive: 第1話",
            "segments": [],
            "lineCount": 4,
            "updatedAt": now_sec,
        },
        "showStatus": {"text": "", "segments": [], "lineCount": 0, "updatedAt": now_sec},
        "improve": {"active": False, "logLines": [], "lineCount": 0,
                    "updatedAt": now_sec, "logUpdatedAt": now_sec},
    }


def _state(timer=None, *, work=False, comment=False):
    now_sec = int(time.time())
    generators = []
    if comment:
        generators.append({"key": "comment", "label": "コメント生成中", "icon": "💬", "ts": now_sec - 3})
    return {
        "gameGapEnabled": False,
        "version": 1,
        "updatedAt": now_sec,
        "nextCorner": None,
        "speedrunTimer": timer,
        "feeds": _summary_feed(now_sec),
        "notifications": {
            "events": [],
            "generators": generators,
            "work": ({"active": True, "title": "作業中バナー", "body": "検証しています",
                      "ts": now_sec - 5} if work else {"active": False}),
        },
        "topOverride": None,
    }


PB_SEGMENTS = [61_000, 30_000, 40_000, 50_000, 1_000, 10_000,
               20_000, 30_000, 40_000, 50_000, 60_000, 70_000]
PB_CUMULATIVE = [61_000, 91_000, 131_000, 181_000, 182_000, 192_000,
                 212_000, 242_000, 282_000, 332_000, 392_000, 462_000]


def _comparison(pb_total_ms=462_000):
    return {
        "pb_total_ms": pb_total_ms,
        "pb_segments_ms": list(PB_SEGMENTS),
        "pb_cumulative_ms": list(PB_CUMULATIVE),
        "gold_segments_ms": [61_000, 30_000, 40_000, 50_000, None, None,
                            None, None, None, None, None, None],
        "sum_of_best_ms": 442_000,
    }


def _running_timer(now_ms=None):
    now = int(time.time() * 1000) if now_ms is None else now_ms
    started = now - 186_000
    return {
        "attempt_no": 7,
        "status": "running",
        "started_at_ms": started,
        "ended_at_ms": None,
        "current_index": 4,
        "splits": [
            {"index": 0, "end_index": 0, "segment_ms": 61_000, "cumulative_ms": 61_000,
             "verdict": "gold", "delta_ms": -1_022},
            {"index": 1, "end_index": 1, "segment_ms": 30_000, "cumulative_ms": 91_000,
             "verdict": "blue", "delta_ms": 15_210},
            {"index": 2, "end_index": 2, "segment_ms": 40_000, "cumulative_ms": 131_000,
             "verdict": "red", "delta_ms": -40_002},
            {"index": 3, "end_index": 3, "segment_ms": 50_000, "cumulative_ms": 181_000,
             "verdict": "white", "delta_ms": 0},
        ],
        "comparison": _comparison(),
        "heartbeat_at_ms": now - 500,
        "pb_eligible": True,
        "stale": False,
        "nowMs": now,
    }


def _merged_timer(now_ms=None):
    now = int(time.time() * 1000) if now_ms is None else now_ms
    timer = _running_timer(now)
    timer["started_at_ms"] = now - 105_000
    timer["current_index"] = 4
    timer["splits"] = [
        {"index": 0, "end_index": 0, "segment_ms": 10_000, "cumulative_ms": 10_000,
         "verdict": "gold", "delta_ms": -1_000},
        {"index": 1, "end_index": 3, "segment_ms": 90_000, "cumulative_ms": 100_000,
         "verdict": "blue", "delta_ms": 5_000},
    ]
    timer["heartbeat_at_ms"] = now - 500
    return timer


def _finished_timer(*, pb_total_ms, pb_eligible=True):
    now = int(time.time() * 1000)
    started = now - 120_000
    splits = []
    cumulative = 0
    for index, segment in enumerate([10_000] * 12):
        cumulative += segment
        splits.append({
            "index": index, "end_index": index, "segment_ms": segment,
            "cumulative_ms": cumulative,
            "verdict": "gold" if pb_total_ms == 120_000 else "red",
            "delta_ms": 0 if pb_total_ms == 120_000 else 1_000,
        })
    return {
        "attempt_no": 8,
        "status": "finished",
        "started_at_ms": started,
        "ended_at_ms": started + 120_000,
        "current_index": 12,
        "splits": splits,
        "comparison": _comparison(pb_total_ms),
        "heartbeat_at_ms": started + 120_000,
        "pb_eligible": pb_eligible,
        "stale": False,
        "nowMs": now,
    }


async def _open_page(browser, url, state):
    context = await browser.new_context(viewport={"width": 1280, "height": 720})
    await context.add_init_script(RAF_COUNTER)
    page = await context.new_page()
    holder = {"state": state}

    async def fulfill(route):
        await route.fulfill(status=200, content_type="application/json",
                            body=json.dumps(holder["state"]))

    await context.route(STATE_ROUTE, fulfill)
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_function(
        "() => document.querySelectorAll('#speedrun-timer .srt-cell').length === 12")
    await page.wait_for_function("() => Boolean(window.__sorenBroadcastOverlayHealth)")
    return context, page, holder


async def _open_harness(browser, base_url, state, mode):
    context = await browser.new_context(viewport={"width": 1280, "height": 720})
    await context.add_init_script(RAF_COUNTER)
    page = await context.new_page()
    holder = {"state": state}

    async def fulfill(route):
        await route.fulfill(status=200, content_type="application/json",
                            body=json.dumps(holder["state"]))

    await context.route(STATE_ROUTE, fulfill)
    await page.goto(f"{base_url}{HARNESS_PATH}?mode={mode}", wait_until="domcontentloaded")
    await page.wait_for_function(
        "() => { const f = document.querySelector('#srt');"
        " return Boolean(f && f.contentDocument"
        " && f.contentDocument.querySelectorAll('#speedrun-timer .srt-cell').length === 12"
        " && f.contentWindow.__sorenBroadcastOverlayHealth); }")
    frame = page.frames[1]
    return context, page, holder, frame


async def _wait_timer(page, expected=True):
    await page.wait_for_function(
        "(expected) => (document.documentElement.dataset.speedrunTimer === '1') === expected",
        arg=expected,
        timeout=4000,
    )


async def _wait_state(page, holder, state):
    holder["state"] = state
    await page.wait_for_timeout(1200)


def _run(scenario, url):
    async def wrapper():
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(
                    headless=True,
                    executable_path=os.environ.get("SOREN_CHROME_EXECUTABLE_PATH") or None,
                )
            except Exception as error:  # noqa: BLE001 - any launch failure means skip
                pytest.skip(f"Chromium unavailable: {error}")
            try:
                await scenario(browser, url)
            finally:
                await browser.close()

    asyncio.run(wrapper())


async def _color(page, selector):
    return await page.locator(selector).evaluate("(el) => getComputedStyle(el).color")


def test_timer_absent_keeps_the_existing_four_line_summary(overlay_url):
    async def scenario(browser, url):
        context, page, _holder = await _open_page(browser, url, _state(None))
        try:
            await _wait_timer(page, False)
            assert await page.locator(".summary-line").count() == 4
            assert await page.locator("#summary").evaluate(
                "(el) => getComputedStyle(el).display") == "grid"
            assert await page.locator("#speedrun-timer").evaluate(
                "(el) => getComputedStyle(el).display") == "none"
        finally:
            await context.close()

    _run(scenario, overlay_url)


def test_running_timer_draws_total_twelve_cells_colors_and_deltas(overlay_url):
    async def scenario(browser, url):
        context, page, _holder = await _open_page(browser, url, _state(_running_timer()))
        try:
            await _wait_timer(page)
            assert await page.locator("#srt-stale").inner_text() == ""
            assert await page.locator(".srt-cell").count() == 12
            assert re.fullmatch(r"\d+:\d{2}:\d{2}\.\d{3}", await page.locator("#srt-total").inner_text())
            assert await page.locator("#srt-attempt").inner_text() == "#7"
            assert await page.locator("#srt-pb").inner_text() == "0:07:42.000"
            assert await page.locator("#srt-sob").inner_text() == "0:07:22.000"

            assert await page.locator('.srt-cell[data-index="0"] .srt-cell-time').inner_text() == "1:01.000"
            assert await page.locator('.srt-cell[data-index="0"] .srt-cell-delta').inner_text() == "−0:01.022"
            assert await _color(page, '.srt-cell[data-index="0"] .srt-cell-time') == GOLD
            assert await _color(page, '.srt-cell[data-index="1"] .srt-cell-time') == BLUE
            assert await page.locator('.srt-cell[data-index="1"] .srt-cell-delta').inner_text() == "+0:15.210"
            assert await _color(page, '.srt-cell[data-index="2"] .srt-cell-time') == RED
            assert await _color(page, '.srt-cell[data-index="3"] .srt-cell-time') == WHITE

            running = page.locator('.srt-cell[data-index="4"]')
            assert "is-running" in (await running.get_attribute("class"))
            assert re.fullmatch(r"0:0[5-9]\.\d{3}", await running.locator(".srt-cell-time").inner_text())
            assert await _color(page, '.srt-cell[data-index="4"] .srt-cell-time') == RED

            unreached = page.locator('.srt-cell[data-index="5"]')
            assert await unreached.locator(".srt-cell-time").inner_text() == "PB 0:00:10"
            assert await _color(page, '.srt-cell[data-index="5"] .srt-cell-time') == GRAY
        finally:
            await context.close()

    _run(scenario, overlay_url)


def test_merged_range_and_unreached_cells_are_gray(overlay_url):
    async def scenario(browser, url):
        context, page, _holder = await _open_page(browser, url, _state(_merged_timer()))
        try:
            await _wait_timer(page)
            first = page.locator('.srt-cell[data-index="1"]')
            assert await first.locator(".srt-cell-time").inner_text() == "1:30.000"
            assert await _color(page, '.srt-cell[data-index="1"] .srt-cell-time') == BLUE
            for index in (2, 3):
                cell = page.locator(f'.srt-cell[data-index="{index}"]')
                assert await cell.locator(".srt-cell-time").inner_text() == ""
                assert await cell.locator(".srt-cell-delta").inner_text() == "結合"
                assert await _color(page, f'.srt-cell[data-index="{index}"] .srt-cell-delta') == GRAY
                assert "is-gray" in (await cell.get_attribute("class"))
            assert await page.locator('.srt-cell[data-index="5"] .srt-cell-time').inner_text() == "PB 0:00:10"
            assert await _color(page, '.srt-cell[data-index="5"] .srt-cell-time') == GRAY
        finally:
            await context.close()

    _run(scenario, overlay_url)


def test_finished_total_is_gold_for_a_pb_red_when_slower_and_white_without_pb(overlay_url):
    async def scenario(browser, url):
        context, page, holder = await _open_page(
            browser, url, _state(_finished_timer(pb_total_ms=120_000)))
        try:
            await _wait_timer(page)
            assert await page.locator("#srt-total").inner_text() == "0:02:00.000"
            assert "is-gold" in (await page.locator("#srt-total").get_attribute("class"))

            await _wait_state(page, holder, _state(_finished_timer(pb_total_ms=100_000)))
            await page.wait_for_function(
                "() => document.querySelector('#srt-total').classList.contains('is-red')")
            assert "is-red" in (await page.locator("#srt-total").get_attribute("class"))

            # No PB is not enough evidence for either yellow or red.
            await _wait_state(page, holder, _state(_finished_timer(pb_total_ms=None)))
            await page.wait_for_function(
                "() => document.querySelector('#srt-total').classList.contains('is-white')")
            assert "is-white" in (await page.locator("#srt-total").get_attribute("class"))
            assert await page.locator("#srt-pb").inner_text() == "—"
        finally:
            await context.close()

    _run(scenario, overlay_url)


def test_work_and_comment_shrink_to_chips_while_the_timer_stays_visible(overlay_url):
    async def scenario(browser, url):
        context, page, _holder = await _open_page(
            browser, url, _state(_running_timer(), work=True, comment=True))
        try:
            await _wait_timer(page)
            assert await page.locator("#summary").evaluate(
                "(el) => getComputedStyle(el).display") == "none"
            assert await page.locator("#speedrun-timer").evaluate(
                "(el) => getComputedStyle(el).display") == "grid"
            work = page.locator("#srt-work-chip")
            comment = page.locator("#srt-comment-chip")
            assert "active" in (await work.get_attribute("class"))
            assert "作業中バナー" in (await work.inner_text())
            assert "active" in (await comment.get_attribute("class"))
            assert "コメント生成中" in (await comment.inner_text())
            assert "active" not in ((await page.locator("#work").get_attribute("class")) or "")
            assert "active" not in ((await page.locator("#gen-top").get_attribute("class")) or "")
        finally:
            await context.close()

    _run(scenario, overlay_url)


def test_timer_null_returns_the_four_line_summary_without_a_stale_cache(overlay_url):
    async def scenario(browser, url):
        context, page, holder = await _open_page(browser, url, _state(_running_timer()))
        try:
            await _wait_timer(page)
            await _wait_state(page, holder, _state(None))
            await _wait_timer(page, False)
            await page.wait_for_function(
                "() => document.querySelectorAll('#summary .summary-line').length === 4")
            assert await page.locator("#summary").evaluate(
                "(el) => getComputedStyle(el).display") == "grid"
            assert await page.locator("#speedrun-timer").evaluate(
                "(el) => getComputedStyle(el).display") == "none"
            # Reappearing must not collide with lastSummaryKey either.
            await _wait_state(page, holder, _state(_running_timer()))
            await _wait_timer(page)
            assert await page.locator("#summary").evaluate(
                "(el) => getComputedStyle(el).display") == "none"
        finally:
            await context.close()

    _run(scenario, overlay_url)


def test_top_rail_keeps_its_960x90_geometry_with_the_timer(overlay_url):
    async def scenario(browser, url):
        context, page, _holder = await _open_page(browser, url, _state(_running_timer()))
        try:
            await _wait_timer(page)
            box = await page.locator("#top-rail").bounding_box()
            assert box is not None
            assert (box["x"], box["y"], box["width"], box["height"]) == (0, 0, 960, 90)
            timer_box = await page.locator("#speedrun-timer").bounding_box()
            assert timer_box is not None
            assert timer_box["height"] == 73
        finally:
            await context.close()

    _run(scenario, overlay_url)


def test_total_advances_with_raf_and_freezes_when_stale(overlay_url):
    async def scenario(browser, url):
        context, page, holder = await _open_page(browser, url, _state(_running_timer()))
        try:
            await _wait_timer(page)
            first = await page.locator("#srt-total").inner_text()
            await page.wait_for_timeout(250)
            second = await page.locator("#srt-total").inner_text()
            assert second != first, (first, second)

            stale = _running_timer()
            stale["heartbeat_at_ms"] = int(time.time() * 1000) - 60_000
            stale["stale"] = True
            await _wait_state(page, holder, _state(stale))
            frozen = await page.locator("#srt-total").inner_text()
            await page.wait_for_timeout(350)
            assert await page.locator("#srt-total").inner_text() == frozen
            assert await page.locator("#srt-stale").inner_text() == "計測停止中"
        finally:
            await context.close()

    _run(scenario, overlay_url)


def test_an_invisible_surface_region_does_not_run_the_clock(overlay_server):
    async def scenario(browser, base_url):
        context, page, _holder, frame = await _open_harness(
            browser, base_url, _state(_running_timer()), "region")
        try:
            await frame.evaluate("() => { window.__srtRafCount = 0; }")
            await page.wait_for_timeout(600)
            assert await frame.evaluate("() => window.__srtRafCount") == 0
            assert await frame.evaluate(
                "() => document.documentElement.dataset.speedrunTimer") == "0"
            assert await frame.locator("#speedrun-timer").evaluate(
                "(el) => getComputedStyle(el).display") == "none"
        finally:
            await context.close()

    _run(scenario, overlay_server)


def test_a_visibility_hidden_frame_pauses_and_resumes_with_the_next_poll(overlay_server):
    async def scenario(browser, base_url):
        context, page, _holder, frame = await _open_harness(
            browser, base_url, _state(_running_timer()), "hidden")
        try:
            await frame.evaluate("() => { window.__srtRafCount = 0; }")
            await page.wait_for_timeout(600)
            assert await frame.evaluate("() => window.__srtRafCount") == 0
            # The hidden buffer must not claim the rail while it is invisible.
            assert await frame.evaluate(
                "() => document.documentElement.dataset.speedrunTimer") == "0"

            await page.evaluate(
                "() => { document.querySelector('#srt').style.visibility = 'visible'; }")
            await frame.wait_for_function(
                "() => document.documentElement.dataset.speedrunTimer === '1'",
                timeout=4000)
            await page.wait_for_timeout(300)
            assert await frame.evaluate("() => window.__srtRafCount") > 0
        finally:
            await context.close()

    _run(scenario, overlay_server)


def test_timer_null_stops_the_raf_loop(overlay_url):
    async def scenario(browser, url):
        context, page, holder = await _open_page(browser, url, _state(_running_timer()))
        try:
            await _wait_timer(page)
            await page.wait_for_timeout(300)
            assert await page.evaluate("() => window.__srtRafCount") > 0

            await _wait_state(page, holder, _state(None))
            await _wait_timer(page, False)
            await page.evaluate("() => { window.__srtRafCount = 0; }")
            await page.wait_for_timeout(600)
            assert await page.evaluate("() => window.__srtRafCount") == 0
        finally:
            await context.close()

    _run(scenario, overlay_url)
