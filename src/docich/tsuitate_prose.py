"""Sentence-level repetition control for the 感想戦 prose (docich#2737).

The review must read as one coherent flow, not as the same idea restated
stretch after stretch.  This module only *selects* sentences that were
already generated from verified facts: it never rewrites, reorders words
inside a sentence, or invents text.  A sentence that repeats an earlier one
— the same words, or a longer near-identical sentence — is dropped, so a
template tail or a disclaimer is said once instead of once per stretch.
"""
from __future__ import annotations

import re

#: A spoken sentence: any run of text ending at (or lacking) a terminator.
_SENTENCE = re.compile(r"[^。！？!?]*[。！？!?]?")
#: Characters that never carry meaning when two sentences are compared.
_NOISE = re.compile(r"[\s、。！？!?「」『』（）()【】,，.:：;；・…‥ー\-—]+")
#: Bigram similarity above which two long sentences count as the same idea.
NEAR_DUPLICATE_JACCARD = 0.8
#: Fuzzy comparison needs some length; short sentences must match exactly.
MIN_FUZZY_CHARS = 12


def split_sentences(text) -> list[str]:
    """Split into spoken sentences, keeping each terminator, dropping empties."""
    if not isinstance(text, str) or not text:
        return []
    return [part for part in _SENTENCE.findall(text) if part.strip()]


def _normalize(sentence: str) -> str:
    return _NOISE.sub("", sentence)


def _bigrams(value: str) -> set[str]:
    return {value[i:i + 2] for i in range(len(value) - 1)}


def _same_idea(a: str, b: str) -> bool:
    """Exact (punctuation-insensitive) match, or a near-identical long sentence."""
    if a == b:
        return True
    if len(a) < MIN_FUZZY_CHARS or len(b) < MIN_FUZZY_CHARS:
        return False
    left, right = _bigrams(a), _bigrams(b)
    union = len(left | right)
    return union > 0 and len(left & right) / union >= NEAR_DUPLICATE_JACCARD


def dedupe_text(text, *, limit=None, seen=None) -> str:
    """Drop sentences that repeat an earlier one, keeping the original order.

    ``seen`` accumulates the normalized sentences kept so far, so several
    texts can be deduped as one flow by passing the same list to consecutive
    calls; without it only repetitions inside ``text`` are dropped.  ``limit``
    bounds the result: sentences are kept whole while they fit, the rest is
    dropped, and only a single over-long first sentence is hard-truncated
    (the spoken bound must always hold).
    """
    kept: list[str] = []
    out: list[str] = []
    length = 0
    for sentence in split_sentences(text):
        normalized = _normalize(sentence)
        if not normalized:
            continue
        context = seen if seen is not None else kept
        if any(_same_idea(normalized, earlier) for earlier in context):
            continue
        if limit is not None:
            if length and length + len(sentence) > limit:
                break
            if len(sentence) > limit:
                sentence = sentence[:limit]
                normalized = _normalize(sentence)
                if not normalized:
                    break
        context.append(normalized)
        out.append(sentence)
        length += len(sentence)
        if limit is not None and length >= limit:
            break
    return "".join(out).strip()


def aggregate_texts(texts, *, limit) -> list[str]:
    """Dedupe several texts as one spoken flow, one bounded text per input.

    The review show speaks these in order, so a sentence a later stretch
    repeats from an earlier stretch is said once.  A stretch whose every
    sentence was already spoken keeps its first sentence: a repeated line is
    still better than silence in the middle of the show.
    """
    seen: list[str] = []
    out = []
    for text in texts:
        deduped = dedupe_text(text, limit=limit, seen=seen)
        if not deduped:
            first = split_sentences(text)
            deduped = first[0][:limit] if first else ""
        out.append(deduped)
    return out
