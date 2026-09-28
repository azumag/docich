"""Read-only, on-demand capture of an explicitly approved program X11 surface.

The direct-stream encoder captures the same configured X11 rectangle. This is
NOT a desktop fallback, an OBS screenshot, or a proof of downstream delivery.
No game adapter is imported and no renderer/encoder is started or restarted.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
import threading
from dataclasses import dataclass, field

from docich.llm.images import ImageAttachment

MAX_STATE_BYTES = 65536
MAX_CAPTURE_SECONDS = 0.5
MAX_FRAME_AGE_SECONDS = 5.0
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def _json(raw: bytes):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("invalid_state")
            out[key] = value
        return out
    def invalid(_):
        raise ValueError("invalid_state")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def read_scene(path: Path) -> tuple[str, int, str, int]:
    """Small read-only projection of the canonical game_switch schema v2."""
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("scene_unavailable")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATE_BYTES:
            raise ValueError("scene_unavailable")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_STATE_BYTES + 1)
        if len(raw) > MAX_STATE_BYTES:
            raise ValueError("scene_unavailable")
    finally:
        os.close(fd)
    state = _json(raw)
    required = {"schema_version", "phase", "revision", "active", "candidate",
                "previous", "operation", "request_id"}
    if (type(state) is not dict or not required <= state.keys()
            or type(state.get("schema_version")) is not int
            or state["schema_version"] != 2 or state.get("phase") != "ready"
            or state.get("candidate") is not None or state.get("previous") is not None
            or state.get("operation") is not None or state.get("request_id") is not None):
        raise ValueError("scene_unavailable")
    active = state.get("active")
    revision = state.get("revision")
    if type(active) is not dict or type(revision) is not int or revision < 0:
        raise ValueError("scene_unavailable")
    game, generation, runtime = active.get("game"), active.get("generation"), active.get("runtime_id")
    if (type(game) is not str or not TOKEN.fullmatch(game)
            or type(runtime) is not str or not TOKEN.fullmatch(runtime)
            or type(generation) is not int or generation < 1):
        raise ValueError("scene_unavailable")
    return game, generation, runtime, revision


@dataclass(frozen=True)
class CaptureConfig:
    display: str
    width: int
    height: int
    scene_path: Path

    def __post_init__(self):
        if type(self.display) is not str or not re.fullmatch(r":[0-9]{1,5}(?:\.[0-9]{1,2})?", self.display):
            raise ValueError("invalid_capture_config")
        if (type(self.width) is not int or not 320 <= self.width <= 3840
                or type(self.height) is not int or not 180 <= self.height <= 2160):
            raise ValueError("invalid_capture_config")
        if not self.scene_path.is_absolute():
            raise ValueError("invalid_capture_config")

    @classmethod
    def from_env(cls, env):
        if (env.get("COMMENT_SCREEN_SOURCE") != "direct_x11"
                or env.get("COMMENT_SCREEN_CAPTURE_APPROVED") != "1"):
            raise ValueError("capture_not_configured")
        display, size = env.get("COMMENT_SCREEN_DISPLAY"), env.get("COMMENT_SCREEN_SIZE")
        # No implicit DISPLAY/default desktop. The operator confirms that these
        # match the effective encoder configuration before enabling capture.
        if (display != env.get("SOREN_DIRECT_STREAM_DISPLAY")
                or size != env.get("SOREN_DIRECT_STREAM_SIZE")):
            raise ValueError("capture_source_mismatch")
        if type(size) is not str or not re.fullmatch(r"[0-9]{3,4}x[0-9]{3,4}", size):
            raise ValueError("invalid_capture_config")
        width, height = map(int, size.split("x"))
        scene = env.get("COMMENT_SCREEN_SCENE_FILE", "")
        if type(scene) is not str or not scene:
            raise ValueError("invalid_capture_config")
        return cls(display, width, height, Path(scene))

    @property
    def output_size(self):
        ratio = min(1.0, 1280 / max(self.width, self.height))
        return max(1, int(self.width * ratio)), max(1, int(self.height * ratio))

    def command(self):
        width, height = self.output_size
        return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-f", "x11grab", "-draw_mouse", "0", "-framerate", "30",
                "-video_size", f"{self.width}x{self.height}", "-i", f"{self.display}+0,0",
                "-frames:v", "1", "-an", "-vf", f"scale={width}:{height}",
                "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]


def bounded_capture(argv: list[str], max_bytes: int, timeout: float) -> bytes:
    """Bound stdout and wall time, and reap the detached capture process group."""
    if (os.name != "posix" or threading.current_thread() is not threading.main_thread()
            or type(max_bytes) is not int or not 0 < max_bytes <= 1280 * 1280 * 3
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= MAX_CAPTURE_SECONDS):
        raise ValueError("capture_unavailable")
    deadline = time.monotonic() + timeout
    process = None
    output = bytearray()
    handlers = {}
    cancelled = None

    def cancel(signum, _frame):
        nonlocal cancelled
        cancelled = signum

    try:
        # Defer TERM/INT during spawn/cleanup so the detached capture is reaped.
        for signum in (signal.SIGTERM, signal.SIGINT):
            handlers[signum] = signal.signal(signum, cancel)
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if cancelled is not None:
                    raise SystemExit(128 + cancelled)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("capture_timeout")
                if not selector.select(min(remaining, 0.05)):
                    continue
                block = os.read(process.stdout.fileno(), min(65536, max_bytes + 1 - len(output)))
                if not block:
                    break
                output.extend(block)
                if len(output) > max_bytes:
                    raise ValueError("capture_oversize")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("capture_timeout")
        try:
            rc = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise ValueError("capture_timeout") from None
        if rc or len(output) != max_bytes:
            raise ValueError("capture_failed")
        return bytes(output)
    finally:
        try:
            # Also kill descendants retaining pipe fds after their parent exits.
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                if process.stdout is not None:
                    process.stdout.close()
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
        if cancelled is not None:
            raise SystemExit(128 + cancelled)


@dataclass(frozen=True)
class ScreenFrame:
    image: ImageAttachment = field(repr=False)
    scene: tuple = field(repr=False)
    captured_at: float
    capture_started_monotonic: float
    source_kind: str = "program_x11_preencode"


class ScreenContextProvider:
    def __init__(self, config: CaptureConfig, *, reader=None, capture=bounded_capture,
                 clock=time.monotonic, wall=time.time):
        self.config = config
        self.reader = reader or (lambda: read_scene(config.scene_path))
        self.capture = capture
        self.clock, self.wall = clock, wall

    def take(self, *, budget=MAX_CAPTURE_SECONDS) -> ScreenFrame:
        if not 0 < budget <= MAX_CAPTURE_SECONDS:
            raise ValueError("capture_timeout")
        started = self.clock()
        scene = self.reader()
        width, height = self.config.output_size
        remaining = budget - (self.clock() - started)
        if remaining <= 0:
            raise ValueError("capture_timeout")
        raw = self.capture(self.config.command(), width * height * 3, remaining)
        acquired_at = self.wall()
        image = ImageAttachment.from_rgb(raw, width, height)
        if self.clock() - started > budget:
            raise ValueError("capture_timeout")
        if self.reader() != scene:
            raise ValueError("scene_changed")
        return ScreenFrame(image, scene, acquired_at, started)

    def current(self, frame: ScreenFrame) -> bool:
        try:
            age = self.clock() - frame.capture_started_monotonic
            return (0 <= age <= MAX_FRAME_AGE_SECONDS and self.reader() == frame.scene)
        except Exception:
            return False
