#!/usr/bin/env python3
"""Score the live-Jev baseline (#1263) with the SAME grader the bench uses.

Both views are graded by ``docich.eval.graders.classifier.evaluate`` -- the exact
call ``bench/jev_bench.py`` makes -- so the numbers are directly comparable with
the local-LLM column in ``bench/results/2026-10-09_rtx3060_jev``.

view "ungated"  one reviewed provider call per case, no production gates, no
                 retries. This is the apples-to-apples comparison with
                 llama3.1:8b (acc 0.629 / macro_f1 0.734 / live-log 0.587),
                 which the harness also scores single-pass.
view "pipeline" the production classifier's own output row per case, including
                 min_confidence 0.70, notification protection and the cooldown
                 gate. This is the honest live-accuracy number.

A response the core's strict validator rejects is a miss in both views and is
counted in ``parse_failures``/``coverage``, exactly as the harness counts an
unparseable model answer.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

REPO = Path("/tmp/jev1933")
sys.path.insert(0, str(REPO / "src"))

from docich.comment_classifier import jev  # noqa: E402
from docich.eval.graders import classifier as grader  # noqa: E402

BASE = Path("/Users/azumag/.hermes/kanban/workspaces/t_7d465767/jev_baseline")
NOTIFICATION = frozenset(jev.NOTIFICATIONS)
LABELS = sorted(jev.CRITERIA)


def load_cases() -> dict:
    out = {}
    with (BASE / "public_cases.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                case = json.loads(line)
                out[case["case_id"]] = case
    return out


def load_ungated() -> dict:
    out = {}
    with (BASE / "ungated_results.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                record = json.loads(line)
                out[record["case_id"]] = record
    return out


def load_pipeline() -> dict:
    """The last pipeline telemetry event per case, in batch order."""
    metrics = BASE / "jev-baseline-metrics/metrics-2026-10-09.jsonl"
    lines = [l for l in metrics.read_text(encoding="utf-8").splitlines() if l.strip()]
    ids = sorted(f.name[:-4] for f in
                 (BASE / "batches").iterdir() if f.name.startswith("jev-"))
    assert len(lines) == len(ids), (len(lines), len(ids))
    return {case_id: json.loads(line) for case_id, line in zip(ids, lines)}


def grade(cases, ids, predictions):
    cases_for_grader = [
        {"case_id": cid,
         "expected": {"category": cases[cid]["expected"]["category"],
                      "intent_family": cases[cid]["expected"]["intent_family"],
                      "screen_need": cases[cid]["expected"].get("screen_need")}}
        for cid in ids]
    outputs = {cid: {"category": pred, "screen_need": None}
               for cid, pred in zip(ids, predictions)}
    return grader.evaluate(cases_for_grader, outputs)["category"]


def micro_prf(gold, pred, labels):
    tp = sum(1 for g, p in zip(gold, pred) if g in labels and p in labels)
    fp = sum(1 for g, p in zip(gold, pred) if g not in labels and p in labels)
    fn = sum(1 for g, p in zip(gold, pred) if g in labels and p not in labels)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn}


def family_prf(gold, pred, labels):
    out = {}
    for label in labels:
        tp = sum(1 for g, p in zip(gold, pred) if g == label and p == label)
        fp = sum(1 for g, p in zip(gold, pred) if g != label and p == label)
        fn = sum(1 for g, p in zip(gold, pred) if g == label and p != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out[label] = {"precision": round(precision, 4), "recall": round(recall, 4),
                      "f1": round(f1, 4), "support": tp + fn, "tp": tp, "fp": fp, "fn": fn}
    return out


def view(gold, pred):
    scored = grade(cases, ids, pred)
    return {
        "n": len(ids),
        "accuracy": round(scored["accuracy_on_available"], 4),
        "coverage": round(scored["coverage"], 4),
        "macro_f1": round(scored["macro_f1_with_abstentions_as_misses"], 4),
        "correct": int(round(scored["correct_fraction_all"] * len(ids))),
        "parse_failures": sum(1 for p in pred if p is None),
        "per_label": family_prf(gold, pred, LABELS),
        "notification_family": micro_prf(gold, pred, NOTIFICATION),
    }


cases = load_cases()
ids = sorted(cases)
ungated = load_ungated()
pipeline = load_pipeline()

gold = [cases[cid]["expected"]["category"] for cid in ids]
pred_ungated = [ungated[cid].get("category") for cid in ids]
pred_pipeline = [pipeline[cid]["rows"][0]["selected"] for cid in ids]
pred_baseline = [pipeline[cid]["rows"][0]["baseline"] for cid in ids]

live_log_ids = [cid for cid in ids if "live-log" in (cases[cid].get("tags") or [])]
live_gold = [cases[cid]["expected"]["category"] for cid in live_log_ids]


def subset(view_dict, name, pred_all):
    sub = grade(cases, live_log_ids,
                [pred_all[ids.index(cid)] for cid in live_log_ids])
    view_dict[f"accuracy_{name}"] = round(sub["accuracy_on_available"], 4)
    view_dict[f"macro_f1_{name}"] = round(sub["macro_f1_with_abstentions_as_misses"], 4)


v_ungated = view(gold, pred_ungated)
subset(v_ungated, "live_log", pred_ungated)
v_pipeline = view(gold, pred_pipeline)
subset(v_pipeline, "live_log", pred_pipeline)
v_baseline = view(gold, pred_baseline)
subset(v_baseline, "live_log", pred_baseline)

from collections import Counter  # noqa: E402
status = {
    "ungated": dict(Counter(ungated[cid]["status"] for cid in ids)),
    "ungated_invalid_detail": dict(Counter(
        ungated[cid].get("detail", "") for cid in ids if ungated[cid]["status"] != "ok")),
    "pipeline_event": dict(Counter(pipeline[cid]["status"] for cid in ids)),
    "pipeline_row": dict(Counter(pipeline[cid]["rows"][0]["status"] for cid in ids)),
    "pipeline_route": dict(Counter(pipeline[cid]["route"] for cid in ids)),
}

latency_ungated = sorted(ungated[cid].get("latency_ms") or 0 for cid in ids)
jev_ms = sorted(pipeline[cid]["jev_ms"] for cid in ids
                if pipeline[cid].get("jev_ms") is not None)
latency = {
    "ungated_total_ms": {"n": len(latency_ungated),
                         "p50": latency_ungated[len(latency_ungated) // 2],
                         "p95": latency_ungated[int(len(latency_ungated) * 0.95)],
                         "min": latency_ungated[0], "max": latency_ungated[-1]},
    "pipeline_jev_ms_attempted": {"n": len(jev_ms),
                                  "p50": jev_ms[len(jev_ms) // 2] if jev_ms else None,
                                  "p95": jev_ms[int(len(jev_ms) * 0.95)] if jev_ms else None},
    "pipeline_attempted_share": round(
        sum(1 for cid in ids if pipeline[cid].get("attempted")) / len(ids), 4),
}

reference = {
    "model": "llama3.1:8b-instruct-q4_K_M",
    "source": "bench/results/2026-10-09_rtx3060_jev/llama3.1-8b-instruct-q4_K_M.report.json",
    "accuracy": 0.6286, "macro_f1": 0.7339, "accuracy_live_log": 0.5867,
    "coverage": 1.0, "parse_failures": 0,
    "notification_precision": 0.882, "notification_recall": 0.882,
    "notification_fp_per_100": 0.6,
}

delta = {
    "accuracy_ungated_minus_llama": round(v_ungated["accuracy"] - reference["accuracy"], 4),
    "macro_f1_ungated_minus_llama": round(v_ungated["macro_f1"] - reference["macro_f1"], 4),
    "accuracy_live_log_ungated_minus_llama": round(
        v_ungated["accuracy_live_log"] - reference["accuracy_live_log"], 4),
    "accuracy_pipeline_minus_llama": round(v_pipeline["accuracy"] - reference["accuracy"], 4),
    "notification_precision_ungated_minus_llama": round(
        v_ungated["notification_family"]["precision"] - reference["notification_precision"], 4),
    "notification_recall_ungated_minus_llama": round(
        v_ungated["notification_family"]["recall"] - reference["notification_recall"], 4),
}

result = {
    "task": "docich#1263 live-Jev baseline on the public eval suite",
    "suite_digest": "sha256:d750a4361f7b7acffd46a192b1f7f5e873e8a2692cf45bbae78ce97240a46c37",
    "grader": "docich.eval.graders.classifier (same call as bench/jev_bench.py)",
    "cases": len(ids),
    "live_log_cases": len(live_log_ids),
    "views": {"ungated_single_pass": v_ungated,
              "production_pipeline": v_pipeline,
              "heuristic_baseline_only": v_baseline},
    "status_counts": status,
    "latency": latency,
    "reference_local_llm": reference,
    "delta_vs_reference": delta,
}

out = BASE / "report.json"
out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
