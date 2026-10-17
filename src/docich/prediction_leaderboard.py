"""Offline core for the 予想王 leaderboard: record the participation and the
result of settled channel-point predictions and rank viewers by five
independent metrics.

This module performs no Twitch I/O, no shell execution and stores no
credentials. Callers hand in a Helix ``predictions`` row that the corner owner
already fetched and validated (``GET /helix/predictions``); the ledger is a
plain JSON document. Wiring the recording into a live corner tick and rendering
a broadcast view is a separate, explicitly enabled step — see
``docs/features/yosou-oh-corner.md``.

Two kinds of settled rounds are recorded (Issue #933 addendum):

* ``RESOLVED`` — a winning outcome was chosen. A ``top_predictors`` row is a
  hit when its outcome is the winner and a miss otherwise; the distributed
  points are a gain and the stake of a miss is a loss.
* ``CANCELED`` — the prediction was canceled and every stake refunded. The
  participation (``used``) and the refund (``refunded``) are kept, but a
  canceled round is never counted as a hit or a miss and its refund is never
  counted as a gain or a loss (the refund returns the stake, it does not pay a
  prize).

Only the viewer fields Twitch itself publishes on a prediction's
``top_predictors`` are kept: the public id/login/display name and the channel
points spent/won on that outcome. Twitch publishes the top predictors per
outcome rather than every participant, so every metric here is a "top
predictors" metric, not a full participant census.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import tempfile

SCHEMA = 2
LEGACY_SCHEMA = 1
FILE = "prediction_leaderboard.json"
LOCK_FILE = "prediction-leaderboard.json.lock"
MAX_BYTES = 512 * 1024
MAX_ROUNDS = 2000
MAX_PREDICTORS = 100
MAX_TEXT = 128
DEFAULT_MIN_ROUNDS = 3
DEFAULT_MIN_PARTICIPATIONS = 1
DEFAULT_LIMIT = 10

RESOLVED = "RESOLVED"
CANCELED = "CANCELED"
STATUSES = frozenset({RESOLVED, CANCELED})
#: Fixed vocabulary for the reason a round was refunded. Only the owner-decided
#: no-contest cancellation is recorded today; other cancels are not recorded.
CANCEL_REASONS = frozenset({"no-contest"})

#: The five independent ranking boards (Issue #933 addendum). ``hit_rate`` is
#: the original board; the other four are participation and points boards.
METRICS = ("hit_rate", "staked", "won", "lost", "plays")
_MIN_COUNT = {"hit_rate": "rounds", "staked": "participations", "won": "participations",
              "lost": "participations", "plays": "participations"}
#: Boards that rank a points total only list viewers who actually have some of
#: it; a gained/lost board padded with zeros below the real entries has no
#: meaning. The stake and play boards list every participant.
_MIN_VALUE = {"won": "won", "lost": "lost"}
_SORT = {
    "hit_rate": lambda e: (-e["hit_rate"], -e["hits"], -e["net"], e["user_login"]),
    "staked": lambda e: (-e["used"], -e["participations"], e["user_login"]),
    "won": lambda e: (-e["won"], -e["participations"], e["user_login"]),
    "lost": lambda e: (-e["lost"], -e["participations"], e["user_login"]),
    "plays": lambda e: (-e["participations"], -e["rounds"], e["user_login"]),
}


class LeaderboardError(ValueError):
    """The prediction row or the ledger violates the record contract."""


def new_ledger() -> dict:
    return {"schema": SCHEMA, "rounds": {}}


def _text(value, what: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_TEXT:
        raise LeaderboardError(f"invalid {what}")
    return value


def _count(value, what: str) -> int:
    if type(value) is not int or value < 0:
        raise LeaderboardError(f"invalid {what}")
    return value


def _check_predictor(row) -> None:
    if not isinstance(row, dict):
        raise LeaderboardError("invalid predictor")
    _text(row.get("user_id"), "user id")
    _count(row.get("used"), "used points")
    _count(row.get("won"), "won points")
    _count(row.get("refunded", 0), "refunded points")
    hit = row.get("hit")
    if hit is not None and not isinstance(hit, bool):
        raise LeaderboardError("invalid hit")


def _check_round(round_) -> None:
    if not isinstance(round_, dict):
        raise LeaderboardError("invalid round")
    status = round_.get("status")
    if status not in STATUSES:
        raise LeaderboardError("invalid round status")
    if not isinstance(round_.get("outcomes"), list):
        raise LeaderboardError("invalid round outcomes")
    predictors = round_.get("predictors")
    if not isinstance(predictors, list):
        raise LeaderboardError("invalid round predictors")
    winner = round_.get("winning_outcome_id")
    if status == RESOLVED and not isinstance(winner, str):
        raise LeaderboardError("resolved round without a winner")
    if status == CANCELED and winner is not None:
        raise LeaderboardError("canceled round with a winner")
    if status == CANCELED and round_.get("cancel_reason") not in CANCEL_REASONS:
        raise LeaderboardError("canceled round without a reason")
    for row in predictors:
        _check_predictor(row)
        if row.get("refunded", 0) != (row["used"] if status == CANCELED else 0):
            raise LeaderboardError("refund does not match the round status")
        if status == CANCELED and (row.get("hit") is not None or row.get("won")):
            raise LeaderboardError("canceled round graded as won or lost")
        if status == RESOLVED and not isinstance(row.get("hit"), bool):
            raise LeaderboardError("resolved round without a hit")


def _check_ledger(ledger) -> None:
    if (not isinstance(ledger, dict) or type(ledger.get("schema")) is not int
            or ledger["schema"] != SCHEMA or not isinstance(ledger.get("rounds"), dict)):
        raise LeaderboardError("invalid leaderboard")
    for prediction_id, round_ in ledger["rounds"].items():
        if not isinstance(prediction_id, str):
            raise LeaderboardError("invalid round id")
        _check_round(round_)


def _upgrade_legacy(ledger) -> dict:
    """Normalise a schema-1 ledger (``RESOLVED`` rounds only) into schema 2.

    Schema 1 stored no round status and no refund, so every round it holds is a
    graded round with nothing refunded. Anything that does not fit that shape is
    refused rather than migrated into a wrong statistic.
    """
    if (not isinstance(ledger, dict) or type(ledger.get("schema")) is not int
            or ledger["schema"] != LEGACY_SCHEMA or not isinstance(ledger.get("rounds"), dict)):
        raise LeaderboardError("invalid leaderboard")
    for prediction_id, round_ in ledger["rounds"].items():
        if not isinstance(prediction_id, str) or not isinstance(round_, dict):
            raise LeaderboardError("invalid round")
        predictors = round_.get("predictors")
        if not isinstance(predictors, list):
            raise LeaderboardError("invalid round predictors")
        round_["status"] = RESOLVED
        for row in predictors:
            if not isinstance(row, dict):
                raise LeaderboardError("invalid predictor")
            row["refunded"] = 0
    ledger["schema"] = SCHEMA
    return ledger


def extract(remote):
    """Split a Helix prediction row into ``(round, predictors)``.

    A row that is neither ``RESOLVED`` nor ``CANCELED`` yields ``(None, [])`` so
    callers record only settled rounds. A malformed settled row raises
    :class:`LeaderboardError` rather than recording a wrong statistic.

    On a ``CANCELED`` row the stake is kept as both ``used`` and ``refunded``,
    ``won`` is zero, and ``hit`` is ``None``: a cancellation proves neither a
    hit nor a miss, so the round must not be graded.
    """
    if (not isinstance(remote, dict) or not isinstance(remote.get("id"), str)
            or not 1 <= len(remote["id"]) <= MAX_TEXT
            or not isinstance(remote.get("title"), str)
            or not isinstance(remote.get("outcomes"), list)
            or not 2 <= len(remote["outcomes"]) <= 10):
        raise LeaderboardError("invalid prediction row")
    prediction_id = remote["id"]
    status = remote.get("status")
    if status not in STATUSES:
        return None, []
    winner = remote.get("winning_outcome_id")
    if status == RESOLVED:
        if not isinstance(winner, str) or not 1 <= len(winner) <= MAX_TEXT:
            raise LeaderboardError("invalid winning outcome")
    elif winner not in (None, ""):
        raise LeaderboardError("canceled prediction with a winning outcome")
    outcomes = []
    predictors = []
    seen = set()
    for outcome in remote["outcomes"]:
        if (not isinstance(outcome, dict) or not isinstance(outcome.get("id"), str)
                or not 1 <= len(outcome["id"]) <= MAX_TEXT
                or not isinstance(outcome.get("title"), str)):
            raise LeaderboardError("invalid outcome")
        outcome_id = outcome["id"]
        outcomes.append({"id": outcome_id, "title": outcome["title"],
                         "users": _count(outcome.get("users", 0), "users"),
                         "points": _count(outcome.get("channel_points", 0), "points")})
        rows = outcome.get("top_predictors") or []
        if not isinstance(rows, list) or len(rows) > MAX_PREDICTORS:
            raise LeaderboardError("invalid top predictors")
        for row in rows:
            if not isinstance(row, dict):
                raise LeaderboardError("invalid top predictor")
            user_id = _text(row.get("user_id"), "user id")
            if (user_id, outcome_id) in seen:
                raise LeaderboardError("duplicate top predictor")
            seen.add((user_id, outcome_id))
            used = _count(row.get("channel_points_used"), "used points")
            if status == RESOLVED:
                hit = outcome_id == winner
                won = _count(row.get("channel_points_won"), "won points")
                refunded = 0
            else:
                # A canceled prediction distributes nothing. Twitch may omit
                # channel_points_won, send it as null, or still echo the value
                # it would have paid; the cancellation is authoritative, so the
                # stored gain is always zero and the stake is the refund.
                hit = None
                won = row.get("channel_points_won")
                if won is not None:
                    _count(won, "won points")
                won, refunded = 0, used
            predictors.append({
                "user_id": user_id,
                "user_login": _text(row.get("user_login"), "user login"),
                "user_name": _text(row.get("user_name"), "user name"),
                "outcome_id": outcome_id,
                "used": used,
                "won": won,
                "refunded": refunded,
                "hit": hit,
            })
    if status == RESOLVED and winner not in {outcome["id"] for outcome in outcomes}:
        raise LeaderboardError("winning outcome is not one of the outcomes")
    round_ = {"id": prediction_id, "title": remote["title"],
              "ended_at": str(remote.get("ended_at") or ""),
              "status": status,
              "winning_outcome_id": winner if status == RESOLVED else None,
              "outcomes": outcomes, "predictors": predictors}
    return round_, predictors


def record(ledger: dict, remote, *, corner: str, now: int | None = None,
           cancel_reason: str | None = None) -> bool:
    """Merge one settled prediction row into ``ledger``.

    Idempotent per prediction id: a row already recorded (or not yet settled)
    returns ``False`` without touching the ledger, so retried settlement ticks
    cannot double count a round. Returns ``True`` only when a new round landed.

    A ``CANCELED`` row must name one of :data:`CANCEL_REASONS`: the refund
    reason is part of the record, and an unexplained cancellation is refused
    instead of being stored as a silent zero.
    """
    _check_ledger(ledger)
    prediction_id = remote.get("id") if isinstance(remote, dict) else None
    if not isinstance(prediction_id, str) or not 1 <= len(prediction_id) <= MAX_TEXT:
        raise LeaderboardError("invalid prediction row")
    if prediction_id in ledger["rounds"]:
        return False
    round_, _ = extract(remote)
    if round_ is None:
        return False
    round_["corner"] = _text(corner, "corner")
    if round_["status"] == CANCELED:
        if cancel_reason not in CANCEL_REASONS:
            raise LeaderboardError("canceled round requires a known reason")
        round_["cancel_reason"] = cancel_reason
    elif cancel_reason is not None:
        raise LeaderboardError("cancel reason on a resolved round")
    if now is not None:
        if type(now) is not int or now < 0:
            raise LeaderboardError("invalid recorded_at")
        round_["recorded_at"] = now
    ledger["rounds"][prediction_id] = round_
    _prune(ledger)
    return True


def record_settled(state_dir, remote, *, corner: str, now: int | None = None,
                   cancel_reason: str | None = None) -> bool:
    """Merge one settled prediction row into the ledger under the shared lock.

    This is the single writer path for every prediction owner (Hanjuku's corner
    tick and Soren's resolved-round handoff). The lock serializes the
    read-modify-write, :func:`save` replaces the file atomically, and a row that
    is already recorded (or not settled) is a no-op. A malformed ledger or row
    raises instead of being overwritten, so the caller can retry the same
    prediction id later.
    """
    state_dir = Path(state_dir)
    lock_path = state_dir / "locks" / LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = load(state_dir / FILE)
        recorded = record(ledger, remote, corner=corner, now=now,
                          cancel_reason=cancel_reason)
        if recorded:
            save(state_dir / FILE, ledger)
        return recorded


def record_resolved(state_dir, remote, *, corner: str, now: int | None = None) -> bool:
    """Compatibility name for the resolved-round handoff writer.

    Soren's ``prediction_resolved.jsonl`` carries ``RESOLVED`` rows only, so the
    callers that already used this name keep their behaviour; a canceled row
    handed in here is refused by :func:`record_settled` for the missing reason.
    """
    return record_settled(state_dir, remote, corner=corner, now=now)


def _prune(ledger: dict) -> None:
    rounds = ledger["rounds"]
    if len(rounds) <= MAX_ROUNDS:
        return
    order = sorted(rounds, key=lambda pid: (rounds[pid].get("ended_at", ""), pid))
    for pid in order[:len(rounds) - MAX_ROUNDS]:
        del rounds[pid]


def _totals(ledger: dict, *, statuses=frozenset(STATUSES)) -> dict:
    """Accumulate every per-viewer total the five boards need.

    ``rounds`` counts graded (``RESOLVED``) appearances — the hit-rate
    denominator — while ``participations`` counts every recorded appearance,
    including no-contest cancellations. ``lost`` is the stake of a graded miss
    and ``refunded`` is returned principal, so neither ever doubles as the
    other. ``statuses`` restricts the accumulation for the graded-only board.
    """
    totals: dict[str, dict] = {}
    for round_ in ledger["rounds"].values():
        status = round_.get("status")
        if status not in statuses:
            continue
        for row in round_.get("predictors", []):
            entry = totals.setdefault(row["user_id"], {
                "user_id": row["user_id"], "user_login": row["user_login"],
                "user_name": row["user_name"], "rounds": 0, "participations": 0,
                "hits": 0, "used": 0, "won": 0, "lost": 0, "refunded": 0})
            entry["participations"] += 1
            entry["used"] += row["used"]
            entry["won"] += row["won"]
            entry["refunded"] += row.get("refunded", 0)
            if status == RESOLVED:
                entry["rounds"] += 1
                if row["hit"]:
                    entry["hits"] += 1
                else:
                    entry["lost"] += row["used"] - row.get("refunded", 0)
            entry["user_login"] = row["user_login"]
            entry["user_name"] = row["user_name"]
    for entry in totals.values():
        entry["net"] = entry["won"] - entry["used"] + entry["refunded"]
        entry["hit_rate"] = entry["hits"] / entry["rounds"] if entry["rounds"] else 0.0
    return totals


def ranking(ledger: dict, *, min_rounds: int = DEFAULT_MIN_ROUNDS,
            limit: int = DEFAULT_LIMIT) -> list:
    """Rank viewers by hit rate over the graded rounds.

    A viewer counts once per ``RESOLVED`` round in which Twitch listed them as a
    top predictor; a no-contest cancellation is participation evidence, not a
    graded round, so it neither raises nor lowers the rate and its refund is not
    part of this board's ``used`` / ``won`` / ``net`` columns. ``min_rounds``
    keeps one-shot appearances off the board; ties break on hits, then net
    points (won - used), then login for a stable order.
    """
    _check_ledger(ledger)
    if (type(min_rounds) is not int or min_rounds < 1
            or type(limit) is not int or limit < 1):
        raise LeaderboardError("invalid ranking bound")
    totals = _totals(ledger, statuses=frozenset({RESOLVED}))
    rows = [entry for entry in totals.values() if entry["rounds"] >= min_rounds]
    rows.sort(key=_SORT["hit_rate"])
    return rows[:limit]


def boards(ledger: dict, *, min_rounds: int = DEFAULT_MIN_ROUNDS,
           min_participations: int = DEFAULT_MIN_PARTICIPATIONS,
           limit: int = DEFAULT_LIMIT) -> dict:
    """Return the five independent ranking boards keyed by :data:`METRICS`.

    Each board is an independent list with its own threshold and deterministic
    tie-break (see ``_SORT``): the hit-rate board needs graded rounds, while the
    participation and points boards count every recorded appearance, including
    no-contest cancellations. ``min_rounds`` is never reused for the boards that
    do not grade a round.
    """
    _check_ledger(ledger)
    if (type(min_rounds) is not int or min_rounds < 1
            or type(min_participations) is not int or min_participations < 1
            or type(limit) is not int or limit < 1):
        raise LeaderboardError("invalid ranking bound")
    totals = _totals(ledger)
    result = {}
    for metric in METRICS:
        minimum = min_rounds if metric == "hit_rate" else min_participations
        count_key = _MIN_COUNT[metric]
        value_key = _MIN_VALUE.get(metric)
        rows = [dict(entry, metric=metric) for entry in totals.values()
                if entry[count_key] >= minimum
                and (value_key is None or entry[value_key] > 0)]
        rows.sort(key=_SORT[metric])
        result[metric] = rows[:limit]
    return result


def stats(ledger: dict) -> dict:
    """Diagnostics-safe counts only; never viewer names or logins."""
    _check_ledger(ledger)
    viewers = set()
    canceled = 0
    for round_ in ledger["rounds"].values():
        if round_.get("status") == CANCELED:
            canceled += 1
        for row in round_.get("predictors", []):
            viewers.add(row["user_id"])
    return {"rounds": len(ledger["rounds"]), "viewers": len(viewers), "canceled": canceled}


def load(path) -> dict:
    path = Path(path)
    if not path.exists():
        return new_ledger()
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise LeaderboardError("leaderboard is not a bounded regular file")
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LeaderboardError("leaderboard is unreadable") from exc
    if isinstance(ledger, dict) and ledger.get("schema") == LEGACY_SCHEMA:
        ledger = _upgrade_legacy(ledger)
    _check_ledger(ledger)
    return ledger


def save(path, ledger: dict) -> None:
    _check_ledger(ledger)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(ledger, stream, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
