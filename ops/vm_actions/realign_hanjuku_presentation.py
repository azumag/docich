#!/usr/bin/env python3
"""One-shot live realignment of the active Hanjuku projection.

Moves only the already-running ffplay projection window.  RetroArch, the
private X server, the agent, audio and the stream are never restarted.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import tomllib

ROOT = Path("/home/ubuntu/docich")
STATE_DIR = ROOT / "run-soren-live"
CONFIG = ROOT / "config/docich.soren-live.toml"
RUNTIME_RE = re.compile(r"g([1-9][0-9]*)-([0-9a-f]{6,32})\Z")
MAX_STATE = 262144

EXIT_NOT_ACTIVE = 20
EXIT_INVALID_STATE = 21
EXIT_UNSUPPORTED_ALIGNMENT = 22
EXIT_WINDOW_UNAVAILABLE = 23
EXIT_GEOMETRY_MISMATCH = 24
EXIT_CONTEXT_CHANGED = 25
EXIT_MOVE_FAILED = 26
EXIT_WRITE_FAILED = 27


class RealignError(RuntimeError):
    def __init__(self, code: int):
        super().__init__("Hanjuku presentation realignment refused")
        self.code = code


def _read_json(path: Path) -> tuple[dict, bytes]:
    if path.is_symlink() or not path.is_file():
        raise RealignError(EXIT_INVALID_STATE)
    size = path.stat().st_size
    if size <= 0 or size > MAX_STATE:
        raise RealignError(EXIT_INVALID_STATE)
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RealignError(EXIT_INVALID_STATE)
    if not isinstance(value, dict):
        raise RealignError(EXIT_INVALID_STATE)
    return value, raw


def _layout(root: Path) -> tuple[str, list[int]]:
    path = root / "config/docich.soren-live.toml"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATE:
        raise RealignError(EXIT_INVALID_STATE)
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        raise RealignError(EXIT_INVALID_STATE)
    display = data.get("display")
    if not isinstance(display, dict):
        raise RealignError(EXIT_INVALID_STATE)
    number = display.get("number")
    viewport = [display.get(key) for key in
                ("viewport_x", "viewport_y", "viewport_width", "viewport_height")]
    if (type(number) is not int or number < 0 or number > 999
            or any(type(value) is not int for value in viewport)
            or viewport[0] < 0 or viewport[1] < 0
            or viewport[2] <= 0 or viewport[3] <= 0):
        raise RealignError(EXIT_INVALID_STATE)
    return f":{number}", viewport


def _active(state_dir: Path) -> tuple[dict, bytes]:
    canonical, raw = _read_json(state_dir / "game_switch.json")
    active = canonical.get("active")
    if canonical.get("phase") != "ready" or not isinstance(active, dict):
        raise RealignError(EXIT_NOT_ACTIVE)
    runtime_id = active.get("runtime_id")
    match = RUNTIME_RE.fullmatch(runtime_id or "")
    if (active.get("game") != "hanjuku-hero" or match is None
            or type(active.get("generation")) is not int
            or active["generation"] != int(match.group(1))
            or not isinstance(active.get("lease_id"), str) or not active["lease_id"]):
        raise RealignError(EXIT_NOT_ACTIVE)
    return active, raw


def _window_ids(display: str, title: str) -> list[str]:
    env = dict(os.environ, DISPLAY=display)
    result = subprocess.run(
        ["xdotool", "search", "--onlyvisible", "--name", f"^{re.escape(title)}$"],
        env=env, capture_output=True, text=True, timeout=3,
    )
    if result.returncode not in (0, 1):
        raise RealignError(EXIT_WINDOW_UNAVAILABLE)
    return [line.strip() for line in result.stdout.splitlines()
            if re.fullmatch(r"[1-9][0-9]*", line.strip())]


def _geometry(display: str, window_id: str) -> dict[str, int]:
    env = dict(os.environ, DISPLAY=display)
    try:
        output = subprocess.check_output(
            ["xdotool", "getwindowgeometry", "--shell", window_id],
            env=env, text=True, timeout=3,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise RealignError(EXIT_WINDOW_UNAVAILABLE)
    values = {}
    for key, value in re.findall(r"^(X|Y|WIDTH|HEIGHT)=(-?[0-9]+)$", output, re.M):
        values[key] = int(value)
    if set(values) != {"X", "Y", "WIDTH", "HEIGHT"}:
        raise RealignError(EXIT_WINDOW_UNAVAILABLE)
    return values


def _move(display: str, window_id: str, x: int, y: int) -> None:
    env = dict(os.environ, DISPLAY=display)
    try:
        subprocess.run(
            ["xdotool", "windowmove", "--sync", window_id, str(x), str(y)],
            env=env, check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=3,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise RealignError(EXIT_MOVE_FAILED)


def _atomic_json(path: Path, value: dict) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".presentation-realign-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def realign(root: Path = ROOT) -> dict[str, object]:
    state_dir = root / "run-soren-live"
    output_display, expected_viewport = _layout(root)
    active, canonical_raw = _active(state_dir)
    runtime_id = active["runtime_id"]
    runtime = state_dir / "runtimes" / runtime_id
    if runtime.is_symlink() or not runtime.is_dir():
        raise RealignError(EXIT_INVALID_STATE)
    state_path = runtime / "presentation.json"
    state, presentation_raw = _read_json(state_path)
    projection = state.get("projection")
    if state.get("status") != "ready" or not isinstance(projection, dict):
        raise RealignError(EXIT_INVALID_STATE)
    viewport = projection.get("viewport")
    content = projection.get("content")
    if (viewport != expected_viewport
            or not isinstance(content, list) or len(content) != 4
            or any(type(value) is not int for value in content)):
        raise RealignError(EXIT_INVALID_STATE)

    vx, vy, vw, vh = viewport
    cx, cy, cw, ch = content
    gap = vw - cw
    if (cw <= 0 or ch <= 0 or cw > vw or ch > vh
            or cy < 0 or cy + ch > vh or gap < 180):
        raise RealignError(EXIT_INVALID_STATE)

    align = projection.get("align")
    expected_cx = 0 if align == "left" else gap if align == "right" else None
    if expected_cx is None or cx != expected_cx:
        raise RealignError(EXIT_UNSUPPORTED_ALIGNMENT)

    title = f"docich-present-{runtime_id}"
    windows = _window_ids(output_display, title)
    if len(windows) != 1:
        raise RealignError(EXIT_WINDOW_UNAVAILABLE)
    window_id = windows[0]
    geometry = _geometry(output_display, window_id)
    expected_x = vx + cx
    if geometry != {"X": expected_x, "Y": vy, "WIDTH": cw, "HEIGHT": vh}:
        raise RealignError(EXIT_GEOMETRY_MISMATCH)
    if align == "right":
        return {"status": "already-right", "runtime_id": runtime_id, "gap_width": gap}

    new_x = vx + gap
    _move(output_display, window_id, new_x, vy)
    moved = True
    updated = None
    state_written = False
    try:
        if _geometry(output_display, window_id) != {
                "X": new_x, "Y": vy, "WIDTH": cw, "HEIGHT": vh}:
            raise RealignError(EXIT_GEOMETRY_MISMATCH)

        latest_active, latest_canonical_raw = _active(state_dir)
        latest_state, latest_presentation_raw = _read_json(state_path)
        identity_keys = ("game", "runtime_id", "generation", "lease_id")
        if (any(latest_active.get(key) != active.get(key) for key in identity_keys)
                or latest_canonical_raw != canonical_raw
                or latest_presentation_raw != presentation_raw
                or latest_state != state):
            raise RealignError(EXIT_CONTEXT_CHANGED)

        updated = copy.deepcopy(state)
        updated_projection = dict(projection)
        updated_projection["align"] = "right"
        updated_projection["content"] = [gap, cy, cw, ch]
        updated["projection"] = updated_projection
        try:
            _atomic_json(state_path, updated)
            state_written = True
        except OSError:
            raise RealignError(EXIT_WRITE_FAILED)

        verified, _ = _read_json(state_path)
        if verified.get("projection") != updated_projection:
            raise RealignError(EXIT_WRITE_FAILED)
        return {"status": "realigned", "runtime_id": runtime_id, "gap_width": gap}
    except Exception:
        if state_written and updated is not None:
            try:
                current, _ = _read_json(state_path)
                if current == updated:
                    _atomic_json(state_path, state)
            except Exception:
                pass
        if moved:
            try:
                _move(output_display, window_id, expected_x, vy)
            except Exception:
                pass
        raise


def main() -> int:
    try:
        result = realign()
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except RealignError as exc:
        return exc.code
    except Exception:
        return EXIT_INVALID_STATE


if __name__ == "__main__":
    raise SystemExit(main())
