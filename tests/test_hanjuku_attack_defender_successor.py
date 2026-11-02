"""#3028: a castle's next defender opens a new battle for the same attacker.

g1075-1d5f65e9 2026-10-29 JST: ココット (24) beat フヌイヌム's リースリング at
18:12:58 and the second defender バーミセリ (89) came out. The bot's own
decision frames at 18:13:14 and 18:13:17 (g1075-attack-successor-1/2.png)
show バーミセリ 89 / ココット 0, but v309 opened no battle, judged the old
panel (24 vs 0), recorded captured:フヌイヌム, and treated the castle as ours
until the 18:16:39 Y map showed the enemy flag.
"""
from pathlib import Path

from docich import hanjuku_policy as policy
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku_screen import Battle, Screen

ROOT = Path(__file__).resolve().parents[1]
FRAMES = [ROOT / 'tests' / 'fixtures' / 'hanjuku' / f'g1075-attack-successor-{n}.png'
          for n in (1, 2)]
CASTLE = 'フヌイヌム'


def memory():
    return {'chapter': 4, 'tick': 100, 'captured': ['アルマムーン'],
            'attack': {'general': 'ココット', 'side': 'attack', 'castle': CASTLE, 'step': None},
            'battle': {'enemy': 'リースリング', 'ally': 'ココット', 'enemy_hp': 0, 'ally_hp': 24,
                       'start_enemy_hp': 37, 'start_ally_hp': 24, 'castle': CASTLE,
                       'side': 'attack', 'step': None, 'cards_used': [], 'planned_cards': []}}


def screens():
    out = []
    for path in FRAMES:
        frame = read_png(path)
        out.append(parse(frame, phase=classify(frame)))
    return out


def decisions(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def test_native_panels_read_as_the_next_defender():
    assert [(s.kind, s.battle.enemy, s.battle.enemy_hp, s.battle.ally, s.battle.ally_hp)
            for s in screens()] == [('battle', 'バーミセリ', 89, 'ココット', 0)] * 2


def test_the_next_defender_opens_a_battle_and_the_loss_is_not_a_capture():
    mem = memory()
    for screen in screens() * 2:
        policy.battle_step(screen, mem)
        mem['tick'] += 1
    [handoff] = decisions(mem, 'attack_defender_successor')
    assert handoff['previous_enemy'] == 'リースリング' and handoff['enemy'] == 'バーミセリ'
    assert (mem['battle']['enemy'], mem['battle']['side'], mem['battle']['castle']) == (
        'バーミセリ', 'attack', CASTLE)
    for _ in range(2):
        policy.battle_end(mem, 'map')
    results = [(r['outcome'], r['resulting_event']) for r in decisions(mem, 'battle_result')]
    assert results == [('win', f'attack_continues:{CASTLE}'), ('loss', 'loss')]
    assert CASTLE not in mem['captured']


def test_one_panel_of_a_new_name_is_not_enough():
    mem = memory()
    policy.battle_step(screens()[0], mem)
    for _ in range(2):
        policy.battle_end(mem, 'map')
    assert not decisions(mem, 'attack_defender_successor')
    assert CASTLE in mem['captured']


def test_a_beaten_defender_misread_at_zero_is_not_a_new_defender():
    mem = memory()
    blurred = Screen([], None, '', kind='battle', battle=Battle('リ一スリング', 0, 'ココット', 24))
    for _ in range(2):
        policy.battle_step(blurred, mem)
    assert not decisions(mem, 'attack_defender_successor')
    assert mem['battle']['enemy'] == 'リースリング'
