"""Identical decisions are not recorded twice (g1133 2026-11-05).

The Y view is read on every picker step and a mechanical plan is rebuilt
whenever its orders stop being runnable; both used to record the same
decision every observation (204 owners records and 17 identical plan
records in half an hour), hiding real changes in the decision log.

The policy is persisted as JSON between observations, so the dedupe
signature has to survive that round trip: a tuple reloads as a list and
never compares equal again (v399 measured 24 chart_adjust_applied records
in 11 minutes, five of them the very same plan).
"""
import json

from docich import hanjuku_policy as p
from docich.hanjuku.strategy import replan


def records(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def reloaded(mem):
    """The policy as the next observation reads it back from disk."""
    return json.loads(json.dumps(mem))


def test_the_same_world_flag_set_is_recorded_once():
    mem = {'chapter': 6, 'tick': 100, '_records': [], 'captured': []}
    flags = {'アルマムーン': 'own', 'アブダレ': 'enemy'}
    p._apply_world_flags(mem, dict(flags))
    p._apply_world_flags(mem, dict(flags))
    assert len(records(mem, 'world_map_owners')) == 1
    p._apply_world_flags(mem, {'アルマムーン': 'own', 'アブダレ': 'own'})
    assert len(records(mem, 'world_map_owners')) == 2


def test_the_world_flag_dedupe_survives_the_json_round_trip():
    mem = {'chapter': 6, 'tick': 100, '_records': [], 'captured': []}
    flags = {'アルマムーン': 'own', 'アブダレ': 'enemy'}
    p._apply_world_flags(mem, dict(flags))
    mem = reloaded(mem)
    p._apply_world_flags(mem, dict(flags))
    assert len(records(mem, 'world_map_owners')) == 1


def test_the_same_adjusted_plan_is_recorded_once():
    mem = {'chapter': 6, 'tick': 100, '_records': []}
    doc = {'orders': [{'step': 'M1', 'general': 'ゼウス', 'source': 'アルマムーン',
                       'target': 'カバルッサ', 'cards': [], 'after': None}],
           'source': 'mechanical'}
    replan._adopt_plan(mem, doc, 'abcd1234')
    replan._adopt_plan(mem, doc, 'abcd1234')
    assert len(records(mem, 'chart_adjust_applied')) == 1


def test_the_plan_dedupe_survives_the_json_round_trip():
    mem = {'chapter': 6, 'tick': 100, '_records': []}
    doc = {'orders': [{'step': 'M1', 'general': 'ゼウス', 'source': 'アルマムーン',
                       'target': 'カバルッサ', 'cards': [], 'after': None}],
           'source': 'mechanical'}
    replan._adopt_plan(mem, doc, 'abcd1234')
    mem = reloaded(mem)
    replan._adopt_plan(mem, doc, 'abcd1234')
    assert len(records(mem, 'chart_adjust_applied')) == 1


def test_a_changed_plan_is_still_recorded_after_the_round_trip():
    mem = {'chapter': 6, 'tick': 100, '_records': []}
    first = {'orders': [{'step': 'M1', 'general': 'ゼウス', 'source': 'アルマムーン',
                         'target': 'カバルッサ', 'cards': [], 'after': None}],
             'source': 'mechanical'}
    second = {'orders': [{'step': 'M2', 'general': 'ライム', 'source': 'グルーポフ',
                          'target': 'オケアナ', 'cards': [], 'after': None}],
              'source': 'mechanical'}
    replan._adopt_plan(mem, first, 'abcd1234')
    mem = reloaded(mem)
    replan._adopt_plan(mem, second, 'abcd1234')
    assert len(records(mem, 'chart_adjust_applied')) == 2
