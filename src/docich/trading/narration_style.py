"""Lead-in (preamble) normalization for PAPER corner narration.

The narration prompts ask the model to 「結論を先に言い、その後に理由や数字を
添える」, and the model routinely paraphrases that instruction into a spoken
lead-in such as 「結論からお伝えしますと、…」 at the head of a segment.  The
phrase carries no information and was reported as unnecessary on air
(2026-09-23), so it is removed from generated segments before they are stored
and spoken.

The rule is deliberately narrow: only a known lead-in form at the very start of
the text is removed, and at least one character after 「結論」 must be consumed.
Plain statements that merely begin with 結論 (「結論は大事だ。…」,
「結論と判断するのは早計だが、…」) are therefore kept untouched.
"""
from __future__ import annotations

import re

# Longer forms first: Python's alternation takes the first branch that matches,
# so 「言いますと」 has to be tried before 「言うと」.
_LEADING_PREAMBLE_CONNECTORS = (
    "お伝えしますと",
    "お伝えいたしますと",
    "お伝えすると",
    "お話ししますと",
    "申し上げますと",
    "申しますと",
    "言わせてもらいますと",
    "言いますと",
    "述べますと",
    "述べると",
    "言うと",
    "いうと",
    "言えば",
    "いえば",
    "すれば",
    "すると",
    "見ると",
    "見れば",
)
_PUNCT_CLASS = r"[、,：: \t]"

# 「まず結論」も同じ前口上として扱う。読み替え不能な平叙文（「結論は大事だ。」
# 「結論と判断するのは早計だが、」）は、既知の接続表現か直後の句読点のどちらかが
# 必須なので、内容文が誤って削られることはない。
_LEADING_PREAMBLE_PREFIX = r"(?:まず)?結論(?:から|を先に|は|ですが)"

# Branch 1: 「結論(から/を先に/は)」 + a known lead-in verb phrase + optional
# punctuation.  Branch 2: 「結論(から/を先に/は)」 immediately followed by
# punctuation (「結論は、…」「結論から、…」).  Neither branch can match an empty
# remainder, so nothing is removed unless the head really is a lead-in.
_LEADING_PREAMBLE_RE = re.compile(
    rf"\A{_LEADING_PREAMBLE_PREFIX}(?:"
    + "|".join(_LEADING_PREAMBLE_CONNECTORS)
    + rf"){_PUNCT_CLASS}*"
    rf"|\A{_LEADING_PREAMBLE_PREFIX}{_PUNCT_CLASS}+"
)

# Bounded so a pathological input cannot loop; two passes already cover a
# doubled lead-in and the third is margin, while anything beyond that is left
# to the prompt contract.
_MAX_PASSES = 3


def strip_leading_preamble(text: str) -> str:
    """Remove a leading conclusion lead-in; never return an empty/blank result.

    Returns the input unchanged when it does not start with a known lead-in or
    when removing one would consume the whole text (a segment that is nothing
    but the preamble is spoken as-is rather than becoming silence).
    """
    value = str(text)
    for _ in range(_MAX_PASSES):
        stripped = _LEADING_PREAMBLE_RE.sub("", value, count=1)
        if stripped == value:
            break
        if not stripped.strip():
            return value
        value = stripped
    return value
