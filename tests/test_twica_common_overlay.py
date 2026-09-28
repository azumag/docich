from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
import select
import shutil
import struct
import subprocess
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from docich.twica_overlay import (
    HEADER, MAGIC, OverlayPipe, SnapshotPublisher, SnapshotReader,
    ffmpeg_input_args, ffmpeg_overlay_filter, frame_size, private_directory,
)
from docich.twica_renderer import capture_once, decode_screenshot, run_renderer, validate_url


@pytest.mark.parametrize('width,height', [(0, 1), (-1, 1), (True, 1), (1.0, 1), (3841, 2160)])
def test_dimensions_are_bounded(width, height):
    with pytest.raises(ValueError):
        frame_size(width, height)


def test_atomic_roundtrip_and_expiry(tmp_path):
    directory = tmp_path / 'overlay'
    with SnapshotPublisher(directory, 2, 1) as publisher:
        pixels = bytes([255, 255, 255, 255, 12, 34, 56, 128])
        publisher.publish(pixels, 1_000_000_000)
        reader = SnapshotReader(directory, 2, 1, ttl_ms=100)
        assert reader.read(1_050_000_000) == pixels
        assert reader.state == 'fresh'
        identity = reader._identity
        assert reader.read(1_100_000_000) == bytes(8)
        assert reader.state == 'stale'
        assert reader._identity == identity  # no megabyte reread per stale frame
        assert reader.read(900_000_000) == bytes(8)  # future/previous-boot packet
        publisher.clear()
        assert reader.read(1_050_000_000) == bytes(8)
        assert reader.state == 'missing'
    assert (directory / 'publisher.lock').exists()
    assert not (directory / 'frame.rgba').exists()


def test_publisher_cannot_be_duplicated_or_used_without_lease(tmp_path):
    directory = tmp_path / 'overlay'
    first = SnapshotPublisher(directory, 1, 1)
    with pytest.raises(RuntimeError):
        first.publish(bytes(4), 10)
    with first:
        first.publish(bytes([1, 2, 3, 255]), 10)
        inode = (directory / 'publisher.lock').stat().st_ino
        with pytest.raises(BlockingIOError):
            with SnapshotPublisher(directory, 1, 1):
                pytest.fail('second publisher acquired the lease')
        assert (directory / 'frame.rgba').exists()
    with SnapshotPublisher(directory, 1, 1):
        assert (directory / 'publisher.lock').stat().st_ino == inode
        assert not (directory / 'frame.rgba').exists()


def test_invalid_snapshot_types_do_not_block(tmp_path):
    directory = private_directory(tmp_path / 'overlay')
    reader = SnapshotReader(directory, 1, 1)
    path = directory / 'frame.rgba'
    other = tmp_path / 'other'
    other.write_bytes(b'private-data')
    path.symlink_to(other)
    assert reader.read() == bytes(4)
    path.unlink()
    os.mkfifo(path, 0o600)
    started = time.monotonic()
    assert reader.read() == bytes(4)
    assert time.monotonic() - started < 0.5
    path.unlink()
    path.mkdir()
    assert reader.read() == bytes(4)


@pytest.mark.parametrize('packet', [
    b'bad', HEADER.pack(b'BADMAGIC', 1, 1, 1) + bytes(4),
    HEADER.pack(MAGIC, 2, 1, 1) + bytes(4),
    HEADER.pack(MAGIC, 1, 1, 0) + bytes(4),
    HEADER.pack(MAGIC, 1, 1, 1) + bytes(5),
])
def test_malformed_packets_are_transparent(tmp_path, packet):
    directory = private_directory(tmp_path / 'overlay')
    path = directory / 'frame.rgba'
    path.write_bytes(packet)
    path.chmod(0o600)
    assert SnapshotReader(directory, 1, 1).read(2) == bytes(4)


def test_private_paths_modes_and_hardlinks(tmp_path):
    directory = tmp_path / 'overlay'
    directory.mkdir(mode=0o755)
    with pytest.raises(ValueError):
        private_directory(directory)
    directory.chmod(0o700)
    alias = tmp_path / 'alias'
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError):
        private_directory(alias)
    with SnapshotPublisher(directory, 1, 1) as publisher:
        publisher.publish(bytes([1, 2, 3, 255]), time.monotonic_ns())
        path = directory / 'frame.rgba'
        path.chmod(0o644)
        assert SnapshotReader(directory, 1, 1).read() == bytes(4)
        path.chmod(0o600)
        os.link(path, directory / 'hardlink')
        assert SnapshotReader(directory, 1, 1).read() == bytes(4)


