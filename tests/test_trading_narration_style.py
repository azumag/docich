from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.narration_style import strip_leading_preamble  # noqa: E402


def test_known_lead_ins_at_the_head_are_removed():
    cases = {
        "結論からお伝えしますと、本日は損益が拮抗しています。": "本日は損益が拮抗しています。",
        "結論からお伝えしますと見送りが正解でした。": "見送りが正解でした。",
        "結論を先に言えば、様子見が正解です。": "様子見が正解です。",
        "まず結論ですが、これは不要です。": "これは不要です。",
        "まず結論から言うと、値幅は狭い。": "値幅は狭い。",
        "結論は、上昇が基調です。": "上昇が基調です。",
        "結論から言えば、見送りです。": "見送りです。",
        # A doubled lead-in is fully removed within the bounded pass count.
        "結論からお伝えしますと、結論から言えば、保留です。": "保留です。",
    }
    for source, expected in cases.items():
        assert strip_leading_preamble(source) == expected, source


def test_statements_that_merely_start_with_conclusion_are_kept():
    kept = [
        "結論は大事だ。そのうえで数字を見ます。",
        "結論と判断するのは早計だが、勢いは強い。",
        "これは結論ではない。中身だけ話す。",
        # Newlines are formatting, never a lead-in boundary: stripping here
        # would delete the subject of a real sentence.
        "結論は\n大事だ。",
        # Nothing but the lead-in must not become an empty/blank utterance.
        "結論からお伝えしますと",
        "結論からお伝えしますと、",
        "",
        "前口上なしの通常の本文です。",
    ]
    for text in kept:
        assert strip_leading_preamble(text) == text, text
