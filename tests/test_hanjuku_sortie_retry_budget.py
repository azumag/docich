"""A fresh sortie retries UI recovery; an existing attempt keeps its bounds.

Synthetic chapter-eight observations reproduce state carried across retries.
They do not establish the cause or frequency of a particular native failure.
"""
from copy import deepcopy

import pytest

from docich.hanjuku.flows import map as field
from docich import hanjuku_policy as p
from docich.hanjuku.os_layer.screen import Screen
from test_hanjuku_sortie_evidence import menu


@pytest.fixture(params=['ながはま', 'いちじょうだに'])
def case(request):
    order = dict(step='retry-sortie', source=request.param, target='しまばら',
                 general='ゼウス', cards=[], after=None, note='retry regression')
    mem = dict(chapter=8, tick=100, active=None, orders={order['step']: 'pending'},
               captured=[request.param], chart_plan={'source': 'test', 'orders': [order]},
               garrison={request.param: ['ゼウス', 'ヴィーナス']}, picked=[], _records=[])
    for key, spent in [('card_unreadable', 2), ('card_skip_departure', 4),
                       ('sortie_confirm_miss', 2), ('sortie_confirm_blind', True)]:
        mem[key] = {order['step']: spent, 'other-step': spent}
    return order, mem


def start(order, mem, monkeypatch):
    # Exercise the actual order-start boundary; navigation is outside this test.
    monkeypatch.setattr(field, 'next_order', lambda _: order)
    monkeypatch.setattr(field, '_measured_goal', lambda *args: None)
    assert field.map_step(Screen([], None, '', kind='map'), mem, None) == []
    assert mem['active'] == order['step']


def test_retry_gets_the_first_card_backout_instead_of_immediate_cancellation(case, monkeypatch):
    order, mem = case
    others = {key: values['other-step'] for key, values in mem.items()
              if isinstance(values, dict) and 'other-step' in values}
    start(order, mem, monkeypatch)
    for key, value in others.items():
        assert mem[key]['other-step'] == value
    panel = menu('card_select', ['クースカン1'])
    for _ in range(p.NORMAL_CARD_UNREADABLE_LIMIT - 1):
        assert p.deploy_step(panel, mem) == []
    assert p.deploy_step(panel, mem) == [p.pad('b')]
    assert mem['card_skip_departure'][order['step']] == 1
    assert mem['orders'][order['step']] == 'pending'


def test_retry_can_approve_once_but_not_repeat_forever(case, monkeypatch):
    order, mem = case
    start(order, mem, monkeypatch)
    panel = menu('sortie_confirm', ['うむッ!'], hand=False)
    for _ in range(p.SORTIE_CONFIRM_UNREADABLE_LIMIT - 1):
        assert p.deploy_step(panel, mem) == []
    assert p.deploy_step(panel, mem) == [p.pad('a'), {'type': 'wait', 'ms': 150}, p.pad('a')]
    for _ in range(p.SORTIE_CONFIRM_UNREADABLE_LIMIT - 1):
        assert p.deploy_step(panel, mem) == []
    assert p.deploy_step(panel, mem) == [p.pad('b'), p.pad('b')]
    assert mem['orders'][order['step']] == 'failed'


def test_continuing_an_active_attempt_does_not_rearm_recovery(case, monkeypatch):
    order, mem = case
    mem['active'] = order['step']
    before = deepcopy({k: mem[k] for k in ('card_unreadable', 'card_skip_departure',
                                           'sortie_confirm_miss', 'sortie_confirm_blind')})
    start(order, mem, monkeypatch)
    assert {k: mem[k] for k in before} == before


def test_unconfirmed_departure_probe_is_not_a_new_sortie(case, monkeypatch):
    order, mem = case
    mem['orders'][order['step']] = 'launched_unconfirmed'
    before = deepcopy({k: mem[k] for k in ('card_unreadable', 'card_skip_departure',
                                           'sortie_confirm_miss', 'sortie_confirm_blind')})
    start(order, mem, monkeypatch)
    assert {k: mem[k] for k in before} == before
