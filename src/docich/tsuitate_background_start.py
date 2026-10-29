"""Start one Tsuitate beta match directly, without the broadcast corner plane.

This is the owner-operated background path.  It reads only the two reviewed
beta-control capability values from the existing WebUI env file, checks the
singleton status, and asks the Worker for at most one run for the current UTC
hour.  Re-running within the same hour is idempotent and never queues a second
match.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from .tsuitate_beta_control import CAPABILITY_ENV_KEYS, ControlError, call_beta_control

FORBIDDEN_SECRET_KEYS = ("DOCICH_WEBUI_TOKEN", "DOCICH_WEBUI_READONLY_TOKEN")
ENV_FILE = Path.home() / ".config" / "docich" / "webui.env"
ACTIVE_STATES = {"queued", "playing", "draining", "paused"}
STARTED_STATES = ACTIVE_STATES | {"finished"}


class BackgroundStartError(RuntimeError):
    pass


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    allowed = set(CAPABILITY_ENV_KEYS + FORBIDDEN_SECRET_KEYS)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BackgroundStartError("control_env_unavailable") from exc
    for line_number, line in enumerate(lines, 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("export "):
            raw = raw[7:].lstrip()
        key, separator, value = raw.partition("=")
        if not separator or key not in allowed:
            continue
        value = value.strip()
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise BackgroundStartError(f"control_env_invalid:{line_number}")
            value = value[1:-1]
        if value:
            values[key] = value
    return values


def _install_control_environment(path: Path = ENV_FILE) -> None:
    values = _read_env_file(path) if path.is_file() else {}
    controls = {key: value for key, value in values.items() if key in CAPABILITY_ENV_KEYS}
    for key in CAPABILITY_ENV_KEYS:
        if key not in controls and os.environ.get(key):
            controls[key] = os.environ[key]
    secret = controls.get("DOCICH_BETA_CONTROL_SECRET")
    forbidden = {
        values.get(key) or os.environ.get(key)
        for key in FORBIDDEN_SECRET_KEYS
    }
    if secret and secret in {value for value in forbidden if value}:
        raise BackgroundStartError("control_secret_reused")
    os.environ.update(controls)


def hourly_run_id(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    return "hourly-" + current.strftime("%Y%m%d%H")


def start_one_background_match(*, now: dt.datetime | None = None) -> str:
    """Start or confirm this hour's one background run.

    Returns the deterministic run id.  Another active run is never stopped,
    reset, or replaced.
    """
    _install_control_environment()
    run_id = hourly_run_id(now)
    status = call_beta_control("status")
    if status.get("runId") == run_id and status.get("state") in STARTED_STATES:
        return run_id
    if status.get("readyForNextRun") is not True or status.get("state") in ACTIVE_STATES:
        raise BackgroundStartError("background_busy")
    started = call_beta_control("start", run_id)
    if started.get("runId") != run_id or started.get("state") not in STARTED_STATES:
        raise BackgroundStartError("start_unverified")
    return run_id


def main() -> int:
    try:
        start_one_background_match()
        return 0
    except BackgroundStartError as exc:
        code = str(exc).split(":", 1)[0]
        return {
            "background_busy": 70,
            "control_env_unavailable": 76,
            "control_env_invalid": 76,
            "control_secret_reused": 76,
            "start_unverified": 77,
        }.get(code, 77)
    except ControlError as exc:
        return {
            "control_not_configured": 71,
            "run_locked": 72,
            "control_authentication_failed": 73,
            "control_timeout": 74,
            "control_unavailable": 74,
            "terminal_unconfirmed": 75,
            "recovery_not_available": 75,
            "recovery_checkpoint_invalid": 75,
            "terminal_result_unavailable": 75,
            "terminal_storage_failure": 75,
            "invalid_beta_operation": 77,
        }.get(exc.code, 75)


if __name__ == "__main__":
    raise SystemExit(main())
