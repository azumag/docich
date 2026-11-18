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
from .trading.live.cycle import next_wait
from .trading.live.ledger import LiveLedger
from .trading.live.overview import live_overview
from .trading.live.paths import live_layout
from .trading.live_worker.status import (
    ALLOWED_KEYS, DEFAULT_INTERVAL_MS, MAX_INTERVAL_MS, MIN_INTERVAL_MS, status_path,
)

MAX_STATUS_BYTES = 64 * 1024
# The runner writes one status per cycle, at the end of the cycle, and then sleeps for
# ``next_wait``.  New statuses carry the actual interval as whole ``interval_ms``; a clean
# running status is fresh for 3 x that interval.  A retrying one additionally gets the
# runner's bounded retry wait before it is considered stale.  Statuses written before
# ``interval_ms`` existed (or with a malformed one) fall back to the legacy 60 s / 180 s.
WORKER_CYCLE_INTERVAL_S = DEFAULT_INTERVAL_MS / 1000.0
WORKER_STALE_AFTER_S = 180                   # legacy statuses without interval_ms
# A status written by these phases describes a worker that is still on its way to its
# terminal state.  ``inert`` and ``exited`` are terminal even when freshly written.
WORKER_ACTIVE_PHASES = frozenset({"starting", "running", "stopping"})

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
            try:
                if math.isfinite(float(alias)):
                    view["written_at"] = int(alias)
            except (OverflowError, TypeError, ValueError):
                pass
    return view


def _status_interval_s(status: dict) -> float:
    """The configured cycle interval recorded by the runner, or the legacy 60 s.

    ``interval_ms`` is allow-listed as an int in the CLI's 30..3600 s range.  Anything
    else (an old status without the key, a float, an out-of-range value) falls back to the
    old 60 s base, keeping the legacy 180 s freshness bound.
    """
    value = status.get("interval_ms")
    if type(value) is int and MIN_INTERVAL_MS <= value <= MAX_INTERVAL_MS:
        return value / 1000.0
    return float(WORKER_CYCLE_INTERVAL_S)


def _worker_alive(status: dict | None, *, now: float) -> bool:
    """Whether ``status`` describes a worker that may still be running.

    Phase is part of the contract: a freshly written ``inert`` or ``exited`` status is a
    finished worker, not a live one.  A clean running status is fresh for 3 x its recorded
    interval; for ``running`` the runner's own bounded retry wait (derived from the
    allow-listed ``consecutive_failures``) extends that bound, because the status is written
    before that sleep.  A missing/future timestamp and an unknown phase stay false: those
    cannot prove liveness and must not hide a crash.
    """
    if not isinstance(status, dict):
        return False
    phase = status.get("phase")
    if not isinstance(phase, str) or phase not in WORKER_ACTIVE_PHASES:
        return False
    written_at = status.get("written_at")
    if type(written_at) not in (int, float):
        return False
    try:
        moment = float(written_at)
        current = float(now)
    except (OverflowError, TypeError, ValueError):
        return False
    if not math.isfinite(moment) or not math.isfinite(current):
        return False
    age = current - moment
    if age < 0:                              # a future timestamp is not fresh proof
        return False
    interval_s = _status_interval_s(status)
    stale_after = interval_s * 3.0
    if phase == "running":
        failures = status.get("consecutive_failures")
        if type(failures) is int and failures > 0:
            try:
                retry_wait = float(next_wait(interval_s, failures))
            except (OverflowError, TypeError, ValueError):
                retry_wait = 0.0
            if math.isfinite(retry_wait) and retry_wait > 0:
                stale_after += retry_wait
    return age < stale_after


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

    ``worker_alive`` is true only for an active phase (``starting``, ``running`` or
    ``stopping``) whose ``written_at`` is not in the future and is younger than the
    phase's bound: 3 x the status's ``interval_ms`` (legacy statuses without it keep the
    old 60 s / 180 s bound), plus the runner's bounded retry wait for ``running``
    (derived from the allow-listed ``consecutive_failures``).  Terminal ``inert`` and
    ``exited`` statuses are never alive, however recently they were written.  A missing
    ledger or status file is reported as ``ledger_present: false`` / ``status: null``;
    the control file missing or invalid is reported by ``describe_control`` as
    ``unknown``.
    """
    try:
        control_view = control.describe_control(state_dir, now=now)
    except Exception:                       # describe_control promises not to raise either
        control_view = {"state": "unknown", "mode": None, "updated_at": None,
                        "updated_by": None, "reasons": ["check_error"]}

    status = _read_status(state_dir)
    alive = _worker_alive(status, now=now)

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
