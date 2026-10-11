"""Regression tests for the native deterministic advice routing (#829 §7).

The first block is the fixture table #829 §7.2 requires verbatim ("必須fixture").
Everything else pins the surrounding contract: the §7.1 priority order, the
§7.0 vocabulary that must *not* select a target, semantic-label fail-closed
behaviour, and the §4.5 exactly-once write projection.
"""

from __future__ import annotations

import pytest

from docich.comment.advice_routing import (
    ADVICE_KINDS,
    KIND_COMMENT_STYLE,
    KIND_NONE,
    KIND_STRATEGY,
    KIND_STREAM_SYSTEM,
    KIND_UNRESOLVED,
    NON_TARGET_TERMS,
    AdviceRoute,
    AdviceRoutingError,
    kind_from_legacy,
    kind_from_semantic,
    normalize_advice_item,
    plan_advice_writes,
    resolve_advice_route,
    strip_mode_prefix,
    TARGET_MAIN,
    TARGET_SOREN91,
)


def route(body, **kwargs):
    """Resolve a strategy row unless the test says otherwise."""
    kwargs.setdefault("kind", KIND_STRATEGY)
    return resolve_advice_route(body, **kwargs)


# --- #829 §7.2 required fixtures -------------------------------------------


@pytest.mark.parametrize(
    "body,intake,expected",
    [
        ("[main] nextをふさがないで", "soren91", (TARGET_MAIN,)),
        ("本編でもnextを見て", "soren91", (TARGET_MAIN,)),
        ("Soren91だけでなく本編も", "main", (TARGET_MAIN, TARGET_SOREN91)),
        ("nextをふさがないで", "soren91", (TARGET_SOREN91,)),
        ("nextをふさがないで", "main", (TARGET_MAIN,)),
        ("本編ではなくSoren91", "main", (TARGET_SOREN91,)),
        ("Soren91ではなく本編", "soren91", (TARGET_MAIN,)),
    ],
)
def test_required_target_fixtures(body, intake, expected):
    assert route(body, intake_mode=intake).targets == expected


def test_correction_without_target_is_unresolved():
    result = route("その話じゃない", intake_mode=TARGET_MAIN)
    assert result.kind == KIND_UNRESOLVED
    assert result.targets == ()


def test_quote_is_never_adopted_as_a_command():
    result = route("「右に置け」と言われたが反対", intake_mode=TARGET_MAIN)
    assert result.kind == KIND_UNRESOLVED
    assert result.targets == ()


# --- §7.0: content vocabulary never selects a target -----------------------


@pytest.mark.parametrize("term", NON_TARGET_TERMS)
def test_strategy_vocabulary_does_not_move_the_target(term):
    result = route(f"{term}のところを見て", intake_mode=TARGET_MAIN)
    assert result.targets == (TARGET_MAIN,)
    assert result.reason == "intake_mode"


@pytest.mark.parametrize("term", ["おじゃま", "盤面タイプ", "試合"])
def test_legacy_soren91_content_words_do_not_force_soren91(term):
    assert route(f"{term}を避けたい", intake_mode=TARGET_MAIN).targets == (TARGET_MAIN,)


def test_next_alone_never_reaches_soren91_without_intake_mode():
    result = route("nextをふさがないで", intake_mode=None)
    assert result.kind == KIND_UNRESOLVED
    assert result.targets == ()


# --- §7.1 priority order ---------------------------------------------------


def test_prefix_wins_over_semantic_target():
    result = route("[main] 右に置いて", explicit_target=TARGET_SOREN91)
    assert result.targets == (TARGET_MAIN,)
    assert result.reason == "explicit_prefix_over_semantic"


def test_prefix_is_read_before_being_stripped():
    result = route("[soren91] 置き方を変えて")
    assert result.targets == (TARGET_SOREN91,)
    assert strip_mode_prefix("[soren91] 置き方を変えて") == "置き方を変えて"


@pytest.mark.parametrize("body", ["[main][soren91] どっちを見る", "[soren91][soren] 両方"])
def test_conflicting_prefixes_are_unresolved(body):
    result = route(body)
    assert result.kind == KIND_UNRESOLVED
    assert result.reason == "conflicting_prefix"


def test_soren_is_a_main_alias_like_the_legacy_detector():
    assert route("[soren] 置き方を見て").targets == (TARGET_MAIN,)


def test_semantic_target_is_used_when_text_has_no_target():
    result = route("右に置くべきだと思う", explicit_target=TARGET_SOREN91, intake_mode=TARGET_MAIN)
    assert result.targets == (TARGET_SOREN91,)
    assert result.reason == "semantic_target"


def test_semantic_both_needs_both_targets_explicitly():
    result = route("次の一手を意識して", explicit_target="both")
    assert result.targets == (TARGET_MAIN, TARGET_SOREN91)
    assert result.reason == "semantic_both"


