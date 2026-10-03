"""Capture gaps must not erase a fresh overlay or extend its original TTL."""
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

from PIL import Image
import pytest

from docich import twica_renderer as renderer
from docich.twica_overlay import HEADER, SnapshotPublisher, SnapshotReader

URL = 'https://example.test/overlay/fixture'
PIXELS = bytes((255, 255, 255, 128)) * 2


def png(transparent=False):
    data = io.BytesIO()
    Image.new('RGBA', (2, 1), (0, 0, 0, 0) if transparent else (255, 255, 255, 128)).save(data, format='PNG')
    return data.getvalue()


class Page:
    url = URL

    def __init__(self, screenshot):
        self._screenshot = screenshot
        self.count = 0
        self.navigations = 0

    async def goto(self, *_args, **_kwargs):
        self.navigations += 1
        return SimpleNamespace(ok=True)

    async def screenshot(self, **_kwargs):
        self.count += 1
        return await self._screenshot(self.count)

    async def evaluate(self, _script):
        return {'present': False, 'visible': False, 'images_ready': False}

    def is_closed(self):
        return False


def factory_for(page, calls):
    class Browser:
        async def new_page(self, **_kwargs):
            calls.append('page')
            return page

        async def new_context(self, **_kwargs):
            return self

        async def add_init_script(self, _script):
            pass

        async def close(self):
            calls.append('close')

    class Chromium:
        async def launch(self, **_kwargs):
            calls.append('launch')
            return Browser()

    class Factory:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_args):
            pass

    return Factory


def test_transient_capture_failure_keeps_original_frame_until_original_ttl(tmp_path):
    async def scenario():
        directory = tmp_path / 'frames'
        reader = SnapshotReader(directory, 2, 1)
        stop, calls, observations = asyncio.Event(), [], {}

        async def screenshot(number):
            if number == 2:
                observations['before'] = reader.path.read_bytes()
                raise TimeoutError('fixture capture failure')
            if number == 3:
                observations['after'] = reader.path.read_bytes() if reader.path.exists() else None
                captured = HEADER.unpack_from(observations['before'])[3]
                observations['fresh'] = reader.read(captured + reader.ttl_ns - 1)
                observations['expired'] = reader.read(captured + reader.ttl_ns)
                observations['expired_state'] = reader.state
                stop.set()
            return png()

        page = Page(screenshot)
        await renderer.run_renderer(URL, directory, width=2, height=1, fps=30,
                                    stop=stop, browser_factory=factory_for(page, calls))
        assert observations['after'] == observations['before']  # no re-timestamp/republication
        assert observations['fresh'] == PIXELS
        assert observations['expired'] == bytes(8)
        assert observations['expired_state'] == 'stale'
        assert page.navigations == 1
        assert calls == ['launch', 'page', 'close']
        assert not reader.path.exists()

    asyncio.run(scenario())


def test_a_successfully_captured_transparent_frame_replaces_the_previous_card(tmp_path):
    async def scenario():
        directory = tmp_path / 'frames'
        reader = SnapshotReader(directory, 2, 1)
        stop, calls, observations = asyncio.Event(), [], []

        async def screenshot(number):
            if number == 3:
                observations.append(reader.read())
                observations.append(reader.state)
                stop.set()
            return png(transparent=number >= 2)

        await renderer.run_renderer(URL, directory, width=2, height=1, fps=30,
                                    stop=stop, browser_factory=factory_for(Page(screenshot), calls))
        assert observations == [bytes(8), 'fresh']

    asyncio.run(scenario())


def test_repeated_capture_failure_still_stops_and_clears_the_publisher(tmp_path):
    async def scenario():
        directory, calls = tmp_path / 'frames', []

        async def screenshot(number):
            if number > 1:
                raise TimeoutError('fixture capture failure')
            return png()

        page = Page(screenshot)
        with pytest.raises(RuntimeError, match='renderer capture unavailable'):
            await renderer.run_renderer(URL, directory, width=2, height=1, fps=30,
                                        browser_factory=factory_for(page, calls))
        assert page.count == 4  # one good frame, three failures
        assert not (directory / 'frame.rgba').exists()
        assert calls == ['launch', 'page', 'close']
        with SnapshotPublisher(directory, 2, 1):
            pass  # the exclusive publisher lease was released

    asyncio.run(scenario())


def test_pending_checkpoint_does_not_block_frames_and_is_joined_before_final_save(tmp_path, monkeypatch):
    async def scenario():
        stop, checkpoint_started, next_frame = (asyncio.Event() for _ in range(3))
        cancelled = asyncio.Event()
        calls, saves, active_saves = [], [], 0

        async def save(*_args):
            nonlocal active_saves
            active_saves += 1
            saves.append((active_saves, cancelled.is_set()))
            try:
                if len(saves) == 1:
                    checkpoint_started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
            finally:
                active_saves -= 1

        monkeypatch.setattr(renderer, 'save_checkpoint', save)

        async def screenshot(_number):
            if checkpoint_started.is_set():
                next_frame.set()
            return png()

        page = Page(screenshot)
        task = asyncio.create_task(renderer.run_renderer(
            URL, tmp_path / 'frames', width=2, height=1, fps=30, stop=stop,
            checkpoint_directory=tmp_path / 'state', browser_factory=factory_for(page, calls)))
        continued = False
        try:
            await asyncio.wait_for(checkpoint_started.wait(), timeout=4)
            # Less than the checkpoint's 1-second timeout; the old serial loop
            # cannot produce a frame here until checkpoint cancellation.
            try:
                await asyncio.wait_for(next_frame.wait(), timeout=0.4)
                continued = True
            except asyncio.TimeoutError:
                pass
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=3)
        assert continued, 'checkpoint IO blocked the RGBA producer'
        assert cancelled.is_set()
        assert saves == [(1, False), (1, True)]  # final save is not concurrent
        assert page.navigations == 1
        assert calls == ['launch', 'page', 'close']
        assert not (tmp_path / 'frames/frame.rgba').exists()

    asyncio.run(scenario())


def test_no_periodic_checkpoint_before_successful_navigation(tmp_path, monkeypatch):
    async def scenario():
        stop, started = asyncio.Event(), asyncio.Event()
        calls, saves = [], []

        async def save(*_args):
            saves.append(stop.is_set())

        monkeypatch.setattr(renderer, 'save_checkpoint', save)

        class NavigatingPage(Page):
            async def goto(self, *_args, **_kwargs):
                started.set()
                await asyncio.Event().wait()

        async def screenshot(_number):
            return png()

        task = asyncio.create_task(renderer.run_renderer(
            URL, tmp_path / 'frames', width=2, height=1, fps=30, stop=stop,
            checkpoint_directory=tmp_path / 'state', browser_factory=factory_for(NavigatingPage(screenshot), calls)))
        try:
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.sleep(1.1)
            assert saves == []
        finally:
            stop.set()
            await asyncio.wait_for(task, 2)
        assert saves == [True]  # existing shutdown checkpoint only
        assert calls == ['launch', 'page', 'close']

    asyncio.run(scenario())
