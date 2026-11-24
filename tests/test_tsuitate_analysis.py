"""Grounded review content, including adverse replies and restricted alternatives."""
import pytest

from docich import shogi_board as sb
from docich import tsuitate_analysis as analysis
from docich import tsuitate_narration as narration
from docich import tsuitate_show as show


def kifu(*moves):
    return sb.annotate([{"usi": usi, "by": "sente" if n % 2 == 0 else "gote"}
                        for n, usi in enumerate(moves)])


def board(**pieces):
    result = [None] * 81
    for square, piece in pieces.items():
        result[analysis._index(square)] = piece
    return result


def candidates(position, side="sente"):
    return {move for move, _, _ in analysis.board_candidates(position, side)}


def test_opening_explains_the_open_line_and_a_concrete_comparison():
    cards = analysis.analysis_cards(kifu("7g7f", "3c3d"), 1, 2)
    first = cards[0]
    assert first["focus"] == 1
    assert "角の通り道" in first["observation"]
    assert "利点" in first["assessment"]
    assert "▲2六歩" in first["alternative"]
    assert "飛の通り道" in first["alternative"]
    assert first["text"].endswith(analysis.LIMIT)


def test_exchange_uses_the_actual_reply_even_across_a_chunk_boundary():
    moves = kifu("7g7f", "3c3d", "8h2b+", "3a2b", "B*5b")
    card = analysis.analysis_cards(moves, 1, 3)[0]
    assert card["focus"] == 3
    assert "4手目△同銀" in card["assessment"]
    assert "交換の代償" in card["assessment"]
    assert "▲2二角不成" in card["alternative"]
    assert "取られる可能性" in card["alternative"]
    assert len(card["text"]) <= show.MAX_CHUNK_SPEECH
    assert "△3四歩" not in card["text"]  # no full move-list recitation


def test_promoted_capture_returns_the_unpromoted_piece_to_hand():
    card = analysis.analysis_cards(kifu("7g7f", "3c3d", "8h2b+", "3a2b"), 4, 4)[0]
    assert "角を持ち駒" in card["observation"]
    assert "馬を持ち駒" not in card["text"]
    assert "△2二飛" in card["alternative"]  # a different recapturing piece


def test_exposed_drop_is_a_risk_not_an_unproved_blunder():
    card = analysis.analysis_cards(kifu("7g7f", "3c3d", "8h2b+", "3a2b", "B*5b"), 5, 5)[0]
    assert "相手が次に取れる" in card["assessment"]
    assert "▲2六歩" in card["alternative"]
    assert "当時の見え方や最善手は判定していません" in card["text"]
    assert "悪手" not in card["text"]


@pytest.mark.parametrize("piece,target,expected", [
    ("P", "5d", True), ("P", "5f", False),
    ("p", "5f", True), ("p", "5d", False),
    ("N", "4c", True), ("n", "4g", True), ("N", "4g", False),
    ("S", "4f", True), ("G", "4f", False), ("+S", "4f", False),
    ("+P", "4e", True), ("+N", "4e", True), ("+L", "5f", True),
    ("L", "5a", True), ("L", "5i", False), ("l", "5i", True),
    ("B", "1a", True), ("R", "1a", False),
    ("+B", "5d", True), ("+B", "5c", False),
    ("+R", "4d", True), ("+R", "3c", False),
    ("K", "4d", True), ("K", "3c", False),
])
def test_piece_geometry_both_sides_and_promotions(piece, target, expected):
    assert analysis.attacks(board(**{"5e": piece}), analysis._index("5e"), analysis._index(target)) is expected


def test_sliders_cannot_jump_and_knights_can():
    position = board(**{"5e": "R", "5c": "P"})
    assert not analysis.attacks(position, analysis._index("5e"), analysis._index("5a"))
    assert analysis.attacks(position, analysis._index("5e"), analysis._index("5c"))
    position = board(**{"5e": "N", "5d": "P", "4d": "P"})
    assert analysis.attacks(position, analysis._index("5e"), analysis._index("4c"))


def test_candidates_respect_path_own_occupancy_and_self_check():
    position, _, _ = sb.parse_sfen(sb.STANDARD_START)
    options = candidates(position)
    assert "7g7f" in options
    assert "8h2b+" not in options  # pawn blocks the bishop
    assert "5i5h" in options
    assert "5i4i" not in options  # own gold
    pinned = board(**{"5i": "K", "9a": "k", "5a": "r", "5h": "G"})
    assert "5h4h" not in candidates(pinned)
    assert "5h5g" in candidates(pinned)
    checked = board(**{"5i": "K", "9a": "k", "5a": "r", "4a": "r"})
    assert "5i4i" not in candidates(checked)
    assert "5i6i" in candidates(checked)


