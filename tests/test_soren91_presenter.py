"""Real Soren91 (Meriken) remote-renderer presenter in an isolated display.

Soren91 renders nothing locally.  The corner owns an ffplay **SRT listener**
and a remote renderer host dials in as the SRT caller; a stub-driven bot would
normally navigate that remote browser over CDP.  This test reproduces the
presenter shape with no remote host, no Tailscale peer and no credential:

    stub renderer agent (HTTP /v1/status on a 100.64.0.0/10 address)
        -> ffmpeg testsrc --srt caller--> ffplay listener   (the owned viewer)
            -> presentation.py projection                   (contained in the viewport)

The 100.64.0.0/10 address is bound on the loopback interface by the CI job, so
``is_tailscale_ipv4`` validates exactly what production validates.
``agent.enabled`` is false, which ``config/games/soren91.toml`` explicitly
allows for display-only verification ("表示切替だけを試す検証では一時的に false
へ戻してよい"), so no Node bot, CDP proxy or remote browser is involved.

Assertions read the final X11 frame of the broadcast display.  Part of
azumag/docich#2400 (parent azumag/docich#1236 acceptance item 4).

Guarded by ``DOCICH_SOREN91_PRESENTER_TESTS=1``.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DOCICH_SOREN91_PRESENTER_TESTS") != "1",
    reason="isolated SRT presenter CI")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.adapters.soren91 import (  # noqa: E402
    DEFAULT_OCI_TAILSCALE_IP_ENV,
    Soren91CoordinatorAdapter,
)
from docich.adapters.soren91 import DEFAULT_AGENT_BASE_URL_ENV, DEFAULT_AGENT_TOKEN_ENV  # noqa: E402
from docich.config import GameAgentConfig, GameConfig, GameLifecycleConfig  # noqa: E402
from docich.game_switch import RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402

DISPLAY_WIDTH, DISPLAY_HEIGHT = 1280, 720
VIEWPORT = (0, 90, 960, 540)
#: Address the CI job binds on lo; is_tailscale_ipv4 accepts 100.64.0.0/10.
TAILSCALE_IP = "100.64.0.1"
#: Fixed, obviously-fake bearer token. Never a real credential.
STUB_TOKEN = "ci-soren91-stub-token"


def _require(binary: str) -> str:
    found = shutil.which(binary)
    if not found:
        pytest.skip(f"{binary} is required for the isolated Soren91 presenter test")
    return found


def _require_srt() -> None:
    result = subprocess.run([_require("ffplay"), "-protocols"],
                            capture_output=True, text=True, timeout=30)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "srt" not in output.lower():
        pytest.skip("ffplay was not built with SRT support")


def _require_loopback_address() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((TAILSCALE_IP, 0))
    except OSError as exc:
        pytest.skip(f"{TAILSCALE_IP} is not bound on this host: {exc}")
    finally:
        probe.close()


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((TAILSCALE_IP, 0))
        return int(sock.getsockname()[1])


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((TAILSCALE_IP, 0))
        return int(sock.getsockname()[1])


class _AgentHandler(BaseHTTPRequestHandler):
    def _reply(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.seen.append((self.command, self.path, self.headers.get("Authorization")))
        if self.path == "/v1/status":
            self._reply({"running": True, "captureSettings": {}})
        else:
            self._reply({}, 404)

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.seen.append((self.command, self.path, self.headers.get("Authorization")))
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path in ("/v1/start", "/v1/stop"):
            self._reply({"ok": True})
        else:
            self._reply({}, 404)

    def log_message(self, *args):  # keep pytest output clean
        pass


class _StubAgent(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address):
        self.seen: list[tuple[str, str, str | None]] = []
        super().__init__(address, _AgentHandler)


def _game_config(*, srt_port: int) -> GameConfig:
    raw = tomllib.loads((ROOT / "config" / "games" / "soren91.toml").read_text(encoding="utf-8"))
    section = raw.setdefault("soren91", {})
    section["srt_port"] = srt_port
    # CI has no PulseAudio sink and no Soren root: keep the presenter itself
    # under test, not the audio routing or the Twitch category sync.
    section["audio_sink"] = ""
    section["soren_root"] = ""
    section["games_dir"] = ""
    return GameConfig(
        name="soren91",
        title="Soren91",
        adapter="soren91",
        raw=raw,
        agent=GameAgentConfig(enabled=False),
        lifecycle=GameLifecycleConfig(require_round_boundary=False),
    )


def _global(tmp_path):
    from types import SimpleNamespace

    config = tmp_path / "docich.toml"
    config.write_text("[display]\nnumber = 96\n", encoding="utf-8")
    state = tmp_path / "run"
    state.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        state_dir=state,
        config_path=config,
        repo_root=ROOT,
        display=SimpleNamespace(
            name=":0", width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT,
            viewport_x=VIEWPORT[0], viewport_y=VIEWPORT[1],
            viewport_width=VIEWPORT[2], viewport_height=VIEWPORT[3],
        ),
    )


def _spec(tmp_path, runtime_id="g4-soren91x"):
    names = runtime_names(4)
    return RuntimeSpec(
        game="soren91", adapter="soren91", generation=4,
        runtime_id=runtime_id, lease_id="00000000-0000-4000-8000-000000000001",
        runtime_dir=tmp_path / "run" / "runtimes" / runtime_id,
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _start_display():
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(
        ["Xvfb", "-displayfd", str(write_fd), "-screen", "0",
         f"{DISPLAY_WIDTH}x{DISPLAY_HEIGHT}x24", "-nolisten", "tcp"],
        pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write_fd)
    try:
        assert select.select([read_fd], [], [], 10)[0], "Xvfb did not report a display"
        number = os.read(read_fd, 32).decode().strip()
        assert number.isdigit(), f"unexpected displayfd payload: {number!r}"
    finally:
        os.close(read_fd)
    return process, f":{number}"


def _caller_command(srt_port: int) -> list[str]:
    # A deterministic, colourful source: the final frame must not depend on any
    # remote renderer or game state.
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-re",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=15",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p", "-g", "30", "-c:a", "aac",
        "-f", "mpegts", f"srt://{TAILSCALE_IP}:{srt_port}?mode=caller",
    ]


def _tail(path: Path, lines: int = 200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"<no presenter log: {exc}>"
    if not text:
        return "<presenter log is empty>"
    if len(text) <= lines:
        return "\n".join(text)
    head, tail = text[:30], text[-(lines - 30):]
    return "\n".join(head + ["... <snip> ..."] + tail)


def _wait_window(display: str, pattern: str, deadline: float, viewer, log_path: Path) -> str:
    while time.monotonic() < deadline:
        if viewer.poll() is not None:
            raise AssertionError(
                f"presenter exited with rc={viewer.returncode} before the window appeared:\n"
                + _tail(log_path))
        found = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", pattern],
            env=dict(os.environ, DISPLAY=display),
            capture_output=True, text=True, timeout=5)
        ids = [line for line in found.stdout.split() if line.isdigit()]
        if ids:
            return ids[0]
        time.sleep(0.25)
    raise AssertionError(
        f"projection window {pattern!r} never appeared on {display}:\n" + _tail(log_path))


def _geometry(display: str, window_id: str) -> dict[str, int]:
    raw = subprocess.check_output(
        ["xdotool", "getwindowgeometry", "--shell", window_id],
        env=dict(os.environ, DISPLAY=display), text=True, timeout=5)
    out = {}
    for line in raw.splitlines():
        key, _, value = line.partition("=")
        if key in {"X", "Y", "WIDTH", "HEIGHT"} and value.isdigit():
            out[key] = int(value)
    return out


def _capture(display: str) -> tuple[bytes, int, int]:
    width, height = DISPLAY_WIDTH, DISPLAY_HEIGHT
    raw = subprocess.check_output([
        "ffmpeg", "-loglevel", "error", "-f", "x11grab", "-draw_mouse", "0",
        "-video_size", f"{width}x{height}", "-i", f"{display}.0+0,0",
        "-frames:v", "1", "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1",
    ], timeout=30)
    assert len(raw) == width * height * 4, f"unexpected frame size {len(raw)}"
    return raw, width, height


def _pixel(frame: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * width + x) * 4
    return frame[offset], frame[offset + 1], frame[offset + 2]


def test_soren91_presenter_contains_the_remote_srt_stream(tmp_path, monkeypatch):
    for binary in ("Xvfb", "ffmpeg", "ffplay", "xdotool"):
        _require(binary)
    _require_srt()
    _require_loopback_address()

    # CI has no audio device; the projection itself is video-only.
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    agent_port = _free_tcp_port()
    srt_port = _free_udp_port()
    monkeypatch.setenv(DEFAULT_AGENT_BASE_URL_ENV, f"http://{TAILSCALE_IP}:{agent_port}")
    monkeypatch.setenv(DEFAULT_AGENT_TOKEN_ENV, STUB_TOKEN)
    monkeypatch.setenv(DEFAULT_OCI_TAILSCALE_IP_ENV, TAILSCALE_IP)

    g = _global(tmp_path)
    spec = _spec(tmp_path)
    adapter = Soren91CoordinatorAdapter(g, _game_config(srt_port=srt_port), spec)
    # The URLs the remote host and the local viewer are handed must agree on the
    # Tailscale address and port that the harness actually binds.
    assert adapter.listener_srt_url() == f"srt://{TAILSCALE_IP}:{srt_port}?mode=listener"
    assert adapter.caller_srt_url() == f"srt://{TAILSCALE_IP}:{srt_port}?mode=caller"

    agent = _StubAgent((TAILSCALE_IP, agent_port))
    threading.Thread(target=agent.serve_forever, daemon=True).start()
    display_process, display = _start_display()
    g.display.name = display
    log_path = tmp_path / "presenter.log"
    logs = log_path.open("wb")
    viewer = caller = None
    try:
        viewer = subprocess.Popen(adapter._xterm_command(), stdout=logs, stderr=logs)

        # The real probes the corner runs before it commits to the switch.
        deadline = time.monotonic() + 60
        adapter._wait_listener(deadline, None)
        assert adapter._agent_running(deadline, None) is True
        assert agent.seen, "the stub renderer agent was never called"
        assert all(auth == f"Bearer {STUB_TOKEN}" for _m, _p, auth in agent.seen), agent.seen

        caller = subprocess.Popen(_caller_command(srt_port), stdout=logs, stderr=logs)
        pattern = f"^docich-present-{spec.runtime_id}$"
        window_id = _wait_window(display, pattern, time.monotonic() + 120, viewer, log_path)
        geometry = _geometry(display, window_id)
        assert (geometry.get("X"), geometry.get("Y")) == (VIEWPORT[0], VIEWPORT[1]), geometry
        assert (geometry.get("WIDTH"), geometry.get("HEIGHT")) == (VIEWPORT[2], VIEWPORT[3]), geometry

        # Let the contained projection receive enough SRT frames to paint.
        deadline = time.monotonic() + 20
        frame = width = height = None
        while time.monotonic() < deadline:
            time.sleep(1)
            frame, width, height = _capture(display)
            inside = {
                _pixel(frame, width, x, y)
                for x in range(VIEWPORT[0] + 20, VIEWPORT[0] + VIEWPORT[2] - 20, 7)
                for y in range(VIEWPORT[1] + 20, VIEWPORT[1] + VIEWPORT[3] - 20, 7)
            }
            if len(inside) >= 16:
                break
        assert frame is not None
        assert len(inside) >= 16, f"viewport looks blank: {sorted(inside)[:5]}"
        assert any(pixel != (0, 0, 0) for pixel in inside), "viewport is entirely black"

        # The presenter must not paint outside its viewport: the rails stay black.
        for x, y in ((1200, 20), (1200, 700), (1000, 360), (10, 20), (10, 700)):
            assert _pixel(frame, width, x, y) == (0, 0, 0), f"painted outside viewport at {(x, y)}"
    finally:
        for child in (caller, viewer):
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        agent.shutdown()
        agent.server_close()
        logs.close()
        display_process.terminate()
        try:
            display_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            display_process.kill()
            display_process.wait()
