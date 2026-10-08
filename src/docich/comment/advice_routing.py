"""Deterministic advice target resolution for the native comment core (#829 §7).

This is the docich native home of the *deterministic* half of the advice
routing rules: the part that #829 §7.0 explicitly requires to be independent of
JEV/semantic decisions and of any new policy flag.

Legacy equivalence
------------------

The behaviour implemented here mirrors the append-side routing that production
soviet_now already uses as its post-bugfix baseline:

* ``_detect_strategy_advice_target_mode`` — prefix/word target detection,
* ``_append_strategy_advice_item_at_target`` — the target is resolved *once*
  from the original text and the mode prefix is stripped only afterwards for
  display,
* ``_append_structured_strategy_advice_at_intake`` — intake rows are projected
  to the intake mode when no explicit target exists.

The differences are the ones #829 §7.0 demands:

1. ``next``/``nextnext``/``hold``/``順位``/``相手`` alone never select a target
   (they are strategy content words, not target words).  The other legacy
   content words that used to force ``soren91`` (``おじゃま``/``盤面タイプ``/
   ``試合``) are treated as content too: with no explicit target the result is
   the intake mode, not a guess.
2. The mode prefix is read before it is stripped, and conflicting prefixes are
   unresolved instead of silently picking the first one.
3. Negation, quotations, comparisons and "correction" statements do not resolve
   a target by word match; without a usable explicit target they become
   ``unresolved`` rather than falling back to a game guess.

The module is deliberately pure (no I/O, no environment, no clock) and is not
wired into a caller yet: ``extract_structured_advice`` still carries the ported
deterministic extractor, and wiring this resolver into the batch path belongs to
the §7 PR-5 slice.  Adding it now gives the cutover a reviewed, testable target
contract instead of a second heuristic.

Not in this module: the JEV auxiliary question set (``comment-routes-v1``),
advice *kind* classification by semantics, file append/locking, and metrics.
``explicit_target`` below is the typed input the semantic layer will supply; the
deterministic rules run identically whether it is ``None`` (JEV off) or given.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


TARGET_MAIN = "main"
TARGET_SOREN91 = "soren91"

KIND_NONE = "none"
KIND_STRATEGY = "strategy"
KIND_COMMENT_STYLE = "comment_style"
KIND_STREAM_SYSTEM = "stream_system"
KIND_UNRESOLVED = "unresolved"

ADVICE_KINDS = (KIND_NONE, KIND_STRATEGY, KIND_COMMENT_STYLE, KIND_STREAM_SYSTEM, KIND_UNRESOLVED)
STRATEGY_TARGETS = (TARGET_MAIN, TARGET_SOREN91)

# Fixed reason enums (#829 §11: never embed free text or raw exceptions).
REASON_NO_ADVICE = "no_advice"
REASON_NO_TARGET_ROUTE = "no_target_route"
REASON_UNRESOLVED_KIND = "unresolved_kind"
REASON_EXPLICIT_PREFIX = "explicit_prefix"
REASON_EXPLICIT_PREFIX_OVER_SEMANTIC = "explicit_prefix_over_semantic"
REASON_CONFLICTING_PREFIX = "conflicting_prefix"
REASON_EXPLICIT_TEXT = "explicit_text"
REASON_EXPLICIT_BOTH = "explicit_both"
REASON_AMBIGUOUS_TARGET = "ambiguous_target"
REASON_OTHER_TARGET = "other_target"
REASON_SEMANTIC_TARGET = "semantic_target"
REASON_SEMANTIC_BOTH = "semantic_both"
REASON_INTAKE_MODE = "intake_mode"
REASON_MISSING_INTAKE_MODE = "missing_intake_mode"

#: JEV/semantic auxiliary labels (§4.1 ``explicit_target``).
SEMANTIC_TARGETS = ("main", "soren91", "both", "other", "unspecified", "ambiguous")

_LEGACY_KIND_MAP = {
    "strategy": KIND_STRATEGY,
    "comment": KIND_COMMENT_STYLE,
    "codex": KIND_STREAM_SYSTEM,
    "": KIND_NONE,
}

_SEMANTIC_KIND_MAP = {
    "none": KIND_NONE,
    "strategy": KIND_STRATEGY,
    "comment_style": KIND_COMMENT_STYLE,
    "stream_system": KIND_STREAM_SYSTEM,
    "mixed_or_unknown": KIND_UNRESOLVED,
}

# Explicit target mentions.  Folded (NFKC + lower-case + no spaces) before use.
# Bare ``main`` is intentionally absent: it is a substring of unrelated English
# words, and the legacy term list used the bracketed form for the same reason.
_MAIN_MENTIONS = ("本編", "中華ai", "strategy.py", "[main]", "[soren]")
_SOREN91_MENTIONS = ("soren91", "対戦版", "メリケン", "91人", "[soren91]")
_MENTIONS = _MAIN_MENTIONS + _SOREN91_MENTIONS
_BOTH_MARKERS = ("両方", "どちらも", "both")

# Strategy vocabulary that used to move the target to soren91 (#829 §7.0).
# Kept as documentation + regression data: these must never select a target.
NON_TARGET_TERMS = (
    "next", "nextnext", "next-next", "hold", "順位", "相手", "rank",
    "おじゃま", "garbage", "盤面タイプ", "試合", "91",
)

_MENTION_ALT = "|".join(re.escape(m) for m in _MENTIONS)
_TAG_RE = re.compile(r"\[(main|soren|soren91)\]")
_LEADING_TAG_RE = re.compile(r"^\[(main|soren|soren91)][ \t]*", re.IGNORECASE)
_QUOTED_RE = re.compile(r"「[^」]*」|『[^』]*』")
_MENTION_RE = re.compile(_MENTION_ALT)
_JOINT_RE = re.compile(
    r"(?P<a>%s)(?:だけでなく|のみならず|も)(?P<b>%s)(?:も|にも|の両方)?" % (_MENTION_ALT, _MENTION_ALT)
)
_NEGATION_RE = re.compile(
    r"(?P<a>%s)(?:ではなく|ではない|じゃなく|じゃない|でなく|でない)[、,]*(?P<b>%s)"
    % (_MENTION_ALT, _MENTION_ALT)
)
_NEGATED_MENTION_RE = re.compile(
    r"(?P<m>%s)(?:ではなく|ではない|じゃなく|じゃない|でなく|でない)" % _MENTION_ALT
)
_QUOTE_CUES = ("と言われ", "と言って", "って言われ", "と書いて", "という引用", "とあった", "発言")
_COMPARISON_CUES = ("その話じゃない", "そうじゃない", "話が違う", "ちがう", "違う", "反対", "勘違い")


class AdviceRoutingError(ValueError):
    """Invalid input to the routing contract."""


@dataclass(frozen=True)
class AdviceRoute:
    """Where one advice row may be written.  No I/O has happened yet."""

    kind: str
    targets: tuple[str, ...]
    reason: str
    source_kind: str = "viewer_proposal"


@dataclass(frozen=True)
class AdviceWrite:
    """One exactly-once projection of an advice row (#829 §4.5/§7.2)."""

    target: str
    proposal_id: str
    body: str
    idempotency_key: str


def kind_from_legacy(label: str | None) -> str:
    """Map the ported deterministic extractor label (strategy/comment/codex)."""
    return _LEGACY_KIND_MAP.get((label or "").strip().lower(), KIND_UNRESOLVED)


def kind_from_semantic(label: str | None) -> str:
    """Map the JEV ``advice_kind`` label (§4.1).  Unknown values are unresolved."""
    return _SEMANTIC_KIND_MAP.get((label or "").strip().lower(), KIND_UNRESOLVED)


def strip_mode_prefix(body: str) -> str:
    """Drop one leading ``[main]/[soren]/[soren91]`` tag (display formatting only).

    Byte-compatible with legacy ``_strip_strategy_advice_mode_prefix``; target
    resolution must happen *before* this is applied.
    """
    return _LEADING_TAG_RE.sub("", body, count=1)


def normalize_advice_item(body: str) -> str:
    """Collapse newlines/whitespace the way the legacy append helper does."""
    return re.sub(r"[ \t\u3000]+", " ", body.replace("\n", " ")).strip()


def resolve_advice_route(
    body: str,
    *,
    kind: str | None = None,
    explicit_target: str | None = None,
    intake_mode: str | None = None,
    source_kind: str = "viewer_proposal",
) -> AdviceRoute:
    """Resolve the advice kind and target(s) from the original viewer text.

    ``body`` is the row as received (mode prefix still attached).  ``kind`` is
    the classified advice kind — legacy extractor labels mapped with
    :func:`kind_from_legacy`, or the semantic labels with
    :func:`kind_from_semantic`; ``None``/unknown is unresolved.
    ``explicit_target`` is the semantic auxiliary label, ``intake_mode`` the
    trusted receiving mode (``main``/``soren91`` only).
    """
    resolved_kind = _normalize_kind(kind)
    if resolved_kind == KIND_NONE:
        return AdviceRoute(KIND_NONE, (), REASON_NO_ADVICE, source_kind)
    if resolved_kind != KIND_STRATEGY:
        # comment_style / stream_system rows go to their own files, which are
        # not target-routed; unresolved rows stay unresolved.
        reason = REASON_UNRESOLVED_KIND if resolved_kind == KIND_UNRESOLVED else REASON_NO_TARGET_ROUTE
        return AdviceRoute(resolved_kind, (), reason, source_kind)

    text = _fold(body)
    semantic = _semantic_label(explicit_target)

    tags = _prefix_targets(text)
    prefix = None
    if tags:
        if len(set(tags)) > 1:
            return AdviceRoute(KIND_UNRESOLVED, (), REASON_CONFLICTING_PREFIX, source_kind)
        prefix = tags[0]

    if prefix is not None:
        # The written prefix is the strongest signal: a provider answer never
        # overrides it (#829 §7.1 order 1).
        conflict = (
            semantic in ("both", "other", "ambiguous")
            or (semantic in STRATEGY_TARGETS and semantic != prefix)
        )
        reason = REASON_EXPLICIT_PREFIX_OVER_SEMANTIC if conflict else REASON_EXPLICIT_PREFIX
        return AdviceRoute(KIND_STRATEGY, (prefix,), reason, source_kind)

    explicit = _explicit_text_target(text)
    if explicit == "both":
        return AdviceRoute(KIND_STRATEGY, (TARGET_MAIN, TARGET_SOREN91), REASON_EXPLICIT_BOTH, source_kind)
    if explicit in STRATEGY_TARGETS:
        return AdviceRoute(KIND_STRATEGY, (explicit,), REASON_EXPLICIT_TEXT, source_kind)
    if explicit == "ambiguous":
        return AdviceRoute(KIND_UNRESOLVED, (), REASON_AMBIGUOUS_TARGET, source_kind)

    if semantic in STRATEGY_TARGETS:
        return AdviceRoute(KIND_STRATEGY, (semantic,), REASON_SEMANTIC_TARGET, source_kind)
    if semantic == "both":
        return AdviceRoute(KIND_STRATEGY, (TARGET_MAIN, TARGET_SOREN91), REASON_SEMANTIC_BOTH, source_kind)
    if semantic in ("other", "ambiguous"):
        reason = REASON_OTHER_TARGET if semantic == "other" else REASON_AMBIGUOUS_TARGET
        return AdviceRoute(KIND_UNRESOLVED, (), reason, source_kind)

    mode = (intake_mode or "").strip().lower()
    if mode in STRATEGY_TARGETS:
        return AdviceRoute(KIND_STRATEGY, (mode,), REASON_INTAKE_MODE, source_kind)
    return AdviceRoute(KIND_UNRESOLVED, (), REASON_MISSING_INTAKE_MODE, source_kind)


def plan_advice_writes(
    route: AdviceRoute, *, body: str, proposal_id: str
) -> tuple[AdviceWrite, ...]:
    """Project a resolved route to one exactly-once write per target (§4.5)."""
    proposal_id = (proposal_id or "").strip()
    if not proposal_id:
        raise AdviceRoutingError("proposal_id が必要です")
    if route.kind != KIND_STRATEGY or not route.targets:
        return ()
    item = normalize_advice_item(strip_mode_prefix(body))
    if not item:
        return ()
    return tuple(
        AdviceWrite(
            target=target,
            proposal_id=proposal_id,
            body=item,
            idempotency_key=f"{proposal_id}:{target}",
        )
        for target in route.targets
    )


def _normalize_kind(kind: str | None) -> str:
    value = (kind or "").strip().lower()
    return value if value in ADVICE_KINDS else KIND_UNRESOLVED


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text or "").lower()
    return folded.replace(" ", "").replace("\u3000", "")


