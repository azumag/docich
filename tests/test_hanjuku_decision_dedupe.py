"""Identical decisions are not recorded twice (g1133 2026-11-05).

The Y view is read on every picker step and a mechanical plan is rebuilt
whenever its orders stop being runnable; both used to record the same
decision every observation (204 owners records and 17 identical plan
records in half an hour), hiding real changes in the decision log.
"""
from docich import hanjuku_policy as p
from docich.hanjuku.strategy import replan


def records(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def test_the_same_world_flag_set_is_recorded_once():
    mem = {'chapter': 6, 'tick': 100, '_records': [], 'captured': []}
    flags = {'アルマムーン': 'own', 'アブダレ': 'enemy'}
    p._apply_world_flags(mem, dict(flags))
    p._apply_world_flags(mem, dict(flags))
    assert len(records(mem, 'world_map_owners')) == 1
    p._apply_world_flags(mem, {'アルマムーン': 'own', 'アブダレ': 'own'})
    assert len(records(mem, 'world_map_owners')) == 2


def test_the_same_adjusted_plan_is_recorded_once():
    mem = {'chapter': 6, 'tick': 100, '_records': []}
    doc = {'orders': [{'step': 'M1', 'general': 'ゼウス', 'source': 'アルマムーン',
                       'target': 'カバルッサ', 'cards': [], 'after': None}],
           'source': 'mechanical'}
    replan._adopt_plan(mem, doc, 'abcd1234')
    replan._adopt_plan(mem, doc, 'abcd1234')
    assert len(records(mem, 'chart_adjust_applied')) == 1
