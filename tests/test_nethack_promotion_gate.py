from __future__ import annotations

from dataclasses import replace

from docich.nethack_promotion_gate import (
    REASON_DEGENERATE,
    REASON_FITNESS,
    REASON_INSUFFICIENT,
    REASON_ISOLATION,
    REASON_REGRESSION,
    REASON_SMOKE,
    REASON_TRACE,
    REASON_UNPAIRED,
    EpisodeOutcome,
    GateConfig,
    KnownGood,
    PromotionInputs,
    apply_decision,
    evaluate_promotion,
)


def outcome(seed, *, arm="candidate", depth=3, score=100, turns=500, terminal="dead", **kw):
    return EpisodeOutcome(
        seed=seed,
        arm=arm,
        status=kw.get("status", "passed"),
        terminal_status=terminal,
        turns=turns,
        max_depth=depth,
        score=score,
        exit_reason=kw.get("exit_reason", "terminal"),
        production_state_touched=kw.get("production_state_touched", False),
        production_fingerprint_unchanged=kw.get("production_fingerprint_unchanged", True),
        container_cleanup_ok=kw.get("container_cleanup_ok", True),
    )


def paired_inputs(*, candidate_kw=None, baseline_kw=None, **kwargs):
    baseline = tuple(outcome(seed, arm="baseline", **(baseline_kw or {})) for seed in (1, 2, 3))
    candidate = tuple(outcome(seed, arm="candidate", **(candidate_kw or {})) for seed in (1, 2, 3))
    return PromotionInputs(
        candidate_id="cand-v2",
        baseline_id="baseline-v1",
        baseline=baseline,
        candidate=candidate,
        **kwargs,
    )


def test_promotes_when_every_gate_passes():
    decision = evaluate_promotion(paired_inputs())
    assert decision.promote is True
    assert decision.status == "promote"
    assert decision.reasons == ()


def test_rejects_on_smoke_gate_failure():
    decision = evaluate_promotion(paired_inputs(smoke_ok=False))
    assert decision.promote is False
    assert REASON_SMOKE in decision.reasons


def test_rejects_candidate_isolation_violation():
    decision = evaluate_promotion(
        paired_inputs(candidate_kw={"production_state_touched": True})
    )
    assert REASON_ISOLATION in decision.reasons


def test_rejects_unverified_trace():
    decision = evaluate_promotion(paired_inputs(trace_unverified=1))
    assert REASON_TRACE in decision.reasons


def test_rejects_regression_failure():
    decision = evaluate_promotion(paired_inputs(regression_green=False))
    assert REASON_REGRESSION in decision.reasons


def test_rejects_when_episodes_are_insufficient():
    inputs = paired_inputs()
    small = replace(
        inputs,
        baseline=inputs.baseline[:2],
        candidate=inputs.candidate[:2],
    )
    decision = evaluate_promotion(small)
    assert decision.reasons == (REASON_INSUFFICIENT,)


def test_rejects_unpaired_seeds():
    inputs = paired_inputs()
    unpaired = replace(inputs, candidate=tuple(outcome(seed) for seed in (1, 2, 4)))
    decision = evaluate_promotion(unpaired)
    assert decision.reasons == (REASON_UNPAIRED,)


def test_rejects_fitness_regression():
    decision = evaluate_promotion(paired_inputs(candidate_kw={"depth": 2}))
    assert decision.reasons == (REASON_FITNESS,)


def test_tolerates_small_turn_drift_between_runs():
    # NetHack is not fully reproducible even with a fixed seed, so a candidate
    # within the turn tolerance is still non-regressed.
    inputs = paired_inputs(
        baseline_kw={"depth": 3, "score": 100, "turns": 100},
        candidate_kw={"depth": 3, "score": 100, "turns": 90},
    )
    assert evaluate_promotion(inputs).promote is True


def test_rejects_regression_beyond_turn_tolerance():
    inputs = paired_inputs(
        baseline_kw={"depth": 3, "score": 100, "turns": 100},
        candidate_kw={"depth": 3, "score": 100, "turns": 50},
    )
    decision = evaluate_promotion(inputs)
    assert decision.reasons == (REASON_FITNESS,)


def test_rejects_degenerate_turns_only_candidate():
    # No terminal and depth 0 on both sides: a turns-only "win" must not promote.
    inputs = paired_inputs(
        baseline_kw={"depth": 0, "score": None, "terminal": "timeout", "turns": 100},
        candidate_kw={"depth": 0, "score": None, "terminal": "timeout", "turns": 99999},
    )
    decision = evaluate_promotion(inputs)
    assert REASON_DEGENERATE in decision.reasons


def test_apply_decision_promotes_or_keeps_known_good():
    known = KnownGood(candidate_id="baseline-v1")
    promoted = evaluate_promotion(paired_inputs())
    assert apply_decision(known, promoted).candidate_id == "cand-v2"
    rejected = evaluate_promotion(paired_inputs(smoke_ok=False))
    assert apply_decision(known, rejected).candidate_id == "baseline-v1"


def test_gate_config_requires_seed_pairs_by_default():
    config = GateConfig()
    assert config.min_episodes_per_arm == 3
    assert config.required_non_regression == 1.0
