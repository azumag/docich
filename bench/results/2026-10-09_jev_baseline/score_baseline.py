#!/usr/bin/env python3
"""Re-score saved #1974 evidence offline with the common classifier grader."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / 'src'))
from docich.eval.graders import classifier as grader
from evidence_inputs import NOTIFICATIONS, WARMUP_IDS, load


def score(pred, subset, gold, tags):
    cases = [{'case_id': cid, 'expected': {'category': gold[cid],
              'intent_family': None, 'screen_need': None}} for cid in subset]
    outputs = {cid: {'category': pred.get(cid)} for cid in subset}
    s = grader.evaluate(cases, outputs)['category']
    per_label = {}
    for label, row in s['per_label'].items():
        tp = sum(gold[i] == pred.get(i) == label for i in subset)
        fp = sum(gold[i] != label and pred.get(i) == label for i in subset)
        fn = sum(gold[i] == label and pred.get(i) != label for i in subset)
        per_label[label] = {**row, 'tp': tp, 'fp': fp, 'fn': fn}
    live = [i for i in subset if 'live-log' in tags[i]]
    live_available = [i for i in live if pred.get(i) is not None]
    ntp = sum(gold[i] in NOTIFICATIONS and pred.get(i) in NOTIFICATIONS for i in subset)
    nfp = sum(gold[i] not in NOTIFICATIONS and pred.get(i) in NOTIFICATIONS for i in subset)
    nfn = sum(gold[i] in NOTIFICATIONS and pred.get(i) not in NOTIFICATIONS for i in subset)
    ratio = lambda a, b: a / b if b else None
    return {'n': s['n'], 'available': s['available_n'], 'coverage': s['coverage'],
            'correct': sum(gold[i] == pred.get(i) for i in subset),
            'accuracy_all': s['correct_fraction_all'],
            'accuracy_on_available': s['accuracy_on_available'],
            'macro_f1': s['macro_f1_with_abstentions_as_misses'],
            'live_log_accuracy': ratio(sum(gold[i] == pred.get(i) for i in live), len(live)),
            'live_log_accuracy_on_available': ratio(sum(gold[i] == pred.get(i) for i in live_available), len(live_available)),
            'live_log_n': len(live), 'live_log_available': len(live_available),
            'live_log_correct': sum(gold[i] == pred.get(i) for i in live),
            'parse_failures': s['n'] - s['available_n'],
            'notification': {'precision': ratio(ntp, ntp + nfp), 'recall': ratio(ntp, ntp + nfn),
                             'f1': ratio(2 * ntp, 2 * ntp + nfp + nfn), 'tp': ntp, 'fp': nfp, 'fn': nfn,
                             'support': sum(gold[i] in NOTIFICATIONS for i in subset),
                             'fp_per_100': ratio(100 * nfp, s['n'])},
            'per_label': per_label}


def latency_quantiles(values):
    # Preserve the published benchmark's upper-index quantile convention.
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return {'n': 0, 'p50': None, 'p95': None}
    return {'n': len(values), 'p50': values[len(values) // 2], 'p95': values[int(len(values) * .95)]}


def public_numbers(value):
    """Canonical JSON precision across supported Python float summations."""
    if isinstance(value, float):
        return round(value, 12)
    if isinstance(value, dict):
        return {key: public_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_numbers(item) for item in value]
    return value


def build_report(repo=REPO, *, scorer=score, pipeline_path=None):
    data = load(repo, pipeline_path)
    gold, tags = data['gold'], data['tags']
    all_ids, matched, measured = data['all_ids'], data['matched'], data['measured_ids']
    ungated = data['ungated']
    pred = {cid: r['category'] for cid, r in ungated.items()}
    u = scorer(pred, matched, gold, tags)
    by_run = data['by_run']
    predictions = {run: {cid: r['choice'] if r['parse_ok'] else None for cid, r in rows.items()}
                   for run, rows in by_run.items()}
    full = {run: scorer(p, measured, gold, tags) for run, p in predictions.items()}
    matched_runs = {run: scorer(p, matched, gold, tags) for run, p in predictions.items()}
    # Each run is scored on exactly the same validated case IDs. Do not select run 1.
    def medians(views):
        out = {}
        for key in ('accuracy_all', 'macro_f1', 'live_log_accuracy', 'coverage'):
            out[key] = statistics.median(v[key] for v in views.values())
        for key in ('precision', 'recall', 'fp_per_100'):
            out['notification_' + key] = statistics.median(v['notification'][key] for v in views.values())
        return out
    l = medians(matched_runs)
    rows = [r for run in sorted(by_run) for r in by_run[run].values()]
    pooled_fp = sum(v['notification']['fp'] for v in full.values())
    matched_fp = sum(v['notification']['fp'] for v in matched_runs.values())
    result = {'schema_version': 2, 'grader': 'docich.eval.graders.classifier.evaluate',
              'numeric_precision': 'Public JSON floats rounded to 12 decimal places after scoring/deltas; integer counts remain exact.',
              'accuracy_policy': 'Missing predictions stay in accuracy_all denominator; available-only is separate.',
              **data['metadata'], 'llama_warmup_excluded': sorted(WARMUP_IDS),
              'current_jev': {'ungated_full_106': scorer(pred, all_ids, gold, tags),
                              'ungated_matched_103': u,
                              'ungated_status': dict(sorted(Counter(r['status'] for r in ungated.values()).items())),
                              'ungated_latency_ms': latency_quantiles(r.get('latency_ms') for r in ungated.values())},
              'llama3.1_8b_q4_K_M': {'n_attempts': len(rows), 'cases_per_run': len(measured),
                                    'per_run_full_105': full, 'per_run_matched_103': matched_runs,
                                    'run_medians': medians(full), 'matched_medians': l,
                                    'pooled_notification_fp': pooled_fp,
                                    'pooled_notification_fp_per_100': 100 * pooled_fp / len(rows),
                                    'matched_notification_fp': matched_fp,
                                    'matched_attempts': len(matched) * len(by_run),
                                    'matched_notification_fp_per_100': 100 * matched_fp / (len(matched) * len(by_run)),
                                    'latency_total_ms': latency_quantiles(r.get('total_ms') for r in rows),
                                    'latency_ttft_ms': latency_quantiles(r.get('ttft_ms') for r in rows),
                                    'parse_failures': sum(not r['parse_ok'] for r in rows)}}
    result['delta_matched_103_jev_minus_llama'] = {
        **{key: u[key] - l[key] for key in ('accuracy_all', 'macro_f1', 'live_log_accuracy')},
        **{'notification_' + key: u['notification'][key] - l['notification_' + key]
           for key in ('precision', 'recall', 'fp_per_100')}}
    if data['pipeline'] is not None:
        pipeline = data['pipeline']
        for field, view_name in (('selected', 'pipeline'), ('baseline', 'heuristic')):
            p = {cid: r['event']['rows'][0][field] for cid, r in pipeline.items()}
            for ids, scope_name in ((all_ids, 'full_106'), (matched, 'matched_103')):
                result['current_jev'][view_name + '_' + scope_name] = scorer(p, ids, gold, tags)
    result['interpretation'] = ('Observed matched-case differences only; no significance or production acceptance established. '
                                'Gated pipeline predictions and ungated Llama predictions do not measure actual TTS firing. '
                                'The separate 19/150 provider experiment has no published raw here.')
    return public_numbers(result)


def write_report(report, out):
    Path(out).write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--pipeline', type=Path, help='Optional case-ID-bound latest records; never positional raw')
    parser.add_argument('--out', type=Path, default=HERE / 'report.json')
    args = parser.parse_args(argv)
    write_report(build_report(args.repo, pipeline_path=args.pipeline), args.out)
    print('wrote offline report')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
