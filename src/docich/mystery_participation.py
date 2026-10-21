"""Generic participation recording for the mystery corner.

The vote tally, the run ledger and the fail-closed validation here are
ported from the trpg offline core (PR #2481, ``trpg_corner.py``) per the
unification ruling on #684 (2026-10-18): the same semantics now live in the
mystery corner's live wiring instead of a standalone offline state machine.

- Votes: idempotent, a participant's last vote wins; the winner is the
  plurality choice with ties broken to the lowest index and an all-zero
  tally yielding no winner (the seeded solo detective takes over).
- Ledger: one bounded entry per finished case; trims by entry count and by
  serialized size, oldest first. Viewer identities are never stored — only
  counts.
- Validation: every stored field is bounded and fail-closed.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Sequence

LEDGER_SCHEMA = 1
LEDGER_FILENAME = "mystery_history.json"

# --- limits (ported from the trpg core, #2481) ---
MAX_USER_ID = 64
MAX_PARTICIPANTS = 100000
MAX_HISTORY_ENTRIES = 500
MAX_BYTES = 256 * 1024
MAX_OUTCOME = 32
MAX_CODE = 64

#: Fixed run outcomes (the trpg core's vocabulary, mapped from the corner's
#: end reasons by ``mystery_corner._outcome_for``).
OUTCOMES = frozenset({"completed", "failed", "timeout", "aborted"})


class ParticipationError(ValueError):
    """A bounded participation or ledger record violates the contract."""


# --- fail-closed validation (ported from the trpg core) ---------------------


def _user(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_USER_ID:
        raise ParticipationError("invalid user id")
    return value


def _text(value: Any, what: str, *, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ParticipationError(f"invalid {what}")
    return value


def _int(value: Any, what: str, *, lo: int | None = None,
         hi: int | None = None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or type(value) is not int:
        raise ParticipationError(f"invalid {what}")
    if lo is not None and value < lo:
        raise ParticipationError(f"invalid {what}")
    if hi is not None and value > hi:
        raise ParticipationError(f"invalid {what}")
    return value


def _number(value: Any, what: str, *, lo: float | None = None) -> float | None:
    """A bounded clock value (wall timestamps are floats)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or value != value or value in (float("inf"), float("-inf")):
        raise ParticipationError(f"invalid {what}")
    if lo is not None and value < lo:
        raise ParticipationError(f"invalid {what}")
    return float(value)


# --- vote tally (ported semantics) ------------------------------------------

VOTE_KEY_SEP = "mystery-choice-"


def choice_key(index: int) -> str:
    """The bounded vote-bucket key for a 0-based choice index."""
    if isinstance(index, bool) or type(index) is not int or index < 0:
        raise ParticipationError("invalid choice index")
    return f"{VOTE_KEY_SEP}{index}"


def record_vote(votes: dict[str, list[str]], order: dict[str, str], user: Any,
                index: int) -> None:
    """Record one participant's vote. Idempotent: their last vote wins.

    Mutates ``votes`` (``{bucket_key: [user, ...]}``) and ``order``
    (``{user: bucket_key}``) in place, mirroring the trpg core's tally.

    The participant bound is checked **before any write**: a rejected
    newcomer must leave the state byte-identical (the trpg core's original
    post-write check let an over-limit user's vote linger in state and even
    score — owner-verified 2026-10-18; this port fixes the order). Existing
    participants may always re-submit.
    """
    uid = _user(user)
    if not isinstance(votes, dict) or not isinstance(order, dict):
        raise ParticipationError("invalid vote containers")
    key = choice_key(index)
    if uid not in order and len(order) >= MAX_PARTICIPANTS:
        raise ParticipationError("too many participants")
    prev = order.get(uid)
    if prev is not None and prev in votes:
        votes[prev] = [u for u in votes[prev] if u != uid]
        if not votes[prev]:
            del votes[prev]
    order[uid] = key
    votes.setdefault(key, [])
    if uid not in votes[key]:
        votes[key].append(uid)


def tally_votes(votes: Mapping[str, list[str]], *, choice_count: int) -> list[int]:
    """Counts per 0-based choice index, in choice order."""
    if type(choice_count) is not int or choice_count < 1:
        raise ParticipationError("invalid choice count")
    counts = [0] * choice_count
    for key, users in votes.items():
        if not isinstance(key, str) or not key.startswith(VOTE_KEY_SEP):
            raise ParticipationError("invalid vote bucket")
        try:
            index = int(key[len(VOTE_KEY_SEP):])
        except ValueError as exc:
            raise ParticipationError("invalid vote bucket") from exc
        if not 0 <= index < choice_count:
            raise ParticipationError("invalid vote bucket")
        if not isinstance(users, list):
            raise ParticipationError("invalid vote bucket")
        counts[index] += len(users)
    return counts


