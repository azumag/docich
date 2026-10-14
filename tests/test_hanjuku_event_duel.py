"""Owner 2026-10-11: 裏切り/武芸者の挑戦の一騎打ちで青ゲージを使っていない."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import Battle, Screen
from docich.hanjuku_egg_reference import enemy_egg_triggers

ENEMY = 'キッシュ'   # egg holder, thought type 2 (clash-position risk)


def fight(mem, n=3):
    screen = Screen(lines=[], hand=None, text='', kind='battle',
                    battle=Battle(ENEMY, 40, 'どうし', 90))
    out = []
    for _ in range(n):
        mem['tick'] = int(mem.get('tick') or 0) + 1
        out.append(policy.battle_step(screen, mem))
    return out


def test_the_enemy_would_trigger_the_egg_safe_hold():
    t = enemy_egg_triggers(ENEMY, player_castle_defense=None)
    assert t.has_egg is True and t.clash_position is not False


def test_a_duel_after_the_event_text_mashes_a_through_the_clash():
    mem = {'chapter': 1, 'tick': 100}
    policy.note_duel_event(Screen([], None, '1ねん4のつき100Gいってごあいてねがえぬか?', kind='yes_no'), mem)
    out = fight(mem)
    assert mem['battle']['event_duel'] is True
    assert all(any(a.get('buttons') == ['a'] for a in acts) for acts in out[1:])
    modes = [r.get('melee_control_mode') for r in mem['_records'] if r['decision'] == 'battle_melee']
    assert modes and set(modes) == {'power_mash'}
    assert any(r['decision'] == 'battle_event_duel' for r in mem['_records'])


def test_without_the_event_the_same_fight_keeps_its_egg_safe_hold():
    mem = {'chapter': 1, 'tick': 100}
    out = fight(mem)
    assert not mem['battle'].get('event_duel')
    modes = [r.get('melee_control_mode') for r in mem['_records'] if r['decision'] == 'battle_melee']
    assert 'egg_safe_hold' in modes


def test_a_stale_event_or_a_castle_battle_is_not_a_duel():
    mem = {'chapter': 1, 'tick': 100}
    policy.note_duel_event(Screen([], None, 'うらぎりもの', kind='text'), mem)
    mem['tick'] = 100 + policy.DUEL_EVENT_WINDOW + 5
    fight(mem)
    assert not mem['battle'].get('event_duel')
    mem = {'chapter': 1, 'tick': 100,
           'attack': {'general': 'どうし', 'castle': 'キカンドン', 'side': 'attack', 'step': None}}
    policy.note_duel_event(Screen([], None, 'はたしあい', kind='text'), mem)
    fight(mem)
    assert not mem['battle'].get('event_duel')
