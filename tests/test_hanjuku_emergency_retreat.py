"""An attack menu with no attack command and no resources retreats once.

g1190 chapter 9 2026-11-16: きりふだ/たいきゃく only, cards exhausted, the
chapter-9 no-retreat rule left no legal action and the battle froze.
"""
from docich.hanjuku.flows.battle_menus import _emergency_retreat_due, _hero_retreat_menu
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen


def menu(rows=('きりふだ', 'たいきゃく')):
    lines = [TextLine(176 + 16 * i, tuple((176 + 8 * j, ch) for j, ch in enumerate(word)))
             for i, word in enumerate(rows)]
    return Screen(lines, None, ''.join(rows), kind='battle_menu')


def battle(**extra):
    cur = {'side': 'attack', 'ally': 'ブレッツェル', 'enemy': 'オレガノ',
           'castle': 'カラ', 'ally_hp': 40, 'enemy_hp': 29,
           'survival': {'cards_exhausted': True}}
    cur.update(extra)
    return cur


def memory(cur):
    return {'chapter': 9, 'battle': cur, '_records': []}


def test_the_exhausted_attack_menu_is_due_and_skips_the_forbidden_note():
    cur = battle()
    mem = memory(cur)
    assert _emergency_retreat_due(menu(), mem, cur) is True
    assert _hero_retreat_menu(menu(), mem, cur) is not None
    assert not any(r.get('decision') == 'battle_retreat_forbidden' for r in mem['_records'])


def test_a_menu_with_an_attack_command_is_not_due():
    cur = battle()
    assert _emergency_retreat_due(menu(('こうげき', 'たいきゃく')), memory(cur), cur) is False


def test_the_emergency_is_used_once_per_battle():
    cur = battle(emergency_retreat_used=True)
    assert _emergency_retreat_due(menu(), memory(cur), cur) is False
