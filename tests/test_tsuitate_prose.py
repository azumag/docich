"""感想戦の文の重複抑制 (docich#2737): 選別だけを行い、内容は変えない。

合成文だけを使い、モデル・ネットワーク・実行環境には触れない。
"""
from __future__ import annotations

from docich import tsuitate_prose as prose

LIMIT = "公開後の盤面での比較で、当時の見え方や最善手は判定していません。"


def test_dedupe_text_drops_repeated_sentences():
    text = "焦点は5手目▲２二飛成。動かした駒は残りません。焦点は5手目▲２二飛成。動かした駒は残りません。"

    assert prose.dedupe_text(text) == "焦点は5手目▲２二飛成。動かした駒は残りません。"


def test_dedupe_text_is_identity_without_repeats():
    text = "AIは先手で反則が重なりました。考慮時間は合計9秒でした。次もお付き合いください。"

    assert prose.dedupe_text(text) == text


def test_dedupe_text_ignores_punctuation_and_whitespace():
    text = f"同じ文です。{LIMIT}公開後の盤面での比較で 当時の見え方や最善手は判定していません。同じ文です。"

    assert prose.dedupe_text(text) == f"同じ文です。{LIMIT}"


def test_dedupe_text_keeps_distinct_short_sentences():
    text = "次もお付き合いください。次の対局もぜひご覧ください。"

    assert prose.dedupe_text(text) == text


def test_dedupe_text_keeps_distinct_long_sentences_with_their_own_facts():
    a = "ただし5手目▲２二飛成で取り返され、動かした駒は残りません。"
    b = "ただし40手目△４五歩で取り返され、動かした駒は残りません。"

    assert prose.dedupe_text(a + b) == a + b


def test_dedupe_text_drops_a_reordered_repeat_of_the_same_idea():
    text = ("持ち時間の使い方は落ち着いていました。反則が重なって最後は粘れませんでした。"
            "落ち着いていました、持ち時間の使い方は。")

    assert prose.dedupe_text(text) == ("持ち時間の使い方は落ち着いていました。"
                                       "反則が重なって最後は粘れませんでした。")


def test_dedupe_text_keeps_a_stylistic_variant_that_adds_its_own_turn():
    text = ("持ち時間の使い方は落ち着いていましたが、反則が重なりました。"
            "持ち時間の使い方は落ち着いていましたが、反則が重なってしまいました。次は改善します。")

    assert prose.dedupe_text(text) == text


def test_dedupe_text_bounds_at_a_sentence_boundary():
    long = "あ" * 100
    out = prose.dedupe_text(f"{long}。続く文です。", limit=105)

    assert out == f"{long}。"


def test_dedupe_text_hard_bounds_a_single_over_long_sentence():
    out = prose.dedupe_text("あ" * 80, limit=40)

    assert out == "あ" * 40


def test_dedupe_text_accumulates_a_shared_flow_in_seen():
    seen: list[str] = []
    first = prose.dedupe_text(f"こちらは冒頭の解説です。{LIMIT}", seen=seen)
    second = prose.dedupe_text(f"こちらは次の区間の解説です。{LIMIT}", seen=seen)

    assert first == f"こちらは冒頭の解説です。{LIMIT}"
    assert second == "こちらは次の区間の解説です。"
    assert LIMIT not in second


def test_dedupe_text_survives_non_string_and_empty_input():
    assert prose.dedupe_text(None) == ""
    assert prose.dedupe_text("") == ""
    assert prose.dedupe_text("。") == ""


def test_aggregate_texts_says_a_template_once_in_flow():
    texts = [f"焦点は{n}手目の局面です。{LIMIT}" for n in (5, 30, 60)]

    assert prose.aggregate_texts(texts, limit=240) == [
        f"焦点は5手目の局面です。{LIMIT}",
        "焦点は30手目の局面です。",
        "焦点は60手目の局面です。",
    ]


def test_aggregate_texts_keeps_one_sentence_when_all_were_spoken():
    texts = ["同じ区間の解説です。", "同じ区間の解説です。", "同じ区間の解説です。"]

    assert prose.aggregate_texts(texts, limit=240) == ["同じ区間の解説です。"] * 3


def test_aggregate_texts_keeps_the_unique_part_of_each_stretch():
    texts = [
        "焦点は5手目▲７六歩。利きを広げています。一方、危険があります。",
        "焦点は30手目△３四歩。形を整えています。一方、危険があります。",
    ]

    assert prose.aggregate_texts(texts, limit=240) == [
        "焦点は5手目▲７六歩。利きを広げています。一方、危険があります。",
        "焦点は30手目△３四歩。形を整えています。",
    ]


def test_aggregate_texts_bounds_each_text_separately():
    long = "あ" * 100
    out = prose.aggregate_texts([f"{long}。続く文です。", f"{long}。重複の文です。"], limit=120)

    # The first stretch keeps both of its sentences (107 chars fit the bound);
    # the second stretch already said its first sentence, so only its own
    # last sentence remains.
    assert out == [f"{long}。続く文です。", "重複の文です。"]


def test_aggregate_texts_survives_empty_and_non_string_input():
    assert prose.aggregate_texts([], limit=240) == []
    assert prose.aggregate_texts([None, "", "一文だけ。"], limit=240) == ["", "", "一文だけ。"]
