"""Campaign records and keep/revert decisions for the offline loop (#1308 §6/§7).

A round is one hypothesis and one mutation. A decision reads the hard gate
first, then a paired primary-quality comparison whose margin is fixed *before*
results are seen, and only then cost/latency as a secondary objective. Every
round is appended to a JSONL experiment log and case bodies are never copied
into it, so a human can reconstruct "why this prompt exists" from patterns
rather than from individual examples.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .contracts import ContractError

DECISIONS = ("keep", "revert", "inconclusive")
RECORD_FIELDS = (
    "campaign_id", "round", "base_revision", "candidate_revision", "suite", "suite_version",
    "rubric_version", "grader_versions", "split_manifest_hash", "mutation_target",
    "hypothesis", "diff_hash", "model", "profile", "train", "validation",
    "hard_failures", "cost", "latency", "leakage", "decision", "decision_reason",
)
REQUIRED_FIELDS = ("campaign_id", "round", "suite", "suite_version", "rubric_version",
                   "split_manifest_hash", "mutation_target", "hypothesis", "diff_hash",
                   "decision", "decision_reason")


def decide(*, hard_fail_regressions=(), critical_regressions=(),
           primary: dict | None = None, margin: float = 0.0,
           cost_regression: bool = False, latency_regression: bool = False) -> dict:
    """Keep / revert / inconclusive for one round (issue #1308 section 6)."""
    if hard_fail_regressions or critical_regressions:
        return {"decision": "revert", "reason": "hard_gate",
                "detail": {"hard_fail_regressions": list(hard_fail_regressions),
                           "critical_regressions": list(critical_regressions)}}
    primary = primary or {"n": 0, "mean": None, "lo": None, "hi": None}
    if not primary.get("n"):
        return {"decision": "inconclusive", "reason": "no_paired_data", "detail": primary}
    mean, lo, hi = primary.get("mean"), primary.get("lo"), primary.get("hi")
    if mean is None or lo is None or hi is None:
        return {"decision": "inconclusive", "reason": "incomplete_interval", "detail": primary}
    if mean < 0:
        return {"decision": "revert", "reason": "primary_regression", "detail": primary}
    if mean < margin:
        return {"decision": "inconclusive", "reason": "below_margin", "detail": primary}
    if lo <= 0:
        return {"decision": "inconclusive", "reason": "interval_crosses_zero", "detail": primary}
    if cost_regression or latency_regression:
        return {"decision": "revert", "reason": "secondary_objective_regression",
                "detail": {**primary, "cost_regression": cost_regression,
                           "latency_regression": latency_regression}}
    return {"decision": "keep", "reason": "primary_improvement", "detail": primary}


def make_record(**fields) -> dict:
    """Build a validated experiment record; missing required fields raise."""
    missing = [name for name in REQUIRED_FIELDS if fields.get(name) in (None, "")]
    if missing:
        raise ContractError("missing_record_fields:" + ",".join(sorted(missing)))
    if fields["decision"] not in DECISIONS:
        raise ContractError("invalid_decision")
    record = {name: fields.get(name) for name in RECORD_FIELDS}
    record["recorded_at"] = fields.get("recorded_at") or datetime.now(timezone.utc).isoformat()
    return record


def append_experiment(path, record: dict) -> None:
    """Append one round to the JSONL experiment log (never rewrites history)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_experiments(path) -> list:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def campaign_report(records) -> dict:
    """Aggregate a campaign's rounds: decisions, reasons, kept mutations."""
    records = list(records)
    decisions = Counter(record.get("decision") for record in records)
    kept = [{"round": record.get("round"), "mutation_target": record.get("mutation_target"),
             "hypothesis": record.get("hypothesis"), "diff_hash": record.get("diff_hash"),
             "reason": record.get("decision_reason")}
            for record in records if record.get("decision") == "keep"]
    return {
        "rounds": len(records),
        "decisions": {decision: decisions.get(decision, 0) for decision in DECISIONS},
        "reasons": dict(Counter(record.get("decision_reason") for record in records)),
        "kept": kept,
        "campaign_id": records[0].get("campaign_id") if records else None,
        "mutation_targets": sorted({record.get("mutation_target") for record in records
                                    if record.get("mutation_target")}),
    }
