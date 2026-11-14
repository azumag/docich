"""A battle duel is declined when our general's live HP is behind.

Owner report 2026-11-15: the bot lost the duel against グリッシーニ every time
and never used cards or eggs. The battle record's current HP panels decide;
an unread battle keeps the owner's 2026-09-25 accept rule.
"""
from test_hanjuku_chart_bot import Canvas

from docich.hanjuku.os_layer.screen import parse
from docich import hanjuku_policy as policy


def duel_screen(hand_y=177):
    c = Canvas()
    c.text(24, 183, 'いって ごあいて ねがえぬか?')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, hand_y)
    return parse(c.frame())


def test_the_heros_losing_duel_is_declined_so_cards_and_eggs_are_used():
    mem = {'battle': {'ally': 'どうし', 'enemy': 'グリッシーニ',
                      'ally_hp': 20, 'enemy_hp': 60}}
    actions = policy.yes_no_step(duel_screen(193), mem)
    assert actions[0]['buttons'] == ['a']
    record = mem['_records'][-1]
    assert record['strategy_variant'] == 'decline_duel_hp_disadvantage'
    assert record['observed_metric']['ally_hp'] == 20
    assert record['observed_metric']['enemy_hp'] == 60


def test_a_general_duel_is_declined_even_at_equal_hp():
    mem = {'battle': {'ally': 'ラズベリー', 'enemy': 'グリッシーニ',
                      'ally_hp': 80, 'enemy_hp': 40}}
    assert policy.yes_no_step(duel_screen(193), mem)[0]['buttons'] == ['a']
    assert mem['_records'][-1]['strategy_variant'] == 'decline_duel_general'


def test_the_heros_equal_or_better_duel_is_accepted():
    for ally_hp, enemy_hp in ((60, 60), (80, 40)):
        mem = {'battle': {'ally': 'どうし', 'enemy': 'グリッシーニ',
                          'ally_hp': ally_hp, 'enemy_hp': enemy_hp}}
        assert policy.yes_no_step(duel_screen(), mem)[0]['buttons'] == ['a']
        assert mem['_records'][-1]['strategy_variant'] == 'accept_duel'


def test_an_unread_battle_keeps_the_owner_accept_rule():
    mem = {}
    assert policy.yes_no_step(duel_screen(), mem)[0]['buttons'] == ['a']
    assert mem['_records'][-1]['strategy_variant'] == 'accept_duel'