def test_intake_mode_is_used_only_for_unspecified_targets():
    result = route("積み方を変えて", intake_mode=TARGET_SOREN91)
    assert result.targets == (TARGET_SOREN91,)
    assert result.reason == "intake_mode"


def test_missing_intake_mode_is_unresolved_not_main():
    result = route("積み方を変えて")
    assert result.kind == KIND_UNRESOLVED
    assert result.reason == "missing_intake_mode"


@pytest.mark.parametrize("label", ["other", "ambiguous", "nonsense"])
def test_other_and_unknown_semantic_targets_stay_unresolved(label):
    result = route("積み方を変えて", explicit_target=label, intake_mode=TARGET_MAIN)
    assert result.kind == KIND_UNRESOLVED
    assert result.targets == ()


def test_unspecified_semantic_target_falls_back_to_intake_mode():
    result = route("積み方を変えて", explicit_target="unspecified", intake_mode=TARGET_MAIN)
    assert result.targets == (TARGET_MAIN,)


# --- explicit text edge cases ---------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "本編もSoren91も見て",
        "本編とSoren91の両方で反映して",
        "Soren91だけでなく本編も",
    ],
)
def test_both_is_recognised(body):
    assert route(body, intake_mode=TARGET_MAIN).targets == (TARGET_MAIN, TARGET_SOREN91)


def test_a_both_marker_without_both_targets_is_not_a_both_projection():
    # §7.1: "両方" alone (no explicit target) must not invent a both-projection.
    assert route("両方の盤面を見てください", intake_mode=TARGET_MAIN).targets == (TARGET_MAIN,)


def test_two_groups_without_a_joint_marker_are_ambiguous():
    result = route("本編の話とSoren91の話はどちらも違う")
    assert result.kind == KIND_UNRESOLVED


def test_negated_mention_alone_does_not_resolve():
    result = route("本編ではない方針で", intake_mode=TARGET_MAIN)
    assert result.kind == KIND_UNRESOLVED


def test_mention_inside_quotes_is_ignored():
    result = route("「本編でやれ」と言われた", intake_mode=TARGET_SOREN91)
    assert result.kind == KIND_UNRESOLVED


@pytest.mark.parametrize(
    "body",
    [
        "「本編ではなくSoren91」と言われたが反対",
        "「本編もSoren91も」と言われた",
        "「本編でやれ」",
    ],
)
def test_review_1924_quoted_targets_are_unresolved(body):
    result = route(body, intake_mode=TARGET_SOREN91)
    assert result == AdviceRoute(KIND_UNRESOLVED, (), "ambiguous_target")
    assert plan_advice_writes(result, body=body, proposal_id="p-quoted") == ()


@pytest.mark.parametrize("opening,closing", [("「", "」"), ("『", "』")])
@pytest.mark.parametrize(
    "quoted",
    ["本編ではなくSoren91", "本編もSoren91も", "本編とSoren91の両方", "本編でやれ", "[soren91] 右に置いて"],
)
def test_all_text_target_rules_ignore_quoted_spans(opening, closing, quoted):
    result = route(f"{opening}{quoted}{closing}", intake_mode=TARGET_MAIN)
    assert result == AdviceRoute(KIND_UNRESOLVED, (), "ambiguous_target")


@pytest.mark.parametrize("semantic", [TARGET_MAIN, TARGET_SOREN91, "both"])
def test_quoted_only_target_cannot_be_rescued_by_semantic_label(semantic):
    result = route("「本編でやれ」", explicit_target=semantic, intake_mode=TARGET_SOREN91)
    assert result == AdviceRoute(KIND_UNRESOLVED, (), "ambiguous_target")


@pytest.mark.parametrize("prefix,target", [("[main]", TARGET_MAIN), ("[soren91]", TARGET_SOREN91)])
@pytest.mark.parametrize("quoted", ["本編ではなくSoren91", "本編もSoren91も", "本編でやれ"])
def test_unquoted_prefix_keeps_priority_over_quoted_targets(prefix, target, quoted):
    result = route(f"{prefix} 「{quoted}」", explicit_target="both")
    assert result == AdviceRoute(KIND_STRATEGY, (target,), "explicit_prefix_over_semantic")


