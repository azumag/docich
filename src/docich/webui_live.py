"""Read-only LIVE snapshot for the WebUI and the owner's one LIVE mutation.

The WebUI runs on the same host as the LIVE worker and already has the LIVE state dir as
``g.state_dir``.  This module joins three allow-listed sources:

* the owner's control file, through :func:`control.describe_control` (never the raw bytes),
* ``live_status.json``, through the worker's own key allow-list and a size cap,
* the LIVE ledger, opened read-only (the same way ``live_owner/cli.py status`` does).

``live_snapshot`` never raises and never includes a path, file content, order/fill id,
API key or an unwatched asset's balance.  ``set_control`` is the only writer here; it
validates the switch first and writes the one control file through ``control.write_control``.
"""
from __future__ import annotations

import json
import math
import os
import stat

from .trading.live import control
from .trading.live.ledger import LiveLedger
from .trading.live.overview import live_overview
from .trading.live.paths import live_layout
from .trading.live_worker.status import ALLOWED_KEYS, status_path

MAX_STATUS_BYTES = 64 * 1024
# The runner's cycle interval is not part of the status allow-list, so the UI uses the
# bounded fallback required by the operator contract: 180 s (3 x the default 60 s cycle).
WORKER_STALE_AFTER_S = 180

_STATES = ("on", "off")
_MODES = ("shadow", "trade")
_TRADE_CONFIRM = "TRADE"
_MAX_UPDATED_BY = 80


def _clean_status_value(value: object) -> object:
    """Keep only the value shapes the worker status writer itself permits."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if type(value) in (int, float):              # bool is neither
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("status numbers must be finite")
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError("not an allow-listed status value")


def _read_status(state_dir) -> dict | None:
    """Read and filter ``live_status.json``; missing, oversized or malformed reads as None.

    No raw bytes escape: only ``ALLOWED_KEYS`` with simple JSON values survive.
    """
    try:
        path = status_path(state_dir)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except Exception:
        return None
    try:
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATUS_BYTES:
                return None
            raw = os.read(fd, MAX_STATUS_BYTES + 1)
        finally:
            os.close(fd)
    except OSError:
        return None
    if len(raw) > MAX_STATUS_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    view: dict = {}
    for key, value in data.items():
        if key not in ALLOWED_KEYS:
            continue
        try:
            view[key] = _clean_status_value(value)
        except ValueError:
            continue
    if "written_at" not in view:
        # Older/adjacent status producers called the same instant ``updated_at``; accept
        # it only as the age anchor, never as an extra exposed key.
        alias = data.get("updated_at")
        if type(alias) in (int, float):
            view["written_at"] = int(alias)
    return view


def _empty_overview() -> dict:
    return live_overview(None, now=0.0)


def _overview(state_dir, *, now: float, ledger_present: bool) -> dict:
    if not ledger_present:
        return _empty_overview()
    try:
        ledger = LiveLedger.open_readonly(live_layout(state_dir))
    except Exception:
        return _empty_overview()
    try:
        return live_overview(ledger, now=now)
    except Exception:                       # live_overview promises not to raise; belt and braces
        return _empty_overview()
    finally:
        try:
            ledger.close()
        except Exception:
            pass


def live_snapshot(state_dir, *, now: float) -> dict:
    """Return the WEBUI-facing LIVE snapshot.  Read-only and never raises.

    ``worker_alive`` is true when the status file's write time is younger than
    ``WORKER_STALE_AFTER_S`` (the cycle interval is not allow-listed).  A missing ledger
    or status file is reported as ``ledger_present: false`` / ``status: null``; the
    control file missing or invalid is reported by ``describe_control`` as ``unknown``.
    """
    try:
        control_view = control.describe_control(state_dir, now=now)
    except Exception:                       # describe_control promises not to raise either
        control_view = {"state": "unknown", "mode": None, "updated_at": None,
                        "updated_by": None, "reasons": ["check_error"]}

    status = _read_status(state_dir)
    written_at = status.get("written_at") if isinstance(status, dict) else None
    alive = False
    if type(written_at) in (int, float):
        try:
            age = float(now) - float(written_at)
        except Exception:
            age = -1.0
        alive = 0 <= age < WORKER_STALE_AFTER_S

    try:
        ledger_path = live_layout(state_dir).ledger_path
        ledger_present = ledger_path.is_file() and not ledger_path.is_symlink()
    except Exception:
        ledger_present = False

    return {
        "control": control_view,
        "status": status,
        "worker_alive": alive,
        "ledger_present": ledger_present,
        "overview": _overview(state_dir, now=now, ledger_present=ledger_present),
    }


def set_control(state_dir, *, state: str, mode: str, confirm: str | None, actor: str,
                now: float) -> dict:
    """Validate and write the owner's switch; return ``describe_control`` after the write.

    OFF and shadow need no confirmation.  Turning the bot ON in ``trade`` mode (which is
    also how a running bot is moved from shadow to trade) requires ``confirm == "TRADE"``;
    otherwise ``ValueError("confirm_required")`` is raised before anything is written.
    """
    if state not in _STATES:
        raise ValueError("state must be 'on' or 'off'")
    if mode not in _MODES:
        raise ValueError("mode must be 'shadow' or 'trade'")
    if state == "on" and mode == "trade" and confirm != _TRADE_CONFIRM:
        raise ValueError("confirm_required")

    actor_text = str(actor or "unknown").strip() or "unknown"
    updated_by = f"webui:{actor_text}"[:_MAX_UPDATED_BY]
    control.write_control(state_dir, state=state, mode=mode,
                          updated_by=updated_by, now=now)
    return control.describe_control(state_dir, now=now)


__all__ = ["live_snapshot", "set_control"]
