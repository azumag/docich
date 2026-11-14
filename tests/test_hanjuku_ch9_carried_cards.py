"""A chapter-9 cursor-picked sortie still spends its carried cards.

The plan names nobody ("—") and the departure binding may be absent, so the
carried cards sat unused in castle attacks (owner 2026-11-15). The launched
snapshot holds the measured general and kit.
"""
from docich.hanjuku.flows import battle


def order(general='ヘルメス', cards=('ブラッキー', 'クースカン', 'ファバード')):
    step = 'I:c9:ドンドン>オオキク:1'
    snapshot = {'step': step, 'source': 'ドンドン', 'target': 'オオキク',
                'general': general, 'cards': list(cards), 'corridor': True}
    return step, snapshot


def test_the_launched_snapshot_cards_are_used_when_no_binding_exists(monkeypatch):
    step, snapshot = order()
    monkeypatch.setattr(battle, '_order_for_step', lambda mem, step: snapshot)
    mem = {'chapter': 9}
    cur = {'step': step, 'ally': 'ヘルメス', 'enemy': 'グリッシーニ'}
    tactics = battle._carried_opening_tactics(mem, cur, [])
    assert [t['card'] for t in tactics] == ['ブラッキー', 'クースカン', 'ファバード']
    assert all(t['open'] is True for t in tactics)


def test_a_different_battle_actor_gets_no_carried_card_tactics(monkeypatch):
    step, snapshot = order()
    monkeypatch.setattr(battle, '_order_for_step', lambda mem, step: snapshot)
    mem = {'chapter': 9}
    cur = {'step': step, 'ally': 'ラズベリー', 'enemy': 'グリッシーニ'}
    assert battle._carried_opening_tactics(mem, cur, []) == []
