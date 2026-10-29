"""v305: an off-chart order never leaves the hero's castle thin.

Owner (2026-10-29): 主人公のいる城が手薄になるのはよくない、主人公がやられたら
ゲームオーバー. g1075 16:26:36 sent ラズベリー from アルマムーン
(どうし, ラズベリー, タピオカ) to retake パタゴーヌ, leaving the hero with one
general, and the next interim order would have taken タピオカ too.
"""
import pytest

from docich import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust
from docich import hanjuku_policy as policy

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


def test_a_lone_hero_keeps_the_existing_sole_fallback():
    mem = _retake_memory([HERO])
    assert policy._interim_source(mem, 'ジョンリギ', None, {'アルマムーン'}, set(),
                                  allow_sole=True) == ('アルマムーン', HERO)
