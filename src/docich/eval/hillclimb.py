"""Offline hillclimb campaign: allowlisted mutations, one per round (#1308 §5).

The loop is deliberately narrow. A mutation may only target an allowlisted
prompt/rubric asset, a reviewed model/effort enum, or an experiment-only
threshold. The candidate generator sees **train cases only**; validation is
reduced to aggregate metrics before it is handed back, and ``sealed_test`` is
never evaluated until the campaign-final step. Nothing here pushes to main or
writes production state.

``generate`` and ``evaluate`` are injected so the loop stays provider-free and
testable; the default is a no-op dry run that records nothing.
"""
from __future__ import annotations

import fnmatch
from datetime import datetime, timezone

from . import campaign as campaign_mod
from . import contracts, leakage, report as report_mod
from .contracts import SPLIT_SEALED, SPLIT_TRAIN, SPLIT_VALIDATION, ContractError

# The issue #1308 section 5 allowlist. Arbitrary file/path targets are refused.
MUTATION_TARGETS = {
    "comment-prompt": {"kind": "asset_glob",
                       "glob": "src/docich/comment/prompts/*.md"},
    "comment-reply-contract": {"kind": "asset_glob",
                               "glob": "src/docich/comment/prompts/comment_reply_contract.md"},
    "jev-rubric-text": {"kind": "rubric_text"},
    "model-profile": {"kind": "enum", "allowed": ("high", "medium", "low")},
    "screen-threshold": {"kind": "threshold", "min": 0.0, "max": 1.0},
}
DEFAULT_PRIMARY_METRIC = "quality"


def assert_target(target: str) -> dict:
    if target not in MUTATION_TARGETS:
        raise ContractError("target_not_allowlisted:" + str(target))
    return MUTATION_TARGETS[target]


def validate_mutation(target: str, mutation: dict) -> dict:
    """Refuse a mutation that is not cleanly inside the target's allowlist."""
    spec = assert_target(target)
    if not isinstance(mutation, dict):
        raise ContractError("invalid_mutation")
    if spec["kind"] == "asset_glob":
        path = mutation.get("path")
        if not isinstance(path, str) or path.startswith("/") or ".." in path.split("/") \
                or not fnmatch.fnmatch(path, spec["glob"]):
            raise ContractError("path_not_allowlisted")
        if not isinstance(mutation.get("text"), str) or not mutation["text"].strip():
            raise ContractError("empty_mutation")
    elif spec["kind"] == "enum":
        if mutation.get("value") not in spec["allowed"]:
            raise ContractError("value_not_allowlisted")
    elif spec["kind"] == "threshold":
        value = mutation.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool) \
                or not spec["min"] <= value <= spec["max"]:
            raise ContractError("threshold_out_of_range")
    else:  # rubric_text
        if not isinstance(mutation.get("text"), str) or not mutation["text"].strip():
            raise ContractError("empty_mutation")
    return {"target": target, "kind": spec["kind"], **mutation}


def _mutation_text(mutation: dict) -> str:
    return mutation.get("text") or str(mutation.get("value", ""))


