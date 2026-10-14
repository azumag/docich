"""Bounded public-replay reader for the 衝立将棋 感想戦."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import tsuitate_review as review  # noqa: E402

GAME_ID = "fixtureGame01"
ENDED = "2026-10-14T20:38:05.153Z"
STARTED = "2026-10-14T20:36:48.743Z"
SFEN = "ln2kgsnl/2s1g2b1/+R1pp1pppp/9/9/1P1P5/P+pP2PPPP/1B2+p4/LN2K1SNL b GSPrgp 51"

MOVES = [
    {"usi": "4i5h", "by": "sente", "ms": 180, "foulsBefore": 0},
    {"usi": "8c8d", "by": "gote", "ms": 2449, "foulsBefore": 0},
    {"usi": "8f8g+", "by": "sente", "ms": 1961, "foulsBefore": 0},
    {"usi": "P*5b", "by": "gote", "ms": 3001, "foulsBefore": 1},
]


def _fixture(*, game_id=GAME_ID, result="gote_win", reason="foul_limit", moves=None,
             fouls=(10, 0), foul_attempts=2, ratings=((1005, 1003), (1471, 1473)),
             names=("DoCiAI", "kakuren_bot"), final_sfen=SFEN,
             started=STARTED, ended=ENDED, extra_game=None, drop=()):
    """Build the same flat ``__data.json`` shape the site serves (synthetic)."""
    moves = MOVES if moves is None else moves
    table = [{"game": 1, "senteName": None, "goteName": None}]
    table.append(None)  # index 1: the game object, filled in below

    def add(value):
        table.append(value)
        return len(table) - 1

    move_refs = []
    for move in moves:
        move_refs.append(add({
            "usi": add(move["usi"]), "byColor": add(move["by"]), "ms": add(move["ms"]),
            "foulsBefore": add(move["foulsBefore"]),
        }))
    game = {
        "id": add(game_id),
        "result": add(result),
        "reason": add(reason),
        "moves": add(move_refs),
        "foulAttempts": add([add({"at": add(index)}) for index in range(foul_attempts)]),
        "senteFouls": add(fouls[0]),
        "goteFouls": add(fouls[1]),
        "senteRatingBefore": add(ratings[0][0]),
        "senteRatingAfter": add(ratings[0][1]),
        "goteRatingBefore": add(ratings[1][0]),
        "goteRatingAfter": add(ratings[1][1]),
        "startedAt": add(["Date", started]),
        "endedAt": add(["Date", ended]),
        "finalSfen": add(final_sfen),
    }
    for key in drop:
        del game[key]
    if extra_game:
        game.update(extra_game)
    table[1] = game
    table[0]["senteName"] = add(names[0])
    table[0]["goteName"] = add(names[1])
    return {
        "type": "data",
        "nodes": [
            {"type": "data", "data": [{"user": 1}, None], "uses": {}},
            {"type": "data", "data": table, "uses": {}},
        ],
    }


def _terminal(**overrides):
    value = {
        "gameId": GAME_ID, "outcome": "win", "reason": "foul_limit",
        "endedAt": ENDED, "moveNumber": 4, "resultConfidence": "verified",
    }
    value.update(overrides)
    return value


class _Response:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def read(self, limit):
        return self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class _Opener:
    def __init__(self, body=None, error=None, status=200):
        self.body = body
        self.error = error
        self.status = status
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return _Response(self.body, self.status)


def test_replay_url_is_pinned_to_the_public_host():
    assert review.replay_url(GAME_ID) == f"https://beta.tsuitate.info/games/{GAME_ID}/__data.json"
    for bad in ["", "../etc", "a/b", "x" * 129, None, 7]:
        with pytest.raises(review.ReviewError):
            review.replay_url(bad)


def test_read_replay_parses_the_bounded_public_facts():
    opener = _Opener(json.dumps(_fixture()).encode("utf-8"))
    parsed = review.read_replay(GAME_ID, opener=opener)

    assert parsed["gameId"] == GAME_ID
    assert parsed["result"] == "gote_win"
    assert parsed["reason"] == "foul_limit"
    assert parsed["endedAt"] == ENDED
    assert parsed["moves"] == MOVES
    assert parsed["fouls"] == {"sente": 10, "gote": 0}
    assert parsed["foulAttempts"] == 2
    assert parsed["ratings"]["sente"] == {"before": 1005, "after": 1003}
    assert parsed["ratings"]["gote"] == {"before": 1471, "after": 1473}
    assert parsed["names"] == {"sente": "DoCiAI", "gote": "kakuren_bot"}
    assert parsed["finalSfen"] == SFEN
    # The reader only ever asks the pinned https URL.
    assert opener.requests[0].full_url == review.replay_url(GAME_ID)
    assert opener.requests[0].method == "GET"


def test_read_replay_fails_closed_on_every_drift():
    cases = {
        "not-json": b"{not json",
        "too-large": b"x" * (review.MAX_REPLAY_BYTES + 1),
        "not-data": json.dumps({"type": "other", "nodes": []}).encode(),
        "no-node": json.dumps({"type": "data", "nodes": []}).encode(),
        "wrong-id": json.dumps(_fixture(game_id="anotherGame")).encode(),
        "bad-result": json.dumps(_fixture(result="sente_mate")).encode(),
        "bad-move": json.dumps(_fixture(moves=[{"usi": "9z9z", "by": "sente", "ms": 1,
                                                "foulsBefore": 0}])).encode(),
        "bad-ms": json.dumps(_fixture(moves=[{"usi": "7g7f", "by": "sente",
                                              "ms": review.MAX_THINK_MS + 1,
                                              "foulsBefore": 0}])).encode(),
        "missing": json.dumps(_fixture(drop=("senteFouls",))).encode(),
        "bad-sfen": json.dumps(_fixture(final_sfen="<script>")).encode(),
        "too-many-moves": json.dumps(_fixture(
            moves=[{"usi": "7g7f", "by": "sente", "ms": 1, "foulsBefore": 0}] * (review.MAX_MOVES + 1)
        )).encode(),
    }
    for name, body in cases.items():
        with pytest.raises(review.ReviewError):
            review.read_replay(GAME_ID, opener=_Opener(body))


def test_read_replay_refuses_a_network_error_and_a_non_200():
    with pytest.raises(review.ReviewError):
        review.read_replay(GAME_ID, opener=_Opener(error=urllib.error.URLError("boom")))
    with pytest.raises(review.ReviewError):
        review.read_replay(GAME_ID, opener=_Opener(json.dumps(_fixture()).encode(), status=302))


def test_verify_pins_the_replay_to_the_settled_match():
    parsed = review.parse_review(_fixture(), GAME_ID)
    assert review.verify(parsed, _terminal(), color="gote") is True
    # A different end time, reason or game id is another match.
    assert review.verify(parsed, _terminal(endedAt="2026-10-14T20:38:06.000Z")) is False
    assert review.verify(parsed, _terminal(reason="checkmate")) is False
    assert review.verify(parsed, _terminal(gameId="otherGame01")) is False
    # With the owner colour known the outcome is cross-checked too.
    assert review.verify(parsed, _terminal(outcome="loss"), color="gote") is False
    # Without a colour the identity is the id plus the settled end facts.
    assert review.verify(parsed, _terminal(outcome="loss")) is True


def test_verify_accepts_an_equivalent_instant_and_normalizes_the_reason():
    parsed = review.parse_review(_fixture(), GAME_ID)

    # The control projection normalizes the date through Date.toISOString while
    # this reader keeps the site's raw string: the same instant still matches.
    assert review.verify(parsed, _terminal(endedAt="2026-10-14T20:38:05.153+00:00")) is True
    assert review.verify(parsed, _terminal(endedAt="2026-10-14T20:38:05Z")) is False
    assert review.verify(parsed, _terminal(endedAt="not-a-date")) is False

    # The Worker publishes its own reason vocabulary; an unmapped site reason is
    # "unknown" there and must be "unknown" here too.
    unmapped = review.parse_review(_fixture(reason="repetition"), GAME_ID)
    assert review.normalized_reason(unmapped) == "unknown"
    assert review.verify(unmapped, _terminal(reason="unknown")) is True
    assert review.verify(unmapped, _terminal(reason="repetition")) is False


def test_summary_for_bot_keeps_only_counts_and_timings():
    parsed = review.parse_review(_fixture(), GAME_ID)
    summary = review.summary_for_bot(parsed, color="gote")

    assert summary["outcome"] == "win"
    assert summary["moveCount"] == 4
    assert summary["ownMoves"] == 2 and summary["opponentMoves"] == 2
    assert summary["own"] == {"moves": 2, "totalMs": 5450, "maxMs": 3001, "medianMs": 3001}
    assert summary["opponent"] == {"moves": 2, "totalMs": 2141, "maxMs": 1961, "medianMs": 1961}
    assert summary["fouls"] == 0 and summary["opponentFouls"] == 10
    assert summary["ratingBefore"] == 1471 and summary["ratingAfter"] == 1473
    assert summary["opponentName"] == "DoCiAI"
    assert summary["lastMove"] == {"usi": "P*5b", "by": "gote", "ms": 3001}
    assert summary["promotions"] == 1 and summary["drops"] == 1
    assert "moves" not in summary  # the raw move list never leaves this module
    with pytest.raises(review.ReviewError):
        review.summary_for_bot(parsed, color="spectator")


def test_read_summary_returns_none_instead_of_failing_the_corner():
    good = _Opener(json.dumps(_fixture()).encode())
    summary = review.read_summary(GAME_ID, _terminal(), color="gote", opener=good)
    assert summary is not None and summary["moveCount"] == 4

    # A replay of another match, a broken page and a dead host are all ``None``.
    assert review.read_summary(GAME_ID, _terminal(reason="checkmate"), color="gote",
                               opener=_Opener(json.dumps(_fixture()).encode())) is None
    assert review.read_summary(GAME_ID, _terminal(), color="gote",
                               opener=_Opener(b"not json")) is None
    assert review.read_summary(GAME_ID, _terminal(), color="gote",
                               opener=_Opener(error=urllib.error.URLError("down"))) is None


def test_review_never_persists_the_move_list():
    """The module is a reader: it has no filesystem write path at all."""
    source = (ROOT / "src" / "docich" / "tsuitate_review.py").read_text(encoding="utf-8")
    for forbidden in ("import os", "json.dump", "atomic_write", "os.replace",
                      "Path(", "state_dir", "write_text", "mkdir", "json.dump("):
        assert forbidden not in source, forbidden
