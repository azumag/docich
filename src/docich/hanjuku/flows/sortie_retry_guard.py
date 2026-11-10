"""Cross-step retry guards for equivalent off-chart sorties (#3265).

Adjusted/mechanical plans have execution ids that include the request id, and
interim orders are numbered per reconstruction. The same logical sortie can
therefore come back with a different ``step`` even though its source, target
and purpose did not change. The original #2841 guards and the #3261 launch
probe are intentionally finite, but their counters are keyed by ``step``; a
replan could reset those budgets and make the UI cancel/reissue the same
sortie forever.

Keep the existing per-step machinery unchanged. This flow-level adapter only
carries its result across equivalent A:/I: execution ids while the material
world state is unchanged, and releases it as soon as that state changes.
"""
from __future__ import annotations
from ..state.regions import world as _world, sorties as _sorties, plan as _plan_region

from ... import hanjuku_chart_adjust as chart_adjust
from ..decision_log import _record
from ..os_layer.world import _en_route, _owned
from ..strategy import orders as _orders
from ..strategy import replan as _replan
from . import deploy as _deploy
from . import map as _map


_CANCEL_DECISIONS = frozenset({
    'sortie_held_source_defender',
    'sortie_held_hero_guard',
})


def _off_chart(order) -> bool:
    step = str((order or {}).get('step') or '')
    return step.startswith((chart_adjust.PLAN_PREFIX, chart_adjust.INTERIM_PREFIX))


def _purpose(order) -> str:
    return str((order or {}).get('purpose') or 'sortie')


def _logical_key(order, mem, *, source=None, target=None, purpose=None):
    if not _off_chart(order):
        return None
    source = source if source is not None else _orders._source(order, mem)
    target = target if target is not None else order.get('target')
    purpose = purpose if purpose is not None else _purpose(order)
    if not source or not target:
        return None
    return '|'.join((str(mem.get('chapter') or 0), str(source), str(target), str(purpose)))


def _cancel_fingerprint(order, mem):
    source = _orders._source(order, mem)
    target = order.get('target')
    garrison = _world(mem).get('garrison') or {}
    present = garrison.get(source)
    busy, _ = _en_route(mem)
    unavailable = busy | set(_world(mem).get('general_location_unknown') or ())
    available = (sorted(set(present) - unavailable)
                 if isinstance(present, list) else None)
    owned = _owned(mem)
    return {
        'chapter': mem.get('chapter'),
        'source': source,
        'target': target,
        'purpose': _purpose(order),
        'present': sorted(present) if isinstance(present, list) else None,
        'available': available,
        'source_owned': source in owned,
        'target_owned': target in owned,
        'target_lost': target in set(mem.get('lost') or ()),
        'hero_broken': _orders._hero_egg_broken(mem),
        'unplaced': _orders._unplaced_castle_generals(mem),
    }


def _launch_fingerprint(order, mem, probe):
    source = probe.get('source') or _orders._source(order, mem)
    target = probe.get('target') or order.get('target')
    owned = _owned(mem)
    return {
        'chapter': mem.get('chapter'),
        'source': source,
        'target': target,
        'purpose': _purpose(order),
        'source_owned': source in owned,
        'target_owned': target in owned,
        'target_lost': target in set(mem.get('lost') or ()),
    }


def _remember_cancel(order, mem, decision):
    key = _logical_key(order, mem)
    if key is None:
        return
    fingerprint = _cancel_fingerprint(order, mem)
    ledger = mem.setdefault('equivalent_sortie_cancels', {})
    previous = ledger.get(key) if isinstance(ledger.get(key), dict) else {}
    count = int(previous.get('count') or 0) + 1 if previous.get('fingerprint') == fingerprint else 1
    ledger[key] = {
        'fingerprint': fingerprint,
        'decision': decision,
        'step': order.get('step'),
        'count': count,
        'suppressed_steps': [],
    }


def _equivalent_cancel_suppressed(order, mem) -> bool:
    key = _logical_key(order, mem)
    ledger = mem.get('equivalent_sortie_cancels') or {}
    row = ledger.get(key) if key is not None else None
    if not isinstance(row, dict):
        return False
    current = _cancel_fingerprint(order, mem)
    if row.get('fingerprint') != current:
        ledger.pop(key, None)
        _record(mem, 'sortie_equivalent_released', chart_step=order.get('step'),
                source=current.get('source'), target=current.get('target'),
                observed_metric={'previous_step': row.get('step'),
                                 'previous_decision': row.get('decision')},
                reason='同等出撃を取り消した時点から駐留・行軍・城所有の状態が変わったため再計画を許可')
        return False
    step = order.get('step')
    seen = row.setdefault('suppressed_steps', [])
    if step not in seen:
        seen.append(step)
        del seen[:-8]
        _record(mem, 'sortie_equivalent_suppressed', chart_step=step,
                source=current.get('source'), target=current.get('target'),
                observed_metric={'previous_step': row.get('step'),
                                 'decision': row.get('decision'),
                                 'cancel_count': row.get('count')},
                reason='条件が変わらない同一の出撃元・行先・目的を別stepで再発行しない')
    return True


