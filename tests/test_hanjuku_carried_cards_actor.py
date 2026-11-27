"""Carried cards follow the actor even when the battle step differs.

Owner 2026-11-15: the castle attack never used the carried cards. The battle's
step can be minted by a retry while the launch snapshot stays under the
original step.
"""
from docich.hanjuku.flows import battle
from docich.hanjuku.judgment import battle as rules


def test_the_actors_launched_cards_are_used_under_another_step(monkeypatch):
    monkeypatch.setattr(battle, '_order_for_step', lambda mem, step: None)
    mem = {'chapter': 9,
           'world': {'launched_orders': {
               'I:c9:サヌト>オシロガ:64': {'general': 'キール',
                                          'cards': ['イッテツーン', 'クースカン'],
                                          'corridor': True, 'confirmation_tick': 5}}}}
    cur = {'step': 'I:c9:サヌト>オシロガ:99', 'ally': 'キール', 'enemy': 'カッペリーニ'}
    tactics = battle._carried_opening_tactics(mem, cur, [])
    assert [t['card'] for t in tactics] == ['イッテツーン', 'クースカン']


def test_a_different_actor_gets_nothing(monkeypatch):
    monkeypatch.setattr(battle, '_order_for_step', lambda mem, step: None)
    mem = {'chapter': 9,
           'world': {'launched_orders': {
               'I:c9:サヌト>オシロガ:64': {'general': 'キール',
                                          'cards': ['イッテツーン'], 'corridor': True}}}}
    cur = {'step': 'I:c9:サヌト>オシロガ:99', 'ally': 'ラズベリー', 'enemy': 'カッペリーニ'}
    assert battle._carried_opening_tactics(mem, cur, []) == []


def test_a_step_less_chain_battle_books_the_snapshot_kit_and_never_replans_it(monkeypatch):
    # docich#5760: an intermediate-castle defender chain ran with step None.
    # The selected-but-unconfirmed card must leave the launched sortie's kit,
    # or the next defender battle re-plans a card that is no longer carried.
    monkeypatch.setattr(battle, '_order_for_step', lambda mem, step: None)
    step = 'A:52b52775:M4499df'
    mem = {'chapter': 5, '_records': [],
           'world': {'launched_orders': {
               step: {'general': 'カシュー', 'cards': ['クースカン'],
                      'corridor': True,
                      'departure_binding': {'confirm_tick': 7}}}}}
    cur = {'step': None, 'ally': 'カシュー', 'enemy': '第一守備将軍',
           'cards_selected': [], 'cards_unclassified': []}
    tactics = battle._carried_opening_tactics(mem, cur, [])
    assert [t['card'] for t in tactics] == ['クースカン']
    assert tactics[0]['kit_step'] == step

    cur['cards_selected'] = ['クースカン']
    cur['card_flow'] = {'card': 'クースカン', 'stage': 'announce',
                        'selection_planned': True}
    rules._card_use_unclassified(mem, cur, '実使用告知がないまま戦闘が終了')
    assert mem['kit_spent'] == {step: ['クースカン']}

    next_battle = {'step': None, 'ally': 'カシュー', 'enemy': 'カシス'}
    assert battle._carried_opening_tactics(mem, next_battle, []) == []


def test_the_newest_empty_launch_snapshot_suppresses_an_older_carded_one(monkeypatch):
    monkeypatch.setattr(battle, '_order_for_step', lambda mem, step: None)
    mem = {'chapter': 5,
           'world': {'launched_orders': {
               'old': {'general': 'カシュー', 'cards': ['クースカン'], 'confirmation_tick': 7},
               'new': {'general': 'カシュー', 'cards': [], 'confirmation_tick': 8}}}}
    cur = {'step': None, 'ally': 'カシュー', 'enemy': 'カシス'}
    assert battle._carried_opening_tactics(mem, cur, []) == []


def test_a_defense_card_never_writes_the_stale_attack_snapshot_ledger():
    mem = {'chapter': 5, '_records': [],
           'world': {'launched_orders': {
               'attack-step': {'general': 'カシュー', 'cards': ['クースカン'],
                               'confirmation_tick': 7}}}}
    cur = {'step': None, 'side': 'defense', 'ally': 'カシュー', 'enemy': '敵将',
           'cards_selected': ['クースカン'], 'cards_unclassified': [],
           'card_flow': {'card': 'クースカン', 'stage': 'announce',
                         'selection_planned': True}}
    rules._card_use_unclassified(mem, cur, '防衛戦の使用告知を確認できない')
    assert not mem.get('kit_spent')
