"""Train-leakage checker for candidate mutations (#1308 section 5).

A candidate may read train failures, but it must generalise a failure *pattern*
rather than paste a case body into a new prompt asset. This checker flags a
long verbatim substring shared with any train case, an excessive n-gram overlap
ratio, and any echo of a ``case_id``/``group_id``. It is deliberately run
before a mutation is evaluated so a leaking candidate is never scored.
"""
from __future__ import annotations

import re
import unicodedata

DEFAULT_N = 8
DEFAULT_MAX_RATIO = 0.05
DEFAULT_MAX_SUBSTRING = 24
_KEEP_RE = re.compile(r"[0-9a-z\u3040-\u30ff\u3400-\u9fff]+")


def normalize_for_overlap(text: str) -> str:
    """NFKC, lowercase, punctuation-stripped form used for overlap comparison."""
    folded = unicodedata.normalize("NFKC", text or "").lower()
    return " ".join(_KEEP_RE.findall(folded))


def ngrams(text: str, n: int = DEFAULT_N) -> set:
    tokens = normalize_for_overlap(text).replace(" ", "")
    if len(tokens) < n:
        return {tokens} if tokens else set()
    return {tokens[i:i + n] for i in range(len(tokens) - n + 1)}


def longest_common_substring(a: str, b: str) -> int:
    a, b = normalize_for_overlap(a).replace(" ", ""), normalize_for_overlap(b).replace(" ", "")
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        current = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def max_overlap(mutation_text: str, cases, *, n: int = DEFAULT_N) -> dict:
    mutation_grams = ngrams(mutation_text, n)
    worst = {"ratio": 0.0, "case_id": None, "matched": [], "longest_substring": 0}
    for case in cases:
        body = case["input"]["comment"]
        shared = sorted(mutation_grams & ngrams(body, n))
        ratio = len(shared) / len(mutation_grams) if mutation_grams else 0.0
        span = longest_common_substring(mutation_text, body)
        if ratio > worst["ratio"] or span > worst["longest_substring"]:
            worst = {"ratio": max(ratio, worst["ratio"]), "case_id": case["case_id"],
                     "matched": shared or worst["matched"],
                     "longest_substring": max(span, worst["longest_substring"])}
    return worst


def check(mutation_text: str, cases, *, n: int = DEFAULT_N,
          max_ratio: float = DEFAULT_MAX_RATIO,
          max_substring: int = DEFAULT_MAX_SUBSTRING) -> dict:
    """Return ``{"ok", "violations", "overlap"}``; ``ok`` False blocks the mutation."""
    if not isinstance(mutation_text, str) or not mutation_text.strip():
        return {"ok": False, "violations": ["empty_mutation"],
                "overlap": {"ratio": 0.0, "case_id": None, "matched": [],
                            "longest_substring": 0}}
    violations = []
    for case in cases:
        if case["case_id"] in mutation_text:
            violations.append("case_id_echo")
        if case["group_id"] in mutation_text:
            violations.append("group_id_echo")
    overlap = max_overlap(mutation_text, cases, n=n)
    if overlap["longest_substring"] >= max_substring:
        violations.append("verbatim_substring")
    if overlap["ratio"] > max_ratio:
        violations.append("ngram_overlap")
    return {"ok": not violations, "violations": sorted(set(violations)), "overlap": overlap}
