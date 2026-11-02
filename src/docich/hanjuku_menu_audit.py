"""Offline audit of explicitly labelled human rescue-menu action plans.

Plans are not sent-input receipts. Missing receipts stay unconfirmed; this
module never reports a gameplay failure or a survival-rate improvement.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path

REQUEST = 'battle_command_open_requested'
BOUNDARIES = {'battle_start', 'battle_result', 'defender_successor',
              'unlocated_successor', 'battle_entry_successor'}


def audit(records):
    counts = Counter(planned_openings=0, menu_observed=0, unconfirmed=0,
                     excluded_unlabelled_b=0, invalid_plans=0)
    pending = None
    seen = set()
    latencies = []

    def close():
        nonlocal pending
        if pending is not None:
            counts['unconfirmed'] += 1
            pending = None

    for row in records:
        if not isinstance(row, dict) or row.get('event') != 'action_plan':
            continue
        identity = tuple(row.get(k) for k in ('runtime_id', 'generation', 'lease_id', 'bot_version'))
        decision_id, at = row.get('decision_id'), row.get('at')
        if (not all(isinstance(identity[i], str) and identity[i] for i in (0, 2, 3))
                or type(identity[1]) is not int or not isinstance(decision_id, str)
                or not decision_id or type(at) not in (int, float)
                or not math.isfinite(at)):
            close()
            counts['invalid_plans'] += 1
            continue
        key = (*identity, decision_id)
        if key in seen:
            continue
        seen.add(key)
        reasons = row.get('reason_decisions')
        actions = row.get('planned_actions')
        if (not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons)
                or not isinstance(actions, list) or not all(isinstance(a, dict) for a in actions)):
            close()
            counts['invalid_plans'] += 1
            continue
        kind = row.get('screen_kind')
        # A gap, run/version change, or new battle cannot provide a receipt
        # for a previous opening. Three seconds bounds missing observations.
        if pending and (identity != pending[0] or not 0 < at - pending[2] <= 3
                        or at - pending[1] > 10 or BOUNDARIES.intersection(reasons)):
            close()
        if pending:
            if kind == 'battle_menu':
                counts['menu_observed'] += 1
                latencies.append(round(at - pending[1], 3))
                pending = None
            elif kind not in ('battle', 'battle_menu_pending', 'text', 'unknown'):
                close()
            else:
                pending = (pending[0], pending[1], at)
        is_b = any(a.get('type') == 'pad' and a.get('buttons') == ['b'] for a in actions)
        if is_b and len(actions) == 1 and REQUEST in reasons and kind == 'battle':
            close()
            counts['planned_openings'] += 1
            pending = (identity, at, at)
        elif is_b:
            counts['excluded_unlabelled_b'] += 1
            # Any other B may close or toggle a menu: do not attribute a
            # later menu to the earlier rescue request across that input.
            close()
    close()
    return {**counts, 'observed_latency_seconds': latencies,
            'menu_observed_per_plan': (counts['menu_observed'] / counts['planned_openings']
                                       if counts['planned_openings'] else None),
            'input_delivery': 'unconfirmed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path, help='One chronological hanjuku_decisions JSONL archive')
    args = parser.parse_args()
    with args.log.open() as stream:
        result = audit(json.loads(line) for line in stream if line.strip())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
