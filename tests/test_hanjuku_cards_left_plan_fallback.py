"""owner 2026-11-26: 出撃確定の binding が無くても計画札から未使用を検出する。"""
from docich.hanjuku.flows import battle as battle_flow


def test_the_plan_cards_are_used_when_no_confirmed_kit_exists():
    mem = {'plan': {'chart_plan': {'orders': [{
        'step': 'A:1:M1', 'purpose': 'attack', 'source': 'S', 'target': 'タタール',
        'general': 'クランベリー', 'cards': ['ミックミー']}]}}}
    cur = {'step': 'A:1:M1', 'ally': 'クランベリー', 'enemy': 'エシャロット'}
    assert battle_flow._leftover_cards(mem, cur) == ['ミックミー']


def test_a_used_plan_card_is_not_left():
    mem = {'plan': {'chart_plan': {'orders': [{
        'step': 'A:1:M1', 'purpose': 'attack', 'source': 'S', 'target': 'タタール',
        'general': 'クランベリー', 'cards': ['ミックミー']}]}}}
    cur = {'step': 'A:1:M1', 'ally': 'クランベリー', 'enemy': 'エシャロット',
           'cards_used': ['ミックミー']}
    assert battle_flow._leftover_cards(mem, cur) == []
