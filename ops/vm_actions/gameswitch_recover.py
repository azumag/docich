#!/usr/bin/env python3
"""Fixed owner-only GameSwitch recover helper on the production VM (#1960).

The only executable surface is the canonical CLI's ``recover``. Before it
runs, this helper double-keys the workflow-authorized declaration against
the canonical GameSwitch state and refuses to run unless every guard holds:

- ``run-soren-live/game_switch.json`` must match the canonical fixed form
  (required keys, schema 2, known phases with their seat invariants,
  integer sanity, ``cleanup_pending`` flags strictly boolean). Anything
  unknown is drift and is rejected without mutation.
- recovery only applies to a canonical phase that equals the declared
  phase (``failed`` or ``recovery_required``) and to the declared
  ``cleanup_pending`` flag; a mismatch is a declared-mismatch refusal.
- ownership proof must exist: a durable request receipt on disk for any
  canonical ``request_id`` and a runtime directory for every referenced
  runtime seat. Missing proof is rejected.
- the canonical CLI runs once with a bounded deadline in a fixed argv; the
  post-run canonical state must again be valid.

The fixed exit code stands for one rejection reason so the owner-only
workflow can report it verbatim. Shared display/audio/stream units are
never touched and no arbitrary command, unit or argument is accepted.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path


DOCICH = Path("/home/ubuntu/docich")
STATE_REL = "run-soren-live/game_switch.json"
REQUESTS_REL = "run-soren-live/game-switch/requests"
RUNTIMES_REL = "run-soren-live/runtimes"
LAUNCHER_REL = "bin/docich"
CONFIG_REL = "config/docich.soren-live.toml"

SCHEMA_VERSION = 2
PHASES = frozenset(
    {
        "idle",
        "validating",
        "preparing",
        "draining",
        "quiescing",
        "starting",
        "probing",
        "committing",
        "ready",
        "stopping",
        "rolling_back",
        "failed",
        "recovery_required",
    }
)
STABLE_PHASES = frozenset({"idle", "ready", "failed", "recovery_required"})
IN_PROGRESS_PHASES = PHASES - STABLE_PHASES
OPERATIONS = frozenset({"start", "stop", "switch", "restart", "rotate", "recover"})
DECLARED_PHASES = frozenset({"failed", "recovery_required"})
DECLARED_CLEANUP_PENDING = frozenset({"true", "false"})

REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "revision",
        "phase",
        "operation",
        "request_id",
        "deadline_at",
        "next_generation",
        "active",
        "candidate",
        "previous",
        "retiring",
        "last_result",
        "last_error",
        "updated_at",
    }
)
RUNTIME_KEYS = frozenset(
    {
        "game",
        "adapter",
        "generation",
        "runtime_id",
        "lease_id",
        "game_window",
        "agent_window",
        "adapter_session",
        "started_at",
    }
)

RUNTIME_ID_RE = re.compile(r"g([1-9][0-9]*)-([a-f0-9]{6,32})\Z")

# Fixed rejection reasons surfaced as exit codes (one meaning per code).
# 2/3/64/70/71/72 follow the reviewed recover_corner_rotation codes; the
# 8x range carries this operation's own guard rejections.
EX_BAD_USAGE = 2
EX_BAD_ROOT = 3
EX_INVALID_REQUEST = 64
EX_RECOVERY_PENDING = 70
EX_RECOVERY_REJECTED = 71
EX_INVALID_RESULT = 72
EX_DECLARED_MISMATCH = 80
EX_UNKNOWN_DRIFT = 81
EX_OWNERSHIP_MISSING = 82

CLI_RECOVER_TIMEOUT_S = 420
CLI_KILL_GRACE_S = 30


class FixedRecoverExit(Exception):
    """Fixed exit code plus one rejection message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = int(code)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _request_id_ok(value: object) -> bool:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return isinstance(value, str) and str(value).lower() == str(parsed)


