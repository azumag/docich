"""Bounded per-match Soren game-over projection tests.

Every finished match leaves ``game_history/<end_ts>_score<N>.jsonl`` whose
last record carries the final board.  The owner-only diagnostics must surface
the end-state bottleneck (top-type histogram, unmerged high pair, end reason)
without projecting the raw turn log.
"""
import importlib.util
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
DIAG_PATH = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
spec = importlib.util.spec_from_file_location("collect_diagnostics_gameover_test", DIAG_PATH)
collect_diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect_diagnostics)


def _piece(piece_type, x):
    return {"type": piece_type, "x": x, "y": 0.0, "r": 0}


def _turn(turn, score, pieces, **extra):
    row = {
        "turn": turn,
        "score": score,
        "decision_x": 1.0,
        "decision_reason": "SEAT_LANE_PROBABLE_MERGE_CONTACT|OPEN_TWIN_MERGE",
        "deadline_crossed": False,
        "danger_piece_count": 0,
        "state_snapshot": {"pieces": pieces},
        "strategy_hash": "a41988221bde",
    }
    row.update(extra)
    return row


def _write_game(history_dir, name, rows):
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / name).write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_gameover_reports_end_board_of_newest_matches(tmp_path):
    soren = tmp_path / "soren"
    history = soren / "game_history"
    _write_game(history, "20261115_000000_score1000.jsonl", [
        _turn(1, 40, [_piece(2, -1.0), _piece(3, 1.0)]),
        _turn(50, 1000, [_piece(14, -2.0), _piece(14, 0.4),
                         _piece(15, 2.6), _piece(9, -3.0)],
              deadline_crossed=True, danger_piece_count=2,
              russia_created=True,
              decision_reason="NO_MERGE_DEADLINE_GUARD_NO_VALID"),
    ])
    _write_game(history, "20261115_010000_score2000.jsonl", [
        _turn(99, 2000, [_piece(15, -2.5), _piece(15, 2.5)],
              russia_created=True),
    ])
    # latest.jsonl is the in-progress match and must never be read as a
    # completed game-over record.
    _write_game(history, "latest.jsonl", [
        _turn(7, 99999, [_piece(1, 0.0)]),
    ])

    result = collect_diagnostics._collect_soren_gameover(soren, now=int(time.time()))

    assert result["present"] is True
    games = result["games"]
    assert len(games) == 2
    newest, older = games
    assert newest["score"] == 2000
    assert newest["turns"] == 99
    assert newest["russia_created"] is True
    assert newest["top_pair"] == {"type": 15, "count": 2, "min_dx": 5.0}
    assert older["score"] == 1000
    assert older["deadline_crossed"] is True
    assert older["danger_pieces"] == 2
    assert older["end_reason"] == "NO_MERGE_DEADLINE_GUARD_NO_VALID"
    assert older["type_hist"] == {"14": 2, "15": 1}
    # separated T14 pair is the highest duplicated type -> the bottleneck pair
    assert older["top_pair"] == {"type": 14, "count": 2, "min_dx": 2.4}
    assert all(g["score"] != 99999 for g in games)


def test_gameover_missing_history_dir_is_absent(tmp_path):
    result = collect_diagnostics._collect_soren_gameover(tmp_path / "nope", now=int(time.time()))
    assert result["present"] is False
    assert result["games"] == []


def test_gameover_skips_unrelated_filenames(tmp_path):
    soren = tmp_path / "soren"
    _write_game(soren / "game_history", "notes.jsonl", [_turn(1, 5, [])])
    _write_game(soren / "game_history", "20261115_020000_scoreX.jsonl", [_turn(1, 5, [])])
    result = collect_diagnostics._collect_soren_gameover(soren, now=int(time.time()))
    assert result["present"] is False


def test_gameover_limits_to_three_newest(tmp_path):
    soren = tmp_path / "soren"
    for index in range(5):
        _write_game(soren / "game_history",
                    f"20261115_0{index}0000_score{index}.jsonl",
                    [_turn(1, index, [])])
    result = collect_diagnostics._collect_soren_gameover(soren, now=int(time.time()))
    assert [g["score"] for g in result["games"]] == [4, 3, 2]


def test_gameover_unreadable_last_line_keeps_bounded_shape(tmp_path):
    soren = tmp_path / "soren"
    _write_game(soren / "game_history", "20261115_030000_score7.jsonl",
                [_turn(1, 7, [_piece(5, 0.0)])])
    path = soren / "game_history" / "20261115_030000_score7.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("{broken json")
    result = collect_diagnostics._collect_soren_gameover(soren, now=int(time.time()))
    game = result["games"][0]
    assert game["present"] is True
    assert game["readable"] is True
    assert game["score"] is None
    assert game["type_hist"] == {}
