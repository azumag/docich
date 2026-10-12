#!/usr/bin/env python3
"""Capture case identity at collection time and merge immutable retry passes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from evidence_inputs import jsonl

RETRYABLE = {'cooldown', 'auth_error', 'invalid_response', 'network_error',
             'server_error', 'overloaded', 'rate_limited', 'http_error', 'timeout'}


def record_case(case_id, pass_number, metrics_dir, rc, latency_ms):
    if not re.fullmatch(r'jev-\d{4}', case_id) or pass_number < 1:
        raise ValueError('invalid case ID/pass')
    events = [r for path in sorted(Path(metrics_dir).glob('metrics-*.jsonl')) for r in jsonl(path)]
    if len(events) != 1:
        raise ValueError('capture requires exactly one telemetry event for the case')
    event = events[0]
    if event.get('batch_size') != 1 or len(event.get('rows', [])) != 1:
        raise ValueError('capture requires exactly one classified row')
    # Isolated per-case metrics directory belongs to this invocation, not to
    # an inferred position in a shared log. Keep collector identity outside raw.
    return {'case_id': case_id, 'pass': pass_number, 'event': event,
            'rc': rc, 'latency_ms': latency_ms}


def retryable(record):
    return (record['rc'] != 0 or record['event']['status'] in RETRYABLE
            or record['event']['rows'][0].get('status') in RETRYABLE)


def merge_passes(passes, expected_ids):
    expected_ids = list(expected_ids)
    if not expected_ids or len(set(expected_ids)) != len(expected_ids):
        raise ValueError('invalid expected case IDs')
    latest = {}
    for pass_number, rows in enumerate(passes, 1):
        # The first pass covers the full suite; each later pass covers exactly
        # the previous pass's pending cases in manifest order.
        pending = expected_ids if pass_number == 1 else [i for i in expected_ids if retryable(latest[i])]
        if [r.get('case_id') for r in rows] != pending:
            raise ValueError('pass case IDs/count/order mismatch')
        for row in rows:
            if row.get('pass') != pass_number:
                raise ValueError('pass number mismatch')
            event = row.get('event', {})
            if event.get('batch_size') != 1 or len(event.get('rows', [])) != 1:
                raise ValueError('invalid case event')
            latest[row['case_id']] = row
    if set(latest) != set(expected_ids):
        raise ValueError('incomplete first pass')
    return [latest[i] for i in expected_ids]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    record = sub.add_parser('record')
    record.add_argument('--case-id', required=True)
    record.add_argument('--pass-number', type=int, required=True)
    record.add_argument('--metrics-dir', type=Path, required=True)
    record.add_argument('--rc', type=int, required=True)
    record.add_argument('--latency-ms', type=int, required=True)
    record.add_argument('--out', type=Path, required=True)
    merge = sub.add_parser('merge')
    merge.add_argument('--expected-ids', type=Path, required=True)
    merge.add_argument('--pass-files', type=Path, nargs='+', required=True)
    merge.add_argument('--out', type=Path, required=True)
    merge.add_argument('--pending', type=Path, required=True)
    args = p.parse_args(argv)
    if args.command == 'record':
        row = record_case(args.case_id, args.pass_number, args.metrics_dir, args.rc, args.latency_ms)
        with args.out.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
    else:
        ids = args.expected_ids.read_text().splitlines()
        rows = merge_passes([jsonl(path) for path in args.pass_files], ids)
        args.out.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))
        args.pending.write_text(''.join(r['case_id'] + '\n' for r in rows if retryable(r)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