def read_exact(fd, size):
    data = bytearray()
    deadline = time.monotonic() + 2
    while len(data) < size and time.monotonic() < deadline:
        if select.select([fd], [], [], 0.05)[0]:
            chunk = os.read(fd, size - len(data))
            if not chunk:
                break
            data.extend(chunk)
    assert len(data) == size
    return bytes(data)


def test_pipe_keeps_sending_after_renderer_disappears(tmp_path):
    directory = tmp_path / 'overlay'
    with SnapshotPublisher(directory, 1, 1) as publisher:
        publisher.publish(bytes([255, 0, 0, 128]), time.monotonic_ns())
        with OverlayPipe(SnapshotReader(directory, 1, 1), 10) as feed:
            consumer = os.dup(feed.read_fd)
            try:
                feed.start()
                assert read_exact(consumer, 4) == bytes([255, 0, 0, 128])
                publisher.clear()
                assert read_exact(consumer, 4) == bytes(4)
                assert read_exact(consumer, 4) == bytes(4)
                assert feed._thread.is_alive()
            finally:
                os.close(consumer)


def test_full_pipe_shutdown_is_bounded(tmp_path):
    feed = OverlayPipe(SnapshotReader(tmp_path / 'overlay', 1280, 720), 30)
    consumer = os.dup(feed.read_fd)
    feed.start()
    time.sleep(0.05)  # consumer deliberately never reads
    started = time.monotonic()
    feed.close()
    assert time.monotonic() - started < 1
    assert not feed._thread.is_alive()
    feed.close()
    os.close(consumer)


def test_pipe_start_failure_cleanup_and_control_stdin_separation(tmp_path):
    with OverlayPipe(SnapshotReader(tmp_path / 'overlay', 1, 1), 30) as feed:
        fd = feed.read_fd
        args = ffmpeg_input_args(fd, 1, 1, 30, sync_to=0)
        assert args[-1] == f'pipe:{fd}'
        assert 'pipe:0' not in args
        assert args[:6] == ['-thread_queue_size', '2', '-use_wallclock_as_timestamps', '1', '-isync', '0']
    with pytest.raises(OSError):
        os.fstat(fd)
    with pytest.raises(ValueError):
        ffmpeg_input_args(0, 1, 1, 30)
    with pytest.raises(ValueError):
        ffmpeg_overlay_filter(1, 1)


def png_bytes(size=(2, 1)):
    image = Image.new('RGBA', size, (255, 255, 255, 128))
    image.putpixel((0, 0), (2, 3, 4, 0))
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def test_png_keeps_white_colours_and_half_alpha():
    assert decode_screenshot(png_bytes(), 2, 1) == bytes([2, 3, 4, 0, 255, 255, 255, 128])
    with pytest.raises(ValueError):
        decode_screenshot(png_bytes(), 3, 1)


def test_capture_does_not_fast_forward_animations_and_rejects_slow_capture(tmp_path):
    class Page:
        async def screenshot(self, **kwargs):
            self.options = kwargs
            return png_bytes()
    page = Page()
    directory = tmp_path / 'overlay'
    with SnapshotPublisher(directory, 2, 1) as publisher:
        stamps = iter([1_000_000_000, 1_050_000_000])
        assert asyncio.run(capture_once(page, publisher, clock=lambda: next(stamps)))
        assert page.options['animations'] == 'allow'
        assert page.options['omit_background'] is True
        assert page.options['full_page'] is False
        stamps = iter([2_000_000_000, 3_000_000_000])
        assert not asyncio.run(capture_once(page, publisher, clock=lambda: next(stamps)))
        assert not publisher.path.exists()


@pytest.mark.parametrize('url', ['file:///overlay/x', 'javascript:alert(1)',
                                 'https://user:secret@example.test/overlay/x',
                                 'http://example.test/overlay/x', 'https://example.test/login'])
def test_renderer_refuses_wrong_urls(url):
    with pytest.raises(ValueError, match='^invalid TwiCa overlay URL$'):
        validate_url(url)


