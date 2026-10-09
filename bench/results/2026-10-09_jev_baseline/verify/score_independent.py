#!/usr/bin/env python3
"""Independent re-scoring of PR #1974's artifacts (current-Jev baseline) and the
llama3.1:8b raw from #1970, using a scorer written from scratch that mirrors
src/docich/eval/metrics.py::score_predictions semantics.

Why this exists: PR #1974 reports numbers computed on the VM. This script
re-derives every headline figure on the workstation from the committed
artifacts only, so the GO/NOGO decision rests on numbers that reproduce.

Run:  python3 score_independent.py
Inputs (all committed, read-only):
  ../jev_eval_v1/public_cases.jsonl
  ungated_results.jsonl          (current Jev, 1 call/case, no gate)
  pipeline_metrics.jsonl         (current Jev, production bin/docich-comment-classify)
  ../2026-10-09_rtx3060_jev/raw/llama3.1-8b-instruct-q4_K_M.raw.jsonl
Outputs: independent_summary.json (this dir)
"""
from __future__ import annotations

import collections
import json
import pathlib
import statistics

HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE.parent                      # .../bench/results/2026-10-09_jev_baseline
BENCH = RESULTS.parent.parent             # .../bench
CASES = BENCH / "jev_eval_v1" / "public_cases.jsonl"
if not CASES.exists():  # bench/ may be a git submodule checked out separately
    CASES = BENCH.parent / "bench" / "jev_eval_v1" / "public_cases.jsonl"
CRITICAL = CASES.parent / "critical_cases.jsonl"
UNGATED = RESULTS / "ungated_results.jsonl"
PIPELINE = RESULTS / "pipeline_metrics.jsonl"
LLAMA_RAW = (BENCH / "results" / "2026-10-09_rtx3060_jev" / "raw"
             / "llama3.1-8b-instruct-q4_K_M.raw.jsonl")
if not LLAMA_RAW.exists():
    LLAMA_RAW = (RESULTS.parent / "2026-10-09_rtx3060_jev" / "raw"
                 / "llama3.1-8b-instruct-q4_K_M.raw.jsonl")

NOTIFICATIONS = frozenset({"card_gacha", "raid", "subscription", "stream_goal", "bits"})

# The llama bench marked the first three cases as warmup, so they were never
# scored. Held out explicitly rather than silently, so a matched comparison is
# possible and the omission is auditable.
LLAMA_WARMUP_EXCLUDED = frozenset({"jev-0001", "jev-0002", "jev-0003"})

# jev_eval_v1/critical_cases.jsonl: the harness loads these alongside the public
# cases, but they are NOT in public_cases.jsonl, so the Jev baseline never
# scored them either.
CRITICAL_IDS = frozenset({"jev-0093", "jev-0094"})


def jsonl(path: pathlib.Path) -> list:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def score(pred: dict, subset: list, gold: dict, tags: dict) -> dict:
    """Mirror of metrics.score_predictions: a None prediction stays a miss."""
    per_label = {}
    labels = sorted({gold[i] for i in subset} | {p for p in pred.values() if p})
    for label in labels:
        tp = sum(gold[i] == pred.get(i) == label for i in subset)
        fp = sum(gold[i] != label and pred.get(i) == label for i in subset)
        fn = sum(gold[i] == label and pred.get(i) != label for i in subset)
        per_label[label] = {
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else None,
            "tp": tp, "fp": fp, "fn": fn,
            "support": sum(gold[i] == label for i in subset),
        }
    available = [i for i in subset if pred.get(i)]
    n = len(subset)
    live = [i for i in subset if "live-log" in tags[i]]
    # Binary notification decision (the label the mis-fire cost rides on):
    # FP = gold is non-notification but predicted as one.
    ntp = sum(gold[i] in NOTIFICATIONS and pred.get(i) in NOTIFICATIONS for i in subset)
    nfp = sum(gold[i] not in NOTIFICATIONS and pred.get(i) in NOTIFICATIONS for i in subset)
    nfn = sum(gold[i] in NOTIFICATIONS and pred.get(i) not in NOTIFICATIONS for i in subset)
    return {
        "n": n,
        "available": len(available),
        "coverage": len(available) / n,
        "accuracy_all": sum(gold[i] == pred.get(i) for i in subset) / n,
        "accuracy_on_available": (sum(gold[i] == pred.get(i) for i in available) / len(available)
                                  if available else None),
        "macro_f1": sum(v["f1"] or 0 for v in per_label.values()) / len(per_label),
        "live_log_accuracy": (sum(gold[i] == pred.get(i) for i in live) / len(live)
                              if live else None),
        "live_log_n": len(live),
        "parse_failures": n - len(available),
        "notification": {
            "precision": ntp / (ntp + nfp) if ntp + nfp else None,
            "recall": ntp / (ntp + nfn) if ntp + nfn else None,
            "f1": 2 * ntp / (2 * ntp + nfp + nfn) if (2 * ntp + nfp + nfn) else None,
            "tp": ntp, "fp": nfp, "fn": nfn,
            "support": sum(gold[i] in NOTIFICATIONS for i in subset),
            "fp_per_100": 100 * nfp / n,
        },
        "per_label": per_label,
    }