@pytest.mark.parametrize(
    "body,expected,reason",
    [
        ("「本編ではなくSoren91」本編でやれ", (TARGET_MAIN,), "explicit_text"),
        ("『Soren91も本編も』Soren91でやれ", (TARGET_SOREN91,), "explicit_text"),
        ("「本編には反対」Soren91でやれ", (TARGET_SOREN91,), "explicit_text"),
        ("「本編とSoren91の両方」本編でやれ", (TARGET_MAIN,), "explicit_text"),
        ("「本編」本編もSoren91も見て", (TARGET_MAIN, TARGET_SOREN91), "explicit_both"),
        ("「Soren91」本編ではなくSoren91", (TARGET_SOREN91,), "explicit_text"),
        ("本編ではなく「引用」Soren91", (TARGET_SOREN91,), "explicit_text"),
        ("本編も「引用」Soren91も", (), "ambiguous_target"),
        ("本編と「Soren91」の両方", (TARGET_MAIN,), "explicit_text"),
        ("「本編とSoren91」両方", (), "ambiguous_target"),
    ],
)
def test_only_unquoted_text_can_resolve_a_target(body, expected, reason):
    result = route(body, intake_mode=TARGET_SOREN91)
    kind = KIND_STRATEGY if expected else KIND_UNRESOLVED
    assert result == AdviceRoute(kind, expected, reason)


def test_quote_without_any_target_keeps_unspecified_intake_fallback():
    assert route("「右に置け」", intake_mode=TARGET_MAIN) == AdviceRoute(
        KIND_STRATEGY, (TARGET_MAIN,), "intake_mode"
    )


def test_case_and_width_folding_matches_the_prefix():
    assert route("[MAIN]　右に置いて", intake_mode=TARGET_SOREN91).targets == (TARGET_MAIN,)


# --- kinds -----------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,expected",
    [
        (KIND_NONE, "no_advice"),
        (KIND_COMMENT_STYLE, "no_target_route"),
        (KIND_STREAM_SYSTEM, "no_target_route"),
        (KIND_UNRESOLVED, "unresolved_kind"),
        (None, "unresolved_kind"),
        ("mixed_or_unknown", "unresolved_kind"),
    ],
)
def test_non_strategy_kinds_are_never_target_routed(kind, expected):
    result = resolve_advice_route("[main] 置き方を変えて", kind=kind, intake_mode=TARGET_MAIN)
    assert result.targets == ()
    assert result.reason == expected
    assert result.kind in ADVICE_KINDS


def test_kind_mapping_helpers():
    assert kind_from_legacy("strategy") == KIND_STRATEGY
    assert kind_from_legacy("comment") == KIND_COMMENT_STYLE
    assert kind_from_legacy("codex") == KIND_STREAM_SYSTEM
    assert kind_from_legacy("") == KIND_NONE
    assert kind_from_legacy("unexpected") == KIND_UNRESOLVED
    assert kind_from_semantic("comment_style") == KIND_COMMENT_STYLE
    assert kind_from_semantic("mixed_or_unknown") == KIND_UNRESOLVED
    assert kind_from_semantic(None) == KIND_UNRESOLVED


def test_source_kind_is_preserved():
    result = resolve_advice_route(
        "[main] 置き方を変えて", kind=KIND_STRATEGY, source_kind="comment_intake"
    )
    assert result.source_kind == "comment_intake"


# --- §4.5 exactly-once write projection ------------------------------------


def test_plan_writes_projects_one_idempotency_key_per_target():
    route_both = route("Soren91だけでなく本編も", intake_mode=TARGET_MAIN)
    writes = plan_advice_writes(route_both, body="[main] Soren91だけでなく本編も\n 見て", proposal_id="p-1")
    assert [w.target for w in writes] == [TARGET_MAIN, TARGET_SOREN91]
    assert {w.idempotency_key for w in writes} == {"p-1:main", "p-1:soren91"}
    assert all(w.proposal_id == "p-1" for w in writes)
    assert all(w.body == "Soren91だけでなく本編も 見て" for w in writes)


def test_plan_writes_is_stable_across_retries_and_drops_empty_rows():
    route_main = route("[main] 置き方を変えて")
    first = plan_advice_writes(route_main, body="[main] 置き方を変えて", proposal_id="p-2")
    second = plan_advice_writes(route_main, body="[main] 置き方を変えて", proposal_id="p-2")
    assert first == second
    assert plan_advice_writes(route_main, body="[main]   ", proposal_id="p-2") == ()
    assert plan_advice_writes(
        AdviceRoute(KIND_COMMENT_STYLE, (), "no_target_route"), body="x", proposal_id="p-2"
    ) == ()


def test_plan_writes_requires_a_proposal_id():
    with pytest.raises(AdviceRoutingError):
        plan_advice_writes(route("[main] x"), body="[main] x", proposal_id="  ")


def test_normalize_advice_item_collapses_whitespace():
    assert normalize_advice_item("  置き方を\n 変えて  ") == "置き方を 変えて"


def test_no_free_text_leaks_into_the_reason():
    for body in ("[main] x", "その話じゃない", "積み方を変えて", "[main][soren91] x"):
        result = route(body, intake_mode=TARGET_MAIN)
        assert result.reason.isascii()
        assert " " not in result.reason
