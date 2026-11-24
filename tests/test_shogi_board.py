"""Public-replay board reconstruction for the 衝立将棋 感想戦."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import shogi_board as sb  # noqa: E402


def _slot(file_no, rank):
    return (sb.RANKS.index(rank)) * 9 + (9 - file_no)


def _game(*moves):
    """``moves`` as ``(usi, side, ms)`` triples, the replay's own shape."""
    return [{"usi": usi, "by": side, "ms": ms if ms is not None else 0, "foulsBefore": 0}
            for usi, side, ms in moves]


#: A short applicable sequence: 7六歩, 3四歩, 2二角成 (captures the bishop),
#: 同銀, then 5二角打 with the bishop taken by the first capture.
SEQUENCE = _game(
    ("7g7f", "sente", 120),
    ("3c3d", "gote", 300),
    ("8h2b+", "sente", 900),
    ("3a2b", "gote", 1500),
    ("B*5b", "sente", 400),
)


def test_standard_start_parses_and_round_trips():
    squares, turn, hands = sb.parse_sfen(sb.STANDARD_START)
    assert turn == "b" and hands == {"sente": {}, "gote": {}}
    assert squares[_slot(7, "g")] == "P"
    assert squares[_slot(7, "f")] is None
    assert squares[_slot(2, "b")] == "b"
    assert sb.to_sfen(squares, hands, "b", 1) == sb.STANDARD_START


def test_parse_sfen_rejects_drift():
    for bad in ["", None, 7, "9/9/9/9/9/9/9/9/9 x - 1", "9/9/9/9/9/9/9/9/9/9 b - 1",
                "9/9/9/9/9/9/9/9/9 b Z 1", "9/9/9/9/9/9/9/9/9 b -- 1",
                "x" * (sb.MAX_SFEN + 1)]:
        with pytest.raises(sb.BoardError):
            sb.parse_sfen(bad)


def test_parse_sfen_gold_hands_round_trip():
    squares, turn, _hands = sb.parse_sfen(sb.STANDARD_START)
    for hand_part, sente_count, gote_count in [
        ("G", 1, 0), ("g", 0, 1), ("2G", 2, 0), ("2g", 0, 2), ("2G2g", 2, 2),
    ]:
        hands = {
            "sente": {"G": sente_count} if sente_count else {},
            "gote": {"G": gote_count} if gote_count else {},
        }
        sfen = sb.to_sfen(squares, hands, turn)
        assert sfen == sb.STANDARD_START.replace(" b - 1", f" b {hand_part} 1")
        assert sb.parse_sfen(sfen) == (squares, turn, hands)


def test_parse_sfen_rejects_non_hand_pieces():
    for hand_part in ["K", "k", "Z", "z", "+P", "+p", "+G", "+g"]:
        sfen = sb.STANDARD_START.replace(" b - 1", f" b {hand_part} 1")
        with pytest.raises(sb.BoardError):
            sb.parse_sfen(sfen)


def test_apply_move_moves_a_piece_and_empties_the_from_square():
    squares, _turn, hands = sb.parse_sfen(sb.STANDARD_START)
    facts = sb.apply_move(squares, hands, "7g7f", "sente")

    assert facts == {"piece": "P", "captured": None, "promote": False, "drop": False}
    assert squares[_slot(7, "g")] is None
    assert squares[_slot(7, "f")] == "P"


def test_apply_move_captures_and_demotes_into_hand():
    squares, _turn, hands = sb.parse_sfen(sb.STANDARD_START)
    # A promoted piece returns to hand as its base role.
    squares[_slot(2, "b")] = "+b"
    facts = sb.apply_move(squares, hands, "8h2b+", "sente")

    assert facts["captured"] == "+b"
    assert hands["sente"] == {"B": 1}
    assert squares[_slot(2, "b")] == "+B"


def test_apply_move_drops_from_hand():
    squares, _turn, hands = sb.parse_sfen(sb.STANDARD_START)
    hands["sente"]["P"] = 1
    facts = sb.apply_move(squares, hands, "P*5b", "sente")

    assert facts == {"piece": "P", "captured": None, "promote": False, "drop": True}
    assert squares[_slot(5, "b")] == "P"
    assert hands["sente"]["P"] == 0


def test_apply_move_fails_closed_on_impossible_input():
    squares, _turn, hands = sb.parse_sfen(sb.STANDARD_START)
    with pytest.raises(sb.BoardError):
        sb.apply_move(squares, hands, "P*5b", "sente")        # nothing in hand
    with pytest.raises(sb.BoardError):
        sb.apply_move(squares, hands, "5b5c", "sente")        # no piece there
    with pytest.raises(sb.BoardError):
        sb.apply_move(squares, hands, "7g7f", "white")        # unknown side
    with pytest.raises(sb.BoardError):
        sb.apply_move(squares, hands, "9z9z", "sente")        # invalid usi
    with pytest.raises(sb.BoardError):
        sb.apply_move(squares, hands, "P*5b", "sente")        # still nothing in hand


