"""Local preview server tests (synthetic timer.json, real overlay HTML)."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from docich.hanjuku_speedrun.rta import preview  # noqa: E402

NOW = int(time.time() * 1000)
RUNTIME = 'g1300-abcdef12'


def _timer(**overrides):
    document = {
        'schema': 1,
        'game': 'hanjuku-speedrun',
        'runtime_id': RUNTIME,
        'generation': 1300,
        'attempt_id': f'a0007-{RUNTIME}',
        'attempt_no': 7,
        'status': 'running',
        'started_at_ms': 1000,
        'ended_at_ms': None,
        'start_source': 'name_screen',
        'name_seen_at_ms': 1000,
        'current_index': 2,
        'splits': [
            {'index': 0, 'end_index': 0, 'segment_ms': 10000,
             'cumulative_ms': 10000, 'verdict': 'gold', 'delta_ms': -1022},
            {'index': 1, 'end_index': 1, 'segment_ms': 20000,
             'cumulative_ms': 30000, 'verdict': 'blue', 'delta_ms': 15210},
        ],
        'comparison': {
            'pb_total_ms': 120000,
            'pb_segments_ms': [10000] * 12,
            'pb_cumulative_ms': [10000] * 12,
            'gold_segments_ms': [10000, 20000] + [None] * 10,
            'sum_of_best_ms': None,
        },
        'heartbeat_at_ms': 60000,
        'pb_eligible': True,
        'total_verdict': None,
        'total_ms': None,
    }
    document.update(overrides)
    return document


def _data_dir(tmp_path, timer=None):
    data = tmp_path / 'run-local' / 'speedrun' / 'hanjuku-speedrun'
    data.mkdir(parents=True)
    if timer is not None:
        (data / 'timer.json').write_text(json.dumps(timer), encoding='utf-8')
    return data


def test_preview_state_from_synthetic_timer(tmp_path):
    data = _data_dir(tmp_path, _timer())
    before = (data / 'timer.json').read_bytes()
    state = preview.build_preview_state(data, now_ms=NOW, prefer_node=False)

    timer = state['speedrunTimer']
    assert timer['status'] == 'running'
    assert timer['attempt_no'] == 7
    assert timer['current_index'] == 2
    assert len(timer['splits']) == 2
    assert timer['splits'][0]['verdict'] == 'gold'
    assert timer['splits'][1]['delta_ms'] == 15210
    # Frame-clock timestamps are shifted onto the display clock; durations are
    # unchanged (heartbeat 60000 - started 1000 = 59000 elapsed).
    assert timer['nowMs'] == NOW
    assert timer['started_at_ms'] == NOW - 59000
    assert timer['heartbeat_at_ms'] <= NOW
    assert timer['stale'] in (False, True)
    assert (data / 'timer.json').read_bytes() == before


@pytest.mark.skipif(preview.shutil.which('node') is None,
                    reason='node is not installed')
def test_preview_uses_the_real_overlay_state_module(tmp_path):
    data = _data_dir(tmp_path, _timer())
    state = preview.build_preview_state(data, now_ms=NOW, prefer_node=True)
    # The node path also returns the feeds/notifications keys; the Python
    # fallback only guarantees the timer.
    assert 'feeds' in state and 'notifications' in state
    assert state['speedrunTimer']['attempt_no'] == 7
    assert len(state['speedrunTimer']['splits']) == 2


def test_preview_serves_html_and_state_without_writing(tmp_path):
    data = _data_dir(tmp_path, _timer())
    before = (data / 'timer.json').read_bytes()
    server = preview.create_server(tmp_path / 'run-local', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        assert host == '127.0.0.1'
        with urllib.request.urlopen(
                f'http://{host}:{port}/', timeout=5) as response:
            html = response.read()
            assert response.status == 200
            assert b'Soren direct broadcast overlay' in html
            assert b'speedrun-timer' in html
        with urllib.request.urlopen(
                f'http://{host}:{port}{preview.STATE_ROUTE}',
                timeout=5) as response:
            document = json.loads(response.read())
            assert response.status == 200
            assert document['speedrunTimer']['attempt_no'] == 7
        assert (data / 'timer.json').read_bytes() == before
        assert not (tmp_path / 'run-local' / 'game_switch.json').exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_preview_without_timer_is_quiet(tmp_path):
    data = _data_dir(tmp_path, None)
    state = preview.build_preview_state(data, now_ms=NOW, prefer_node=False)
    assert state['speedrunTimer'] is None


def test_preview_finished_attempt_stays_visible(tmp_path):
    data = _data_dir(tmp_path, _timer(
        status='finished', ended_at_ms=120000, current_index=12,
        total_ms=119000, total_verdict='gold'))
    state = preview.build_preview_state(data, now_ms=NOW, prefer_node=False)
    timer = state['speedrunTimer']
    assert timer['status'] == 'finished'
    assert timer['ended_at_ms'] == NOW
    assert timer['started_at_ms'] == NOW - 119000


def test_preview_renders_splits_with_playwright(tmp_path):
    pytest.importorskip(
        'playwright.async_api',
        reason='Playwright is not installed; the DOM-level split check is '
               'covered by tests/test_speedrun_timer_view.mjs')
    import asyncio
    from playwright.async_api import async_playwright

    data = _data_dir(tmp_path, _timer())
    server = preview.create_server(tmp_path / 'run-local', 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        async def check():
            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch()
                except Exception as exc:  # no browser binary in this image
                    pytest.skip(f'Chromium is unavailable: {type(exc).__name__}')
                page = await browser.new_page(viewport={'width': 1280, 'height': 720})
                await page.goto(f'http://{host}:{port}/')
                await page.wait_for_selector('#speedrun-timer', state='visible',
                                             timeout=15000)
                text = await page.inner_text('#speedrun-timer')
                assert '#7' in text
                assert '0:01:00.000' in text or '1:00.000' in text
                await browser.close()
        asyncio.run(check())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
