"""Lead-in (preamble) normalization for PAPER corner narration.

The narration prompts ask the model to 「結論を先に言い、その後に理由や数字を
添える」, and the model routinely paraphrases that instruction into a spoken
lead-in such as 「結論からお伝えしますと、…」 at the head of a segment.  The
phrase carries no information and was reported as unnecessary on air
(2026-09-23), so it is removed from generated segments before they are stored
and spoken.

The rule is deliberately narrow: only a known lead-in verb phrase, or the
fixed 「まず結論ですが」 form, at the very start of the text is removed.
Statements that merely begin with 結論 keep their subject/origin:
「結論は、まだ確定していない。」 and 「結論から、逆算する戦略は取らない。」 are
left untouched, as are 「結論は大事だ。」 and mid-text occurrences.
"""
from __future__ import annotations

import re

# Longer forms first: Python's alternation takes the first branch that matches,
# so 「言いますと」 has to be tried before 「言うと」.  None of these is a prefix
# of another, so ordering is belt-and-braces rather than load-bearing.
_LEADING_PREAMBLE_CONNECTORS = (
    "お伝えしますと",
    "お伝えいたしますと",
    "お伝えすると",
    "お話ししますと",
    "申し上げますと",
    "申し上げると",
    "言わせてもらいますと",
    "言わせてもらえますと",
    "言わせていただくと",
    "言いますと",
    "言うならば",
    "述べますと",
    "述べると",
    "言うと",
    "いうと",
    "言えば",
    "いえば",
    "としては",
    "すれば",
    "すると",
    "見ると",
    "見れば",
)
# Trailing separators after the lead-in: commas/colons, the sentence closers a
# model may substitute for a comma, ideographic/half-width space, tab, CR.
_PUNCT_CLASS = r"[、,：:。．！？!?　 \t\r]"

# 「まず結論ですが」はそれ自体が前口上なので、続く区切り文字ごと落としてよい。
# 一方「結論は」「結論から」だけでは主語・起点になり得る（「結論は、まだ確定
# していない。」）ため識別は接続表現を必須とし、「結論は大事だ。」
# 「結論と判断するのは早計だが、」のような内容文は削らない。
_BARE_PUNCT_PREFIX = r"まず結論ですが"

# Branch 1: 結論(から/を先に/は) — 任意 — + optional spacing + a known lead-in
# verb phrase + optional separator.  The connector is mandatory, so a plain
# statement (「結論は大事だ。」「結論は\n大事だ。」「結論は 大事だ。」「結論とは
# 違う。」) can never match, while 「結論としては、」と「結論を先に言えば、」は
# それでも捕捉できる。Branch 2: the fixed 「まず結論ですが」 + separator.
_LEADING_PREAMBLE_RE = re.compile(
    r"\A(?:まず)?結論(?:から|を先に|は|とは)?[ \t]*(?:"
    + "|".join(_LEADING_PREAMBLE_CONNECTORS)
    + rf"){_PUNCT_CLASS}*"
    rf"|\A{_BARE_PUNCT_PREFIX}{_PUNCT_CLASS}+"
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
