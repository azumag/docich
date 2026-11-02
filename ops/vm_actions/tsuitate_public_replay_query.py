#!/usr/bin/env python3
"""Fetch and project bounded, post-game facts from one verified public replay.

This helper is intentionally downstream of the existing owner-only status query.
It accepts only a finished, verified run status, fetches beta.tsuitate.info's
public ``__data.json`` through ``tsuitate_review``, cross-checks the replay
identity, and emits derived public facts suitable for the fixed control Issue.

It never reads Worker playerView, decision records, observations, credentials,
or live game state.  The raw replay and reconstructed boards are not emitted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "ops" / "vm_actions"))

from docich import tsuitate_analysis as analysis  # noqa: E402
from docich.tsuitate_review import read_kifu_detail, summary_for_bot  # noqa: E402
from tsuitate_status_query_reply import load_status  # noqa: E402

MAX_EVENTS = 16
MAX_SLOW = 5


def _other(side: str) -> str:
    return "gote" if side == "sente" else "sente"


def _foul_deltas(review: dict, color: str) -> tuple[list[dict], int]:
    """Return public foul-count increments before accepted bot moves.

    The replay publishes only cumulative ``foulsBefore`` alongside accepted
    moves.  This can show how many foul attempts happened before an accepted
    move, but it does not reveal the rejected move identities or the brain's
    private candidate ordering.
    """
    seen = {"sente": 0, "gote": 0}
    events: list[dict] = []
    for number, move in enumerate(review["moves"], 1):
        side = move["by"]
        count = move["foulsBefore"]
        if count < seen[side]:
            raise ValueError("non_monotonic_public_fouls")
        delta = count - seen[side]
        seen[side] = count
        if side == color and delta:
            events.append({"n": number, "usi": move["usi"], "added": delta})
    terminal = review["fouls"][color] - seen[color]
    if terminal < 0:
        raise ValueError("non_monotonic_public_fouls")
    return events[:MAX_EVENTS], terminal


def _check_events(detail: dict, color: str) -> tuple[list[dict], list[dict]]:
    moves = detail["kifu"]["moves"]
    positions = analysis._positions(moves)
    if len(positions) != len(moves):
        raise ValueError("board_rebuild_failed")
    given: list[dict] = []
    received: list[dict] = []
    raw_moves = detail["review"]["moves"]
    for index, (move, (_before, after, _facts)) in enumerate(zip(moves, positions)):
        mover = move["side"]
        checked_side = _other(mover)
        if not analysis._checked(after, checked_side):
            continue
        response = moves[index + 1] if index + 1 < len(moves) else None
        row = {
            "checkN": move["n"],
            "checkUsi": move["usi"],
            "responseN": response["n"] if response else None,
            "responseUsi": response["usi"] if response else None,
            "responseMs": response["ms"] if response else None,
            "responseFoulsBefore": (
                raw_moves[index + 1]["foulsBefore"] if response else None
            ),
        }
        (given if mover == color else received).append(row)
    return given[:MAX_EVENTS], received[:MAX_EVENTS]


def analyze_detail(detail: dict) -> dict:
    color = detail["color"]
    review = detail["review"]
    summary = summary_for_bot(review, color=color)
    foul_turns, terminal_fouls = _foul_deltas(review, color)
    checks_given, checks_received = _check_events(detail, color)
    own_slow = [
        {"n": n, "usi": move["usi"], "ms": move["ms"]}
        for n, move in enumerate(review["moves"], 1)
        if move["by"] == color
    ]
    own_slow.sort(key=lambda row: (-row["ms"], row["n"]))
    return {
        "gameId": review["gameId"],
        "color": color,
        "outcome": summary["outcome"],
        "reason": summary["reason"],
        "moveCount": summary["moveCount"],
        "opponentName": summary["opponentName"],
        "fouls": summary["fouls"],
        "opponentFouls": summary["opponentFouls"],
        "foulAttempts": summary["foulAttempts"],
        "ownThink": summary["own"],
        "opponentThink": summary["opponent"],
        "foulTurns": foul_turns,
        "terminalFoulsWithoutAcceptedMove": terminal_fouls,
        "checksGiven": checks_given,
        "checksReceived": checks_received,
        "slowestOwn": own_slow[:MAX_SLOW],
        "candidateOrdering": "not_exposed_by_public_replay",
    }


def _compact_fouls(rows: list[dict], terminal: int) -> str:
    parts = [f"{r['n']}:{r['usi']}:+{r['added']}" for r in rows]
    if terminal:
        parts.append(f"terminal:+{terminal}")
    return ",".join(parts) if parts else "-"


def _compact_checks(rows: list[dict]) -> str:
    parts = []
    for row in rows:
        if row["responseN"] is None:
            parts.append(f"{row['checkN']}:{row['checkUsi']}->mate/no-response")
        else:
            parts.append(
                f"{row['checkN']}:{row['checkUsi']}->{row['responseN']}:"
                f"{row['responseUsi']}({row['responseMs']}ms,f{row['responseFoulsBefore']})"
            )
    return ",".join(parts) if parts else "-"


def _compact_slow(rows: list[dict]) -> str:
    return ",".join(f"{r['n']}:{r['usi']}:{r['ms']}ms" for r in rows) or "-"


def build_reply(status_path: Path, run_id: str) -> dict[str, str]:
    status = load_status(status_path, run_id)
    base = [
        f"Tsuitate-Run: {run_id}",
        f"Game-ID: {status['gameId'] or '-'}",
    ]
    if (status["state"] != "finished" or status["completedGames"] != 1
            or status["gameId"] is None or status["gameResult"] is None):
        return {"body": "\n".join(base + [
            "Public-Replay: not-terminal",
        ])}
    terminal = status["gameResult"]
    if terminal.get("resultConfidence") != "verified":
        return {"body": "\n".join(base + [
            "Public-Replay: terminal-not-verified",
        ])}
    detail, failure = read_kifu_detail(status["gameId"], terminal)
    if detail is None:
        return {"body": "\n".join(base + [
            "Public-Replay: unavailable",
            f"Replay-Reason: {failure}",
        ])}
    facts = analyze_detail(detail)
    own = facts["ownThink"]
    opp = facts["opponentThink"]
    lines = base + [
        "Public-Replay: verified",
        f"Replay-URL: https://beta.tsuitate.info/games/{facts['gameId']}",
        f"Bot-Color: {facts['color']}",
        f"Opponent: {facts['opponentName'] or '-'}",
        f"Replay-Outcome: {facts['outcome']}",
        f"Replay-Reason: {facts['reason']}",
        f"Replay-Moves: {facts['moveCount']}",
        f"Bot-Fouls: {facts['fouls']}",
        f"Opponent-Fouls: {facts['opponentFouls']}",
        f"Public-Foul-Attempts: {facts['foulAttempts']}",
        f"Bot-Foul-Turns: {_compact_fouls(facts['foulTurns'], facts['terminalFoulsWithoutAcceptedMove'])}",
        f"Checks-Given: {_compact_checks(facts['checksGiven'])}",
        f"Checks-Received: {_compact_checks(facts['checksReceived'])}",
        f"Bot-Think: moves={own['moves']},total={own['totalMs']}ms,max={own['maxMs']}ms,median={own['medianMs']}ms",
        f"Opponent-Think: moves={opp['moves']},total={opp['totalMs']}ms,max={opp['maxMs']}ms,median={opp['medianMs']}ms",
        f"Slowest-Bot-Moves: {_compact_slow(facts['slowestOwn'])}",
        "Candidate-Ordering: not_exposed_by_public_replay",
    ]
    return {"body": "\n".join(lines)}


def main() -> int:
    try:
        status_path = Path(os.environ["STATUS_FILE"])
        run_id = os.environ["TSUITATE_RUN_ID"]
        json.dump(build_reply(status_path, run_id), sys.stdout, separators=(",", ":"))
    except Exception:
        print("Tsuitate public replay query rejected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
