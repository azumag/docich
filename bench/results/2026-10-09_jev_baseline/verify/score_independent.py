#!/usr/bin/env python3
"""Independent metric arithmetic over the same validated public #1974 inputs.

No API calls. Uses the strict input loader and report schema of score_baseline,
while computing metric arithmetic here without invoking the common grader.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from evidence_inputs import NOTIFICATIONS
from score_baseline import REPO, build_report, write_report


def score(pred: dict, subset: list, gold: dict, tags: dict) -> dict:
    """Mirror of metrics.score_predictions: a None prediction stays a miss."""
    per_label = {}
    labels = sorted({gold[i] for i in subset} | {pred[i] for i in subset if pred.get(i) is not None})
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
    available = [i for i in subset if pred.get(i) is not None]
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
        "coverage": len(available) / n if n else None,
        "correct": sum(gold[i] == pred.get(i) for i in subset),
        "accuracy_all": sum(gold[i] == pred.get(i) for i in subset) / n if n else None,
        "accuracy_on_available": (sum(gold[i] == pred.get(i) for i in available) / len(available)
                                  if available else None),
        "macro_f1": sum(v["f1"] or 0 for v in per_label.values()) / len(per_label) if per_label else None,
        "live_log_accuracy": (sum(gold[i] == pred.get(i) for i in live) / len(live)
                              if live else None),
        "live_log_n": len(live),
        "live_log_available": sum(pred.get(i) is not None for i in live),
        "live_log_correct": sum(gold[i] == pred.get(i) for i in live),
        "live_log_accuracy_on_available": (sum(gold[i] == pred.get(i) for i in live)
            / sum(pred.get(i) is not None for i in live) if any(pred.get(i) is not None for i in live) else None),
        "parse_failures": n - len(available),
        "notification": {
            "precision": ntp / (ntp + nfp) if ntp + nfp else None,
            "recall": ntp / (ntp + nfn) if ntp + nfn else None,
            "f1": 2 * ntp / (2 * ntp + nfp + nfn) if (2 * ntp + nfp + nfn) else None,
            "tp": ntp, "fp": nfp, "fn": nfn,
            "support": sum(gold[i] in NOTIFICATIONS for i in subset),
            "fp_per_100": 100 * nfp / n if n else None,
        },
        "per_label": per_label,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--pipeline', type=Path)
    parser.add_argument('--out', type=Path, default=HERE / 'independent_summary.json')
    args = parser.parse_args(argv)
    report = build_report(args.repo, scorer=score, pipeline_path=args.pipeline)
    report['grader'] = 'Independent arithmetic in verify/score_independent.py; shared input identity validation'
    write_report(report, args.out)
    print('wrote independent offline report')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
