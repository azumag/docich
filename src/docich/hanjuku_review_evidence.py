"""Evidence-only postmortem: a combat win is not a castle-ownership receipt.

This module neither sends input nor edits the permanent chart. It reads the
retained decision log in order. A promotion needs an actual map-ownership
observation after an unambiguous attack result, not its inferred captured event.
"""
from __future__ import annotations

from collections import Counter

MAX_TRACKED_STEPS = 512
MAX_CASTLES = 256


def _label(value):
    return value if isinstance(value, str) and 0 < len(value) <= 160 else None


def audit(records) -> dict:
    counts = Counter()
    outcomes = Counter()
    unattributed = Counter()
    pending = {}
    confirmed = {}
    credited = {}
    tracked = set()
    castles_seen = set()
    overflow = False

    def revoke(key):
        step = credited.pop(key, None)
        if step is not None:
            confirmed[step].discard(key)

    def owner_seen(chapter, castle, owner):
        key = (chapter, castle)
        if owner not in ('own', 'enemy'):
            return  # unreadable flags never prove a loss or a capture
        counts['ownership_receipts'] += 1
        if owner == 'enemy':
            revoke(key)
            pending.pop(key, None)
            return
        candidates = pending.pop(key, set())
        if len(candidates) == 1:
            step = next(iter(candidates))
            if step is not None:
                confirmed.setdefault(step, set()).add(key)
                credited[key] = step
        elif len(candidates) > 1:
            counts['ambiguous_capture_receipts'] += 1

    for item in records:
        if not isinstance(item, dict) or item.get('event') != 'decision':
            continue
        decision = item.get('decision')
        chapter = item.get('chapter')
        valid_chapter = type(chapter) is int and 1 <= chapter <= 12
        if decision == 'chart_adjust_request':
            counts['chart_adjust_requests'] += 1
        if decision in ('order_failed', 'order_retry', 'menu_nav_stuck',
                        'sortie_kit_mismatch', 'battle_card_selected',
                        'battle_card_candidate', 'battle_egg_drop_unconfirmed'):
            counts[decision] += 1
        if decision == 'battle_result':
            outcome = item.get('outcome')
            outcome = outcome if outcome in ('win', 'loss') else 'unclassified'
            outcomes[outcome] += 1
            step = _label(item.get('chart_step'))
            if step is None:
                unattributed[outcome] += 1
            castle = _label(item.get('castle'))
            if valid_chapter and castle and outcome == 'win' and item.get('side') == 'attack':
                key = (chapter, castle)
                if key not in castles_seen and len(castles_seen) >= MAX_CASTLES:
                    overflow = True
                    continue
                if step is not None and step not in tracked:
                    if len(tracked) >= MAX_TRACKED_STEPS:
                        overflow = True
                        continue
                    tracked.add(step)
                castles_seen.add(key)
                revoke(key)
                # None makes an unassigned competing attack ambiguous, not an
                # invitation to credit the only named plan in the same window.
                pending.setdefault(key, set()).add(step)
        if not valid_chapter:
            continue
        observed = item.get('observed_metric')
        if not isinstance(observed, dict):
            continue
        if decision == 'world_map_owners':
            for castle, owner in observed.items():
                castle = _label(castle)
                if castle:
                    owner_seen(chapter, castle, owner)
        elif decision in ('castle_owned_observed', 'castle_lost_observed'):
            castle = _label(item.get('castle'))
            if castle:
                owner_seen(chapter, castle, observed.get('world_map'))
    # Truncation must never unlock a proposal after a competing candidate was
    # dropped from this bounded audit. Keep totals, fail closed for promotions.
    if overflow:
        confirmed.clear()
    return {'schema': 1, 'scope': 'retained_decision_logs',
            'counts': dict(counts), 'battle_outcomes': dict(outcomes),
            'unattributed_battles': dict(unattributed),
            'capture_evidence_overflow': overflow,
            'verified_captures': {step: [{'chapter': chapter, 'castle': castle}
                                        for chapter, castle in sorted(captures)]
                                  for step, captures in sorted(confirmed.items()) if captures}}


def gate_proposals(proposals: list[dict], evidence: dict, steps: dict) -> list[dict]:
    """Keep failures actionable and downgrade unverified promotion suggestions."""
    gated = []
    verified = evidence['verified_captures']
    for step, row in steps.items():
        # Retain the legacy HP-only result under an honest name. Downstream
        # intros must not mistake it for confirmed ownership after a loss.
        row['battle_win_at_target'] = row.get('captured_target') is True
        row['captured_target'] = {'chapter': row.get('chapter'),
                                  'castle': row.get('target')} in verified.get(step, [])
    for proposal in proposals:
        candidate = dict(proposal)
        kind = candidate.get('type')
        if kind in ('promote_adjusted_step', 'promote_interim_attack'):
            order = candidate.get('order') or {}
            step = candidate.get('step')
            target = order.get('target') or candidate.get('target')
            chapter = order.get('chapter') or (steps.get(step) or {}).get('chapter')
            proof = {'chapter': chapter, 'castle': target}
            if proof not in verified.get(step, []):
                candidate.update(type='review_unverified_capture', proposed_type=kind,
                                 capture_evidence='no_unambiguous_later_ownership_receipt')
            else:
                candidate['capture_evidence'] = 'observed_ownership_after_attack'
        gated.append(candidate)
    base_reviewed = {p.get('step') for p in gated if p.get('type') == 'review_base_step'}
    for step, row in sorted(steps.items()):
        if (row.get('kind') == 'base' and not row.get('captured_target')
                and (row.get('failed') or row.get('losses')) and step not in base_reviewed):
            gated.append({'type': 'review_base_step', 'step': step,
                          'target': row.get('target'), 'failed': row.get('failed', 0),
                          'losses': row.get('losses', 0), 'retries': row.get('retries', 0)})
        if row.get('kind') not in ('adjusted', 'interim'):
            continue
        if row.get('failed') or row.get('losses'):
            gated.append({'type': 'review_adjusted_failure', 'step': step,
                          'target': row.get('target'), 'failed': row.get('failed', 0),
                          'losses': row.get('losses', 0), 'retries': row.get('retries', 0)})
    unassigned = evidence['unattributed_battles']
    if unassigned.get('loss', 0) or unassigned.get('unclassified', 0):
        gated.append({'type': 'review_unattributed_battles',
                      'outcomes': dict(unassigned),
                      'reason': 'no_chart_step_is_not_no_battle'})
    return gated
