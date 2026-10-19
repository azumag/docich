#!/usr/bin/env python3
"""Fixed owner-only GameSwitch failed-candidate adoption on production (#2088).

The only executable surface is the canonical CLI's
``recover --adopt-failed-candidate``. Before it runs, this helper double-keys
the workflow-authorized declaration against the canonical GameSwitch state and
refuses to run unless every guard holds:

- the canonical state must be exactly the adoptable failed-candidate shape:
  ``failed`` with no active/candidate seat, exactly one ``retiring`` seat
  flagged ``cleanup_role=failed_candidate``, a non-candidate ``previous`` and
  an immutable failed receipt whose ``rollback_failed`` result names that
  candidate's game and generation. Anything else is drift and is rejected
  without mutation.
- the declared phase and cleanup-pending flag must equal what canonical says;
  a mismatch is a declared-mismatch refusal.
- ownership proof must exist: the runtime directory for every referenced seat
  and the immutable failed request receipt on disk. Missing proof is rejected.
- the canonical CLI runs once with a bounded deadline in a fixed argv.

Adoption must be verified, not merely exited: after the CLI returns success the
helper requires the *same* runtime (identical runtime_id/generation/lease_id)
to have become ``active``, the previous game's retirement to be settled (no
``retiring`` residue, no pending cleanup), and the post-run canonical state to
still be in the fixed form. A success exit with any other outcome is refused,
so exit 0 always means the wanted candidate was adopted without stopping it.

Shared display/audio/stream units are never touched and no arbitrary command,
unit or argument is accepted.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

# The canonical fixed-form guards live in the sibling recover helper (#1960).
# Both operations are deployed together from the same trusted main, so the
# adopt helper reuses them instead of drifting a second copy of the shape
# contract; their agreement is pinned by the regression suite.
_RECOVER_MODULE = "_docich_gameswitch_recover"
if _RECOVER_MODULE in sys.modules:  # reused when both helpers are loaded
    recover = sys.modules[_RECOVER_MODULE]
else:
    _RECOVER_PATH = Path(__file__).resolve().parent / "gameswitch_recover.py"
    if not _RECOVER_PATH.is_file():
        # Both fixed operators deploy together from the same trusted main; a
        # missing sibling means the deploy is incomplete, so fail closed with
        # the shared invalid-runtime code instead of a bare traceback.
        print(f"shared recover helper is missing: {_RECOVER_PATH}", file=sys.stderr)
        raise SystemExit(25)
    _RECOVER_SPEC = importlib.util.spec_from_file_location(_RECOVER_MODULE, _RECOVER_PATH)
    if _RECOVER_SPEC is None or _RECOVER_SPEC.loader is None:  # pragma: no cover - fixed deploy
        raise SystemExit(64)
    recover = importlib.util.module_from_spec(_RECOVER_SPEC)
    sys.modules[_RECOVER_MODULE] = recover
    _RECOVER_SPEC.loader.exec_module(recover)

DOCICH = Path("/home/ubuntu/docich")
STATE_REL = recover.STATE_REL
REQUESTS_REL = recover.REQUESTS_REL
RUNTIMES_REL = recover.RUNTIMES_REL
LAUNCHER_REL = recover.LAUNCHER_REL
CONFIG_REL = recover.CONFIG_REL

FixedRecoverExit = recover.FixedRecoverExit
validate_shape = recover.validate_shape

# Fixed rejection reasons surfaced as exit codes (one meaning per code).
# The 6x/7x/8x range is shared with the reviewed recover helper so the
# owner-only workflows speak one vocabulary; 83 is adopt-specific.
EX_BAD_USAGE = recover.EX_BAD_USAGE  # 2
EX_BAD_ROOT = recover.EX_BAD_ROOT  # 3
EX_LAUNCHER_MISSING = 25
EX_INVALID_REQUEST = recover.EX_INVALID_REQUEST  # 64
EX_ADOPT_REFUSED = recover.EX_RECOVERY_REJECTED  # 71
EX_INVALID_RESULT = recover.EX_INVALID_RESULT  # 72
EX_DECLARED_MISMATCH = recover.EX_DECLARED_MISMATCH  # 80
EX_UNKNOWN_DRIFT = recover.EX_UNKNOWN_DRIFT  # 81
EX_OWNERSHIP_MISSING = recover.EX_OWNERSHIP_MISSING  # 82
EX_ADOPT_UNPROVEN = 83

# Adoption only applies to a canonical ``failed`` state whose single retiring
# seat is the still-running wanted candidate. The declared cleanup-pending flag
# is fixed to ``true`` because that shape always holds a retiring seat.
DECLARED_PHASES = frozenset({"failed"})
DECLARED_CLEANUP_PENDING = frozenset({"true"})

CLI_ADOPT_TIMEOUT_S = 420
CLI_KILL_GRACE_S = 30


def _seat_identity(seat: dict) -> dict:
    """The identity that proves adoption kept the very same running runtime."""

    return {
        key: seat.get(key)
        for key in ("game", "adapter", "runtime_id", "generation", "lease_id")
    }


def failed_candidate(state: dict) -> dict:
    """Return the single retiring failed candidate, or refuse the shape."""

    previous = state.get("previous")
    retiring = state.get("retiring")
    last = state.get("last_result")
    if (state.get("phase") != "failed" or state.get("active") is not None
            or state.get("candidate") is not None or state.get("request_id") is not None
            or not isinstance(previous, dict)
            or not isinstance(retiring, list) or len(retiring) != 1
            or not isinstance(last, dict)):
        raise FixedRecoverExit(
            EX_UNKNOWN_DRIFT, "canonical is not the adoptable failed-candidate shape")
    failed = retiring[0]
    if (failed.get("cleanup_role") != "failed_candidate" or "retirement" in failed):
        raise FixedRecoverExit(
            EX_UNKNOWN_DRIFT, "canonical retiring seat is not a failed candidate")
    request_id = last.get("request_id")
    if not recover._request_id_ok(request_id):
        raise FixedRecoverExit(
            EX_UNKNOWN_DRIFT, "canonical failed result has no immutable request id")
    if (last.get("status") != "failed" or last.get("error_code") != "rollback_failed"
            or last.get("to_game") != failed.get("game")
            or not recover._is_int(last.get("generation"))
            or last.get("generation") != failed.get("generation")):
        raise FixedRecoverExit(
            EX_UNKNOWN_DRIFT, "canonical last_result is not this candidate's rollback failure")
    if (previous.get("adapter") == failed.get("adapter")
            or previous.get("game") == failed.get("game")):
        raise FixedRecoverExit(
            EX_UNKNOWN_DRIFT, "canonical previous is not a distinct non-candidate game")
    if (not recover._is_int(previous.get("generation"))
            or previous["generation"] >= failed["generation"]):
        raise FixedRecoverExit(
            EX_UNKNOWN_DRIFT, "canonical previous generation does not precede the candidate")
    return failed


def declared_matches(state: dict, declared_phase: str, declared_cleanup_pending: bool) -> None:
    """The authorized declaration must be exactly what canonical says."""

    if state.get("phase") != declared_phase:
        raise FixedRecoverExit(
            EX_DECLARED_MISMATCH,
            f"canonical phase {state.get('phase')} does not match the declared {declared_phase}")
    pending_true = bool(state.get("retiring"))
    last_result = state.get("last_result")
    if isinstance(last_result, dict) and last_result.get("cleanup_pending") is True:
        pending_true = True
    if pending_true != declared_cleanup_pending:
        raise FixedRecoverExit(
            EX_DECLARED_MISMATCH,
            "canonical cleanup pending does not match the declared cleanup_pending")


def verify_ownership(root: Path, state: dict) -> None:
    """Runtime directories and the immutable failed receipt prove ownership."""

    # Reuses the reviewed recover proof (seated runtime directories plus any
    # durable request receipt); the adopt shape holds no live request_id, so
    # the failed receipt below is the operation's own ownership anchor.
    recover.verify_ownership(root, state)
    request_id = state["last_result"]["request_id"]
    receipt_path = root / REQUESTS_REL / f"{request_id}.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FixedRecoverExit(
            EX_OWNERSHIP_MISSING, f"failed receipt {receipt_path} is unreadable: {exc}")
    if not isinstance(receipt, dict) or receipt.get("request_id") != request_id:
        raise FixedRecoverExit(EX_OWNERSHIP_MISSING, "failed receipt owns another request")
    last = state["last_result"]
    if (receipt.get("status") != "failed" or receipt.get("error_code") != "rollback_failed"
            or receipt.get("to_game") != last.get("to_game")
            or receipt.get("generation") != last.get("generation")):
        raise FixedRecoverExit(
            EX_OWNERSHIP_MISSING, "failed receipt is not the immutable rollback failure")


def run_canonical_adopt(root: Path) -> int:
    """Run only the canonical CLI adoption once, with bounded time."""

    launcher = root / LAUNCHER_REL
    config = root / CONFIG_REL
    if not (launcher.is_file() and os.access(launcher, os.X_OK)) or not config.is_file():
        raise FixedRecoverExit(EX_LAUNCHER_MISSING, "reviewed docich launcher or config is missing")
    argv = [
        str(launcher), "--config", str(config),
        "recover", "--adopt-failed-candidate",
        "--timeout", str(CLI_ADOPT_TIMEOUT_S),
    ]
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            timeout=CLI_ADOPT_TIMEOUT_S + CLI_KILL_GRACE_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise FixedRecoverExit(EX_ADOPT_REFUSED, "canonical adoption exceeded its bounded deadline")
    return completed.returncode


def verify_adopted(root: Path, before: dict, after: dict) -> None:
    """Exit 0 must mean the same running candidate became active, settled."""

    failed = before["retiring"][0]
    if after.get("phase") in {"failed", "recovery_required"}:
        raise FixedRecoverExit(
            EX_ADOPT_UNPROVEN, f"canonical stayed {after.get('phase')} after adoption")
    if after.get("candidate") is not None:
        raise FixedRecoverExit(EX_ADOPT_UNPROVEN, "adoption left a candidate seat behind")
    active = after.get("active")
    if not isinstance(active, dict) or _seat_identity(active) != _seat_identity(failed):
        raise FixedRecoverExit(
            EX_ADOPT_UNPROVEN, "adoption did not publish the same failed candidate runtime")
    if after.get("retiring"):
        raise FixedRecoverExit(
            EX_ADOPT_UNPROVEN, "adoption left cleanup residue in the retiring seats")
    last = after.get("last_result")
    if (not isinstance(last, dict) or last.get("status") != "succeeded"
            or last.get("to_game") != failed.get("game")
            or last.get("generation") != failed.get("generation")):
        raise FixedRecoverExit(
            EX_ADOPT_UNPROVEN, "canonical does not record the adopted candidate as the result")
    runtime_dir = root / RUNTIMES_REL / str(active["runtime_id"])
    if not runtime_dir.is_dir():
        raise FixedRecoverExit(
            EX_ADOPT_UNPROVEN, "the adopted runtime directory is missing")


def adopt(root: Path, declared_phase: str, declared_cleanup_pending: bool) -> int:
    before = recover.load_canonical(root)
    # The declaration is checked against canonical first: declaring a phase or
    # cleanup flag that canonical does not show is a declared mismatch, not an
    # unknown shape.
    declared_matches(before, declared_phase, declared_cleanup_pending)
    failed_candidate(before)
    verify_ownership(root, before)
    cli_code = run_canonical_adopt(root)
    try:
        after = recover.load_canonical(root)
    except FixedRecoverExit as exc:
        # An unreadable/drifted post-run state is reported as such even when the
        # CLI already failed: it is the more severe fact for the operator.
        raise FixedRecoverExit(EX_INVALID_RESULT, f"post-adopt canonical state is invalid: {exc}")
    if cli_code != 0:
        raise FixedRecoverExit(
            EX_ADOPT_REFUSED, f"canonical recover --adopt-failed-candidate refused (exit {cli_code})")
    verify_adopted(root, before, after)
    return 0


def main(argv: list[str]) -> int:
    docich = Path(os.environ.get("DOCICH_ROOT") or DOCICH)
    if Path.cwd() != docich:
        raise FixedRecoverExit(EX_BAD_ROOT, "this adoption only runs in its own production docich root")
    if len(argv) != 2:
        raise FixedRecoverExit(EX_BAD_USAGE, "usage: gameswitch_adopt.py PHASE CLEANUP_PENDING")
    phase, cleanup_pending = argv
    if phase not in DECLARED_PHASES or cleanup_pending not in DECLARED_CLEANUP_PENDING:
        raise FixedRecoverExit(EX_INVALID_REQUEST, "declared phase or cleanup_pending is out of the fixed form")
    return adopt(docich, phase, cleanup_pending == "true")


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except FixedRecoverExit as exc:
        print(f"guarded GameSwitch adoption refused ({exc.code}): {exc}", file=sys.stderr)
        # Propagate the fixed code: the owner-only workflow maps it to a
        # rejection reason, so a bare re-raise (exit 1) would lose it.
        raise SystemExit(exc.code)
    raise SystemExit(code)
