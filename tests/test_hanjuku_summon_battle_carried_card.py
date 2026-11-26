"""docich#5600: 召喚獣戦では希少戦術が無くても携行札を開いて使う。"""
from types import SimpleNamespace

from docich.hanjuku.flows import battle_menus as menus


def test_a_carried_card_opens_the_card_row_in_a_summon_battle(monkeypatch):
    events = []
    monkeypatch.setattr(menus, '_record', lambda mem, event, **kw: events.append((event, kw)))
    monkeypatch.setattr(menus, '_migrate_card_evidence', lambda mem: None)
    monkeypatch.setattr(menus, '_options', lambda screen: [])
    monkeypatch.setattr(menus, '_human_scroll_owns', lambda *a, **kw: False)
    monkeypatch.setattr(menus, '_screen_egg_empty', lambda *a, **kw: False)
    monkeypatch.setattr(menus, '_card_battle_reading', lambda *a, **kw: None)
    monkeypatch.setattr(menus, '_white_egg_available', lambda screen: False)
    monkeypatch.setattr(menus, '_available_rare_tactic', lambda mem, cur: None)
    monkeypatch.setattr(menus, '_carried_card_left', lambda mem, cur: 'バグストーム')
    monkeypatch.setattr(menus, '_battle_menu_move', lambda screen, mem, label, **kw: 'here')
    monkeypatch.setattr(menus, '_survival_needed', lambda cur: False)

    screen = SimpleNamespace(kind='battle_menu', text='たまごをつかう きりふだ たいきゃく',
                             menu_rows=[], menu_cursor=176, egg_rows=[],
                             disabled_item_box=False, hidden_battle_commands=[],
                             frame_sha256=None,
                             has=lambda word: word in ('たまごをつかう', 'きりふだ', 'たいきゃく'))
    mem = {'chapter': 2, 'battle': {'ally': 'ヘルメス', 'enemy': 'エシャロット',
                                    'egg_battle': True, 'planned_cards': ['バグストーム'],
                                    'enemy_summon_observed': True}}
    actions = menus.battle_menu_step(screen, mem)

    assert actions == [menus.pad('a')]
    assert mem['battle']['card_flow']['card'] == 'バグストーム'
    assert any(name == 'battle_card' and kw.get('card') == 'バグストーム'
               for name, kw in events)
