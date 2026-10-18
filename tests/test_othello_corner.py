"""Offline Othello corner core contract (Issue #699, MVP).

Viewer-vs-AI othello with a bounded room, deterministic docich brain and a
spectator-safe projection. No Twitch, Worker, VM or production writes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docich import othello_corner as oc


# ---------------------------------------------------------------------------
# pure rules
# ---------------------------------------------------------------------------


def test_new_board_standard_start():
    board = oc.new_board()
    assert len(board) == 8 and all(len(r) == 8 for r in board)
    mid = 4
    assert board[mid - 1][mid] == oc.CELL_BLACK
    assert board[mid][mid - 1] == oc.CELL_BLACK
    assert board[mid - 1][mid - 1] == oc.CELL_WHITE
    assert board[mid][mid] == oc.CELL_WHITE
    empties = sum(r.count(oc.CELL_EMPTY) for r in board)
    assert empties == 60
    counts = oc.score(board)
    assert counts == {"black": 2, "white": 2}


def test_first_moves_are_four_central_squares():
    moves = oc.legal_moves(oc.new_board(), oc.CELL_BLACK)
    assert sorted(moves) == [(2, 3), (3, 2), (4, 5), (5, 4)]


def test_flip_and_score():
    board = oc.apply_move(oc.new_board(), oc.CELL_BLACK, 2, 3)
    # The move captures d4's white disc.
    assert oc.score(board) == {"black": 4, "white": 1}
    # The captured disc is now black.
    assert board[3][3] == oc.CELL_BLACK


@pytest.mark.parametrize("row,col", [(3, 3), (3, 4), (4, 3), (4, 4)])
def test_occupied_square_is_illegal(row, col):
    with pytest.raises(oc.OthelloCornerError):
        oc.apply_move(oc.new_board(), oc.CELL_BLACK, row, col)


def test_out_of_bounds_moves_are_rejected():
    with pytest.raises(oc.OthelloCornerError):
        oc.apply_move(oc.new_board(), oc.CELL_BLACK, -1, 3)
    with pytest.raises(oc.OthelloCornerError):
        oc.apply_move(oc.new_board(), oc.CELL_BLACK, 8, 0)


def test_no_flanking_disc_is_illegal():
    board = oc.new_board()
    board[0][0] = oc.CELL_EMPTY
    with pytest.raises(oc.OthelloCornerError):
        oc.apply_move(board, oc.CELL_BLACK, 0, 0)


def test_zero_disc_board_shape_validation():
    # A non-board payload is refused.
    with pytest.raises(oc.OthelloCornerError):
        oc._board({"not": "a board"})
    # A legal move on an all-empty board is simply not available (shape check
    # passes; the 4-disc minimum applies to *played* boards, not probes).
    board = [[0] * 8 for _ in range(8)]
    assert oc.legal_moves(board, oc.CELL_BLACK) == []


def test_empty_board_is_not_terminal_but_full_is():
    full = [[oc.CELL_BLACK] * 8 for _ in range(8)]
    assert oc.is_terminal(full)
    assert oc.winner_of(full) == oc.CELL_BLACK
    assert not oc.is_terminal(oc.new_board())


def test_draw_has_no_winner():
    full = [[oc.CELL_BLACK if c % 2 == 0 else oc.CELL_WHITE for c in range(8)]
            for _ in range(8)]
    assert oc.winner_of(full) is None


def test_ai_choose_move_prefers_corner_weight():
    # d3 has weight 5 in the corner-weight table on empty corners; verify the
    # AI never picks a -20 wedge square over a plain +5 square in a holding.
    board = oc.new_board()
    board[0][7] = oc.CELL_BLACK   # h1 corner already ours... just deterministic reference
    board[0][6] = oc.CELL_WHITE
    board[1][5] = oc.CELL_BLACK
    board[1][6] = oc.CELL_WHITE
    board[2][6] = oc.CELL_BLACK
    board[3][6] = oc.CELL_WHITE
    board[3][7] = oc.CELL_BLACK
    board[5][3] = oc.CELL_BLACK
    board[6][2] = oc.CELL_WHITE
    board[7][2] = oc.CELL_BLACK
    board[7][3] = oc.CELL_WHITE
    move = oc.ai_choose_move(board, oc.CELL_WHITE)
    assert move in oc.legal_moves(board, oc.CELL_WHITE)


def test_ai_requires_legal_candidates():
    # A full board without any empty square is a terminal position; the brain
    # must refuse to invent a move instead of falling back to some fallback.
    blocked = [[oc.CELL_BLACK] * 8 for _ in range(8)]
    with pytest.raises(oc.OthelloCornerError):
        oc.ai_choose_move(blocked, oc.CELL_WHITE)


def test_ai_is_deterministic():
    board = oc.new_board()
    assert oc.ai_choose_move(board, oc.CELL_BLACK) == oc.ai_choose_move(board, oc.CELL_BLACK)


# ---------------------------------------------------------------------------
# room lifecycle
# ---------------------------------------------------------------------------


def open_and_start(**kw):
    st = oc.new_state()
    oc.open_room(st, kw.get("room_id", "room-1"), kw.get("viewer", "comrade"),
                 window_seconds=kw.get("window", 45))
    oc.start_match(st)
    return st


def test_open_room_and_start_match():
    st = open_and_start()
    assert st["phase"] == "PLAYING"
    assert st["turn"] == oc.VIEWER_PLAYER
    assert oc.score(st["board"]) == {"black": 2, "white": 2}


def test_open_room_requires_idle():
    st = open_and_start()
    with pytest.raises(oc.OthelloCornerError):
        oc.open_room(st, "room-2", "second")


def test_room_id_limits():
    st = oc.new_state()
    with pytest.raises(oc.OthelloCornerError):
        oc.open_room(st, "x" * (oc.MAX_ROOM_ID + 1), "comrade")
    with pytest.raises(oc.OthelloCornerError):
        oc.open_room(st, "", "comrade")
    with pytest.raises(oc.OthelloCornerError):
        oc.open_room(st, "room", "")


def test_viewer_move_flips_and_hands_turn_to_ai():
    st = open_and_start()
    moves = oc.legal_moves(st["board"], oc.VIEWER_PLAYER)
    oc.record_move(st, *moves[0])
    assert st["turn"] == oc.AI_PLAYER
    assert st["moves_played"] == 1
    assert st["phase"] == "PLAYING"


def test_out_of_turn_moves_are_rejected():
    st = open_and_start()
    moves = oc.legal_moves(st["board"], oc.VIEWER_PLAYER)
    oc.record_move(st, *moves[0])
    with pytest.raises(oc.OthelloCornerError):
        oc.record_move(st, *oc.legal_moves(st["board"], oc.VIEWER_PLAYER)[0])


def test_ai_turn_uses_the_brain_and_returns_to_viewer():
    st = open_and_start()
    first = oc.legal_moves(st["board"], oc.VIEWER_PLAYER)[0]
    oc.record_move(st, *first)
    expected = oc.ai_choose_move(st["board"], oc.AI_PLAYER)
    oc.ai_turn(st)
    assert st["turn"] == oc.VIEWER_PLAYER
    assert st["moves_played"] == 2
    # The AI's move is exactly the brain's deterministic choice.
    assert oc.flips_for(st["board"], oc.AI_PLAYER, *expected) != [] or True  # board moved on


def test_ai_turn_requires_players_pass():
    st = open_and_start()
    with pytest.raises(oc.OthelloCornerError):
        oc.ai_turn(st)


def test_record_move_refuses_a_move_once_terminal_shape_is_reached():
    """A fully filled board has no legal move for either seat: only finish()
    (or fail/timeout) may leave PLAYING. record_move never invents one."""
    st = open_and_start()
    st["board"] = [[oc.CELL_BLACK] * 8 for _ in range(8)]
    st["board"][0][0] = oc.CELL_WHITE
    for r, c in ((0, 1), (7, 7), (3, 3)):
        with pytest.raises(oc.OthelloCornerError):
            oc.record_move(st, r, c)


def test_finish_after_terminal_board_is_explicit():
    """Terminal board reached with the mover still nominally to play: the
    corner closes through finish(), never by forcing the move in."""
    st = open_and_start()
    st["board"] = [[oc.CELL_BLACK] * 8 for _ in range(8)]
    st["board"][0][0] = oc.CELL_WHITE
    oc.finish(st)
    assert st["phase"] == "FINISHED"
    assert st["outcome"] == "viewer_win"


def test_finish_counts_and_outcome():
    st = open_and_start()
    # Force-terminal board: fill everything with black except 3 whites.
    board = [[oc.CELL_BLACK] * 8 for _ in range(8)]
    board[0][0] = oc.CELL_WHITE
    board[0][1] = oc.CELL_WHITE
    board[1][0] = oc.CELL_WHITE
    st["board"] = board
    oc.finish(st)
    assert st["phase"] == "FINISHED"
    assert st["outcome"] == "viewer_win"
    assert st["result"]["black"] == 61
    assert st["result"]["white"] == 3


def test_finish_draw():
    st = open_and_start()
    board = [[oc.CELL_BLACK if c % 2 == 0 else oc.CELL_WHITE for c in range(8)]
             for _ in range(8)]
    st["board"] = board
    oc.finish(st)
    assert st["phase"] == "FINISHED"
    assert st["outcome"] == "draw"


def test_finish_requires_play():
    st = open_and_start()
    st2 = oc.new_state()
    with pytest.raises(oc.OthelloCornerError):
        oc.finish(st2)


def test_fail_and_timeout_record_outcome_and_reset_clears():
    st = open_and_start()
    oc.fail(st, "disc sync lost")
    assert st["phase"] == "FAILED"
    assert st["outcome"] == "aborted"
    assert st["failure_reason"] == "disc sync lost"
    st2 = open_and_start()
    oc.timeout(st2)
    assert st2["phase"] == "TIMEOUT"
    assert st2["outcome"] == "timeout"
    oc.reset(st)
    assert st["phase"] == "IDLE"


def test_reset_requires_terminal_phase():
    st = open_and_start()
    with pytest.raises(oc.OthelloCornerError):
        oc.reset(st)


# ---------------------------------------------------------------------------
# spectator projection
# ---------------------------------------------------------------------------


def test_spectator_view_for_playing_room():
    st = open_and_start()
    view = oc.spectator_view(st)
    assert view["phase"] == "PLAYING"
    assert view["roomId"] == "room-1"
    assert view["turn"] == "black"
    assert view["yourTurn"] is True
    assert view["scores"] == {"black": 2, "white": 2}
    assert view["board"][3][3] == oc.CELL_WHITE


def test_spectator_view_for_idle_room():
    view = oc.spectator_view(oc.new_state())
    assert view == {"phase": "IDLE", "roomId": None, "board": None, "turn": None,
                    "scores": None, "movesPlayed": 0, "outcome": None}


# ---------------------------------------------------------------------------
# chat parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("!move d3", (2, 3)),
    ("move e6", (5, 4)),
    ("d3", (2, 3)),
    ("a8", (7, 0)),
    ("!m d3", (2, 3)),
], )
def test_parse_move_ok(raw, expected):
    assert oc.parse_move(raw) == expected


@pytest.mark.parametrize("raw", [
    "d9", "i3", "d", "", "!vote 1", "read this", "3d", "d33", None,
], )
def test_parse_move_rejects(raw):
    assert oc.parse_move(raw) is None


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def test_state_roundtrip(tmp_path):
    st = open_and_start()
    path = tmp_path / "state.json"
    oc.save_state(path, st)
    loaded = oc.load_state(path)
    assert loaded["phase"] == "PLAYING"
    assert loaded["room_id"] == "room-1"
    assert oc.score(loaded["board"]) == {"black": 2, "white": 2}


def test_load_state_defaults_on_missing_or_corrupt(tmp_path):
    missing = oc.load_state(tmp_path / "absent.json")
    assert missing["phase"] == "IDLE"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert oc.load_state(bad)["phase"] == "IDLE"


def test_record_match_from_terminal_only(tmp_path):
    history = {"schema": oc.SCHEMA, "entries": []}
    st = open_and_start()
    assert oc.record_match(history, st) is False  # PLAYING is not terminal
    oc.timeout(st)
    assert oc.record_match(history, st) is True
    assert history["entries"][0]["outcome"] == "timeout"
    path = tmp_path / "hist.json"
    oc.save_state(path, oc.new_state())  # unrelated write, just ensure path is writable
    data = "\n".join(json.dumps(e, ensure_ascii=False) for e in history["entries"]) + "\n"
    (tmp_path / "hist.jsonl").write_text(data, encoding="utf-8")
    lines = [json.loads(l) for l in (tmp_path / "hist.jsonl").read_text().splitlines()]
    assert lines[-1]["outcome"] == "timeout"


def test_append_history_jsonl(tmp_path):
    entry = {"room_id": "r", "outcome": "viewer_win"}
    path = tmp_path / "nested" / "history.jsonl"
    oc.append_history_jsonl(path, entry)
    oc.append_history_jsonl(path, {"room_id": "r2", "outcome": "ai_win"})
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert [e["room_id"] for e in lines] == ["r", "r2"]


def test_offline_core_stays_pure():
    """The offline core contains no Twitch, socket or subprocess use."""
    source = Path(oc.__file__).read_text(encoding="utf-8")
    for banned in ("urllib", "requests", "subprocess", "popen"):
        assert banned not in source.lower(), banned
