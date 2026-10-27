"""Boss unlock is a target invariant, including adjusted/old active orders."""
from copy import deepcopy
import json

import pytest

from docich import hanjuku_chart as chart, hanjuku_policy as p
from docich.hanjuku_screen import Screen
from test_hanjuku_sortie_evidence import actor_panel, measured_loaded_sortie


def memory(chapter=1, after=None):
    order = {'step': 'A:locked:J1', 'general': 'ヴィーナス', 'source': chart.home_castle(chapter),
             'target': chart.boss_castle(chapter), 'cards': [], 'after': after, 'note': 'test'}
    mem = {'chapter': chapter, 'tick': 100, 'active': order['step'],
           'chart_plan': {'orders': [order]}, 'orders': {order['step']: 'pending'},
           'captured': [], 'sortie_general': {order['step']: 'ヴィーナス'},
           'order_context': {order['step']: {'actual_general': 'ヴィーナス',
                'observed_metric': {'cards': []}}},
           'sortie_attempt': {'step': order['step'], 'target_seen': True}}
    return order, mem


@pytest.mark.parametrize('chapter', range(1, 13))
@pytest.mark.parametrize('condition', [None, 'source'])
def test_adjusted_boss_target_cannot_bypass_unlock_with_an_unconditional_order(chapter, condition):
    order, mem = memory(chapter)
    if condition == 'source':
        order['after'] = ['captured', order['source']]
        mem['captured'] = [order['source']]
    assert not p._ready(order, mem)
    mem['captured'] = list(set(chart.CASTLE_NAMES[chapter]) - {order['target']})
    # Four distinct chapter-4 sites and a moving chapter-9 boss cannot be
    # unlocked as the legacy single static point, even after ordinary castles.
    assert p._ready(order, json.loads(json.dumps(mem))) is (chapter not in (4, 9))


def test_unsent_hotloaded_boss_marker_cannot_confirm_a_locked_castle(monkeypatch):
    order, mem = memory()
    monkeypatch.setattr(p, 'nav_step', lambda *args: 'arrived')
    monkeypatch.setattr(p, '_target_roof_under_marker', lambda *args: True)
    monkeypatch.setattr(p, '_want_y_jump', lambda *args: False)
    actions = p.target_step(Screen([], None, '', kind='map_target'), mem, None)
    assert p.pad('a') not in actions
    assert mem['active'] is None and mem['orders'][order['step']] == 'pending'
    assert not mem.get('sorties') and not mem.get('sortie_attempt')


def test_unsent_confirmation_is_held_without_changing_a_sent_sortie():
    order, mem = memory(); before = {'general': 'ゼウス', 'status': 'en_route', 'tick': 80}
    mem['sorties'] = {'other': deepcopy(before)}
    p.deploy_step(actor_panel(measured_loaded_sortie(), 'ヴィーナス'), mem)
    assert mem['active'] is None and not mem.get('sortie_attempt')
    assert mem['sorties'] == {'other': before}
    assert mem['_records'][-1]['decision'] == 'boss_target_locked'


def test_complete_unlock_can_be_revoked_by_a_new_enemy_flag():
    order, mem = memory()
    mem['captured'] = list(set(chart.CASTLE_NAMES[1]) - {order['source'], order['target']})
    assert p._ready(order, mem)
    castle = mem['captured'][0]
    p._apply_world_flags(mem, {castle: 'enemy'})
    assert not p._ready(order, mem)


def test_normal_castle_and_move_to_normal_castle_remain_eligible():
    order, mem = memory()
    order['target'] = 'ゴーメン'
    assert p._ready(order, mem)
    order['purpose'] = 'move'
    assert p._ready(order, mem)


@pytest.mark.parametrize('chapter', [None, True, 0, 99])
@pytest.mark.parametrize('target', ['けっかい', 'ボス'])
def test_unknown_chapter_cannot_unlock_either_boss_label(chapter, target):
    order, mem = memory(); order['target'] = target; mem['chapter'] = chapter
    assert not p._ready(order, mem)


def test_pending_retry_does_not_use_an_old_arrival_as_current_unlock():
    from docich.hanjuku.flows.deploy import _hold_locked_boss
    order, mem = memory()
    old = {'general': 'ヴィーナス', 'status': 'arrived', 'tick': 1, 'target': order['target']}
    mem['sorties'] = {order['step']: deepcopy(old)}
    assert _hold_locked_boss(mem, order) == [p.pad('b'), p.pad('b')]
    assert mem['sorties'][order['step']] == old


@pytest.mark.parametrize('status', ['launched', 'launched_unconfirmed'])
def test_already_sent_sortie_keeps_its_reconciliation_state(status):
    from docich.hanjuku.flows.deploy import _hold_locked_boss
    order, mem = memory(); mem['orders'][order['step']] = status
    before = deepcopy(mem)
    assert _hold_locked_boss(mem, order) is None
    assert mem == before
