"""Reconstruct a 衝立将棋 game from its public replay, without a rules engine.

The site publishes only the move list (USI) and the final SFEN.  Applying the
moves to the standard initial position reproduces the site's own final position
(verified against ``finalSfen`` on real finished games), which is exactly what
the 感想戦 needs: which piece moved, captures, promotions, drops, the Japanese
notation and a per-move board snapshot for the broadcast view.

This module is an **applier**, not a referee.  It never decides legality, never
claims check or mate, and it fails closed: :func:`reconstruct` returns ``None``
unless the rebuilt position equals the authoritative ``finalSfen``, so a drifted
or misunderstood move list can never put a wrong board on air.
"""
from __future__ import annotations

import re

#: Standard shogi start.  The site's replays carry no start position, and this
#: one reproduces every checked final position exactly.
STANDARD_START = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"
RANKS = "abcdefghi"
FILES = "987654321"
PROMOTED = {"P", "L", "N", "S", "B", "R"}
ROLE_NAMES = {"P": "歩", "L": "香", "N": "桂", "S": "銀", "G": "金", "B": "角", "R": "飛", "K": "玉"}
PROMOTED_NAMES = {"+P": "と", "+L": "成香", "+N": "成桂", "+S": "成銀", "+B": "馬", "+R": "龍", "+K": "玉"}
RANK_KANJI = "一二三四五六七八九"
SIDE_MARK = {"sente": "▲", "gote": "△"}
MAX_MOVES = 512
MAX_SFEN = 200
SFEN = re.compile(r"[A-Za-z0-9+/ \-]{1,%d}\Z" % MAX_SFEN)
USI = re.compile(r"(?:[1-9][a-i][1-9][a-i]\+?|[PLNSGBR]\*[1-9][a-i])\Z")
SQUARE = re.compile(r"[1-9][a-i]\Z")


class BoardError(RuntimeError):
    """The replay could not be applied to a position."""


def _square_index(file_no: int, rank: str) -> int:
    return (int(rank, 36) - 10) * 9 + (9 - file_no)


def _empty_board():
    return [None] * 81


def parse_sfen(sfen: str):
    """``(squares, turn, hands)`` from a bounded SFEN string.

    ``squares`` is 81 entries in SFEN rank order (rank a first, file 9 first),
    each ``None`` or a piece code such as ``P`` / ``+r``.  ``hands`` maps side
    to ``{ROLE: count}``.
    """
    if not isinstance(sfen, str) or not SFEN.fullmatch(sfen):
        raise BoardError("invalid sfen")
    parts = sfen.split(" ")
    if len(parts) < 3:
        raise BoardError("invalid sfen")
    board_part, turn, hand_part = parts[0], parts[1], parts[2]
    if turn not in {"b", "w"}:
        raise BoardError("invalid sfen turn")
    rows = board_part.split("/")
    if len(rows) != 9:
        raise BoardError("invalid sfen board")
    squares = _empty_board()
    for row_index, row in enumerate(rows):
        file_slot = 0
        index = 0
        while index < len(row):
            char = row[index]
            if char.isdigit():
                file_slot += int(char)
                index += 1
                continue
            promoted = False
            if char == "+":
                promoted = True
                index += 1
                if index >= len(row):
                    raise BoardError("invalid sfen promotion")
                char = row[index]
            if char.upper() not in ROLE_NAMES:
                raise BoardError("invalid sfen piece")
            if not 0 <= file_slot <= 8:
                raise BoardError("invalid sfen row")
            squares[row_index * 9 + file_slot] = ("+" if promoted else "") + char
            file_slot += 1
            index += 1
        if file_slot > 9:
            raise BoardError("invalid sfen row width")
    hands = {"sente": {}, "gote": {}}
    if hand_part != "-":
        count = 0
        for char in hand_part:
            if char.isdigit():
                count = count * 10 + int(char)
                continue
            if char.upper() not in PROMOTED:
                raise BoardError("invalid sfen hand")
            side = "sente" if char.isupper() else "gote"
            hands[side][char.upper()] = hands[side].get(char.upper(), 0) + (count or 1)
            count = 0
    return squares, turn, hands


