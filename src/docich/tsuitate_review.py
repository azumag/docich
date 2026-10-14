"""Bounded reader for the public replay of one finished 衝立将棋 match.

The corner's 感想戦 needs the public move list, the thinking times, the fouls and
the rating change of the match it just played.  beta.tsuitate.info publishes them
as SvelteKit ``__data.json`` under ``/games/<gameId>``.  That is public frontend
data, not a promised API, so every read is:

- fixed host, https, no redirect, no ambient proxy, no credentials,
- bounded body, bounded table, bounded move/foul counts,
- fail closed: any drift returns ``None`` and the narration falls back to the
  reviewed facts docich already owns.

Nothing is persisted here.  The replay is fetched transiently once, at the end of
a match, and only derived text and bounded counters survive in the corner state;
the move list itself never reaches the view, the diagnostics or a file.
"""
from __future__ import annotations

import datetime as _datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request

REPLAY_HOST = "beta.tsuitate.info"
REPLAY_TIMEOUT_S = 5.0
MAX_REPLAY_BYTES = 256 * 1024
MAX_TABLE_ENTRIES = 100_000
MAX_MOVES = 512
MAX_FOUL_ATTEMPTS = 256
MAX_THINK_MS = 3_600_000
MAX_SFEN = 200
GAME_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
#: USI square pair with an optional promotion, or a drop (``P*5b``).
USI = re.compile(r"(?:[1-9][a-i][1-9][a-i]\+?|[PLNSGBR]\*[1-9][a-i])\Z")
SFEN = re.compile(r"[A-Za-z0-9+/ ]{1,%d}\Z" % MAX_SFEN)
NAME = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
REASON = re.compile(r"[a-z_]{1,32}\Z")
SITE_RESULTS = {"sente_win", "gote_win", "draw", "aborted"}
COLORS = {"sente", "gote"}


