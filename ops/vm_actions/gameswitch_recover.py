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
  runtime seat. Missing proof is rejected. Two reviewed exceptions:
  ``soren``/``soren91`` seats never materialize a per-runtime directory, so
  they are proven by the durable identity a commit recorded for exactly that
  runtime (runtime_id + generation + lease; never by liveness), and a failed
  candidate that never reached ``materialize`` has no directory to require.
  A directory-owning seat whose receipt proves materialize completed keeps
  the ownership failure when its directory is gone. Without these, no later
  surface could settle a dead seat and the whole corner rotation stayed
  latched (2026-11-14).
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

#: Coordinator adapters that never materialize ``spec.runtime_dir``. ``soren``
#: owns one shared runtime for sorengame and ``soren91`` rides the same
#: process, so a seated runtime of these kinds has no directory to prove
#: anything with. They are proven by the durable commit identity instead
#: (``_committed_runtime_identities``). Keep in sync with the adapters that
#: use the spec directory (``git grep runtime_dir -- src/docich/adapters``).
SOREN_ADAPTERS = frozenset({"soren", "soren91"})

#: Failure codes that prove the candidate never reached ``materialize``.
#: ``_failure_code`` maps a generic materialize exception to ``start_failed``;
#: preflight failures happen before a candidate seat exists (permission from
#: a terminal failure alone is never enough, see ``_materialize_stage``).
PRE_MATERIALIZE_FAILURE_CODES = frozenset({"start_failed"})
#: Failure codes that can only happen after ``materialize`` succeeded. A
#: request-wide ``timeout`` is ambiguous and deliberately fails closed here.
POST_MATERIALIZE_FAILURE_CODES = frozenset(
    {"readiness_timeout", "agent_start_failed", "timeout"}
)

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
# A missing runtime directory is reported with the seat class it belongs to so
# the owner-only workflow can say which seat holds the unproven proof. The
# stable seats keep the original fixed reason (82): only a failed candidate is
# ever allowed to lose its directory without losing ownership (see
# ``_terminal_failure_receipt``).
EX_OWNERSHIP_MISSING_CANDIDATE = 83
EX_OWNERSHIP_MISSING_RETIRING = 84

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
    terminal_failure = _terminal_failure_receipt(root, state)
    committed: set[tuple[str, str, int, str]] | None = None
    for label, runtime in _seated_runtimes(state):
        if runtime["adapter"] in SOREN_ADAPTERS:
            # Soren seats never own a per-runtime directory. This checkout's
            # ownership is proven by the durable identity a commit recorded for
            # exactly this runtime (runtime_id + generation + lease), not by a
            # directory and never by liveness (a recovery target is expected to
            # be dead; liveness belongs to the canonical CLI).
            if committed is None:
                committed = _committed_runtime_identities(root, state)
            if _soren_seat_is_owned(state, terminal_failure, committed, label, runtime):
                continue
            raise FixedRecoverExit(
                EX_OWNERSHIP_MISSING,
                f"{label} Soren runtime has no durable commit identity",
            )
        runtime_dir = root / RUNTIMES_REL / str(runtime["runtime_id"])
        if runtime_dir.is_dir():
            continue
        # A directory-owning adapter whose candidate failed before
        # ``materialize`` never created the directory. The receipt's terminal
        # stage decides that; a candidate that did materialize keeps the
        # ownership failure (the directory cannot silently disappear).
        stage = _materialize_stage(state, terminal_failure, label, runtime)
        if stage == "pre":
            continue
        if stage == "post":
            raise FixedRecoverExit(
                _missing_directory_reason(label),
                f"{label} runtime directory {runtime_dir} is missing after materialize",
            )
        # Unknown stage (e.g. rollback_failed without a start-stage code): keep
        # the reviewed failed-candidate proof. Tightening this without a
        # durable stage marker would re-latch the 2026-11-14 dead view seat.
        if terminal_failure is not None and _dead_seat_is_owned(state, terminal_failure, label, runtime):
            continue
        raise FixedRecoverExit(
            _missing_directory_reason(label),
            f"{label} runtime directory {runtime_dir} is missing",
        )


def _missing_directory_reason(label: str) -> int:
    if label == "candidate":
        return EX_OWNERSHIP_MISSING_CANDIDATE
    if label.startswith("retiring["):
        return EX_OWNERSHIP_MISSING_RETIRING
    return EX_OWNERSHIP_MISSING