def to_sfen(squares, hands, turn: str = "b", move_number: int = 1) -> str:
    rows = []
    for row_index in range(9):
        row = ""
        empty = 0
        for file_slot in range(9):
            piece = squares[row_index * 9 + file_slot]
            if piece is None:
                empty += 1
                continue
            if empty:
                row += str(empty)
                empty = 0
            row += piece
        if empty:
            row += str(empty)
        rows.append(row)
    hand_part = ""
    for side, upper in (("sente", True), ("gote", False)):
        for role in "RBGSNLP":
            count = (hands.get(side) or {}).get(role, 0)
            if count <= 0:
                continue
            hand_part += (str(count) if count > 1 else "") + (role if upper else role.lower())
    return "/".join(rows) + f" {turn} {hand_part or '-'} {max(1, int(move_number))}"


def demote(piece: str) -> str:
    """The base piece a capture returns to hand."""
    return piece[1:] if piece.startswith("+") else piece


def apply_move(squares, hands, usi: str, side: str) -> dict:
    """Apply one USI move in place; return the bounded facts of that move.

    No legality is checked: the site already refereed the game, and this only
    needs to know which piece moved and what it did.
    """
    if side not in SIDE_MARK:
        raise BoardError("invalid side")
    if not isinstance(usi, str) or not USI.fullmatch(usi):
        raise BoardError("invalid usi")
    if "*" in usi:
        role, square = usi.split("*")
        index = _square_index(int(square[0]), square[1])
        if squares[index] is not None:
            raise BoardError("drop onto an occupied square")
        held = hands.setdefault(side, {})
        if held.get(role, 0) <= 0:
            raise BoardError("drop without the piece in hand")
        piece = role if side == "sente" else role.lower()
        squares[index] = piece
        held[role] -= 1
        return {"piece": piece, "captured": None, "promote": False, "drop": True}
    promote = usi.endswith("+")
    body = usi[:-1] if promote else usi
    from_index = _square_index(int(body[0]), body[1])
    to_index = _square_index(int(body[2]), body[3])
    piece = squares[from_index]
    if piece is None:
        raise BoardError("no piece on the from square")
    if promote:
        if piece.startswith("+") or piece.upper() not in PROMOTED:
            raise BoardError("invalid promotion")
        piece = "+" + piece
    captured = squares[to_index]
    if captured is not None:
        base = demote(captured).upper()
        hands.setdefault(side, {})[base] = hands.setdefault(side, {}).get(base, 0) + 1
    squares[from_index] = None
    squares[to_index] = piece
    return {"piece": piece, "captured": captured, "promote": promote, "drop": False}


def piece_name(piece: str) -> str:
    if not isinstance(piece, str) or not piece:
        raise BoardError("invalid piece")
    if piece.startswith("+"):
        return PROMOTED_NAMES.get("+" + piece[1].upper(), "?")
    return ROLE_NAMES.get(piece.upper(), "?")


def _promotion_available(piece: str, from_square: str, to_square: str, side: str) -> bool:
    """Whether the moving piece could have promoted (for the 不成 suffix)."""
    if piece.startswith("+") or piece.upper() not in PROMOTED:
        return False
    zone = "abc" if side == "sente" else "ghi"
    return from_square[1] in zone or to_square[1] in zone


