"""Offline core for the 予想王 leaderboard: record resolved prediction
top predictors and rank them by hit rate.

This module performs no Twitch I/O, no shell execution and stores no
credentials. Callers hand in a Helix ``predictions`` row that the corner owner
already fetched and validated (``GET /helix/predictions``); the ledger is a
plain JSON document. Wiring the recording into a live corner tick and rendering
a broadcast view is a separate, explicitly enabled step — see
``docs/features/yosou-oh-corner.md``.

The only viewer fields kept are the ones Twitch itself publishes on the
prediction's ``top_predictors``: the public id/login/display name and the
channel points spent/won on that outcome. Wrong picks are not counted as
appearances unless Twitch lists the viewer, so a hit rate here means "share of
the rounds in which this viewer was listed as a top predictor and chose the
winning outcome".
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

SCHEMA = 1
FILE = "prediction_leaderboard.json"
MAX_BYTES = 512 * 1024
MAX_ROUNDS = 2000
MAX_PREDICTORS = 100
MAX_TEXT = 128
DEFAULT_MIN_ROUNDS = 3
DEFAULT_LIMIT = 10


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


def _check_ledger(ledger) -> None:
    if (not isinstance(ledger, dict) or type(ledger.get("schema")) is not int
            or ledger["schema"] != SCHEMA or not isinstance(ledger.get("rounds"), dict)):
        raise LeaderboardError("invalid leaderboard")


def extract(remote):
    """Split a Helix prediction row into ``(round, predictors)``.

    A row that is not ``RESOLVED`` yields ``(None, [])`` so callers record only
    settled rounds. A malformed resolved row raises :class:`LeaderboardError`
    rather than recording a wrong stat.
    """
    if (not isinstance(remote, dict) or not isinstance(remote.get("id"), str)
            or not 1 <= len(remote["id"]) <= MAX_TEXT
            or not isinstance(remote.get("title"), str)
            or not isinstance(remote.get("outcomes"), list)
            or not 2 <= len(remote["outcomes"]) <= 10):
        raise LeaderboardError("invalid prediction row")
    prediction_id = remote["id"]
    if remote.get("status") != "RESOLVED":
        return None, []
    winner = remote.get("winning_outcome_id")
    if not isinstance(winner, str) or not 1 <= len(winner) <= MAX_TEXT:
        raise LeaderboardError("invalid winning outcome")
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
            predictors.append({
                "user_id": user_id,
                "user_login": _text(row.get("user_login"), "user login"),
                "user_name": _text(row.get("user_name"), "user name"),
                "outcome_id": outcome_id,
                "used": _count(row.get("channel_points_used"), "used points"),
                "won": _count(row.get("channel_points_won"), "won points"),
                "hit": outcome_id == winner,
            })
    if winner not in {outcome["id"] for outcome in outcomes}:
        raise LeaderboardError("winning outcome is not one of the outcomes")
    round_ = {"id": prediction_id, "title": remote["title"],
              "ended_at": str(remote.get("ended_at") or ""),
              "winning_outcome_id": winner, "outcomes": outcomes,
              "predictors": predictors}
    return round_, predictors


def record(ledger: dict, remote, *, corner: str, now: int | None = None) -> bool:
    """Merge one resolved prediction row into ``ledger``.

    Idempotent per prediction id: a row already recorded (or not yet resolved)
    returns ``False`` without touching the ledger, so retried settlement ticks
    cannot double count a round. Returns ``True`` only when a new round landed.
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
    if now is not None:
        if type(now) is not int or now < 0:
            raise LeaderboardError("invalid recorded_at")
        round_["recorded_at"] = now
    ledger["rounds"][prediction_id] = round_
    _prune(ledger)
    return True


def _prune(ledger: dict) -> None:
    rounds = ledger["rounds"]
    if len(rounds) <= MAX_ROUNDS:
        return
    order = sorted(rounds, key=lambda pid: (rounds[pid].get("ended_at", ""), pid))
    for pid in order[:len(rounds) - MAX_ROUNDS]:
        del rounds[pid]


def ranking(ledger: dict, *, min_rounds: int = DEFAULT_MIN_ROUNDS,
            limit: int = DEFAULT_LIMIT) -> list:
    """Rank viewers by hit rate over the recorded rounds.

    A viewer counts once per round in which Twitch listed them as a top
    predictor. ``min_rounds`` keeps one-shot appearances off the board; ties
    break on hits, then net points (won - used), then login for a stable order.
    """
    _check_ledger(ledger)
    if (type(min_rounds) is not int or min_rounds < 1
            or type(limit) is not int or limit < 1):
        raise LeaderboardError("invalid ranking bound")
    totals: dict[str, dict] = {}
    for round_ in ledger["rounds"].values():
        for row in round_.get("predictors", []):
            entry = totals.setdefault(row["user_id"], {
                "user_id": row["user_id"], "user_login": row["user_login"],
                "user_name": row["user_name"], "rounds": 0, "hits": 0,
                "used": 0, "won": 0})
            entry["rounds"] += 1
            entry["hits"] += 1 if row["hit"] else 0
            entry["used"] += row["used"]
            entry["won"] += row["won"]
            entry["user_login"] = row["user_login"]
            entry["user_name"] = row["user_name"]
    rows = []
    for entry in totals.values():
        if entry["rounds"] < min_rounds:
            continue
        entry["hit_rate"] = entry["hits"] / entry["rounds"]
        entry["net"] = entry["won"] - entry["used"]
        rows.append(entry)
    rows.sort(key=lambda e: (-e["hit_rate"], -e["hits"], -e["net"], e["user_login"]))
    return rows[:limit]


def stats(ledger: dict) -> dict:
    """Diagnostics-safe counts only; never viewer names or logins."""
    _check_ledger(ledger)
    viewers = set()
    for round_ in ledger["rounds"].values():
        for row in round_.get("predictors", []):
            viewers.add(row["user_id"])
    return {"rounds": len(ledger["rounds"]), "viewers": len(viewers)}


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
