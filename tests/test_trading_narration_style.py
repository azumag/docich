from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.corner_script import (  # noqa: E402
    parse_next_narration,
    parse_script,
)
from docich.trading.narration_style import strip_leading_preamble  # noqa: E402


def test_known_lead_ins_at_the_head_are_removed():
    cases = {
        "結論からお伝えしますと、本日は損益が拮抗しています。": "本日は損益が拮抗しています。",
        "結論からお伝えしますと見送りが正解でした。": "見送りが正解でした。",
        "結論を先に言えば、様子見が正解です。": "様子見が正解です。",
        "まず結論ですが、これは不要です。": "これは不要です。",
        "まず結論から言うと、値幅は狭い。": "値幅は狭い。",
        "結論から言えば、見送りです。": "見送りです。",
        "結論としては、見送りです。": "見送りです。",
        "結論から申し上げると、保留です。": "保留です。",
        # Sentence closers substituted for a comma must not survive as a head.
        "結論から言うと。値幅は狭い。": "値幅は狭い。",
        # A doubled lead-in is fully removed within the bounded pass count.
        "結論からお伝えしますと、結論から言えば、保留です。": "保留です。",
    }
    for source, expected in cases.items():
        assert strip_leading_preamble(source) == expected, source


def test_statements_that_merely_start_with_conclusion_are_kept():
    kept = [
        "結論は大事だ。そのうえで数字を見ます。",
        # Subject/origin must survive: 結論は/結論から alone is never a lead-in.
        "結論は、まだ確定していない。明日の米国次回を待ちます。",
        "結論から、逆算する戦略は取らない。まず事実を並べます。",
        "結論と判断するのは早計だが、勢いは強い。",
        "結論とは違う話をする。",
        "これは結論ではない。中身だけ話す。",
        # Newlines and spaces are formatting, never a lead-in boundary.
        "結論は\n大事だ。",
        "結論は 大事だ。",
        # A separator is required for the bare 「まず結論ですが」 form.
        "まず結論ですがこれは不要です。",
        # Non-lead-in verbs after 結論 are not in the connector allowlist.
        "結論を見る。",
        "結論を述べる。",
        # The preamble only matters at the head, never mid-sentence.
        "本日は結論から言えば保留です。",
        # Nothing but the lead-in must not become an empty/blank utterance.
        "結論からお伝えしますと",
        "結論からお伝えしますと、",
        "",
        "前口上なしの通常の本文です。",
    ]
    for text in kept:
        assert strip_leading_preamble(text) == text, text


def test_parse_path_keeps_subject_across_newline_normalization():
    """corner_script replaces newlines with spaces *before* stripping, so the

    unit-level contract has to hold through the real parse path too.
    """
    text = "結論は\n大事だ。そのうえで数字を見ます。"
    item = parse_next_narration(json.dumps({"topic": "方針", "text": text}))
    assert item["text"] == "結論は 大事だ。そのうえで数字を見ます。"
    assert parse_script(json.dumps({"corner": text}))["corner"] == (
        "結論は 大事だ。そのうえで数字を見ます。"
    )