def notation(usi: str, side: str, piece: str, *, captured=None,
             same_square: bool = False) -> str:
    """Japanese notation for one move, e.g. ``▲7六歩`` / ``△5二歩打`` / ``▲2二角成``."""
    if not isinstance(usi, str) or not USI.fullmatch(usi):
        raise BoardError("invalid usi")
    mark = SIDE_MARK.get(side, "▲")
    if "*" in usi:
        role, square = usi.split("*")
        return f"{mark}{_square(square, same_square)}" + piece_name(role) + "打"
    promote = usi.endswith("+")
    body = usi[:-1] if promote else usi
    to_square = body[2:4]
    if promote:
        # A promoting move is written with the piece's base name plus 成.
        name, suffix = piece_name(demote(piece)), "成"
    else:
        name = piece_name(piece)
        suffix = ("不成" if _promotion_available(piece, body[:2], to_square, side) else "")
    return f"{mark}{_square(to_square, same_square)}{name}{suffix}"


def _square(square: str, same: bool) -> str:
    if same:
        return "同"
    return f"{square[0]}{RANK_KANJI[RANKS.index(square[1])]}"


def snapshot(squares) -> str:
    """The 81-square board as a fixed-width string for the broadcast view.

    Two characters per square in SFEN rank order: a promoted flag (``+`` or
    ``.``) and the piece letter (or ``.`` when empty), so ``..`` is an empty
    square, ``.P`` a plain pawn and ``+B`` a promoted bishop.  Fixed width
    keeps the view's parsing trivial and cannot drift with promotion.
    """
    out = []
    for piece in squares:
        if not piece:
            out.append("..")
        elif piece.startswith("+"):
            out.append("+" + piece[1])
        else:
            out.append("." + piece)
    return "".join(out)


def annotate(moves) -> list[dict]:
    """Apply a replay's move list and return the bounded per-move record.

    Each entry carries the notation, the moving piece, captures, promotions,
    drops, the thinking time the site published and the board **after** the
    move, which is what the 感想戦 steps through.
    """
    if not isinstance(moves, list) or not 1 <= len(moves) <= MAX_MOVES:
        raise BoardError("invalid move list")
    squares, _turn, hands = parse_sfen(STANDARD_START)
    annotated = []
    previous_to = None
    for number, move in enumerate(moves, 1):
        if not isinstance(move, dict) or not isinstance(move.get("usi"), str):
            raise BoardError("invalid move entry")
        side = move.get("by")
        facts = apply_move(squares, hands, move["usi"], side)
        body = move["usi"][:-1] if move["usi"].endswith("+") else move["usi"]
        to_square = body.split("*")[-1] if "*" in body else body[2:4]
        annotated.append({
            "n": number,
            "side": side,
            "usi": move["usi"],
            "notation": notation(move["usi"], side, facts["piece"],
                                 captured=facts["captured"],
                                 same_square=to_square == previous_to),
            "piece": piece_name(facts["piece"]),
            "captured": piece_name(facts["captured"]) if facts["captured"] else None,
            "promote": facts["promote"],
            "drop": facts["drop"],
            "ms": move.get("ms") if type(move.get("ms")) is int else None,
            "board": snapshot(squares),
        })
        previous_to = to_square
    return annotated


def reconstruct(moves, final_sfen: str) -> dict | None:
    """The annotated kifu, or ``None`` when the rebuild misses ``finalSfen``.

    The authoritative final position is the site's own; a mismatch means the
    move list was not understood (drift, a different ruleset, a truncated
    replay) and the caller must not display a board.
    """
    try:
        if not isinstance(final_sfen, str) or not SFEN.fullmatch(final_sfen):
            return None
        annotated = annotate(moves)
        squares, turn, hands = parse_sfen(STANDARD_START)
        for move in moves:
            apply_move(squares, hands, move["usi"], move["by"])
        rebuilt = to_sfen(squares, hands, turn="b" if move["by"] == "gote" else "w",
                          move_number=len(moves) + 1)
    except (BoardError, KeyError, TypeError, ValueError):
        return None
    authoritative = " ".join(final_sfen.split(" ")[:3])
    if " ".join(rebuilt.split(" ")[:3]) != authoritative:
        return None
    return {
        "start": STANDARD_START,
        "final": rebuilt,
        "moves": annotated,
        "moveCount": len(annotated),
    }