def test_renderer_has_one_page_one_navigation_and_cleans_up(tmp_path):
    async def scenario():
        stop = asyncio.Event()
        calls = []
        class Page:
            async def goto(self, url, **kwargs):
                calls.append('goto')
                return SimpleNamespace(ok=True)
            async def screenshot(self, **kwargs):
                calls.append('frame')
                if calls.count('frame') == 3:
                    stop.set()
                return png_bytes()
            def is_closed(self):
                return False
        class Browser:
            async def new_page(self, **kwargs):
                calls.append('page')
                assert kwargs['viewport'] == {'width': 2, 'height': 1}
                return Page()
            async def close(self):
                calls.append('close')
        class Chromium:
            async def launch(self, **kwargs):
                calls.append('launch')
                assert kwargs['ignore_default_args'] == ['--mute-audio']
                assert kwargs['env']['PULSE_SINK'] == 'fixture_sink'
                return Browser()
        class Factory:
            async def __aenter__(self):
                return SimpleNamespace(chromium=Chromium())
            async def __aexit__(self, *args):
                pass
        await run_renderer('https://example.test/overlay/fixture', tmp_path / 'overlay',
                           width=2, height=1, fps=30, audio_sink='fixture_sink',
                           stop=stop, browser_factory=Factory)
        assert calls == ['launch', 'page', 'goto', 'frame', 'frame', 'frame', 'close']
        assert not (tmp_path / 'overlay' / 'frame.rgba').exists()
    asyncio.run(scenario())


def test_real_ffmpeg_composites_in_front_of_changing_games_and_expires(tmp_path):
    ffmpeg = shutil.which('ffmpeg')
    assert ffmpeg, 'This contract requires a real FFmpeg (CI installs it).'
    width, height, fps = 120, 72, 10
    backgrounds = [(0, 0, 200, 255), (0, 200, 0, 255), (0, 0, 0, 255)]
    base = tmp_path / 'base.rgba'
    base.write_bytes(b''.join(bytes(colour) * (width * height) for colour in backgrounds * 2))
    rgba = Image.new('RGBA', (width, height))
    # Original viewport-centred card. Output x=88..111 overlaps the game x<90.
    for y in range(24, 48):
        for x in range(48, 72):
            rgba.putpixel((x, y), (255, 255, 255, 128))
    with SnapshotPublisher(tmp_path / 'overlay', width, height) as publisher:
        stamp = time.monotonic_ns()
        publisher.publish(rgba.tobytes(), stamp)
        class ExpiringReader(SnapshotReader):
            count = 0
            def read(self, now_ns=None):
                self.count += 1
                # Deterministic renderer-stop point with file still present.
                return super().read(stamp + (0 if self.count <= 3 else self.ttl_ns))
        reader = ExpiringReader(tmp_path / 'overlay', width, height)
        with OverlayPipe(reader, fps) as feed:
            command = [ffmpeg, '-hide_banner', '-loglevel', 'error',
                       '-f', 'rawvideo', '-pixel_format', 'rgba', '-video_size', f'{width}x{height}',
                       '-framerate', str(fps), '-i', str(base)]
            command += ffmpeg_input_args(feed.read_fd, width, height, fps)
            command += ['-filter_complex_threads', '1', '-filter_complex',
                        ffmpeg_overlay_filter(0, 1) + '[out]', '-map', '[out]',
                        '-frames:v', '6', '-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1']
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, pass_fds=(feed.read_fd,))
            try:
                feed.start()
                output, error = child.communicate(timeout=10)
                assert child.returncode == 0, error.decode()
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
    size = width * height * 4
    assert len(output) == 6 * size
    for index in range(6):
        image = Image.frombytes('RGBA', (width, height), output[index * size:(index + 1) * size])
        background = backgrounds[index % 3]
        assert image.getpixel((1, 1)) == background
        pixel = image.getpixel((89, 30))  # visible even inside the foreground game region
        if index < 3:
            expected = tuple(round((255 * 128 + c * 127) / 255) for c in background[:3])
            assert all(abs(pixel[c] - expected[c]) <= 1 for c in range(3))
        else:
            assert pixel == background  # TTL expiry does not stop the six-frame output.