def _prefix_targets(normalized: str) -> list[str]:
    """Return the target of every contiguous leading ``[...]`` tag."""
    tags: list[str] = []
    position = 0
    while True:
        match = _TAG_RE.match(normalized, position)
        if not match:
            break
        tags.append(TARGET_MAIN if match.group(1) in ("main", "soren") else TARGET_SOREN91)
        position = match.end()
    return tags


def _group(mention: str) -> str | None:
    if mention in _MAIN_MENTIONS:
        return TARGET_MAIN
    if mention in _SOREN91_MENTIONS:
        return TARGET_SOREN91
    return None


def _explicit_text_target(normalized: str) -> str | None:
    """Resolve an explicitly written target, or ``None`` when none is usable.

    Returns ``"both"``, ``"main"``, ``"soren91"``, ``"ambiguous"`` or ``None``.
    """
    quoted_target = any(
        _MENTION_RE.search(match.group(0)) for match in _QUOTED_RE.finditer(normalized)
    )
    # Every target rule sees the same unquoted text.  Keep a separator so
    # removing a quote cannot join mentions into a negation or joint pair.
    normalized = _QUOTED_RE.sub(lambda match: " " * len(match.group(0)), normalized)

    negation = _NEGATION_RE.search(normalized)
    if negation is not None:
        # "本編ではなくSoren91" resolves to the non-negated side.
        group = _group(negation.group("b"))
        if group:
            return group

    # A disagreement/comparison statement is not a target instruction
    # (#829 §7.1: 否定・引用・比較では単語一致で確定しない).
    if any(cue in normalized for cue in _COMPARISON_CUES):
        return "ambiguous"

    joint = _JOINT_RE.search(normalized)
    if joint is not None:
        first, second = _group(joint.group("a")), _group(joint.group("b"))
        if first and second:
            return "both" if first != second else first

    if any(marker in normalized for marker in _BOTH_MARKERS):
        groups = {group for group in (_group(m) for m in _MENTION_RE.findall(normalized)) if group}
        if len(groups) > 1:
            return "both"
        if len(groups) == 1:
            return next(iter(groups))

    negated_spans = [m.span() for m in _NEGATED_MENTION_RE.finditer(normalized)]
    groups = set()
    for match in _MENTION_RE.finditer(normalized):
        span = match.span()
        if any(start <= span[0] and span[1] <= end for start, end in negated_spans):
            continue
        group = _group(match.group(0))
        if group:
            groups.add(group)
    if len(groups) > 1:
        return "ambiguous"
    if len(groups) == 1:
        return next(iter(groups))

    if negated_spans or quoted_target:
        # "本編ではない方針で" names no usable target: do not fall back to a
        # guess, or let intake mode re-introduce a denied or quoted target.
        return "ambiguous"
    if any(cue in normalized for cue in _QUOTE_CUES):
        return "ambiguous"
    return None


def _semantic_label(explicit_target: str | None) -> str | None:
    """Return a validated semantic label; unknown values fail closed."""
    value = (explicit_target or "").strip().lower()
    if not value:
        return None
    return value if value in SEMANTIC_TARGETS else "ambiguous"