def _seat_runtime_ok(runtime: object, label: str) -> None:
    """One canonical runtime seat: fixed form only, no drift tolerated."""

    where = f"{label} runtime"
    if not isinstance(runtime, dict):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} is not an object")
    missing = sorted(RUNTIME_KEYS - runtime.keys())
    if missing:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} is missing {missing}")
    for key in ("game", "adapter", "runtime_id", "game_window", "agent_window",
                "adapter_session", "started_at"):
        if not isinstance(runtime[key], str):
            raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} {key} is not a string")
    if not runtime["game"] or not runtime["runtime_id"] or not runtime["started_at"]:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} has an empty fixed field")
    generation = runtime["generation"]
    if not _is_int(generation) or generation < 1:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} generation is not a valid int")
    match = RUNTIME_ID_RE.fullmatch(runtime["runtime_id"])
    if not match:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} runtime_id is not in the fixed form")
    if int(match.group(1)) != generation:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} runtime_id does not own its generation")
    if runtime["game_window"] != f"game-g{generation}":
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} game_window is not in the fixed form")
    if runtime["agent_window"] != f"agent-g{generation}":
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} agent_window is not in the fixed form")
    if runtime["adapter_session"] != f"docich-game-g{generation}":
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} adapter_session is not in the fixed form")
    lease_id = runtime["lease_id"]
    if lease_id is not None and not isinstance(lease_id, str):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{where} lease_id is not a string or null")


def _seated_runtimes(state: dict) -> list[tuple[str, dict]]:
    entries: list[tuple[str, dict]] = []
    for label in ("active", "candidate", "previous"):
        value = state.get(label)
        if value is not None:
            entries.append((label, value))
    for index, value in enumerate(state.get("retiring") or []):
        entries.append((f"retiring[{index}]", value))
    return entries


def validate_shape(state: object) -> dict:
    """Canonical game switch state, exact fixed form (fail-closed)."""

    if not isinstance(state, dict):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical state is not an object")
    missing = sorted(REQUIRED_KEYS - state.keys())
    if missing:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"canonical state is missing {missing}")
    if state["schema_version"] != SCHEMA_VERSION:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical state schema_version is unknown")
    if not _is_int(state["revision"]) or state["revision"] < 0:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical revision is not a valid int")
    if not _is_int(state["next_generation"]) or state["next_generation"] < 1:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical next_generation is not a valid int")
    if not isinstance(state["updated_at"], str) or not state["updated_at"]:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical updated_at is not a string")
    phase = state["phase"]
    if not isinstance(phase, str) or phase not in PHASES:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical phase is unknown")
    operation = state["operation"]
    if operation is not None and operation not in OPERATIONS:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical operation is unknown")
    request_id = state["request_id"]
    if request_id is not None and not _request_id_ok(request_id):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical request_id is not a canonical UUID")
    if (operation is None) != (request_id is None):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "operation and request_id must be set together")
    if phase in IN_PROGRESS_PHASES and (operation is None or request_id is None):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "in-progress phase lost its durable request")
    if phase in {"idle", "ready"} and (operation is not None or request_id is not None):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "stable phase keeps a durable request")
    deadline_at = state["deadline_at"]
    if deadline_at is not None and not isinstance(deadline_at, str):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical deadline_at is not a string or null")
    if not isinstance(state["retiring"], list):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical retiring is not a list")
    if phase == "idle" and any(state.get(label) is not None for label in ("active", "candidate", "previous")):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "idle phase keeps a runtime seat")
    if phase == "ready" and (
        state.get("active") is None or state.get("candidate") is not None
        or state.get("previous") is not None
    ):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "ready phase does not hold exactly the active seat")
    if phase == "draining" and (
        state.get("active") is None or state.get("candidate") is not None
        or state.get("previous") is not None
    ):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "draining phase does not hold exactly the active seat")
    if phase in {"validating", "stopping"} and state.get("candidate") is not None:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"{phase} phase keeps a candidate seat")
    if phase == "recovery_required" and not isinstance(state.get("last_error"), dict):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "recovery_required phase lost its fixed last_error")
    for label, runtime in _seated_runtimes(state):
        _seat_runtime_ok(runtime, label)
    generations = [runtime["generation"] for _, runtime in _seated_runtimes(state)]
    if len(generations) != len(set(generations)):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical runtime generations are not unique")
    if generations and state["next_generation"] <= max(generations):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical next_generation does not advance")
    last_result = state["last_result"]
    if last_result is not None:
        if not isinstance(last_result, dict):
            raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical last_result is not an object")
        pending = last_result.get("cleanup_pending")
        if pending is not None and not isinstance(pending, bool):
            raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical last_result cleanup_pending is not bool")
    last_error = state["last_error"]
    if last_error is not None and not isinstance(last_error, dict):
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "canonical last_error is not an object")
    return state


def load_canonical(root: Path) -> dict:
    path = root / STATE_REL
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FixedRecoverExit(EX_UNKNOWN_DRIFT, f"canonical state {path} is unreadable: {exc}")
    return validate_shape(state)