def _launch_order(mem, step, probe):
    order = (_world(mem).get('launched_orders') or {}).get(step)
    if isinstance(order, dict):
        return order
    return {
        'step': step,
        'source': probe.get('source'),
        'target': probe.get('target'),
        'general': probe.get('general'),
        'purpose': probe.get('purpose') or 'sortie',
    }


def _logical_launch_budget(mem, step, probe):
    order = _launch_order(mem, step, probe)
    key = _logical_key(order, mem, source=probe.get('source'), target=probe.get('target'),
                       purpose=_purpose(order))
    if key is None:
        return None, None, None
    fingerprint = _launch_fingerprint(order, mem, probe)
    ledger = mem.setdefault('equivalent_launch_miss', {})
    row = ledger.get(key)
    if isinstance(row, dict) and row.get('fingerprint') != fingerprint:
        ledger.pop(key, None)
        row = None
    return key, fingerprint, row


def _launch_state(mem):
    """Use #3274's flow region when present, while remaining hotload-compatible."""
    accessor = getattr(_map, '_flow', None)
    return accessor(mem) if callable(accessor) else mem


def _is_bounded_location_recheck(decision, observed) -> bool:
    """Do not turn the intentional finite source re-read into a permanent hold."""
    return (decision == 'sortie_held_source_defender'
            and isinstance(observed, dict)
            and observed.get('location_recheck_attempts') is not None)


def install() -> None:
    """Patch the loaded layered policy once; direct layer imports see it too."""
    if getattr(_orders, '_equivalent_sortie_guard_installed', False):
        return
    _orders._equivalent_sortie_guard_installed = True

    original_ready = _orders._ready
    original_hold = _deploy._hold_guarded_sortie
    original_cancelled = _orders._record_sortie_cancelled_observed
    original_rollback = _map._rollback_unverified_launch
    original_probe = _map._probe_launch_markers

    def ready(order, mem):
        if _equivalent_cancel_suppressed(order, mem):
            return False
        return original_ready(order, mem)

    def hold(mem, order, decision, reason, observed):
        actions = original_hold(mem, order, decision, reason, observed)
        if decision in _CANCEL_DECISIONS and not _is_bounded_location_recheck(decision, observed):
            _remember_cancel(order, mem, decision)
        return actions

    def cancelled(mem, order, sortie):
        original_cancelled(mem, order, sortie)
        _remember_cancel(order, mem, 'sortie_cancelled_observed')

    def rollback(mem, step, probe, markers, reason=None):
        key, fingerprint, row = _logical_launch_budget(mem, step, probe)
        launch_state = _launch_state(mem)
        if isinstance(row, dict):
            misses = launch_state.setdefault('launch_miss', {})
            misses[step] = max(int(misses.get(step) or 0), int(row.get('count') or 0))
        result = original_rollback(mem, step, probe, markers, reason=reason)
        if key is not None:
            launch_state = _launch_state(mem)
            count = int((launch_state.get('launch_miss') or {}).get(step) or 0)
            mem.setdefault('equivalent_launch_miss', {})[key] = {
                'fingerprint': fingerprint,
                'count': count,
                'step': step,
            }
        return result

    def probe(mem, markers):
        before = {}
        launch_state = _launch_state(mem)
        for step, launch in list((launch_state.get('launch_probe') or {}).items()):
            if not isinstance(launch, dict):
                continue
            key, fingerprint, _ = _logical_launch_budget(mem, step, launch)
            if key is not None:
                before[step] = (key, fingerprint)
        result = original_probe(mem, markers)
        remaining = _launch_state(mem).get('launch_probe') or {}
        for step, (key, fingerprint) in before.items():
            if step in remaining:
                continue
            sortie = _sorties(mem).get(step)
            status = (_plan_region(mem).get('orders') or {}).get(step)
            if (isinstance(sortie, dict)
                    and sortie.get('status') in ('en_route', 'launched_unconfirmed', 'arrived')
                    and status in ('launched', 'launched_unconfirmed')):
                row = (mem.get('equivalent_launch_miss') or {}).get(key)
                if not isinstance(row, dict) or row.get('fingerprint') == fingerprint:
                    (mem.get('equivalent_launch_miss') or {}).pop(key, None)
        return result

    _orders._ready = ready
    _replan._ready = ready
    _map._ready = ready
    _deploy._hold_guarded_sortie = hold
    _map._hold_guarded_sortie = hold
    _orders._record_sortie_cancelled_observed = cancelled
    _deploy._record_sortie_cancelled_observed = cancelled
    _map._rollback_unverified_launch = rollback
    _map._probe_launch_markers = probe


install()
