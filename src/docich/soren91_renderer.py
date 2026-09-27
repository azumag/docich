"""Soren91 (Meriken) remote-renderer host selection.

The Meriken corner renders on a remote host that runs the soviet_now local
agent (``/v1/status|start|stop`` + a Tailscale-only CDP proxy).  Two hosts
exist: the wired Windows desktop (primary) and the Mac mini (failover).

* the *policy* (``soren91_renderer.json`` in the state dir) is the operator's
  choice, written by the webui: ``auto`` tries Windows then Mac, ``windows`` /
  ``mac`` pin one host.  A missing or unreadable policy means ``auto``.
* the *selection* (``soren91_renderer_active.json``) records which host the
  current runtime actually started on.  The coordinator drives start, stop
  and liveness through different adapter instances (and the manual stop is a
  separate process), so the chosen host must be durable, keyed by runtime id.

Neither file ever holds URLs or tokens: those stay in the runtime-scoped env.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

POLICY_FILE = "soren91_renderer.json"
SELECTION_FILE = "soren91_renderer_active.json"

HOSTS = ("windows", "mac")
HOST_LABELS = {"windows": "Windows", "mac": "Mac"}
MODES = {
    "auto": ("windows", "mac"),
    "windows": ("windows",),
    "mac": ("mac",),
}
DEFAULT_MODE = "auto"


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_mode(state_dir) -> str:
    data = _load(Path(state_dir) / POLICY_FILE)
    mode = data.get("mode") if data else None
    return mode if mode in MODES else DEFAULT_MODE


def save_mode(state_dir, mode: str) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    data = {"mode": mode, "updated_at": time.time()}
    _write_atomic(Path(state_dir) / POLICY_FILE, data)
    return data


def host_order(state_dir) -> tuple[str, ...]:
    return MODES[load_mode(state_dir)]


def load_selection(state_dir) -> dict | None:
    data = _load(Path(state_dir) / SELECTION_FILE)
    if not data or data.get("host") not in HOSTS:
        return None
    return data


def selected_host(state_dir, runtime_id: str) -> str | None:
    data = load_selection(state_dir)
    if data and runtime_id and data.get("runtime_id") == runtime_id:
        return data["host"]
    return None


def record_selection(state_dir, runtime_id: str, host: str, attempts: list[dict]) -> None:
    if host not in HOSTS:
        raise ValueError("unknown renderer host")
    _write_atomic(
        Path(state_dir) / SELECTION_FILE,
        {
            "runtime_id": runtime_id,
            "host": host,
            "selected_at": time.time(),
            "attempts": attempts,
        },
    )
