"""v305: an off-chart order never leaves the hero's castle thin.

Owner (2026-10-29): 主人公のいる城が手薄になるのはよくない、主人公がやられたら
ゲームオーバー. g1075 16:26:36 sent ラズベリー from アルマムーン
(どうし, ラズベリー, タピオカ) to retake パタゴーヌ, leaving the hero with one
general, and the next interim order would have taken タピオカ too.
"""
import pytest

from docich import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust
from docich import hanjuku_policy as policy
from docich.hanjuku.state.regions import world as _world

HERO = chart.HERO


@pytest.mark.parametrize('names,general,short', [
    ([HERO, 'ラズベリー', 'タピオカ'], 'ラズベリー', True),
    ([HERO, 'ラズベリー', 'タピオカ', 'ミルフィーユ'], 'ラズベリー', False),
    ([HERO, 'タピオカ'], 'タピオカ', True),
    ([HERO, 'ラズベリー', 'タピオカ'], HERO, False),          # the hero leaving is another rule
    (['ラズベリー', 'タピオカ'], 'ラズベリー', False),          # not the hero's castle
    ([HERO, 'ラズベリー', 'タピオカ', 'ミルフィーユ'], None, False),  # a spare picked on the list
    ([HERO, 'ラズベリー', 'タピオカ'], None, True),
])
def test_hero_guard_counts_the_others_who_stay(names, general, short):
    assert chart_adjust.hero_guard_short(names, general) is short


def test_a_marching_general_does_not_guard_the_hero():
    names = [HERO, 'ラズベリー', 'タピオカ', 'ミルフィーユ']
    assert chart_adjust.hero_guard_short(names, 'ラズベリー', {'ミルフィーユ'}) is True


def _order(general, source='アルマムーン', target='ジョンリギ'):
    return {'step': 'A:x:1', 'general': general, 'source': source, 'target': target}


def test_startable_refuses_an_order_that_thins_the_hero_castle():
    request = {'garrison': {'アルマムーン': [HERO, 'ゼウス', 'アルテミス']}, 'en_route': []}
    assert chart_adjust.startable(_order('ゼウス'), request) == 'hero_castle_guard'
    request['garrison']['アルマムーン'].append('クミン')
    assert chart_adjust.startable(_order('ゼウス'), request) is None
    request['en_route'] = [{'general': 'クミン'}]
    assert chart_adjust.startable(_order('ゼウス'), request) == 'hero_castle_guard'


def _retake_memory(home):
    return {'chapter': 1, 'tick': 100, 'orders': {}, '_records': [],
            'captured': ['カストーラ', 'キカンドン'], 'lost': ['ジョンリギ'],
            'garrison': {'アルマムーン': home, 'カストーラ': ['ヴィーナス'], 'キカンドン': ['ココット']}}


def test_interim_retake_never_takes_a_guard_from_the_hero():
    mem = _retake_memory([HERO, 'ゼウス', 'アルテミス'])
    assert all(o['source'] != 'アルマムーン' for o in policy.interim_candidates(mem).values())
    # The hero is not sent instead while his guards stay.
    assert all(o['general'] != HERO for o in policy.interim_candidates(mem).values())


def test_interim_retake_takes_a_spare_when_two_others_stay():
    mem = _retake_memory([HERO, 'ゼウス', 'アルテミス', 'クミン'])
    retake = [o for o in policy.interim_candidates(mem).values()
              if o['purpose'] == 'retake' and o['target'] == 'ジョンリギ']
    assert retake and (retake[0]['source'], retake[0]['general']) == ('アルマムーン', 'ゼウス')


def test_a_lone_hero_is_never_sent_from_the_only_castle():
    # Owner 2026-11-06: an emptied castle falls to the next enemy; the guard
    # is never lifted, not even for our only castle.
    mem = _retake_memory([HERO])
    assert policy._interim_source(mem, 'ジョンリギ', None, {'アルマムーン'}, set(),
                                  allow_sole=True) is None


# Owner (2026-10-29): 自城が一つだけのときは、攻める手立てがなくなってしまうため、
# しゅじんこう一人になるのを許容する.

def test_the_guard_still_applies_to_our_only_castle():
    assert chart_adjust.hero_guard_short([HERO, 'タピオカ'], 'タピオカ', sole_castle=True) is True


def test_startable_never_lifts_the_guard_for_a_single_castle():
    home = chart.home_castle(1)
    request = {'chapter': 1, 'captured': [], 'home_lost': False,
               'garrison': {home: [HERO, 'ゼウス']}, 'en_route': []}
    assert chart_adjust.startable(_order('ゼウス', source=home), request) == 'hero_castle_guard'
    request['captured'] = ['キカンドン']
    assert chart_adjust.startable(_order('ゼウス', source=home), request) == 'hero_castle_guard'
    assert chart_adjust.startable(_order('ゼウス', source=home),
                                  {**request, 'owned': [home]}) == 'hero_castle_guard'


def test_interim_from_our_only_castle_keeps_the_spare():
    home = chart.home_castle(1)
    mem = {'chapter': 1, 'tick': 100, 'orders': {}, '_records': [], 'captured': [],
           'garrison': {home: [HERO, 'ゼウス']}}
    assert not [o for o in policy.interim_candidates(mem).values() if o['source'] == home]
    mem['captured'] = ['キカンドン']
    _world(mem)['garrison']['キカンドン'] = ['ココット']
    assert all(o['source'] != home or o['general'] == policy.MOVE_ANY_GENERAL
               for o in policy.interim_candidates(mem).values())


def _plan_memory(present, general='ゼウス', owned=('アルマムーン', 'ジョンリギ')):
    chapter = 1
    home = chart.home_castle(chapter)
    return {'chapter': chapter, 'tick': 100, 'orders': {}, '_records': [],
            'captured': [c for c in owned],
            'lost': [],
            'world': {'garrison': {home: list(present)}},
            'chart_plan': {'orders': [{'step': 'A:x:1', 'general': general,
                                       'source': home, 'target': 'ジョンリギ'}]}}


@pytest.mark.parametrize('present,general,short', [
    ([HERO, 'ゼウス', 'ピオーネ'], 'ゼウス', True),            # hero + 2: leaving one leaves 1
    ([HERO, 'ゼウス', 'ピオーネ', 'ミント'], 'ゼウス', False),   # hero + 3 keeps the guard
    ([HERO, 'ゼウス', 'ピオーネ'], HERO, False),               # the hero leaving is another rule
])
def test_the_planner_does_not_start_a_sortie_the_hero_guard_would_cancel(
        present, general, short):
    from docich.hanjuku.strategy.orders import _source_spare_missing
    mem = _plan_memory(present, general)
    assert _source_spare_missing(mem['chart_plan']['orders'][0], mem, allow_probe=True) is short


def test_another_castle_keeps_its_own_spare_rule():
    from docich.hanjuku.strategy.orders import _source_spare_missing
    mem = _plan_memory([HERO, 'ゼウス', 'ピオーネ'])
    mem['world']['garrison']['ジョンリギ'] = ['タピオカ', 'クミン']
    order = {'step': 'A:x:2', 'general': 'タピオカ', 'source': 'ジョンリギ', 'target': 'カストーラ'}
    assert _source_spare_missing(order, mem, allow_probe=True) is False
