"""Small, deterministic post-game explanations, not a strength/search engine.

Inputs are the complete annotated *public* replay accepted by read_kifu_detail.
We compare local board changes and the recorded reply. Alternatives deliberately
cover board moves only: movement, blocking, promotion and own-king safety are
checked, but drops, repetition and deeper variations are not searched. Nothing
here reconstructs what a player knew behind the screen or grades their intent.
"""
from __future__ import annotations

from . import shogi_board as sb

LIMIT = "公開後の盤面での比較で、当時の見え方や最善手は判定していません。"
UNAVAILABLE = "盤面を照合できないため、この区間の狙いや代案は判断できません。"
_VALUE = {"P": 1, "L": 3, "N": 3, "S": 5, "G": 6, "B": 8, "R": 10, "K": 0}


def _side(piece):
    return "sente" if piece[-1].isupper() else "gote"


def _square(index):
    return str(9 - index % 9) + sb.RANKS[index // 9]


def _index(square):
    return sb.RANKS.index(square[1]) * 9 + 9 - int(square[0])


def _destination(usi):
    return _index(usi[2:4])


def attacks(board, source, target):
    """Piece geometry including blockers; not a checkmate/legal-move claim."""
    piece = board[source]
    if not piece or source == target:
        return False
    dr, dc = target // 9 - source // 9, target % 9 - source % 9
    forward = -1 if _side(piece) == "sente" else 1
    r = dr * forward
    role = piece.upper()
    if role in {"G", "+P", "+L", "+N", "+S"}:
        return (r, dc) in {(1, -1), (1, 0), (1, 1), (0, -1), (0, 1), (-1, 0)}
    if role == "P":
        return (r, dc) == (1, 0)
    if role == "N":
        return r == 2 and abs(dc) == 1
    if role == "S":
        return (r, dc) in {(1, -1), (1, 0), (1, 1), (-1, -1), (-1, 1)}
    if role == "K":
        return max(abs(dr), abs(dc)) == 1
    if role == "+B" and abs(dr) + abs(dc) == 1:
        return True
    if role == "+R" and abs(dr) == abs(dc) == 1:
        return True
    ray = ((role in {"B", "+B"} and abs(dr) == abs(dc))
           or (role in {"R", "+R"} and (dr == 0 or dc == 0))
           or (role == "L" and dc == 0 and r > 0))
    if not ray:
        return False
    step_r, step_c = (dr > 0) - (dr < 0), (dc > 0) - (dc < 0)
    row, col = source // 9 + step_r, source % 9 + step_c
    while (row, col) != (target // 9, target % 9):
        if board[row * 9 + col]:
            return False
        row, col = row + step_r, col + step_c
    return True


def _checked(board, side):
    kings = [i for i, p in enumerate(board) if p == ("K" if side == "sente" else "k")]
    # A malformed board must not authorize a candidate.
    if len(kings) != 1:
        return True
    return any(p and _side(p) != side and attacks(board, i, kings[0])
               for i, p in enumerate(board))


def board_candidates(board, side):
    """Locally checked on-board alternatives; excludes drops and king captures."""
    for source, piece in enumerate(board):
        if not piece or _side(piece) != side:
            continue
        for target, captured in enumerate(board):
            if captured and (_side(captured) == side or captured.upper() == "K"):
                continue
            if not attacks(board, source, target):
                continue
            origin, destination = _square(source), _square(target)
            zone = "abc" if side == "sente" else "ghi"
            promotion = (piece.upper() in sb.PROMOTED
                         and (origin[1] in zone or destination[1] in zone))
            last = "a" if side == "sente" else "i"
            last_two = "ab" if side == "sente" else "hi"
            forced = ((piece.upper() in {"P", "L"} and destination[1] == last)
                      or (piece.upper() == "N" and destination[1] in last_two))
            for promote in ((False, True) if promotion else (False,)):
                if forced and not promote:
                    continue
                after = list(board)
                after[source] = None
                after[target] = ("+" if promote else "") + piece
                if not _checked(after, side):
                    usi = origin + destination + ("+" if promote else "")
                    yield usi, after, captured


def _line_gain(before, after, side):
    """A stationary bishop/rook whose unobstructed destinations increased."""
    for i, piece in enumerate(before):
        if not piece or _side(piece) != side or piece.upper() not in {"B", "R"} or after[i] != piece:
            continue
        def reach(board):
            return {j for j, p in enumerate(board)
                    if (not p or _side(p) != side) and attacks(board, i, j)}
        forward = -1 if side == "sente" else 1
        old, new = reach(before), reach(after)
        if len(new) > len(old) and any((j // 9 - i // 9) * forward >= 0 for j in new - old):
            return sb.piece_name(piece)
    return None


def _purpose(before, after, usi, side, captured=None):
    piece = after[_destination(usi)]
    if _checked(before, side) and not _checked(after, side):
        return "自玉への王手を解消しています", 8
    if captured:
        return f"{sb.piece_name(sb.demote(captured))}を持ち駒にし、後で打つ選択肢を得ています", 6
    if usi.endswith("+"):
        benefit = {"+B": "一歩の縦横移動を加えています",
                   "+R": "一歩の斜め移動を加えています"}.get(
                       piece.upper(), "金と同じ動きに変えています")
        return f"{sb.piece_name(piece)}を作り、{benefit}", 5
    opened = _line_gain(before, after, side)
    if opened:
        return f"{opened}の通り道を開き、その駒を使える範囲を広げています", 4
    if "*" in usi:
        return f"持ち駒の{sb.piece_name(piece)}を投入し、盤上で働かせる形にしています", 3
    return f"{sb.piece_name(piece)}の配置を変える手です", 0


def _can_be_taken(board, target, side):
    opponent = "gote" if side == "sente" else "sente"
    # Only test pieces attacking this square, rather than enumerate every reply.
    for source, piece in enumerate(board):
        if not piece or _side(piece) != opponent or not attacks(board, source, target):
            continue
        after = list(board)
        after[target], after[source] = piece, None
        if not _checked(after, opponent):
            return True
    return False


def _alternative(before, actual, side):
    candidates = []
    for usi, after, captured in board_candidates(before, side):
        if usi == actual:
            continue
        purpose, motif = _purpose(before, after, usi, side, captured)
        danger = _can_be_taken(after, _destination(usi), side)
        # This only picks an explanatory contrast, never an evaluation of who wins.
        gain = _VALUE.get(sb.demote(captured or "K").upper(), 0)
        risk = _VALUE.get(sb.demote(after[_destination(usi)]).upper(), 0) if danger else 0
        score = (gain - risk, motif, usi[:2] == actual[:2], -abs(_destination(usi) % 9 - 4))
        candidates.append((score, usi, after, purpose, danger))
    if not candidates:
        return "盤上の移動だけでは別候補を確認できず、持ち駒の打ち手は未検討です。"
    _score, usi, after, purpose, danger = max(candidates, key=lambda row: (row[0], row[1]))
    name = sb.notation(usi, side, after[_destination(usi)])
    contrast = "取られる可能性が残ります" if danger else "移動した駒は直後には取られません"
    return f"比較候補は{name}で、{purpose}。{contrast}が、続く応手の読みは必要です。"


def _positions(moves):
    """Refuse a partial kifu or a drifted snapshot, even on the fallback path."""
    if not isinstance(moves, list) or not 1 <= len(moves) <= sb.MAX_MOVES:
        return []
    board, _turn, hands = sb.parse_sfen(sb.STANDARD_START)
    positions = []
    try:
        for n, move in enumerate(moves, 1):
            side = "sente" if n % 2 else "gote"
            if move["n"] != n or move["side"] != side:
                return []
            before = list(board)
            facts = sb.apply_move(board, hands, move["usi"], side)
            if sb.snapshot(board) != move.get("board"):
                return []
            if any(board.count(k) != 1 for k in ("K", "k")):
                return []
            positions.append((before, list(board), facts))
    except (KeyError, TypeError, ValueError, sb.BoardError):
        return []
    return positions


def analysis_cards(moves, start, end):
    """At most three evidence-backed focal points, best explanatory contrast first."""
    positions = _positions(moves)
    if not positions:
        return []
    ranked = []
    for move, (before, after, facts) in zip(moves, positions):
        n, side, usi = move["n"], move["side"], move["usi"]
        if not start <= n <= end:
            continue
        purpose, priority = _purpose(before, after, usi, side, facts["captured"])
        target = _destination(usi)
        reply = moves[n] if n < len(moves) else None
        recaptured = (reply is not None and reply["side"] != side
                      and "*" not in reply["usi"] and _destination(reply["usi"]) == target)
        if recaptured:
            verb = "取り返され" if facts["captured"] else "取られ"
            cost = "交換の代償もある手です。" if facts["captured"] else "駒を失う代償があった手です。"
            assessment = f"ただし{n + 1}手目{reply['notation']}で{verb}、動かした駒は残りません。{cost}"
            priority += 10
        elif after[target].upper() != "K" and _can_be_taken(after, target, side):
            assessment = "一方、動かした駒は相手が次に取れる位置なので、駒を失う危険があります。"
            priority += 7
        elif priority:
            assessment = "この働きは利点ですが、これだけで形勢の優劣は決まりません。"
        else:
            assessment = "直後の一手で取られない配置ですが、それだけで好手とは断定できません。"
        ranked.append((priority, n, before, purpose, assessment))
    cards = []
    for priority, n, before, purpose, assessment in sorted(ranked, key=lambda x: (-x[0], x[1]))[:3]:
        move = moves[n - 1]
        observation = f"焦点は{n}手目{move['notation']}。{purpose}。"
        alternative = _alternative(before, move["usi"], move["side"])
        cards.append({"focus": n, "observation": observation, "assessment": assessment,
                      "alternative": alternative, "limit": LIMIT,
                      "text": observation + assessment + alternative + LIMIT})
    return cards