class ReviewError(RuntimeError):
    """The public replay could not be read for this match."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ReviewError("public replay redirect refused")


def replay_url(game_id: str) -> str:
    if not isinstance(game_id, str) or not GAME_ID.fullmatch(game_id):
        raise ReviewError("invalid beta game id")
    return f"https://{REPLAY_HOST}/games/{urllib.parse.quote(game_id)}/__data.json"


def _bounded_number(table, index, maximum):
    value = _entry(table, index)
    if type(value) is not int or not 0 <= value <= maximum:
        raise ReviewError("unexpected replay number")
    return value


def _entry(table, index):
    if type(index) is not int or not 0 <= index < len(table):
        raise ReviewError("replay reference is out of range")
    return table[index]


def _text(table, index, pattern):
    value = _entry(table, index)
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ReviewError("unexpected replay text")
    return value


def _date(table, index):
    value = _entry(table, index)
    if (not isinstance(value, list) or len(value) != 2 or value[0] != "Date"
            or not isinstance(value[1], str) or len(value[1]) > 40):
        raise ReviewError("unexpected replay date")
    return value[1]


def _table_of(payload, game_id):
    """Return the single data table whose root carries this game id."""
    if not isinstance(payload, dict) or payload.get("type") != "data":
        raise ReviewError("replay is not frontend data")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 8:
        raise ReviewError("unexpected replay nodes")
    found = None
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "data":
            continue
        table = node.get("data")
        if not isinstance(table, list) or not 1 <= len(table) <= MAX_TABLE_ENTRIES:
            continue
        root = table[0]
        if not isinstance(root, dict) or "game" not in root:
            continue
        game = _entry(table, root["game"])
        if not isinstance(game, dict) or "id" not in game:
            continue
        if _text(table, game["id"], GAME_ID) != game_id:
            continue
        if found is not None:
            raise ReviewError("replay carries the game id twice")
        found = (table, root, game)
    if found is None:
        raise ReviewError("replay does not carry this game id")
    return found


def _moves(table, game):
    raw = _entry(table, game["moves"]) if "moves" in game else None
    if not isinstance(raw, list) or len(raw) > MAX_MOVES:
        raise ReviewError("unexpected replay move list")
    moves = []
    for reference in raw:
        move = _entry(table, reference)
        if not isinstance(move, dict) or set(move) != {"usi", "byColor", "ms", "foulsBefore"}:
            raise ReviewError("unexpected replay move shape")
        moves.append({
            "usi": _text(table, move["usi"], USI),
            "by": _text(table, move["byColor"], re.compile(r"(?:sente|gote)\Z")),
            "ms": _bounded_number(table, move["ms"], MAX_THINK_MS),
            "foulsBefore": _bounded_number(table, move["foulsBefore"], 10),
        })
    return moves


def _names(root):
    names = {}
    for side in ("sente", "gote"):
        key = f"{side}Name"
        names[side] = None
        if key in root:
            names[side] = root[key]
    return names


def parse_review(payload, game_id: str) -> dict:
    """Rebuild the bounded public facts of one finished match.

    Raises :class:`ReviewError` on any shape drift so the caller can fall back
    instead of narrating unverified data.
    """
    table, root, game = _table_of(payload, game_id)
    required = {
        "result", "reason", "moves", "senteFouls", "goteFouls", "foulAttempts",
        "senteRatingBefore", "senteRatingAfter", "goteRatingBefore", "goteRatingAfter",
        "startedAt", "endedAt",
    }
    if not required.issubset(game):
        raise ReviewError("replay is missing required fields")
    result = _text(table, game["result"], re.compile(r"[a-z_]{1,16}\Z"))
    if result not in SITE_RESULTS:
        raise ReviewError("unexpected replay result")
    attempts = _entry(table, game["foulAttempts"])
    if not isinstance(attempts, list) or len(attempts) > MAX_FOUL_ATTEMPTS:
        raise ReviewError("unexpected replay foul attempts")
    names = {}
    for side in ("sente", "gote"):
        key = f"{side}Name"
        names[side] = _text(table, root[key], NAME) if key in root else None
    review = {
        "gameId": game_id,
        "result": result,
        "reason": _text(table, game["reason"], REASON),
        "startedAt": _date(table, game["startedAt"]),
        "endedAt": _date(table, game["endedAt"]),
        "moves": _moves(table, game),
        "fouls": {
            "sente": _bounded_number(table, game["senteFouls"], 10),
            "gote": _bounded_number(table, game["goteFouls"], 10),
        },
        "foulAttempts": len(attempts),
        "ratings": {
            side: {
                "before": _bounded_number(table, game[f"{side}RatingBefore"], 100000),
                "after": _bounded_number(table, game[f"{side}RatingAfter"], 100000),
            }
            for side in ("sente", "gote")
        },
        "names": names,
    }
    if "finalSfen" in game:
        review["finalSfen"] = _text(table, game["finalSfen"], SFEN)
    return review


def read_replay(game_id: str, *, opener=None, timeout: float = REPLAY_TIMEOUT_S) -> dict:
    """Fetch and parse the public replay; never follows a redirect."""
    request = urllib.request.Request(replay_url(game_id), method="GET", headers={
        "Accept": "application/json",
        "User-Agent": "docich-tsuitate-review/1.0",
    })
    if opener is None:
        # No redirects and no ambient proxy: this read never leaves the pinned host.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                raise ReviewError("public replay is not available")
            raw = response.read(MAX_REPLAY_BYTES + 1)
    except ReviewError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReviewError("public replay is unreachable") from exc
    if len(raw) > MAX_REPLAY_BYTES:
        raise ReviewError("public replay is too large")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ReviewError("public replay is not JSON") from exc
    return parse_review(payload, game_id)


def _outcome_for_bot(result: str, color: str) -> str:
    if result == "aborted":
        return "unknown"
    if result == "draw":
        return "draw"
    return "win" if (result == "sente_win") == (color == "sente") else "loss"


#: The Worker's own site-reason vocabulary. Anything else is ``unknown`` there,
#: so the replay is normalized the same way before it is compared.
SITE_REASONS = {
    "checkmate": "checkmate",
    "resign": "resign",
    "timeout": "timeout",
    "foul_limit": "foul_limit",
    "disconnect": "disconnect",
    "stalemate": "stalemate",
}


def normalized_reason(review: dict) -> str:
    if review.get("result") == "aborted":
        return "interrupted"
    return SITE_REASONS.get(review.get("reason"), "unknown")


def _same_instant(left, right) -> bool:
    """Compare two ISO timestamps by instant, not by string spelling.

    The control projection normalizes the site's date through ``Date.toISOString``
    while this reader keeps the raw string, so an equivalent instant written with
    another offset must still identify the same match.
    """
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    try:
        return (_datetime.datetime.fromisoformat(left.replace("Z", "+00:00"))
                == _datetime.datetime.fromisoformat(right.replace("Z", "+00:00")))
    except (TypeError, ValueError, AttributeError):
        return False


def verify(review: dict, terminal: dict, *, color: str | None = None) -> bool:
    """Whether the replay is the very match the corner already settled.

    The identity is the game id plus the settled end instant and the normalized
    end reason; when the owner projection still carries the bot's colour, the
    outcome is cross-checked too.  A mismatch means the replay belongs to
    another match and must not be narrated.
    """
    if not isinstance(review, dict) or not isinstance(terminal, dict):
        return False
    if review.get("gameId") != terminal.get("gameId"):
        return False
    if not _same_instant(review.get("endedAt"), terminal.get("endedAt")):
        return False
    if normalized_reason(review) != terminal.get("reason"):
        return False
    if color in COLORS:
        return _outcome_for_bot(review["result"], color) == terminal.get("outcome")
    return True


def _think_stats(moves, side):
    values = [move["ms"] for move in moves if move["by"] == side]
    if not values:
        return {"moves": 0, "totalMs": 0, "maxMs": 0, "medianMs": 0}
    ordered = sorted(values)
    return {
        "moves": len(values),
        "totalMs": sum(values),
        "maxMs": ordered[-1],
        "medianMs": ordered[len(ordered) // 2],
    }


def summary_for_bot(review: dict, *, color: str) -> dict:
    """Derive the bot's own bounded view of one verified replay.

    Only counts and timings survive: the narration never receives the raw move
    list, and no board is reconstructed.
    """
    if color not in COLORS:
        raise ReviewError("the bot's colour is unknown")
    opponent = "gote" if color == "sente" else "sente"
    moves = review["moves"]
    own = [move for move in moves if move["by"] == color]
    last = moves[-1] if moves else None
    return {
        "gameId": review["gameId"],
        "color": color,
        "outcome": _outcome_for_bot(review["result"], color),
        "reason": review["reason"],
        "moveCount": len(moves),
        "ownMoves": len(own),
        "opponentMoves": len(moves) - len(own),
        "own": _think_stats(moves, color),
        "opponent": _think_stats(moves, opponent),
        "fouls": review["fouls"][color],
        "opponentFouls": review["fouls"][opponent],
        "foulAttempts": review["foulAttempts"],
        "ratingBefore": review["ratings"][color]["before"],
        "ratingAfter": review["ratings"][color]["after"],
        "opponentName": review["names"].get(opponent),
        "lastMove": ({"usi": last["usi"], "by": last["by"], "ms": last["ms"]}
                     if last is not None else None),
        "promotions": sum(1 for move in moves if move["usi"].endswith("+")),
        "drops": sum(1 for move in moves if "*" in move["usi"]),
    }


#: Fixed vocabulary for why a 感想戦 could not use the public replay.  The
#: corner records it so a silent fallback is diagnosable without a log.
REVIEW_FAILURES = frozenset({
    "ok", "read-failed", "verify-failed", "invalid-color", "invalid-game-id",
})


def _infer_color(review: dict, outcome) -> str | None:
    """Recover the bot's side from the settled outcome and the replay result.

    The owner projection carries the colour while the match is live, but the
    public replay plus the settled outcome are enough on their own, so the 感想戦
    never depends on a player view that may already be cleaned up.
    """
    result = review.get("result")
    if result not in {"sente_win", "gote_win"} or outcome not in {"win", "loss"}:
        return None
    won = outcome == "win"
    if result == "sente_win":
        return "sente" if won else "gote"
    return "gote" if won else "sente"


def read_summary_detail(game_id: str, terminal: dict, *, color: str | None = None,
                        opener=None) -> tuple[dict | None, str]:
    """Read, verify and summarize one public replay, with a fixed failure code.

    ``terminal`` is the settled control result (outcome/reason/endedAt/...); the
    game id is the argument, because the projection does not carry it.  Every
    failure returns ``(None, code)``: the narration falls back to the reviewed
    facts and the corner is never failed by a public page.
    """
    if not isinstance(game_id, str) or not GAME_ID.fullmatch(game_id):
        return None, "invalid-game-id"
    try:
        review = read_replay(game_id, opener=opener)
    except (ReviewError, KeyError, TypeError, ValueError):
        return None, "read-failed"
    settled = dict(terminal) if isinstance(terminal, dict) else {}
    # The id identifies the replay; it is never taken from the result projection.
    settled["gameId"] = game_id
    resolved = color if color in COLORS else _infer_color(review, settled.get("outcome"))
    if resolved not in COLORS:
        return None, "invalid-color"
    if not verify(review, settled, color=resolved):
        return None, "verify-failed"
    try:
        return summary_for_bot(review, color=resolved), "ok"
    except (ReviewError, KeyError, TypeError, ValueError):
        return None, "read-failed"


def read_summary(game_id: str, terminal: dict, *, color: str | None = None,
                 opener=None) -> dict | None:
    """Read, verify and summarize the public replay, or ``None``."""
    return read_summary_detail(game_id, terminal, color=color, opener=opener)[0]
