"""Real PAPER dashboard presenter in an isolated broadcast display.

Runs the production presenter path for the PAPER corner (``paper-view``) with no
market provider, no credentials and no production access:

    bin/docich ... trading dashboard-server    (loopback JSON server, fixture data)
        -> presentation.py -> chromium --app    (native viewer on a private Xvfb)
            -> ffplay projection                (contained into the display viewport)

Assertions read the **final X11 frame** of the broadcast display, not the
adapter's internal state, and the dashboard data comes from a fixed fixture so
the result is deterministic.  This is the PAPER piece of azumag/docich#1236
acceptance item 4 (see azumag/docich#2400).

Guarded by ``DOCICH_PAPER_VIEW_TESTS=1`` because it needs a real Chromium-family
browser, Xvfb, ffplay, ffmpeg and xdotool.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DOCICH_PAPER_VIEW_TESTS") != "1",
    reason="isolated real Chromium presenter CI")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.adapters import make_coordinator_adapter, program  # noqa: E402
from docich.game_switch import RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402

DISPLAY_WIDTH, DISPLAY_HEIGHT = 1280, 720
VIEWPORT = (0, 90, 960, 540)
#: Sentinel that only the fixture can put on the page.
SYMBOL = "ZXQAFIX"

CONFIG = f"""
[display]
number = 96
width = {DISPLAY_WIDTH}
height = {DISPLAY_HEIGHT}

[audio]
enabled = false

[paper_corner]
dashboard = "html"
dashboard_port = {{port}}
"""

FIXTURE_STATUS = {
    "open_positions": {SYMBOL: "2.5"},
    "recent_fills": [],
    "capital_reference": "100000",
    "snapshot_generated_at": 1790000000.0,
    "signal_summary": {"candidate_count": 3, "candidate_reason_codes": []},
}
FIXTURE_CACHE = {"symbols": {SYMBOL: {"closes": [100.0, 101.0, 102.0]}}}


def _require(binary: str) -> str:
    found = shutil.which(binary)
    if not found:
        pytest.skip(f"{binary} is required for the isolated PAPER presenter test")
    return found


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _global(tmp_path, port: int):
    from types import SimpleNamespace

    config = tmp_path / "docich.toml"
    config.write_text(CONFIG.format(port=port), encoding="utf-8")
    state = tmp_path / "run"
    trading = state / "trading"
    trading.mkdir(parents=True, exist_ok=True)
    (trading / "status.json").write_text(json.dumps(FIXTURE_STATUS), encoding="utf-8")
    (trading / "market_cache.json").write_text(json.dumps(FIXTURE_CACHE), encoding="utf-8")
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


def _spec(tmp_path, runtime_id="g4-paper001"):
    names = runtime_names(4)
    return RuntimeSpec(
        game="paper-view", adapter="program", generation=4,
        runtime_id=runtime_id, lease_id="00000000-0000-4000-8000-000000000000",
        runtime_dir=tmp_path / "run" / "runtimes" / runtime_id,
        game_window=names.game_window, agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _start_display():
    """Start the broadcast display and return (process, ':N')."""
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


def _wait_api(url: str, deadline: float) -> str:
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 300:
                    return response.read().decode("utf-8")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
        time.sleep(0.25)
    raise AssertionError(f"dashboard server did not answer {url}: {last}")


def _tail(path: Path, lines: int = 200) -> str:
    """Bounded presenter log excerpt: the head usually carries the real error."""
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


def _capture(display: str, tmp_path: Path) -> tuple[bytes, int, int]:
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


def test_paper_view_renders_the_dashboard_fixture_inside_the_viewport(tmp_path):
    for binary in ("Xvfb", "ffmpeg", "ffplay", "xdotool", "xterm"):
        _require(binary)
    if not program._browser_bin():
        pytest.skip("a Chromium-family browser is required for the PAPER dashboard view")

    port = _free_port()
    g = _global(tmp_path, port)
    adapter = make_coordinator_adapter(g, _spec(tmp_path))
    assert adapter.name == "program", "paper-view must resolve to the program view adapter"

    display_process, display = _start_display()
    g.display.name = display
    api_url = f"http://127.0.0.1:{port}/api/trading/dashboard"
    log_path = tmp_path / "presenter.log"
    logs = log_path.open("wb")
    server = viewer = None
    try:
        server = subprocess.Popen(adapter._game_command(), stdout=logs, stderr=logs)
        body = _wait_api(api_url, time.monotonic() + 30)
        # The fixture reached the page: the sentinel only exists in our files.
        assert SYMBOL in body, "the dashboard server did not render the fixture"

        viewer_command = adapter._viewer_command()
        browser_command = viewer_command[viewer_command.index("--") + 1:]

        # Production reuses /tmp/docich-paper-view-<port>, so its Chromium
        # profile starts warm. Warm the same profile here too: a cold Chromium
        # start on the runner can outlast presentation.py's 10s viewer window,
        # and the production browser command must not be widened for CI.
        warm = subprocess.Popen(browser_command, env=dict(os.environ, DISPLAY=display),
                               stdout=logs, stderr=logs)
        warm_deadline = time.monotonic() + 90
        while time.monotonic() < warm_deadline and warm.poll() is None:
            found = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--pid", str(warm.pid)],
                env=dict(os.environ, DISPLAY=display),
                capture_output=True, text=True, timeout=5)
            if [item for item in found.stdout.split() if item.isdigit()]:
                break
            time.sleep(0.25)
        if warm.poll() is None:
            warm.terminate()
            try:
                warm.wait(timeout=30)
            except subprocess.TimeoutExpired:
                warm.kill()
                warm.wait()

        viewer = subprocess.Popen(viewer_command, stdout=logs, stderr=logs)
        pattern = f"docich-present-{adapter.spec.runtime_id}"
        window_id = _wait_window(display, pattern, time.monotonic() + 60, viewer, log_path)
        geometry = _geometry(display, window_id)
        assert (geometry.get("X"), geometry.get("Y")) == (VIEWPORT[0], VIEWPORT[1]), geometry
        assert (geometry.get("WIDTH"), geometry.get("HEIGHT")) == (VIEWPORT[2], VIEWPORT[3]), geometry

        # The dashboard page paints its canvas after the window appears, so poll
        # the final frame instead of trusting one fixed delay: a single 2s wait
        # captured an all-white page on a slower runner.
        deadline = time.monotonic() + 30
        frame = width = height = None
        inside: set[tuple[int, int, int]] = set()
        while time.monotonic() < deadline:
            time.sleep(1)
            frame, width, height = _capture(display, tmp_path)
            inside = {
                _pixel(frame, width, x, y)
                for x in range(VIEWPORT[0] + 20, VIEWPORT[0] + VIEWPORT[2] - 20, 7)
                for y in range(VIEWPORT[1] + 20, VIEWPORT[1] + VIEWPORT[3] - 20, 7)
            }
            if len(inside) >= 8:
                break
        assert frame is not None
        assert len(inside) >= 8, f"viewport looks blank: {sorted(inside)[:5]}"
        assert any(pixel != (0, 0, 0) for pixel in inside), "viewport is entirely black"

        # The presenter must not paint outside its viewport: the rails stay black.
        for x, y in ((1200, 20), (1200, 700), (1000, 360), (10, 20), (10, 700)):
            assert _pixel(frame, width, x, y) == (0, 0, 0), f"painted outside viewport at {(x, y)}"
    finally:
        for child in (viewer, server):
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        logs.close()
        display_process.terminate()
        try:
            display_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            display_process.kill()
            display_process.wait()