def test_candidates_respect_forced_and_optional_promotions():
    position = board(**{"5i": "K", "9a": "k", "5b": "P", "4b": "S", "2c": "N"})
    options = candidates(position)
    assert "5b5a+" in options and "5b5a" not in options
    assert {"4b3a", "4b3a+"} <= options
    assert "2c1a+" in options and "2c1a" not in options
    gote = board(**{"9i": "K", "5a": "k", "5h": "p", "2g": "n"})
    options = candidates(gote, "gote")
    assert "5h5i+" in options and "5h5i" not in options
    assert "2g1i+" in options and "2g1i" not in options


def test_candidates_never_include_drops_or_king_capture():
    position = board(**{"5i": "K", "5a": "k", "5b": "R"})
    options = candidates(position)
    assert "5b5a" not in options and "5b5a+" not in options
    assert all("*" not in move for move in options)
    assert not candidates(board(**{"5b": "R", "5a": "k"}))


def test_promotion_explanation_does_not_say_silver_keeps_all_its_moves():
    before = board(**{"5i": "K", "9a": "k", "5c": "S"})
    after = board(**{"5i": "K", "9a": "k", "5b": "+S"})
    text, _ = analysis._purpose(before, after, "5c5b+", "sente")
    assert "金と同じ動き" in text


@pytest.mark.parametrize("bad", [None, [], [{"n": 1}], kifu("7g7f")[1:]])
def test_missing_or_incomplete_evidence_never_becomes_board_analysis(bad):
    assert analysis.analysis_cards(bad, 1, 6) == []


def test_snapshot_drift_and_missing_board_fail_closed():
    moves = kifu("7g7f", "3c3d")
    moves[0]["board"] = moves[1]["board"]
    assert analysis.analysis_cards(moves, 1, 2) == []
    moves[0]["board"] = None
    assert show.fallback_chunk_text(moves, 1, 2) == analysis.UNAVAILABLE


@pytest.mark.parametrize("response", [
    '{"focus": true}', '{"focus": 999}', '{"focus": 1, "text": "必勝です"}',
    '```json\n{"focus": 1}\n```', '相手の隠し駒を読んだ最善手です。',
    '{"focus":', None, 'x' * 101,
])
def test_model_cannot_introduce_unverified_prose_or_alternatives(response):
    moves = kifu("7g7f", "3c3d")
    text, kind = narration.chunk_commentary(None, "fixture", moves, 1, 2,
                                            generate_fn=lambda *_: response)
    assert kind == "fallback"
    assert text == show.fallback_chunk_text(moves, 1, 2)
    assert "比較候補" in text


def test_model_selects_only_a_supplied_grounded_point():
    moves = kifu("7g7f", "3c3d")
    seen = []
    def choose(_g, _agents, facts):
        seen.append(facts)
        return '{"focus": 2}'
    text, kind = narration.chunk_commentary(None, "fixture", moves, 1, 2, generate_fn=choose)
    assert kind == "ai" and "焦点は2手目" in text
    assert "比較候補" in text and text.endswith(analysis.LIMIT)
    assert "assessment" in seen[0] and "alternative" in seen[0]
    assert "board" not in seen[0] and "gameId" not in seen[0]


def test_no_model_and_provider_exception_keep_analysis():
    moves = kifu("7g7f", "3c3d")
    def fail(*_):
        raise RuntimeError("provider unavailable")
    for agents in ("", "fixture"):
        text, kind = narration.chunk_commentary(None, agents, moves, 1, 2, generate_fn=fail)
        assert kind == "fallback" and "角の通り道" in text and "比較候補" in text


def test_prompt_asks_for_selection_instead_of_unchecked_commentary():
    prompt = narration.build_chunk_prompt(show.chunk_facts(kifu("7g7f", "3c3d"), 1, 2))
    assert "JSONだけ" in prompt
    assert "当時の見え方や最善手は判定していません" in prompt
    assert "指し手の列挙はしません" in prompt


def test_long_record_and_three_digit_move_numbers_keep_speech_bounded():
    moves = kifu(*(["4i5h", "4a5b", "5h4i", "5b4a"] * 128))
    for start, end in show.chunk_ranges(len(moves)):
        cards = analysis.analysis_cards(moves, start, end)
        assert 1 <= len(cards) <= 3
        assert all(len(card["text"]) <= show.MAX_CHUNK_SPEECH for card in cards)
        assert all(card["text"].endswith(analysis.LIMIT) for card in cards)


def test_lost_drop_is_not_described_as_a_recapture_or_exchange():
    moves = kifu("7g7f", "3c3d", "8h2b+", "3a2b", "B*5b", "4a5b")
    card = analysis.analysis_cards(moves, 5, 5)[0]
    assert "6手目△同金で取られ" in card["assessment"]
    assert "交換" not in card["assessment"] and "取り返され" not in card["assessment"]
