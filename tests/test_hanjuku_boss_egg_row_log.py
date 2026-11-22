"""A greyed egg row in a boss fight marks the general as unable to summon.

g1163 chapter 6 2026-11-09 13:36: ヘーゼル entered ハードロボ (152 HP), the egg
row read as unavailable, the fight dealt no damage, and the same general was
queued for the next wave.
"""
from types import SimpleNamespace

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import battle_menus as B
from docich.hanjuku.os_layer.screen import Screen

BOSS = chart.boss_castle(6)


def screen(text='たまごをつかうきりふだたいきゃく', kind='egg_battle_menu'):
    value = Screen([], None, text, kind=kind)
    return value


def memory(castle=BOSS, general='ヘーゼル', uses=None):
    mem = {'chapter': 6, 'tick': 100, '_records': [],
           'attack': {'general': general, 'castle': castle, 'side': 'attack', 'step': 'A:x:M1'},
           'battle': {'ally': general, 'enemy': 'ハードロボ', 'step': 'A:x:M1',
                      'castle': castle, 'side': 'attack'},
           'sorties': {'A:x:M1': {'general': general, 'target': castle,
                                  'status': 'arrived', 'tick': 90}}}
    if uses is not None:
        mem['egg_uses'] = {general: uses}
    return mem


def test_a_boss_entry_with_a_grey_egg_row_is_logged_but_not_benched():
    """g1163 2026-11-09 14:55: ペコリーノ's grey row still took 152 -> 70."""
    mem = memory()
    B.egg_battle_step(screen(text='こうげき'), mem)
    assert not (mem.get('egg_uses') or {})
    assert any(r['decision'] == 'egg_row_unavailable' for r in mem['_records'])
    assert not any(r['decision'] == 'egg_unusable_recorded' for r in mem['_records'])


def test_a_normal_castle_battle_never_logs_it():
    mem = memory(castle='セレーネ')
    B.egg_battle_step(screen(text='こうげき'), mem)
    assert not any(r['decision'] == 'egg_row_unavailable' for r in mem['_records'])

def test_a_carried_card_is_opened_before_attacking_a_summon(monkeypatch):
    # Owner 2026-11-22: enemy summon + our general used こうげき while a
    # carried card was still in planned_cards; the card menu was usable.
    mem = memory()
    mem['battle']['planned_cards'] = ['エンジェリン']
    monkeypatch.setattr(B, '_battle_menu_to', lambda *a, **k: (160, 176))
    monkeypatch.setattr(B, '_menu_move_batch', lambda *a, **k: 'here')
    acts = B.egg_battle_step(screen(text='こうげききりふだたいきゃく'), mem)
    assert acts == [B.pad('a')]
    assert mem['battle']['card_flow']['card'] == 'エンジェリン'
    assert any(r['decision'] == 'battle_summon_card_open' for r in mem['_records'])

def test_the_summon_card_row_is_revealed_by_scrolling_down():
    # Owner 2026-11-22: in a summon fight きりふだ sits below たまごをつかう;
    # the command box must scroll down before the card can be opened.
    mem = memory(uses=0)
    mem['battle']['planned_cards'] = ['エンジェリン']
    acts = B.egg_battle_step(screen(text='たまごをつかうおくのて'), mem)
    assert acts == [B.pad('down')]
    assert mem['battle']['summon_card_scroll'] == 1
    assert any(r['decision'] == 'battle_summon_card_scroll' for r in mem['_records'])
