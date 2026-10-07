"""Adjusted attacks must not empty their source castles; no emulator or API."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen


def memory(step='A:guard001:J1'):
    # Validated adjusted orders normally omit purpose; interim attacks do not.
    order = {
        'step': step, 'general': 'ゼウス', 'source': 'アルマムーン',
        'target': 'ゴーメン', 'cards': [],
        'after': None, 'note': 'source defender regression',
    }
    if step.startswith(p.chart_adjust.INTERIM_PREFIX):
        order['purpose'] = 'attack'
    return {
        'chapter': 1, 'tick': 100, 'captured': ['カストーラ', 'キカンドン'],
        'lost': [], 'active': step, 'picked': [], '_records': [],
        'garrison': {
            'アルマムーン': ['ゼウス', 'どうし'],
            'カストーラ': ['ヴィーナス'], 'キカンドン': ['ココット'],
        },
        'orders': {}, 'chart_plan': {'orders': [order]},
        'launched_orders': {step: order},
    }


def generals(names):
    lines = [
        TextLine(39 + 16 * i, tuple((144 + 8 * j, c) for j, c in enumerate(name)))
        for i, name in enumerate(names)
    ]
    return Screen(
        lines=lines, hand=(122, 33, 139, 45),
        text=''.join(names) if names else 'おりません', kind='general_list',
    )


@pytest.mark.parametrize('step', ['A:guard001:J1', 'I:guard001:1'])
@pytest.mark.parametrize('names', [['ゼウス'], [], ['ゼウス', 'ゼウス']])
def test_attack_guard_requires_two_distinct_available_generals(step, names):
    mem = memory(step)
    order = p._order(mem)
    mem['garrison']['アルマムーン'] = names
    before = deepcopy(mem)
    assert p._reserve_source_guard(order, mem)
    assert p._source_spare_missing(order, mem)
    assert mem == before


@pytest.mark.parametrize('purpose', [None, 'attack', 'retake'])
def test_explicit_adjusted_purpose_also_preserves_a_defender(purpose):
    mem = memory()
    order = p._order(mem)
    order['purpose'] = purpose
    mem['garrison']['アルマムーン'] = ['ゼウス']
    assert p._source_spare_missing(order, mem)


def test_validated_and_adopted_real_plan_is_guarded():
    mem = memory()
    raw_order = dict(p._order(mem), step='J1')
    rid = '0123456789abcdef'
    doc = p.chart_adjust.validate({
        'schema': p.chart_adjust.SCHEMA, 'chapter': 1, 'request_id': rid,
        'orders': [raw_order],
    })
    p._adopt_plan(mem, doc, rid)
    order = mem['chart_plan']['orders'][0]
    assert 'purpose' not in order
    assert order['step'] == p.chart_adjust.execution_step(rid, 'J1')
    mem['active'] = order['step']
    mem['garrison']['アルマムーン'] = ['ゼウス']
    assert not p._plan_pending(mem)
    assert p._source_spare_missing(order, mem)
    assert p.deploy_step(generals(['ゼウス']), mem) == [p.pad('b'), p.pad('b')]
    assert mem['orders'][order['step']] == 'pending'


def test_adjusted_attack_is_pending_but_does_not_starve_other_orders(monkeypatch):
    mem = memory()
    order = p._order(mem)
    mem['garrison']['アルマムーン'] = ['ゼウス']
    monkeypatch.setattr(p.chart, 'orders', lambda _chapter: ())
    assert not p._plan_pending(mem)
    assert p.next_order(mem) is None
    assert mem['orders'].get(order['step']) in (None, 'pending')

    # A measured reinforcement releases the same order, not a new generation.
    mem['garrison']['アルマムーン'].append('どうし')
    assert p._plan_pending(mem)
    assert p.next_order(mem) is order


@pytest.mark.parametrize('names', [['ゼウス'], [], ['ゼウス', 'ゼウス']])
def test_live_adjusted_list_rechecks_stale_two_general_snapshot(names):
    mem = memory()
    step = mem['active']
    assert p.deploy_step(generals(names), mem) == [p.pad('b'), p.pad('b')]
    assert mem['_records'][-1]['decision'] == 'sortie_held_source_defender'
    assert mem['active'] is None
    assert mem['orders'][step] == 'pending'


@pytest.mark.parametrize('kind', ['card_select', 'sortie_confirm'])
def test_hotloaded_adjusted_order_is_rechecked_after_source_changes(kind):
    mem = memory()
    mem['garrison']['アルマムーン'] = ['ゼウス']
    screen = Screen(kind=kind, lines=[], hand=None, text='')
    assert p.deploy_step(screen, mem) == [p.pad('b'), p.pad('b')]
    assert mem['_records'][-1]['decision'] == 'sortie_held_source_defender'


@pytest.mark.parametrize('status', ['en_route', 'launched_unconfirmed'])
def test_busy_spare_does_not_count_even_in_cached_live_list(status):
    mem = memory()
    mem['sorties'] = {
        'earlier': {'general': 'どうし', 'target': 'ジョンリギ',
                    'status': status, 'tick': 99},
    }
    assert p._source_spare_missing(p._order(mem), mem)
    assert p.deploy_step(generals(['ゼウス', 'どうし']), mem) == [p.pad('b'), p.pad('b')]


@pytest.mark.parametrize('kind', ['card_select', 'sortie_confirm'])
def test_location_unknown_spare_is_excluded_without_fresh_roster(kind):
    mem = memory()
    mem['general_location_unknown'] = ['どうし']
    assert p._source_spare_missing(p._order(mem), mem)
    assert p.deploy_step(Screen(kind=kind, lines=[], hand=None, text=''), mem) == [p.pad('b'), p.pad('b')]


def test_fresh_roster_can_resolve_unknown_spare_and_release_guard():
    mem = memory()
    mem['general_location_unknown'] = ['どうし']
    assert p._source_spare_missing(p._order(mem), mem)
    # Actual re-observation is new evidence; stale unknown must not block forever.
    assert p.deploy_step(generals(['ゼウス', 'どうし']), mem) == [p.pad('a')]
    assert 'どうし' not in (mem.get('general_location_unknown') or ())
    assert not p._source_spare_missing(p._order(mem), mem)


def test_live_spare_allows_dispatch_without_claiming_departure():
    mem = memory()
    assert not p._source_spare_missing(p._order(mem), mem)
    assert p.deploy_step(generals(['ゼウス', 'どうし']), mem) == [p.pad('a')]
    assert mem['garrison']['アルマムーン'] == ['ゼウス', 'どうし']
    assert not mem.get('sorties')


def test_second_adjusted_attack_cannot_drain_source_after_departure(monkeypatch):
    mem = memory('A:guard001:J2')
    p._garrison_move(mem, 'どうし', source='アルマムーン')
    mem['sorties'] = {
        'A:guard001:J1': {'general': 'どうし', 'target': 'ジョンリギ',
                         'status': 'en_route', 'tick': 99},
    }
    monkeypatch.setattr(p.chart, 'orders', lambda _chapter: ())
    assert p.next_order(mem) is None
    assert not p._plan_pending(mem)


def test_guard_uses_observed_source_override():
    mem = memory()
    order = p._order(mem)
    mem['source_override'] = {order['step']: 'カストーラ'}
    assert p._source_spare_missing(order, mem)
    mem['garrison']['カストーラ'].append('ココット')
    assert not p._source_spare_missing(order, mem)


def test_unread_garrison_is_not_invented_but_live_list_still_guards():
    mem = memory()
    del mem['garrison']['アルマムーン']
    assert not p._source_spare_missing(p._order(mem), mem)
    assert p.deploy_step(generals(['ゼウス']), mem) == [p.pad('b'), p.pad('b')]


def test_unread_live_list_never_confirms():
    mem = memory()
    actions = p.deploy_step(Screen(kind='general_list', lines=[], hand=None, text=''), mem)
    assert not any('a' in action.get('buttons', ()) for action in actions)
    assert mem['active'] == 'A:guard001:J1'


def test_unconfirmed_issued_sortie_is_reconciled_not_cancelled():
    mem = memory()
    order = p._order(mem)
    mem['garrison']['アルマムーン'] = ['ゼウス']
    mem['orders'][order['step']] = 'launched_unconfirmed'
    assert not p._source_spare_missing(order, mem)


@pytest.mark.parametrize('step', ['A:guard001:J1', '1-B1'])
@pytest.mark.parametrize('purpose', [None, 'attack', 'retake'])
def test_boss_waves_remain_exempt_even_if_recorded_lost(step, purpose):
    mem = memory(step)
    order = p._order(mem)
    order['target'] = p.chart.boss_castle(mem['chapter'])
    order['purpose'] = purpose
    mem['lost'] = [order['target']]
    mem['garrison']['アルマムーン'] = ['ゼウス']
    assert not p._reserve_source_guard(order, mem)
    assert not p._source_spare_missing(order, mem)


def test_original_non_recapture_chart_keeps_its_attack_sequence():
    mem = memory('1-A2')
    mem['garrison']['アルマムーン'] = ['ゼウス']
    assert not p._reserve_source_guard(p._order(mem), mem)


def test_original_chart_recapture_still_requires_a_spare():
    mem = memory('1-A2')
    order = p._order(mem)
    mem['lost'] = [order['target']]
    mem['garrison']['アルマムーン'] = ['ゼウス']
    assert p._source_spare_missing(order, mem)


@pytest.mark.parametrize('step', ['A:guard001:J1', 'I:guard001:1'])
def test_staffing_moves_do_not_acquire_attack_guard(step):
    mem = memory(step)
    order = p._order(mem)
    order.update(purpose='move', target='カストーラ')
    assert not p._reserve_source_guard(order, mem)
