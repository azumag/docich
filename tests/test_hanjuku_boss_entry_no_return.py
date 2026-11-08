"""A measured boss entry must never be cancelled by the egg-retreat B route.

g1133 2026-11-06: ヘラ (04:39), アマンディーヌ (04:43) and パプリカ (04:48)
entered the boss castle, pressed the bounded B return at the battle menu, and
ended at the main menu with battle_entry_expired. The boss fight never
started, so no damage could be measured.
"""
from docich.hanjuku.flows import battle_menus as B
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import Screen


def memory(castle):
    return {'tick': 100, '_records': [], 'battle': {},
            'attack': {'castle': castle, 'general': 'ヘラ', 'side': 'attack', 'step': 'I:x'}}


def test_a_boss_entry_never_takes_the_b_return():
    mem = memory('ボス')
    battle = {'ally': 'ヘラ', 'enemy': 'アポロン', 'castle': 'ボス',
              'side': 'attack', 'step': 'I:x'}
    assert B._egg_retreat_return(
        Screen([], None, '', kind='egg_battle_menu'), mem, battle) is None
    assert not any(r['decision'] == 'battle_egg_retreat_attempt' for r in mem['_records'])
    assert any(r['decision'] == 'boss_battle_return_held' for r in mem['_records'])
    assert battle.get('egg_retreat_attempts') is None


def test_a_normal_castle_keeps_the_bounded_return():
    mem = memory('オケアナ')
    battle = {'ally': 'ヘラ', 'enemy': 'アポロン', 'castle': 'オケアナ',
              'side': 'attack', 'step': 'I:x'}
    assert B._egg_retreat_return(
        Screen([], None, '', kind='egg_battle_menu'), mem, battle) == [pad('b')]
    assert battle['egg_retreat_attempts'] == 1
