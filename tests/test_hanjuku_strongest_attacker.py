"""The strongest measured spare attacks a castle first.

g1163 chapter 5 (2026-11-09): プトレマイス was attacked five times with
expiring weak generals (ビシソワーズ 27, セミヨン 36) while カルーン held
ポセイドン 85. The garrison list is the game's roster order, not strength.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan as p
from docich.hanjuku.state.regions import world as _world


def statuses(mem, rows):
    mem['general_statuses'] = {'statuses': {g: {'max_hp': hp} for g, hp in rows.items()}}
    return mem


def test_known_max_hp_orders_the_spares_strongest_first():
    mem = statuses({}, {'ポセイドン': 85, 'ビシソワーズ': 27, 'セミヨン': 36})
    assert p._strongest_first(mem, ['ビシソワーズ', 'セミヨン', 'ポセイドン']) == [
        'ポセイドン', 'セミヨン', 'ビシソワーズ']
    # An unread max HP keeps its list position after the measured ones.
    assert p._strongest_first(mem, ['ミルフィーユ', 'ビシソワーズ']) == [
        'ビシソワーズ', 'ミルフィーユ']
    assert p._strongest_first({'general_statuses': None}, ['b', 'a']) == ['b', 'a']


def memory():
    chapter = 1
    home = chart.home_castle(chapter)
    mem = {'chapter': chapter, 'month': '4-2', 'tick': 17900, '_records': [],
           'captured': [home], 'lost': [],
           'garrison': {home: ['ビシソワーズ', 'ポセイドン', 'ミルフィーユ']},
           'house_eggs': {}}
    return statuses(mem, {'ビシソワーズ': 27, 'ポセイドン': 85})


def test_the_interim_attack_sends_the_strongest_spare():
    mem = memory()
    home = chart.home_castle(1)
    source, general = p._interim_source(mem, 'スペンソニア', None, p._owned(mem), set())
    assert source == home
    assert general == 'ポセイドン'


def test_the_choice_keeps_the_hero_castle_guard():
    mem = memory()
    _world(mem)['garrison'][chart.home_castle(1)] = ['どうし', 'ビシソワーズ', 'ポセイドン']
    # hero + 2 others: sending one leaves only one beside the hero, so none is sent.
    assert p._interim_source(mem, 'スペンソニア', None, p._owned(mem), set()) is None
    _world(mem)['garrison'][chart.home_castle(1)].append('ミルフィーユ')
    source, general = p._interim_source(mem, 'スペンソニア', None, p._owned(mem), set())
    assert general == 'ポセイドン'


def test_current_hp_outranks_max_hp_and_zero_hp_goes_last():
    """g1163 chapter 7 2026-11-09: hp 1/49 and hp 10/30 were sent and lost."""
    mem = statuses({'general_statuses': {'statuses': {
        'アルテミス': {'hp': 1, 'max_hp': 49},
        'リーキ': {'hp': 10, 'max_hp': 30},
        'ポセイドン': {'hp': 40, 'max_hp': 85},
        'ゼウス': {'hp': 0, 'max_hp': 99}}}}, {})
    mem['general_statuses']['statuses'] = {
        'アルテミス': {'hp': 1, 'max_hp': 49},
        'リーキ': {'hp': 10, 'max_hp': 30},
        'ポセイドン': {'hp': 40, 'max_hp': 85},
        'ゼウス': {'hp': 0, 'max_hp': 99}}
    assert p._strongest_first(mem, ['アルテミス', 'ゼウス', 'リーキ', 'ポセイドン']) == [
        'ポセイドン', 'リーキ', 'アルテミス', 'ゼウス']
