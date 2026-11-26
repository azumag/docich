"""Regression coverage for selected card flows interrupted outside the list-open path."""
from docich import hanjuku_policy as p
from docich.hanjuku.flows import battle as battle_flow
from docich.hanjuku.flows import battle_menus as menus

from test_hanjuku_card_damage_gate import cards, memory


def _interrupted_memory(selected=True):
    mem = memory(enemy_hp=32, soldiers=(0, 0))
    cur = mem['battle']
    if selected:
        cur['cards_selected'].append('ゼンマイン')
    cur['card_flow'].update(stage='list', selection_planned=False,
                            announce_retried=selected)
    return mem


def _unclassified(mem):
    return next(r for r in reversed(mem['_records'])
                if r['decision'] == 'battle_card_unclassified')


def test_gate_close_books_a_selected_interrupted_flow():
    mem = _interrupted_memory()
    cur = mem['battle']

    assert battle_flow._card_gate_close(mem, cur, ['ゼンマイン']) == [p.pad('b')]

    assert cur['card_flow'] is None
    assert cur['cards_unclassified'] == ['ゼンマイン']
    assert _unclassified(mem)['observed_metric']['selected_interrupted'] is True


def test_gate_close_before_selection_keeps_the_kit_clean():
    mem = _interrupted_memory(selected=False)
    cur = mem['battle']

    assert battle_flow._card_gate_close(mem, cur, ['ゼンマイン']) == [p.pad('b')]

    assert cur['card_flow'] is None
    assert cur['cards_selected'] == cur['cards_unclassified'] == []
    assert not mem.get('kit_spent')
    assert not any(r['decision'] == 'battle_card_unclassified' for r in mem['_records'])


def test_one_hit_close_books_a_selected_interrupted_flow():
    mem = _interrupted_memory()
    cur = mem['battle']
    cur.update(enemy_hp=1, enemy_soldiers=0, card_hp_unread=False,
               card_context_unclassified=False)

    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]

    assert cur['card_flow'] is None
    assert cur['cards_unclassified'] == ['ゼンマイン']
    assert _unclassified(mem)['observed_metric']['selected_interrupted'] is True


def test_retreat_cancel_books_a_selected_interrupted_flow(monkeypatch):
    mem = _interrupted_memory()
    cur = mem['battle']
    cur['card_flow']['announce_retried'] = False
    monkeypatch.setattr(menus, '_one_hit_lethal', lambda cur: False)
    monkeypatch.setattr(menus, '_hp1_resource_hold', lambda cur: False)
    monkeypatch.setattr(menus, '_hero_retreat_needed', lambda cur: True)
    monkeypatch.setattr(menus, '_critical_safe_card', lambda *a, **kw: None)

    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]

    assert cur['card_flow'] is None
    assert cur['cards_unclassified'] == ['ゼンマイン']
    assert cur['card_retreat_cancels'] == 1
    assert _unclassified(mem)['observed_metric']['selected_interrupted'] is True
    assert any(r['decision'] == 'battle_hero_retreat_cancel_card' for r in mem['_records'])