def latency_quantiles(values: list) -> dict:
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return {"n": 0}
    return {"n": len(values),
            "p50": values[max(0, len(values) // 2)],
            "p95": values[max(0, int(len(values) * .95))]}


def main() -> int:
    cases = jsonl(CASES)
    gold = {c["case_id"]: c["expected"]["category"] for c in cases}
    tags = {c["case_id"]: set(c["tags"]) for c in cases}
    all_ids = [c["case_id"] for c in cases]
    # The llama bench also scored the critical cases, which carry their own gold
    # labels in critical_cases.jsonl. Merge them in so llama's per-run figures
    # can be reproduced on llama's own 105-case set.
    critical_rows = jsonl(CRITICAL) if CRITICAL.exists() else []
    gold.update({c["case_id"]: c["expected"]["category"] for c in critical_rows})
    tags.update({c["case_id"]: set(c["tags"]) for c in critical_rows})

    llama_rows = [r for r in jsonl(LLAMA_RAW) if not r["warmup"]]
    by_run = collections.defaultdict(list)
    for row in llama_rows:
        by_run[row["run"]].append(row)
    # Suite-membership audit. The llama bench's 105 scored cases per run are NOT
    # the 106 public cases minus warmup: the harness (corpus.load_public_cases)
    # loads public_cases.jsonl *plus* critical_cases.jsonl, so jev-0093/jev-0094
    # were scored by llama but never existed in public_cases.jsonl. Meanwhile
    # jev-0001..0003 were consumed as warmup and never scored. Net: 106 - 3 + 2
    # = 105. A like-for-like comparison against the Jev baseline (which scored
    # exactly the 106 public cases) must therefore intersect on the public set.
    llama_run_ids = sorted({r["case_id"] for r in llama_rows})
    llama_extra_ids = sorted(set(llama_run_ids) - set(all_ids))
    llama_missing_ids = sorted(set(all_ids) - set(llama_run_ids))
    subset = [i for i in all_ids if i in set(llama_run_ids)]
    assert set(LLAMA_WARMUP_EXCLUDED) <= set(llama_missing_ids), (
        "expected the warmup cases to be among those the llama bench never scored")
    assert llama_extra_ids == sorted(CRITICAL_IDS), (
        "the only cases llama scored outside public_cases should be the critical ones")

    ungated_rows = {r["case_id"]: r for r in jsonl(UNGATED)}
    ungated_pred = {cid: row["category"] for cid, row in ungated_rows.items()}
    ungated_status = collections.Counter(r["status"] for r in ungated_rows.values())
    ungated_detail = collections.Counter(r.get("detail", "-")
                                         for r in ungated_rows.values() if r["status"] != "ok")

    pipeline_rows = jsonl(PIPELINE)
    pipeline_pred = {cid: (row["rows"][0]["selected"] if row["rows"] else None)
                     for cid, row in zip(all_ids, pipeline_rows)}
    pipeline_status = collections.Counter(r["status"] for r in pipeline_rows)

    llama_medians, llama_per_run = {}, {}
    for key, fn in (("accuracy_all", lambda s: s["accuracy_all"]),
                    ("macro_f1", lambda s: s["macro_f1"]),
                    ("live_log_accuracy", lambda s: s["live_log_accuracy"]),
                    ("notif_precision", lambda s: s["notification"]["precision"]),
                    ("notif_recall", lambda s: s["notification"]["recall"]),
                    ("notif_fp_per_100", lambda s: s["notification"]["fp_per_100"])):
        vals = [fn(score({r["case_id"]: r["choice"] for r in by_run[run]},
                         llama_run_ids, gold, tags)) for run in sorted(by_run)]
        llama_medians[key] = statistics.median(vals)
        llama_per_run[key] = vals

    summary = {
        "grader": "score_independent.py (mirrors src/docich/eval/metrics.py::score_predictions)",
        "suite": {"path": str(CASES), "n": len(all_ids),
                  "sha256": __import__("hashlib").sha256(CASES.read_bytes()).hexdigest(),
                  "label_counts": dict(collections.Counter(gold.values())),
                  "intent_family_counts": dict(collections.Counter(
                      c["expected"]["intent_family"] for c in cases))},
        "llama_warmup_excluded": sorted(LLAMA_WARMUP_EXCLUDED),
        "current_jev": {
            "ungated_full_106": score(ungated_pred, all_ids, gold, tags),
            "ungated_matched_103": score(ungated_pred, subset, gold, tags),
            "pipeline_full_106": score(pipeline_pred, all_ids, gold, tags),
            "pipeline_matched_103": score(pipeline_pred, subset, gold, tags),
            "ungated_status": dict(ungated_status),
            "ungated_non_ok_detail": dict(ungated_detail),
            "pipeline_status": dict(pipeline_status),
            "ungated_latency_ms": latency_quantiles(
                [r["latency_ms"] for r in ungated_rows.values()]),
            "pipeline_jev_ms": latency_quantiles(
                [r["jev_ms"] for r in pipeline_rows if r.get("jev_ms")]),
            "pipeline_attempted": collections.Counter(
                r["attempted"] for r in pipeline_rows),
        },
        "llama3.1_8b_q4_K_M": {
            "source": str(LLAMA_RAW),
            "n_attempts": len(llama_rows),
            "cases_per_run": len(llama_run_ids),
            "run_medians": llama_medians,
            "per_run": llama_per_run,
            "matched_103": score({r["case_id"]: r["choice"]
                                  for r in llama_rows if r["run"] == 1}, subset, gold, tags),
            "pooled_notification_fp": sum(
                r for r in llama_rows if False) or None,
            "latency_total_ms": latency_quantiles([r["total_ms"] for r in llama_rows]),
            "latency_ttft_ms": latency_quantiles([r["ttft_ms"] for r in llama_rows]),
            "parse_failures": sum(1 for r in llama_rows if not r["parse_ok"]),
        },
    }
    pooled_fp = sum(s["notification"]["fp"] for s in
                    (score({r["case_id"]: r["choice"] for r in by_run[run]},
                           llama_run_ids, gold, tags) for run in sorted(by_run)))
    summary["llama3.1_8b_q4_K_M"]["pooled_notification_fp"] = pooled_fp
    summary["llama3.1_8b_q4_K_M"]["pooled_notification_fp_per_100"] = (
        100 * pooled_fp / len(llama_rows))

    u = summary["current_jev"]["ungated_matched_103"]
    l = summary["llama3.1_8b_q4_K_M"]["matched_103"]
    summary["delta_matched_103_jev_minus_llama"] = {
        "accuracy_all": u["accuracy_all"] - l["accuracy_all"],
        "macro_f1": u["macro_f1"] - l["macro_f1"],
        "live_log_accuracy": u["live_log_accuracy"] - l["live_log_accuracy"],
        "notification_precision": u["notification"]["precision"] - l["notification"]["precision"],
        "notification_recall": u["notification"]["recall"] - l["notification"]["recall"],
        "notification_fp_per_100": u["notification"]["fp_per_100"] - l["notification"]["fp_per_100"],
    }

    out = HERE / "independent_summary.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}")
    print(f"\nsuite n={len(all_ids)} digest={summary['suite']['sha256'][:16]}...")
    for name, view in (("Jev ungated (106)", summary["current_jev"]["ungated_full_106"]),
                       ("Jev pipeline (106)", summary["current_jev"]["pipeline_full_106"]),
                       ("Jev ungated (103)", u),
                       ("Jev pipeline (103)", summary["current_jev"]["pipeline_matched_103"]),
                       ("llama (103)", l)):
        nf = view["notification"]
        print(f"{name:20s} acc={view['accuracy_all']:.4f} macroF1={view['macro_f1']:.4f} "
              f"live={view['live_log_accuracy']:.4f} cov={view['coverage']:.4f} "
              f"notifP={nf['precision']:.4f} notifR={nf['recall']:.4f} FP/100={nf['fp_per_100']:.2f}")
    print(f"\nmatched-103 delta (Jev ungated - llama): "
          f"{json.dumps(summary['delta_matched_103_jev_minus_llama'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
