"""Group-aware deterministic train/validation/sealed-test split (#1308 PR-1).

Members of one thread/batch must never straddle a split, otherwise a candidate
is scored on a near-duplicate of a case it was allowed to analyse. Splitting is
therefore decided per ``group_id`` (never per case), seeded and reproducible,
and the manifest hash pins the exact assignment a campaign ran against.

``sealed_test`` is only handed to a runner through an explicit opt-in (see
:mod:`docich.eval.runner`); the splitter itself keeps the assignment so the
sealed bodies stay addressable for the campaign-final step and nothing else.
"""
from __future__ import annotations

import hashlib

from . import contracts
from .contracts import (DEFAULT_RATIOS, DEFAULT_SEED, SPLIT_SEALED, SPLIT_TRAIN,
                        SPLIT_VALIDATION, SPLITS, ContractError)


def bucket_for_group(group_id: str, *, seed: int = DEFAULT_SEED, ratios=None) -> str:
    """Deterministic split for a group; same inputs always give the same split."""
    ratios = DEFAULT_RATIOS if ratios is None else ratios
    if set(ratios) != set(SPLITS):
        raise ContractError("invalid_ratios")
    total = sum(ratios.values())
    if abs(total - 1.0) > 1e-9:
        raise ContractError("ratios_must_sum_to_one")
    raw = hashlib.sha256(f"{seed}:{group_id}".encode("utf-8")).digest()
    point = int.from_bytes(raw[:8], "big") / float(1 << 64)
    cumulative = 0.0
    for split in SPLITS:
        cumulative += ratios[split] / total
        if point < cumulative:
            return split
    return SPLITS[-1]


def split_manifest(cases, assignments) -> dict:
    """Counts + hash of the exact (case, group, split) assignment."""
    by_split = {split: 0 for split in SPLITS}
    groups = {split: set() for split in SPLITS}
    for case in cases:
        split = assignments[case["case_id"]]
        by_split[split] += 1
        groups[split].add(case["group_id"])
    pairs = sorted((case["case_id"], case["group_id"], assignments[case["case_id"]])
                   for case in cases)
    return {
        "split_manifest_hash": contracts.digest(pairs),
        "seed": None,  # filled by the caller, which owns the seed
        "counts": by_split,
        "group_counts": {split: len(groups[split]) for split in SPLITS},
        "case_ids": {split: sorted(cid for cid, _g, s in pairs if s == split)
                     for split in SPLITS},
    }


def assert_group_integrity(cases, assignments) -> None:
    """No group may appear in two splits (issue #1308 section 1 contract)."""
    seen = {}
    for case in cases:
        if case["case_id"] not in assignments:
            continue  # critical fixtures live in their own always-on bucket
        split = assignments[case["case_id"]]
        previous = seen.setdefault(case["group_id"], split)
        if previous != split:
            raise ContractError("group_leak:" + case["group_id"])


def split_cases(cases, *, seed: int = DEFAULT_SEED, ratios=None,
                critical_ids=frozenset()) -> dict:
    """Assign every case a split; critical fixtures stay in their own bucket.

    Returns ``{"assignments", "cases", "by_split", "critical", "manifest"}``.
    ``critical`` cases are *not* copied into the three ratios: they are the
    always-on hard-gate fixtures from issue #1308 section 1.
    """
    ratios = dict(DEFAULT_RATIOS if ratios is None else ratios)
    assignments, critical, grouped = {}, [], {}
    for case in cases:
        if case["case_id"] in critical_ids:
            critical.append(case)
            continue
        assignments[case["case_id"]] = bucket_for_group(
            case["group_id"], seed=seed, ratios=ratios)
        grouped[case["case_id"]] = case
    assert_group_integrity(list(grouped.values()), assignments)
    manifest = split_manifest(list(grouped.values()), assignments)
    manifest["seed"] = seed
    manifest["ratios"] = {key: float(value) for key, value in ratios.items()}
    manifest["critical_count"] = len(critical)
    manifest["critical_ids"] = sorted(case["case_id"] for case in critical)
    by_split = {split: [] for split in SPLITS}
    for case in cases:
        if case["case_id"] in assignments:
            by_split[assignments[case["case_id"]]].append(case)
    return {
        "assignments": assignments,
        "by_split": by_split,
        "critical": critical,
        "manifest": manifest,
    }


def with_split(case: dict, split: str) -> dict:
    if split not in SPLITS:
        raise ContractError("invalid_split")
    return {**case, "split": split}


def manifest_for_suite(suite_manifest: dict, split_result: dict) -> dict:
    """Version-pin the suite manifest with the split result (no case bodies)."""
    return {
        "schema": contracts.SUITE_SCHEMA,
        "suite": suite_manifest["suite"],
        "rubric_version": suite_manifest["rubric_version"],
        "grader_versions": dict(suite_manifest["grader_versions"]),
        "corpus_digest": suite_manifest.get("corpus_digest"),
        "split": split_result["manifest"],
    }
