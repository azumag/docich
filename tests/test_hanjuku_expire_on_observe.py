"""A stale location mark must expire while the bot is not planning zorties.

g1163 2026-11-11 23:15-00:05: the run spent an hour in battles and the month
menu, the corridor planner never ran, and 29 of the 32 living generals stayed
in ``general_location_unknown``. The release belongs to the observation, not to
one planner.
"""
from docich.hanjuku import orchestrator as O
from docich.hanjuku.os_layer.world import LOCATION_UNKNOWN_TTL
from docich.hanjuku.strategy import corridor


def memory(unknown, at, tick):
    return {'chapter': 9, 'tick': tick, 'month': '22-9',
            'policy': {'chapter': 9, 'tick': tick, 'month': '22-9',
                       'world': {'general_location_unknown': list(unknown),
                                 'general_location_unknown_at': dict(at)}}}


def test_a_stale_mark_is_released_before_any_sortie_is_planned(monkeypatch):
    mem = memory(['バトウラ', 'リーキ'], {'バトウラ': 100, 'リーキ': 900}, 1000)
    # Whatever the screen is, the observation releases the expired general.
    released = []
    monkeypatch.setattr(O, '_decide_impl',
                        lambda frame, state, **kw: (released.append(
                            state['policy']['world']['general_location_unknown'][:]) or ([], state)))
    O.decide(object(), {'policy': mem['policy']})
    world = mem['policy']['world']
    assert world['general_location_unknown'] == ['リーキ']
    assert 'バトウラ' not in world['general_location_unknown_at']


def test_the_planner_call_site_still_releases(monkeypatch):
    mem = {'chapter': 9, 'tick': 1000 + LOCATION_UNKNOWN_TTL,
           'month': '22-9', 'world': {'general_location_unknown': ['バトウラ'],
                                      'general_location_unknown_at': {'バトウラ': 1000}}}
    monkeypatch.setattr(corridor, 'mechanical', lambda mem: True)
    monkeypatch.setattr(corridor.chart, 'corridor_next', lambda *a: 'オシロガ')
    corridor.orders(mem)
    assert mem['world']['general_location_unknown'] == []