def winner(tally: Sequence[int]) -> int | None:
    """Plurality choice index; ties break to the lowest index; 0 votes → None.

    The single canonical tie-break for the corner (poll winners and the
    crowd's pick by users both use it).
    """
    best = None
    best_n = 0
    for index, count in enumerate(tally):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ParticipationError("invalid tally")
        if count > best_n:
            best_n, best = count, index
    return best


# --- run ledger (ported semantics) ------------------------------------------


def new_ledger() -> dict:
    return {"schema": LEDGER_SCHEMA, "entries": []}


def trim_ledger(ledger: dict) -> None:
    """Bound the ledger by entry count and serialized size, oldest first."""
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        return
    while len(entries) > MAX_HISTORY_ENTRIES:
        entries.pop(0)
    while entries:
        try:
            size = len(json.dumps(ledger, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            break
        if size <= MAX_BYTES:
            break
        entries.pop(0)


def record_run(ledger: Any, entry: Mapping[str, Any]) -> bool:
    """Append one bounded run summary. Returns True when recorded."""
    if not isinstance(ledger, dict):
        raise ParticipationError("invalid ledger")
    if not isinstance(entry, dict):
        raise ParticipationError("invalid run entry")
    outcome = _text(entry.get("outcome"), "outcome", limit=MAX_OUTCOME)
    if outcome not in OUTCOMES:
        raise ParticipationError("invalid outcome")
    checked = {
        "seed": _int(entry.get("seed"), "seed", lo=0, hi=2**64 - 1),
        "commitment": _text(entry.get("commitment"), "commitment", limit=128)
        if entry.get("commitment") is not None else None,
        "culprit_index": _int(entry.get("culprit_index"), "culprit_index", lo=0),
        "started_at": _number(entry.get("started_at"), "started_at", lo=0),
        "ended_at": _number(entry.get("ended_at"), "ended_at", lo=0),
        "outcome": outcome,
        "participants": _int(entry.get("participants"), "participants", lo=0) or 0,
        "votes_total": _int(entry.get("votes_total"), "votes_total", lo=0) or 0,
        "polls": _int(entry.get("polls"), "polls", lo=0) or 0,
        "prediction": entry.get("prediction")
        if isinstance(entry.get("prediction"), dict) else None,
        "end_reason": _text(entry.get("end_reason"), "end_reason", limit=MAX_CODE),
        "error_code": _text(entry.get("error_code"), "error_code", limit=MAX_CODE)
        if entry.get("error_code") else None,
    }
    if "entries" not in ledger or not isinstance(ledger["entries"], list):
        ledger["entries"] = []
    ledger["entries"].append(checked)
    trim_ledger(ledger)
    return True


def load_ledger(path: Any) -> dict:
    """Schema-checked load; absent/corrupt files return a fresh ledger."""
    path = Path(path)
    if not path.exists():
        return new_ledger()
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError):
        return new_ledger()
    if not isinstance(data, dict) or data.get("schema") != LEDGER_SCHEMA:
        return new_ledger()
    if not isinstance(data.get("entries"), list):
        data["entries"] = []
    base = new_ledger()
    base.update(data)
    trim_ledger(base)
    return base


def save_ledger(path: Any, ledger: dict) -> None:
    """Atomic, fsynced ledger write (the trpg core's persistence rule)."""
    path = Path(path)
    if not isinstance(ledger, dict):
        raise ParticipationError("invalid ledger")
    payload = json.dumps(ledger, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")) + "\n"
    if len(payload.encode("utf-8")) > MAX_BYTES:
        raise ParticipationError("ledger exceeds the size bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


# --- prediction participant extraction ---------------------------------------


def participants_from_prediction(row: Mapping[str, Any], *, max_per_outcome: int = 10
                                 ) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Build the per-user tally from a prediction row's ``top_predictors``.

    Twitch exposes only the top predictors per outcome (bounded here by
    ``max_per_outcome``); unreadable entries are skipped fail-open because
    this feeds the record ledger, never the settlement itself. Returns
    ``(votes, order)`` in :func:`record_vote` shape.
    """
    votes: dict[str, list[str]] = {}
    order: dict[str, str] = {}
    outcomes = row.get("outcomes") if isinstance(row, dict) else None
    if not isinstance(outcomes, list):
        return votes, order
    for index, outcome in enumerate(outcomes):
        if not isinstance(outcome, dict):
            continue
        predictors = outcome.get("top_predictors")
        if not isinstance(predictors, list):
            continue
        for predictor in predictors[:max_per_outcome]:
            if not isinstance(predictor, dict):
                continue
            user = predictor.get("user_login") or predictor.get("user_name")
            try:
                record_vote(votes, order, user, index)
            except ParticipationError:
                continue
    return votes, order