def run_campaign(*, campaign_id: str, target: str, cases_by_split, split_manifest_hash: str,
                 generate, evaluate, rounds: int = 8,
                 primary_metric: str = DEFAULT_PRIMARY_METRIC, margin: float = 0.0,
                 experiment_path=None, suite: str = "comment-v1", suite_version: str = "v1",
                 rubric_version: str = "comment-reply-rubric-v1", grader_versions=None,
                 model=None, profile=None, seed: int = contracts.DEFAULT_SEED,
                 dry_run: bool = True, sealed_evaluate=None) -> dict:
    """Run up to ``rounds`` of one-hypothesis/one-mutation hillclimb.

    Returns ``{"campaign_id", "rounds", "report", "sealed", "dry_run"}``. With
    ``dry_run`` (the default) a no-op generator is used and nothing is mutated.
    """
    assert_target(target)
    train = list(cases_by_split.get(SPLIT_TRAIN, []))
    validation = list(cases_by_split.get(SPLIT_VALIDATION, []))
    if any(case.get("split") == SPLIT_SEALED for case in train + validation):
        raise ContractError("sealed_case_in_loop")
    if dry_run:
        generate = lambda round_index, train_cases: None  # noqa: E731 - explicit no-op

    baseline_validation = evaluate(validation)
    records, sealed_report = [], None
    for round_index in range(1, max(0, rounds) + 1):
        proposal = generate(round_index, train) or {}
        hypothesis = proposal.get("hypothesis")
        mutation = proposal.get("mutation")
        if not hypothesis or not isinstance(mutation, dict):
            break
        mutation = validate_mutation(target, mutation)
        leak = leakage.check(_mutation_text(mutation), train)
        if not leak["ok"]:
            record = _record(campaign_id, round_index, target, hypothesis, mutation,
                             suite, suite_version, rubric_version, grader_versions,
                             split_manifest_hash, model, profile,
                             {"leakage": leak}, {"leakage": leak}, leak,
                             {"decision": "revert", "reason": "leakage_detected"})
            records.append(record)
            _persist(experiment_path, record)
            break
        candidate_validation = evaluate(validation)
        comparison = report_mod._comparison(baseline_validation, candidate_validation,
                                            seed=seed)
        primary = comparison["metrics"].get(primary_metric, {"n": 0, "mean": None,
                                                             "lo": None, "hi": None})
        verdict = campaign_mod.decide(hard_fail_regressions=comparison["hard_fail_regressions"],
                                      primary=primary, margin=margin)
        train_aggregate = _train_metrics(evaluate(train))
        record = _record(campaign_id, round_index, target, hypothesis, mutation,
                         suite, suite_version, rubric_version, grader_versions,
                         split_manifest_hash, model, profile, train_aggregate,
                         _aggregate(candidate_validation), leak, verdict,
                         primary=primary)
        records.append(record)
        _persist(experiment_path, record)
        # A kept candidate becomes the new baseline for the next round; a
        # reverted one does not (the prompt stays as it was).
        if verdict["decision"] == "keep":
            baseline_validation = candidate_validation
    if sealed_evaluate is not None:
        sealed_report = sealed_evaluate(cases_by_split.get(SPLIT_SEALED, []))
    return {"campaign_id": campaign_id, "rounds": records, "dry_run": dry_run,
            "report": campaign_mod.campaign_report(records), "sealed": sealed_report}


def _train_metrics(run) -> dict:
    return {"hard_failures": run.deterministic_failures,
            "case_count": len(run.results)}


def _aggregate(run) -> dict:
    """Validation is reduced to aggregates: no case bodies, no transcripts."""
    aggregate = {"hard_failures": run.deterministic_failures, "case_count": len(run.results),
                 "latency": run.latency, "cost": run.cost, "usage": run.usage}
    if run.classifier:
        aggregate["classifier"] = {
            "macro_f1": run.classifier["category"]["macro_f1_with_abstentions_as_misses"],
            "screen_required_recall": run.classifier["screen_need"]["required_recall"],
            "game_intent_recall": run.classifier["game_intent"]["recall"],
        }
    return aggregate


def _record(campaign_id, round_index, target, hypothesis, mutation, suite, suite_version,
            rubric_version, grader_versions, split_manifest_hash, model, profile,
            train_metrics, validation_metrics, leak, verdict, primary=None) -> dict:
    return campaign_mod.make_record(
        campaign_id=campaign_id, round=round_index,
        base_revision=None, candidate_revision=mutation.get("diff_hash"),
        suite=suite, suite_version=suite_version, rubric_version=rubric_version,
        grader_versions=dict(grader_versions or contracts.GRADER_VERSIONS),
        split_manifest_hash=split_manifest_hash, mutation_target=target,
        hypothesis=hypothesis, diff_hash=mutation.get("diff_hash"),
        model=model, profile=profile, train=train_metrics, validation=validation_metrics,
        hard_failures=validation_metrics.get("hard_failures"),
        cost=validation_metrics.get("cost"), latency=validation_metrics.get("latency"),
        leakage=leak, decision=verdict["decision"],
        decision_reason=verdict["reason"], primary=primary,
        recorded_at=datetime.now(timezone.utc).isoformat())


def _persist(path, record) -> None:
    if path is not None:
        campaign_mod.append_experiment(path, record)
