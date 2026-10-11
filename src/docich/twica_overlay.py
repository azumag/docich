"""Bounded RGBA snapshots and a non-blocking input for a common TwiCa layer.

This module does not start a browser, change a game, or restart an encoder.
The foreground is composited AFTER the captured game/rails, not in a game page.
Snapshots are private, atomic, and expire using the host's monotonic clock.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import select
import stat
import struct
import tempfile
import threading
import time
from typing import Callable

MAGIC = b'TWICARG1'
HEADER = struct.Struct('<8sIIQ')
MAX_PIXELS = 3840 * 2160
SNAPSHOT_NAME = 'frame.rgba'


def frame_size(width: int, height: int) -> int:
    if (type(width) is not int or type(height) is not int
            or width < 1 or height < 1 or width * height > MAX_PIXELS):
        raise ValueError('invalid overlay dimensions')
    return width * height * 4


def private_directory(directory: Path) -> Path:
    """Refuse links/insecure existing directories instead of changing their mode."""
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError('overlay directory must be private and owned')
    if directory.resolve() != directory:
        raise ValueError('overlay directory must not contain symlinks')
    return directory


class SnapshotPublisher:
    """One publisher per stream directory; never unlink the flock inode."""
    def __init__(self, directory: Path, width: int, height: int):
        self.width, self.height = width, height
        self.size = frame_size(width, height)
        self.directory = private_directory(directory)
        self.path = self.directory / SNAPSHOT_NAME
        self._lock_fd: int | None = None

    def __enter__(self):
        if self._lock_fd is not None:
            raise RuntimeError('publisher lease is already held')
        fd = os.open(self.directory / 'publisher.lock',
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
                raise ValueError('invalid publisher lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        self._lock_fd = fd
        try:
            self.clear()  # Never reuse a previous process's frame.
        except BaseException:
            self._lock_fd = None
            os.close(fd)
            raise
        return self

    def publish(self, rgba: bytes, captured_ns: int) -> None:
        if self._lock_fd is None:
            raise RuntimeError('publisher lease is not held')
        if (len(rgba) != self.size or type(captured_ns) is not int
                or not 0 < captured_ns < 2**64):
            raise ValueError('invalid overlay frame')
        fd, temporary = tempfile.mkstemp(prefix='.frame-', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(HEADER.pack(MAGIC, self.width, self.height, captured_ns))
                stream.write(rgba)
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def clear(self) -> None:
        if self._lock_fd is None:
            raise RuntimeError('publisher lease is not held')
        self.path.unlink(missing_ok=True)

    def __exit__(self, *_exc):
        if self._lock_fd is not None:
            try:
                self.clear()
            finally:
                fd, self._lock_fd = self._lock_fd, None
                os.close(fd)


class SnapshotReader:
    """Read one bounded regular file. Missing/malformed/stale means transparent."""
    def __init__(self, directory: Path, width: int, height: int, *, ttl_ms: int = 1000):
        if type(ttl_ms) is not int or not 50 <= ttl_ms <= 5000:
            raise ValueError('invalid overlay TTL')
        self.size = frame_size(width, height)
        self.width, self.height = width, height
        self.path = private_directory(directory) / SNAPSHOT_NAME
        self.ttl_ns = ttl_ms * 1_000_000
        self.transparent = bytes(self.size)
        self.state = 'missing'
        self._identity = None
        self._pixels = self.transparent
        self._captured_ns = 0

    def _reset(self, state: str) -> bytes:
        self.state = state
        self._identity = None
        self._captured_ns = 0
        self._pixels = self.transparent
        return self.transparent

    def read(self, now_ns: int | None = None) -> bytes:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077
                        or info.st_size != HEADER.size + self.size):
                    return self._reset('invalid')
                identity = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
                if identity != self._identity:
                    with os.fdopen(os.dup(fd), 'rb') as stream:
                        packet = stream.read(HEADER.size + self.size + 1)
                    if len(packet) != HEADER.size + self.size:
                        return self._reset('invalid')
                    magic, width, height, captured = HEADER.unpack_from(packet)
                    if magic != MAGIC or (width, height) != (self.width, self.height):
                        return self._reset('invalid')
                    self._identity = identity
                    self._pixels = packet[HEADER.size:]
                    self._captured_ns = captured
            finally:
                os.close(fd)
        except FileNotFoundError:
            return self._reset('missing')
        except (OSError, ValueError, struct.error):
            return self._reset('unavailable')
        now = time.monotonic_ns() if now_ns is None else now_ns
        age = now - self._captured_ns
        if self._captured_ns <= 0 or age < 0 or age >= self.ttl_ns:
            # Cache even expired snapshots: do not reread megabytes each tick.
            self.state = 'stale'
            return self.transparent
        self.state = 'fresh'
        return self._pixels


class OverlayPipe:
    """Own a separate FFmpeg pipe; stdin remains available for graceful 'q'.

    The reader never waits for the renderer. A daemon sends transparent RGBA at
    the requested cadence when snapshots are absent or expired. Writes use
    bounded polling so closing an unconsumed/full pipe cannot hang shutdown.
    Call start() only AFTER passing read_fd to Popen(pass_fds=(read_fd,)).
    """
    def __init__(self, reader: SnapshotReader, fps: int,
                 *, clock: Callable[[], float] = time.monotonic):
        if type(fps) is not int or not 1 <= fps <= 60:
            raise ValueError('invalid overlay framerate')
        self.reader, self.fps, self.clock = reader, fps, clock
        self.read_fd, self._write_fd = os.pipe()
        os.set_blocking(self._write_fd, False)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = 'prepared'
        self.frames_sent = 0

    def start(self) -> None:
        if self._thread is not None or self._stop.is_set():
            raise RuntimeError('overlay pipe cannot be restarted')
        os.close(self.read_fd)
        self.read_fd = -1
        self._thread = threading.Thread(target=self._run, name='twica-overlay-feed', daemon=True)
        self._thread.start()

    def _run(self) -> None:
        due = self.clock()
        try:
            while not self._stop.is_set():
                if self._stop.wait(max(0, due - self.clock())):
                    break
                try:
                    frame = self.reader.read()
                except Exception:
                    # A failed sampler must never leave framesync waiting.
                    frame = self.reader.transparent
                    self.reader.state = 'unavailable'
                view = memoryview(frame)
                while view and not self._stop.is_set():
                    if not select.select([], [self._write_fd], [], 0.05)[1]:
                        continue
                    try:
                        count = os.write(self._write_fd, view[:65536])
                    except BlockingIOError:
                        continue
                    view = view[count:]
                if not view:
                    self.frames_sent += 1
                self.state = self.reader.state
                # Do not replay a burst of stale frames after backpressure.
                due = max(due + 1 / self.fps, self.clock())
        except (BrokenPipeError, OSError):
            self.state = 'closed'
        finally:
            os.close(self._write_fd)
            self._write_fd = -1

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise RuntimeError('overlay feeder did not stop')
        else:
            for name in ('read_fd', '_write_fd'):
                fd = getattr(self, name)
                if fd >= 0:
                    os.close(fd)
                    setattr(self, name, -1)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def ffmpeg_input_args(fd: int, width: int, height: int, fps: int,
                      *, sync_to: int | None = None) -> list[str]:
    """Supply RGBA on an inherited FD, never on FFmpeg's control stdin."""
    frame_size(width, height)
    if type(fd) is not int or fd < 3 or type(fps) is not int or not 1 <= fps <= 60:
        raise ValueError('invalid overlay input')
    args = ['-thread_queue_size', '2']
    if sync_to is not None:
        if type(sync_to) is not int or sync_to < 0:
            raise ValueError('invalid overlay clock reference')
        args += ['-use_wallclock_as_timestamps', '1', '-isync', str(sync_to)]
    return args + ['-f', 'rawvideo', '-pixel_format', 'rgba', '-video_size',
                   f'{width}x{height}', '-framerate', str(fps), '-i', f'pipe:{fd}']


def ffmpeg_overlay_filter(base_input: int, overlay_input: int) -> str:
    """Shift the original viewport right by 1/3; preserve Y, scale and alpha."""
    if any(type(i) is not int or i < 0 for i in (base_input, overlay_input)):
        raise ValueError('invalid overlay stream index')
    if base_input == overlay_input:
        raise ValueError('overlay requires distinct inputs')
    return (f'[{base_input}:v:0][{overlay_input}:v:0]'
            'overlay=x=round(main_w/3):y=0:format=auto:alpha=straight:'
            'eof_action=pass:repeatlast=0')