def declared_matches(state: dict, declared_phase: str, declared_cleanup_pending: bool) -> None:
    """The authorized declaration must be exactly what canonical says."""

    phase = state["phase"]
    if phase != declared_phase:
        raise FixedRecoverExit(
            EX_DECLARED_MISMATCH,
            f"canonical phase {phase} does not match the declared {declared_phase}",
        )
    pending_true = bool(state.get("retiring"))
    last_result = state.get("last_result")
    if isinstance(last_result, dict) and last_result.get("cleanup_pending") is True:
        pending_true = True
    if pending_true != declared_cleanup_pending:
        raise FixedRecoverExit(
            EX_DECLARED_MISMATCH,
            f"canonical cleanup pending={pending_true} does not match the declared {declared_cleanup_pending}",
        )


def verify_ownership(root: Path, state: dict) -> None:
    """Receipts and runtime directories prove this checkout owns the state."""

    request_id = state.get("request_id")
    if request_id is not None:
        receipt_path = root / REQUESTS_REL / f"{request_id}.json"
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FixedRecoverExit(
                EX_OWNERSHIP_MISSING, f"request receipt {receipt_path} is unreadable: {exc}"
            )
        if not isinstance(receipt, dict):
            raise FixedRecoverExit(EX_OWNERSHIP_MISSING, "request receipt is not an object")
        if receipt.get("request_id") != request_id:
            raise FixedRecoverExit(EX_OWNERSHIP_MISSING, "request receipt owns another request")
        result = receipt.get("result")
        if result is not None:
            if not isinstance(result, dict):
                raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "request receipt result is not an object")
            pending = result.get("cleanup_pending")
            if pending is not None and not isinstance(pending, bool):
                raise FixedRecoverExit(EX_UNKNOWN_DRIFT, "request receipt cleanup_pending is not bool")
    for label, runtime in _seated_runtimes(state):
        runtime_dir = root / RUNTIMES_REL / str(runtime["runtime_id"])
        if not runtime_dir.is_dir():
            raise FixedRecoverExit(
                EX_OWNERSHIP_MISSING,
                f"{label} runtime directory {runtime_dir} is missing",
            )


def run_canonical_recover(root: Path) -> int:
    """Run only the canonical CLI recover once, with bounded time."""

    launcher = root / LAUNCHER_REL
    config = root / CONFIG_REL
    if not (launcher.is_file() and os.access(launcher, os.X_OK)) or not config.is_file():
        raise FixedRecoverExit(25, "reviewed docich launcher or config is missing")
    argv = [
        str(launcher), "--config", str(config),
        "recover", "--timeout", str(CLI_RECOVER_TIMEOUT_S),
    ]
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            timeout=CLI_RECOVER_TIMEOUT_S + CLI_KILL_GRACE_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise FixedRecoverExit(70, "canonical recover exceeded its bounded deadline")
    return completed.returncode


def recover(root: Path, declared_phase: str, declared_cleanup_pending: bool) -> int:
    state = load_canonical(root)
    declared_matches(state, declared_phase, declared_cleanup_pending)
    verify_ownership(root, state)
    cli_code = run_canonical_recover(root)
    load_canonical(root)
    if cli_code == 0:
        return 0
    if cli_code == 1:
        raise FixedRecoverExit(EX_RECOVERY_PENDING, "canonical recover is pending (busy/rolled_back)")
    raise FixedRecoverExit(EX_RECOVERY_REJECTED, "canonical recover refused this state")


def main(argv: list[str]) -> int:
    docich = Path(os.environ.get("DOCICH_ROOT") or DOCICH)
    if Path.cwd() != docich:
        raise FixedRecoverExit(EX_BAD_ROOT, "this recover only runs in its own production docich root")
    if len(argv) != 2:
        raise FixedRecoverExit(EX_BAD_USAGE, "usage: gameswitch_recover.py PHASE CLEANUP_PENDING")
    phase, cleanup_pending = argv
    if phase not in DECLARED_PHASES or cleanup_pending not in DECLARED_CLEANUP_PENDING:
        raise FixedRecoverExit(EX_INVALID_REQUEST, "declared phase or cleanup_pending is out of the fixed form")
    return recover(docich, phase, cleanup_pending == "true")


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except FixedRecoverExit as exc:
        print(f"guarded GameSwitch recovery refused ({exc.code}): {exc}", file=sys.stderr)
        # Propagate the fixed code: the owner-only workflow maps it to a
        # rejection reason, so a bare re-raise (exit 1) would lose it.
        raise SystemExit(exc.code)
    raise SystemExit(code)
