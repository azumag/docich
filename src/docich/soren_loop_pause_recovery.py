"""Release the loop pause owned by a consumed, succeeded switch (issue #2061).

The lifecycle broker parks ``tmp/state/soren_loop.paused`` for the request that
stopped Soren, and ``tmp/state/game_lifecycle/loop_pause.json`` is the only
durable proof of that ownership.  When candidate cleanup replaced that request,
the marker survived with no path that could clear it: ``fresh-start`` only
releases the pause of its own request, and ``recover_soren_game`` refuses while
any pause exists.  Readiness and rollback then time out.

This module releases exactly that one marker, and only when every guard below
proves the original owner and that no game is running:

- the canonical game switch is in a stable failure phase with no active seat,
  so releasing the park cannot start Soren underneath a live game;
- a Soren failed candidate is still seated (the recovery this release feeds);
- the marker body and its ownership record name the same request R, and the
  record positively claims it created the marker;
- R's immutable receipt is a *succeeded* switch/rotate whose recorded result
  moved the switch away from ``sorengame``, and R is no longer the canonical
  live request (it is consumed);
- the marker is copied to a private backup before it is removed.

Nothing else is touched: no canonical ledger, no resource/ack/request record,
no worker pid, no systemd unit, and no shared display/audio/stream service.
The supervisor alone decides whether to start a fresh loop.

``tmp/state/soren_loop.resumed`` is deliberately left alone.  It is the
adapter-side snapshot #2396 writes for cross-instance recovery, not broker
state, and its recovery is fail-closed on a request-id mismatch, so a stale
snapshot cannot adopt a later request.  Removing it here would widen this
operator's blast radius past the one marker it proves.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from pathlib import Path

from .game_switch import GameSwitchBusyError, GameSwitchStore, StateCorruptError


RELEASE_PHASES = frozenset({"failed", "recovery_required"})
SOURCE_GAME = "sorengame"
RECEIPT_OPERATIONS = frozenset({"switch", "rotate"})
REASONS = frozenset(
    {
        "canonical_unavailable",
        "phase_not_releasable",
        "game_still_active",
        "no_soren_candidate",
        "pause_not_owned",
        "receipt_not_owner",
        "unexpected_failure",
    }
)
# One fixed exit code per refusal reason, so the owner-only workflow can report
# the reason verbatim without publishing VM-private output.  The 7x range is
# local to this operation; the value carries no meaning for any other one.
REASON_EXIT_CODES = {
    "canonical_unavailable": 70,
    "phase_not_releasable": 71,
    "game_still_active": 72,
    "no_soren_candidate": 73,
    "pause_not_owned": 74,
    "receipt_not_owner": 75,
    "unexpected_failure": 76,
}


class LoopPauseReleaseRefused(RuntimeError):
    """Fixed refusal; the message is a member of ``REASONS``."""


def _reason(value: object) -> str:
    return value if type(value) is str and value in REASONS else "unexpected_failure"


def incomplete_result(reason: object = "unexpected_failure") -> dict:
    """Fixed, public-safe schema; unobserved facts are null, never success."""
    return {
        "status": "incomplete",
        "reason": _reason(reason),
        "released": None,
        "ownership_proven": False,
        "backup": None,
    }


def completed_result() -> dict:
    return {
        "status": "completed",
        "reason": "released",
        "released": "soren_loop_pause",
        "ownership_proven": True,
        "backup": "private",
    }


def exit_code(result: object) -> int:
    """0 for a completed release, otherwise the fixed code for its reason."""
    if isinstance(result, dict) and result.get("status") == "completed":
        return 0
    reason = result.get("reason") if isinstance(result, dict) else None
    return REASON_EXIT_CODES[_reason(reason)]


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _soren_seat(runtime: object) -> bool:
    return (
        isinstance(runtime, dict)
        and runtime.get("game") == SOURCE_GAME
        and runtime.get("adapter") == "soren"
    )


def _marker_owner(marker: Path) -> str | None:
    """The lifecycle request the pause marker names, or None when it is not ours.

    A marker another writer created (operator stop, legacy file) never carries
    the ``lifecycle:<request_id>`` form, so it is never adopted here.
    """
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    prefix = "lifecycle:"
    if not value.startswith(prefix):
        return None
    owner = value[len(prefix):]
    if not owner:
        return None
    try:
        uuid.UUID(owner)
    except (ValueError, TypeError, AttributeError):
        return None
    return owner


def prove_owner(canonical: object, store: GameSwitchStore, marker: Path, record: Path) -> str:
    """Return the proven owning request, or refuse without mutating anything."""
    if not isinstance(canonical, dict):
        raise LoopPauseReleaseRefused("canonical_unavailable")
    if canonical.get("phase") not in RELEASE_PHASES:
        raise LoopPauseReleaseRefused("phase_not_releasable")
    if canonical.get("active") is not None:
        # A live seat means a game is running; releasing the park here would
        # start Soren underneath it.
        raise LoopPauseReleaseRefused("game_still_active")
    if _soren_seat(canonical.get("previous")):
        raise LoopPauseReleaseRefused("game_still_active")
    seats = [canonical.get("candidate"), *(canonical.get("retiring") or [])]
    if not any(_soren_seat(seat) for seat in seats):
        raise LoopPauseReleaseRefused("no_soren_candidate")

    owner = _marker_owner(marker)
    if owner is None:
        raise LoopPauseReleaseRefused("pause_not_owned")
    ownership = _read_json(record)
    if (
        not isinstance(ownership, dict)
        or ownership.get("request_id") != owner
        or ownership.get("loop_marker_created") is not True
    ):
        # Only a record that positively claims it created this exact marker
        # proves the lifecycle owns the pause (issue #2061).
        raise LoopPauseReleaseRefused("pause_not_owned")

    try:
        receipt = store.receipts.load(owner)
    except (StateCorruptError, OSError, ValueError) as exc:
        raise LoopPauseReleaseRefused("receipt_not_owner") from exc
    if not isinstance(receipt, dict) or receipt.get("status") != "succeeded":
        raise LoopPauseReleaseRefused("receipt_not_owner")
    if receipt.get("request_id") != owner:
        raise LoopPauseReleaseRefused("receipt_not_owner")
    result = receipt.get("result")
    if (
        not isinstance(result, dict)
        or result.get("status") != "succeeded"
        or result.get("request_id") != owner
        or result.get("operation") not in RECEIPT_OPERATIONS
        or result.get("from_game") != SOURCE_GAME
        or result.get("to_game") == SOURCE_GAME
    ):
        # The proof must be a succeeded move *away* from Sorengame: the park
        # belongs to the switch that stopped it, not to any later one.
        raise LoopPauseReleaseRefused("receipt_not_owner")
    if canonical.get("request_id") == owner:
        # The owning request is still the live canonical request, so it is not
        # a consumed switch and its own fresh-start must release the park.
        raise LoopPauseReleaseRefused("receipt_not_owner")
    return owner


def _backup(marker: Path, directory: Path, *, clock) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    stamp = dt.datetime.fromtimestamp(clock(), dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = marker.read_bytes()
    path = directory / f"loop-pause-release-{stamp}-{uuid.uuid4().hex[:8]}.paused"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


class OrphanedLoopPauseRelease:
    """Fixed owner-only release of a consumed switch's loop pause."""

    def __init__(self, state_dir, soren_root, *, clock=time.time):
        self.state_dir = Path(state_dir)
        self.soren_root = Path(soren_root)
        self.clock = clock
        self.store = GameSwitchStore(self.state_dir)

    def run(self) -> dict:
        marker = self.soren_root / "tmp" / "state" / "soren_loop.paused"
        record = self.soren_root / "tmp" / "state" / "game_lifecycle" / "loop_pause.json"
        try:
            # The exclusive lock keeps a concurrent switch from changing the
            # canonical seats between the proof and the release.  It is held
            # only for the decision and the single unlink.
            with self.store.lock(exclusive=True):
                canonical, missing = self.store.canonical.load()
                if missing:
                    raise LoopPauseReleaseRefused("canonical_unavailable")
                prove_owner(canonical, self.store, marker, record)
                _backup(marker, self.soren_root / "tmp" / "debug", clock=self.clock)
                marker.unlink()
        except LoopPauseReleaseRefused as exc:
            return incomplete_result(str(exc))
        except GameSwitchBusyError:
            return incomplete_result("canonical_unavailable")
        except (OSError, ValueError, RuntimeError, StateCorruptError):
            return incomplete_result("unexpected_failure")
        return completed_result()