def test_piece_names_cover_both_sides_and_promotions():
    for piece, name in [("P", "歩"), ("p", "歩"), ("K", "玉"), ("k", "玉"),
                        ("+P", "と"), ("+p", "と"), ("+R", "龍"), ("+r", "龍"),
                        ("+B", "馬"), ("+b", "馬"), ("+S", "成銀"), ("+s", "成銀"),
                        ("+L", "成香"), ("+l", "成香"), ("+N", "成桂"), ("+n", "成桂")]:
        assert sb.piece_name(piece) == name
    with pytest.raises(sb.BoardError):
        sb.piece_name("")


def test_notation_is_japanese_and_marks_promotion_and_drops():
    assert sb.notation("7g7f", "sente", "P") == "▲7六歩"
    assert sb.notation("3c3d", "gote", "p") == "△3四歩"
    assert sb.notation("P*5b", "sente", "P") == "▲5二歩打"
    assert sb.notation("8h2b+", "sente", "B") == "▲2二角成"
    # A promotable piece leaving the promotion zone without promoting.
    assert sb.notation("2b3c", "sente", "B") == "▲3三角不成"
    # A piece already promoted keeps its promoted name and no suffix.
    assert sb.notation("2b3c", "sente", "+B") == "▲3三馬"
    # The same destination as the previous move reads as 同.
    assert sb.notation("3a2b", "gote", "S", same_square=True) == "△同銀"


def test_annotate_walks_the_replay_and_snapshots_each_board():
    annotated = sb.annotate(SEQUENCE)

    assert [move["n"] for move in annotated] == [1, 2, 3, 4, 5]
    assert [move["notation"] for move in annotated] == [
        "▲7六歩", "△3四歩", "▲2二角成", "△同銀", "▲5二角打"]
    assert annotated[2]["captured"] == "角" and annotated[2]["promote"] is True
    assert annotated[3]["captured"] == "馬" and annotated[3]["usi"] == "3a2b"
    assert annotated[4]["drop"] is True and annotated[4]["piece"] == "角"
    assert annotated[0]["ms"] == 120

    # Every snapshot is a fixed-width board and the last one is the applied
    # position: two characters per square (promotion flag + piece letter).
    assert all(len(move["board"]) == 162 for move in annotated)
    assert annotated[-1]["board"][2 * _slot(5, "b"):2 * _slot(5, "b") + 2] == ".B"
    assert annotated[2]["board"][2 * _slot(2, "b"):2 * _slot(2, "b") + 2] == "+B"
    # The pawn has left 7七 after the first move.
    assert annotated[0]["board"][2 * _slot(7, "g"):2 * _slot(7, "g") + 2] == ".."
    assert annotated[0]["board"][2 * _slot(7, "f"):2 * _slot(7, "f") + 2] == ".P"


def test_annotate_rejects_a_broken_move_list():
    for bad in [None, [], [{"usi": "7g7f"}], [{"usi": "9z9z", "by": "sente"}],
                [{"usi": "7g7f", "by": "white"}],
                [{"usi": "7g7f", "by": "sente"}] * (sb.MAX_MOVES + 1)]:
        with pytest.raises(sb.BoardError):
            sb.annotate(bad)


def test_reconstruct_verifies_against_the_authoritative_final_sfen():
    # Build the final position with the module itself, then confirm the check.
    squares, _turn, hands = sb.parse_sfen(sb.STANDARD_START)
    for move in SEQUENCE:
        sb.apply_move(squares, hands, move["usi"], move["by"])
    final = sb.to_sfen(squares, hands, "w", len(SEQUENCE) + 1)

    rebuilt = sb.reconstruct(SEQUENCE, final)
    assert rebuilt is not None
    assert rebuilt["moveCount"] == 5
    assert rebuilt["final"] == final
    assert rebuilt["moves"][2]["notation"] == "▲2二角成"

    # A drifted move list, a different final position and a broken sfen all
    # fail closed: the caller must not display a board.
    assert sb.reconstruct(SEQUENCE, sb.STANDARD_START) is None
    assert sb.reconstruct(SEQUENCE, "not a sfen") is None
    assert sb.reconstruct(SEQUENCE, None) is None
    assert sb.reconstruct([{"usi": "5e5f", "by": "sente"}], final) is None
    assert sb.reconstruct(SEQUENCE, final.replace(" w ", " b ")) is None


def test_reconstruct_ignores_the_move_number_in_the_final_sfen():
    squares, _turn, hands = sb.parse_sfen(sb.STANDARD_START)
    for move in SEQUENCE:
        sb.apply_move(squares, hands, move["usi"], move["by"])
    final = sb.to_sfen(squares, hands, "w", len(SEQUENCE) + 1)
    # The site's own SFEN carries its move number; only the position is identity.
    assert sb.reconstruct(SEQUENCE, final.replace(" 6", " 99")) is not None
