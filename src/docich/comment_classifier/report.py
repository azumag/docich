"""Read sanitised Jev telemetry, or explicitly evaluate a labelled JSONL corpus.

Ported from soviet_now ``lib/comment_classifier_jev_report.py``. Telemetry
agreement is not accuracy. Gold-label evaluation is a separate mode and only
contacts the provider with ``--allow-api`` and the selected route's runtime
credential.

    python -m docich.comment_classifier.report tmp/comment_classifier_jev/metrics-*.jsonl*
    python -m docich.comment_classifier.report --evaluate labelled.jsonl --allow-api
"""
from __future__ import annotations

import argparse
from collections import Counter
import math
import os
from pathlib import Path
import sys
import tempfile

from docich.semantic_decision.routes import resolve_route
from docich.semantic_decision.validator import dumps, number, strict_json

from . import classify_file, jev


def ratio(a, b):
    return a / b if b else None


def quantiles(values):
    values = sorted(v for v in values if number(v, 0, 1e12))
    return {'n': len(values), **{name: values[max(0, math.ceil(len(values) * p) - 1)] if values else None
                                for name, p in (('p50', .50), ('p95', .95), ('p99', .99))}}


def summarize(events):
    events = [e for e in events if isinstance(e, dict) and e.get('schema_version') == 1]
    attempts = [e for e in events if e.get('attempted') is True]
    rows = [r for e in events for r in e.get('rows', [])]
    compared = [r for r in rows if r.get('candidate') in jev.CRITERIA]
    costs = [e['estimated_usd'] for e in attempts if e.get('estimated_usd') is not None]
    transitions = Counter(f"{r['baseline']}->{r['candidate']}" for r in compared)
    return {
        'batches': len(events), 'comments': len(rows), 'requests_attempted': len(attempts),
        'request_success_rate': ratio(sum(e['status'] == 'ok' for e in attempts), len(attempts)),
        'request_status_counts': dict(Counter(e['status'] for e in events)),
        'row_status_counts': dict(Counter(r['status'] for r in rows)),
        'route_counts': dict(Counter(e.get('route', 'direct') for e in events)),
        'jev_coverage': ratio(sum(r['status'] == 'jev' for r in rows), len(rows)),
        'fallback_rate_excluding_local_notifications': ratio(
            sum(r['status'] not in ('jev', 'local_notification') for r in rows),
            sum(r['status'] != 'local_notification' for r in rows)),
        'agreement_sample_count': len(compared),
        'agreement_not_accuracy': ratio(sum(r['baseline'] == r['candidate'] for r in compared), len(compared)),
        'category_transitions': dict(transitions),
        'latency_ms': {key: quantiles([e.get(key) for e in events])
                       for key in ('heuristic_ms', 'jev_ms', 'classification_ms')},
        'classification_ms_by_batch_size': {
            str(size): quantiles([e.get('classification_ms') for e in events if e['batch_size'] == size])
            for size in sorted({e['batch_size'] for e in events})},
        'usage_known_requests': sum(e.get('usage') is not None for e in attempts),
        'usage_unknown_requests': sum(e.get('usage') is None for e in attempts),
        'known_input_tokens': sum(e['usage']['input_tokens'] for e in attempts if e.get('usage')),
        'known_estimated_usd': sum(costs) if costs else None,
        'cost_unknown_requests': sum(e.get('estimated_usd') is None for e in attempts),
    }


def score_predictions(pairs):
    """Unsuccessful Jev answers stay in the denominator via explicit coverage."""
    available = [(gold, prediction) for gold, prediction in pairs if prediction is not None]
    labels = sorted({gold for gold, _ in pairs} | {pred for _, pred in available})
    per_class = {}
    for label in labels:
        tp = sum(g == p == label for g, p in available)
        fp = sum(g != label and p == label for g, p in available)
        fn = sum(g == label and p != label for g, p in pairs)
        per_class[label] = {'precision': ratio(tp, tp + fp), 'recall': ratio(tp, tp + fn),
                            'f1': ratio(2 * tp, 2 * tp + fp + fn)}
    f1s = [row['f1'] or 0 for row in per_class.values()]
    return {'n': len(pairs), 'available_n': len(available), 'coverage': ratio(len(available), len(pairs)),
            'accuracy_on_available': ratio(sum(g == p for g, p in available), len(available)),
            'correct_fraction_all': ratio(sum(g == p for g, p in available), len(pairs)),
            'macro_f1_with_abstentions_as_misses': sum(f1s) / len(f1s) if f1s else None,
            'per_category': per_class,
            'confusion': dict(Counter(f'{g}->{p or "unavailable"}' for g, p in pairs))}


def evaluate(path, *, env=None, transport=None):
    env = dict(os.environ if env is None else env)
    profile = resolve_route(env.get('DOCICH_JEV_ROUTE', 'direct'))
    if not env.get(profile.credential_env):
        raise ValueError('missing_key')
    examples = []
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():
                continue
            item = strict_json(line)
            if (not isinstance(item, dict) or not isinstance(item.get('text'), str)
                    or item.get('category') not in jev.CRITERIA or '\n' in item['text']
                    or '\r' in item['text'] or len(item['text'].encode()) > jev.MAX_COMMENT_BYTES
                    or not item['text'].strip() or len(examples) >= 5000):
                raise ValueError('invalid_dataset')
            examples.append(item)
    pairs = {key: [] for key in ('heuristic', 'jev', 'hybrid')}
    events, ambiguous = [], 0
    with tempfile.TemporaryDirectory(prefix='jev-eval-') as temp:
        directory = Path(temp)
        run_env = {**env, 'COMMENT_CLASSIFIER_BACKEND': 'jev', 'COMMENT_CLASSIFIER_JEV_LOG_ENABLED': '0',
                   'COMMENT_CLASSIFIER_JEV_STATE_DIR': str(directory / 'state')}
        for item in examples:
            if item.get('ambiguous') is True:
                ambiguous += 1
                continue
            source = directory / 'comment.txt'
            source.write_text('viewer: ' + item['text'] + '\n', encoding='utf-8')
            source.chmod(0o600)
            result, event = classify_file(source, env=run_env, transport=transport)
            if event is None:
                raise ValueError('evaluation_failed')
            events.append(event)
            detail = event['rows'][0]
            for key, prediction in (('heuristic', detail['baseline']), ('jev', detail['candidate']),
                                    ('hybrid', result[0]['category'])):
                pairs[key].append((item['category'], prediction))
    return {'ambiguous_excluded': ambiguous, 'evaluation_batch_size': 1,
            'scores': {key: score_predictions(value) for key, value in pairs.items()},
            'operational_metrics': summarize(events)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='*', type=Path)
    parser.add_argument('--evaluate', type=Path)
    parser.add_argument('--allow-api', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.evaluate:
            if not args.allow_api:
                parser.error('--evaluate requires --allow-api')
            result = evaluate(args.evaluate)
        else:
            events, invalid = [], 0
            for path in args.logs:
                with path.open(encoding='utf-8') as stream:
                    for line in stream:
                        try:
                            events.append(strict_json(line))
                        except ValueError:
                            invalid += 1
            result = {**summarize(events), 'invalid_json_lines': invalid}
        print(dumps(result))
        return 0
    except SystemExit:
        raise
    except Exception:
        print('Unable to read/evaluate input; no raw data was logged.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
