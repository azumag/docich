"""owner 2026-11-26: 計画も binding も無くても、出撃記録の携行札を証拠にする。"""
from docich.hanjuku.flows import battle as battle_flow


def test_the_sortie_record_cards_are_used_as_the_last_source():
    mem = {'sorties': {'A:1:M1': {'general': 'クランベリー',
                                    'cards': ['ミックミー', 'ノリウツール']}}}
    cur = {'step': 'A:1:M1', 'ally': 'クランベリー'}
    assert battle_flow._leftover_cards(mem, cur) == ['ミックミー', 'ノリウツール']


def test_a_used_sortie_card_is_not_left():
    mem = {'sorties': {'A:1:M1': {'cards': ['ミックミー']}}}
    cur = {'step': 'A:1:M1', 'cards_used': ['ミックミー']}
    assert battle_flow._leftover_cards(mem, cur) == []
