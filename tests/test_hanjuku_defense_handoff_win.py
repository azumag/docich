"""#2986: a successor defender who finishes the attacker keeps the castle.

g1075-1d5f65e9 2026-10-29 JST: クミン fell at アルマムーン with カシュー at 17
HP. The next defender クランベリー then showed 51 vs カシュー 1, a partial
「ク」 name, and four panels at カシュー 0 (g1075-defense-handoff-1..6.png,
14:20:36-14:20:44). v290 recorded lost:アルマムーン; the 14:24:40 Y map showed
the castle still ours and the lost entry never cleared.
"""
from pathlib import Path

from docich import hanjuku_policy as policy
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku_screen import Battle, Screen

ROOT = Path(__file__).resolve().parents[1]
FRAMES = [ROOT / 'tests' / 'fixtures' / 'hanjuku' / f'g1075-defense-handoff-{n}.png'
          for n in range(1, 7)]
HOME = 'アルマムーン'


def memory():
    return {'chapter': 3, 'tick': 100, 'captured': ['リグア'],
            'garrison': {HOME: ['クミン', 'クランベリー']},
            'battle': {'enemy': 'カシュー', 'ally': 'クミン', 'enemy_hp': 17, 'ally_hp': 0,
                       'start_enemy_hp': 39, 'start_ally_hp': 27, 'castle': HOME,
                       'side': 'defense', 'step': None, 'cards_used': [], 'planned_cards': []}}


def screens():
    out = []
    for path in FRAMES:
        frame = read_png(path)
        out.append(parse(frame, phase=classify(frame)))
    return out


def decisions(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def test_native_panels_read_as_the_successor_finishing_the_attacker():
    readings = [(s.kind, s.battle.ally, s.battle.ally_hp, s.battle.enemy, s.battle.enemy_hp)
                for s in screens()]
    assert readings == [('battle', 'クランベリー', 51, 'カシュー', 1),
                        ('battle', 'ク', 51, 'カシュー', 0),
                        *[('battle', 'クランベリー', 51, 'カシュー', 0)] * 4]


def test_native_handoff_continues_the_defense_and_records_a_win():
    mem = memory()
    for screen in screens():
        policy.battle_step(screen, mem)
        mem['tick'] += 1
    assert (mem['battle']['ally'], mem['battle']['side'], mem['battle']['castle']) == (
        'クランベリー', 'defense', HOME)
    [handoff] = decisions(mem, 'defender_successor')
    assert handoff['previous_ally'] == 'クミン' and handoff['ally'] == 'クランベリー'
    for _ in range(2):
        policy.battle_end(mem, 'map')
    results = [r['resulting_event'] for r in decisions(mem, 'battle_result')]
    assert results == [f'defense_continues:{HOME}', 'win']
    assert not mem.get('lost') and not mem.get('home_lost')
    assert HOME in policy._owned(mem)


def test_one_zero_panel_is_not_enough_to_continue_the_defense():
    mem = memory()
    zero = Screen([], None, '', kind='battle', battle=Battle('カシュー', 0, 'クランベリー', 51))
    policy.battle_step(zero, mem)
    for _ in range(2):
        policy.battle_end(mem, 'map')
    assert not decisions(mem, 'defender_successor')
    assert mem['lost'] == [HOME]


def test_own_home_flag_clears_a_defense_loss_entry_without_home_lost():
    mem = {'chapter': 3, 'tick': 200, 'captured': ['リグア'], 'lost': [HOME]}
    policy._apply_world_flags(mem, {HOME: 'own', 'リグア': 'own'})
    assert mem['lost'] == [] and mem['home_lost'] is False
    assert [r['resulting_event'] for r in decisions(mem, 'castle_owned_observed')] == [
        f'captured:{HOME}']
    assert HOME in policy._owned(mem)


def test_enemy_home_flag_still_marks_the_home_castle_lost():
    mem = {'chapter': 3, 'tick': 200, 'captured': ['リグア'], 'lost': []}
    policy._apply_world_flags(mem, {HOME: 'enemy'})
    assert mem['home_lost'] is True and mem['lost'] == [HOME]
    assert HOME not in policy._owned(mem)