def _terminal_failure_receipt(root: Path, state: dict) -> dict | None:
    """The durable receipt for the request canonical already recorded failed.

    ``failed`` keeps no live ``request_id``, so ``last_result`` is the only
    pointer to it. The receipt has to be this checkout's own terminal failure;
    anything unreadable or inconsistent yields no proof (never permission).
    """

    last_result = state.get("last_result")
    if not isinstance(last_result, dict) or last_result.get("status") != "failed":
        return None
    request_id = last_result.get("request_id")
    if not _request_id_ok(request_id):
        return None
    try:
        receipt = json.loads(
            (root / REQUESTS_REL / f"{request_id}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(receipt, dict):
        return None
    if receipt.get("request_id") != request_id or receipt.get("status") != "failed":
        return None
    result = receipt.get("result")
    if not isinstance(result, dict) or result.get("status") != "failed":
        return None
    return receipt


def _dead_seat_is_owned(state: dict, receipt: dict, label: str, runtime: dict) -> bool:
    """The terminal failure receipt names exactly this candidate seat.

    Only a failed candidate (the ``candidate`` seat or a ``retiring`` entry
    whose cleanup role is ``failed_candidate``) can lose its directory this
    way. A stable seat without its directory stays an ownership failure, and
    the receipt has to name the same runtime identity and target as the seat.
    """

    if label != "candidate" and not label.startswith("retiring["):
        return False
    if label.startswith("retiring[") and runtime.get("cleanup_role") != "failed_candidate":
        return False
    last_result = state.get("last_result")
    if not isinstance(last_result, dict):
        return False
    return (
        receipt.get("runtime_id") == runtime.get("runtime_id")
        and receipt.get("generation") == runtime.get("generation")
        and receipt.get("target") == runtime.get("game")
        and last_result.get("generation") == runtime.get("generation")
        and last_result.get("to_game") == runtime.get("game")
    )


def _runtime_identity(record: object) -> tuple[str, str, int, str] | None:
    """The exact runtime identity a commit result records, when complete."""

    if not isinstance(record, dict):
        return None
    game = record.get("game")
    runtime_id = record.get("runtime_id")
    generation = record.get("generation")
    lease_id = record.get("lease_id")
    if not isinstance(game, str) or not game:
        return None
    match = RUNTIME_ID_RE.fullmatch(runtime_id) if isinstance(runtime_id, str) else None
    if match is None:
        return None
    if not _is_int(generation) or generation < 1:
        return None
    if not isinstance(lease_id, str) or not lease_id:
        return None
    if int(match.group(1)) != generation:
        return None
    return (game, runtime_id, generation, lease_id)


def _committed_runtime_identities(
    root: Path, state: dict
) -> set[tuple[str, str, int, str]]:
    """Collect the identities that durable commit results made live.

    Soren has no per-runtime directory, so the commit itself is the ownership
    proof: the result of the switch/start that published the runtime, or the
    retirement proof of the switch that replaced it. Only complete identities
    (game + runtime_id + generation + lease) count; anything else is ignored,
    so a seat without a commit proof fails closed.
    """

    identities: set[tuple[str, str, int, str]] = set()

    def add(record: object) -> None:
        identity = _runtime_identity(record)
        if identity is not None:
            identities.add(identity)

    last_result = state.get("last_result")
    if isinstance(last_result, dict):
        add(last_result.get("active_runtime"))
        add(last_result.get("stopped_runtime"))
    for entry in state.get("retiring") or []:
        if not isinstance(entry, dict):
            continue
        retirement = entry.get("retirement")
        if isinstance(retirement, dict):
            add(retirement.get("active_runtime"))
            add(retirement.get("source_runtime"))
    directory = root / REQUESTS_REL
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return identities
    for path in paths:
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(receipt, dict):
            continue
        result = receipt.get("result")
        if not isinstance(result, dict):
            continue
        add(result.get("active_runtime"))
        add(result.get("stopped_runtime"))
        add(result.get("source_runtime"))
    return identities


def _soren_seat_is_owned(
    state: dict,
    terminal_failure: dict | None,
    committed: set[tuple[str, str, int, str]],
    label: str,
    runtime: dict,
) -> bool:
    """A Soren seat is owned by its commit identity or its terminal failure.

    A failed candidate is proven by the terminal-failure receipt (the only
    durable record for a request that never committed). A stable or retiring
    seat needs the identity of the commit that published the very same
    runtime, including its lease.
    """

    if _is_failed_candidate_seat(label, runtime):
        if terminal_failure is not None and _dead_seat_is_owned(
            state, terminal_failure, label, runtime
        ):
            return True
    identity = _runtime_identity(runtime)
    return identity is not None and identity in committed


def _is_failed_candidate_seat(label: str, runtime: dict) -> bool:
    if label == "candidate":
        return True
    return (
        label.startswith("retiring[")
        and runtime.get("cleanup_role") == "failed_candidate"
    )


def _materialize_stage(
    state: dict, terminal_failure: dict | None, label: str, runtime: dict
) -> str:
    """Classify a directory-owning seat that lost its runtime directory.

    ``pre``: the terminal failure code proves ``materialize`` never ran, so a
    missing directory is expected. ``post``: materialize must have completed
    (stable seats, readiness/agent-start/timeout), so a missing directory is
    an ownership failure. ``unknown``: the receipt does not name a start
    stage; callers keep the reviewed failed-candidate proof.
    """

    if not _is_failed_candidate_seat(label, runtime):
        return "post"
    if terminal_failure is None or not _dead_seat_is_owned(
        state, terminal_failure, label, runtime
    ):
        return "post"
    result = terminal_failure.get("result")
    code = result.get("error_code") if isinstance(result, dict) else None
    if code in PRE_MATERIALIZE_FAILURE_CODES:
        return "pre"
    if code in POST_MATERIALIZE_FAILURE_CODES:
        return "post"
    return "unknown"


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
