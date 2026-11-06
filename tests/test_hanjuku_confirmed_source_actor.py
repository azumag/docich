"""A named sortie actor must be verified at its source before a plan is adopted.

Unvisited castles are probed with a non-person placeholder. The real name
is only selected from the live menu; an assumed chart name is never a roster
receipt.
"""
import pytest

from docich import hanjuku_chart as chart
from docich import hanjuku_chart_adjust as adjust
from docich.hanjuku.strategy import replan


HOME = chart.home_castle(1)


def order(general='ゼウス', **extra):
    return {'step': 'J1', 'general': general, 'source': HOME,
            'target': 'キカンドン', 'cards': [], 'after': None, **extra}


@pytest.mark.parametrize('garrison,expected', [
    ({HOME: ['ゼウス', 'ココット']}, None),
    ({HOME: ['ココット']}, 'general_unconfirmed_at_source'),
    ({HOME: []}, 'general_unconfirmed_at_source'),
    ({HOME: None}, 'general_unconfirmed_at_source'),
    ({'ゴーメン': ['ゼウス']}, 'general_elsewhere'),
    ({}, 'general_unconfirmed_at_source'),
])
def test_named_actor_requires_proven_source_membership(garrison, expected):
    assert adjust.startable(order(), {'garrison': garrison, 'en_route': []}) == expected


def test_marching_actor_is_not_plannable_even_with_a_stale_garrison():
    assert adjust.startable(order(), {'garrison': {HOME: ['ゼウス', 'ココット']},
                                      'en_route': [{'general': 'ゼウス'}]}) == 'general_marching'


def test_source_probe_is_not_a_named_sortie_and_survives_validation():
    probe = order('しょうぐん', source_probe=True)
    doc = {'schema': 1, 'chapter': 1, 'request_id': 'a' * 16, 'orders': [probe]}
    validated = adjust.validate(doc)
    assert validated['orders'][0]['source_probe'] is True
    assert adjust.startable(validated['orders'][0], {'garrison': {}, 'en_route': []}) is None
    assert adjust.startable(order('しょうぐん'), {'garrison': {}, 'en_route': []}) == (
        'general_unconfirmed_at_source')


@pytest.mark.parametrize('invalid', [
    order('ゼウス', source_probe=True),
    order('しょうぐん', source_probe=False),
])
def test_probe_flag_cannot_authorize_a_named_actor(invalid):
    doc = {'schema': 1, 'chapter': 1, 'request_id': 'a' * 16, 'orders': [invalid]}
    with pytest.raises(ValueError, match='invalid source_probe'):
        adjust.validate(doc)


def test_unread_chart_source_plans_a_probe_not_the_chart_general(monkeypatch):
    monkeypatch.setattr(replan.routes, 'probe_due', lambda *args, **kwargs: False)
    monkeypatch.setattr(replan.routes, 'arrival_probe_due', lambda *args, **kwargs: False)
    mem = {'chapter': 1, 'garrison': {}, 'orders': {}, 'captured': [], '_records': []}
    picked = replan._interim_source(mem, 'キカンドン',
                                    {'general': 'ゼウス', 'source': HOME},
                                    {HOME}, set())
    assert picked == (HOME, replan.MOVE_ANY_GENERAL)


def test_mechanical_chart_preserves_actor_probe_through_adoption(monkeypatch):
    candidate = {**order('しょうぐん', source_probe=True), 'purpose': 'attack'}
    monkeypatch.setattr(replan, 'interim_candidates',
                        lambda mem, allow_sole=False: {'attack_1': candidate})
    mem = {'chapter': 1, 'tick': 10, 'orders': {}, 'captured': [],
           '_records': [], 'garrison': {}}
    replan._mechanical_chart(mem, {}, 'a' * 16, 'b' * 64)
    adopted = mem['chart_plan']['orders'][0]
    assert adopted['general'] == 'しょうぐん'
    assert adopted['source_probe'] is True
    assert adjust.startable(adopted, {'garrison': {}, 'en_route': []}) is None
