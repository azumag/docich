"""#2989: the text after a chosen okunote candidate is its effect, not an unread menu.

g1075-1d5f65e9 2026-10-29 JST, リグア defense, レモン 27 vs ビシソワーズ 13:
g1075-okunote-parent-1..4 (14:35:48-50, scroll x3 then おくのて),
g1075-okunote-candidates-1..3 (14:35:51-52, てぶくろ / ブンシーンもどき /
リューキシもどき), g1075-okunote-effect-1..2 (14:35:54/59, the effect
dialogue) and g1075-okunote-return (14:36:05, ビシソワーズ 0). Production
recorded battle_okunote_egg_menu_unread at 14:35:59 although the choice was
made and the battle was won. g1075-okunote-enemy-egg-1..3 (14:14:36-41) are
the opposite: the enemy's たまごをつかう and summon chant right after our B,
before any okunote menu appeared, so that one stays an unread menu.

Only the record changes; every planned input is the production one.
"""
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows.okunote import _scope
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku_screen import Battle, Screen

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'
WAIT = {'type': 'wait', 'ms': 50}
UNREAD, EFFECT = 'battle_okunote_egg_menu_unread', 'battle_okunote_effect_text_limit'


def native(name):
    frame = read_png(FIXTURES / f'g1075-okunote-{name}.png')
    return parse(frame, phase=classify(frame))


def lemon():
    return {'chapter': 3, 'tick': 500, 'battle': {
        'enemy': 'ビシソワーズ', 'ally': 'レモン', 'castle': 'リグア', 'side': 'defense',
        'start_enemy_hp': 27, 'enemy_hp': 13, 'start_ally_hp': 27, 'ally_hp': 27,
        'step': None, 'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
        'card_evidence_version': 1, 'planned_cards': [], 'okunote_only_observed': True,
        'defender_enemy_hp_drop': 14,
        'survival': {'opens': 1, 'menu_ticks': 0, 'cards_checked': 0,
                     'cards_attempted': [], 'egg_attempted': False, 'exhausted': True}}}


def step(screen, mem):
    actions = p.defender_egg_pending_step(screen, mem)
    if actions is None and screen.kind == 'battle_menu':
        actions = p.battle_menu_step(screen, mem)
    elif actions is None and screen.kind == 'okunote_menu':
        actions = p.okunote_step(screen, mem)
    return actions


def choose(mem):
    """The production menu walk up to and including the candidate A."""
    names = ['parent-1', 'parent-2', 'parent-3', 'parent-4',
             'candidates-1', 'candidates-2', 'candidates-3']
    return [step(native(n), mem) for n in names]


def decisions(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def text_frames(mem, screens):
    return [step(s, mem) for s in screens]


EFFECT_TEXT = ['effect-1'] * 4 + ['effect-2'] * 5


def test_native_choice_then_effect_dialogue_keeps_inputs_and_is_not_an_unread_menu():
    mem = lemon()
    assert [a[0]['buttons'] for a in choose(mem)] == [['down']] * 3 + [['a']] + [['down']] * 2 + [['a']]
    assert decisions(mem, 'battle_okunote_select')[-1]['choice'] == 'リューキシもどき'
    # Two more observations of the closing candidate list, then the dialogue.
    assert [step(native('candidates-3'), mem) for _ in range(2)] == [[WAIT]] * 2
    assert text_frames(mem, [native(n) for n in EFFECT_TEXT]) == [[WAIT]] * 8 + [[p.pad('b')]]
    assert len(decisions(mem, EFFECT)) == 1 and not decisions(mem, UNREAD)
    # The close stays bounded exactly as before: further text still gets B.
    assert step(native('effect-2'), mem) == [p.pad('b')]
    assert len(decisions(mem, EFFECT)) == 1
    ret = native('return')
    assert (ret.kind, ret.battle.ally, ret.battle.enemy, ret.battle.enemy_hp) == (
        'battle', 'レモン', 'ビシソワーズ', 0)
    assert p.defender_egg_pending_step(ret, mem) is None


def kashu_opening():
    mem = {'chapter': 3, 'tick': 400, 'battle': {
        'enemy': 'カシュー', 'ally': 'クミン', 'castle': 'アルマムーン', 'side': 'defense',
        'start_enemy_hp': 39, 'enemy_hp': 37, 'start_ally_hp': 27, 'ally_hp': 27,
        'step': None, 'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
        'card_evidence_version': 1, 'planned_cards': [], 'okunote_only_observed': True,
        'okunote_egg_preempt': {'opens': 1, 'stage': 'opening', 'reason': 'unmeasured_clash_guard_exhausted'}}}
    return mem


def unread_after(mem, screens):
    actions = text_frames(mem, screens)
    assert actions == [[WAIT]] * 8 + [[p.pad('b')]]
    assert len(decisions(mem, UNREAD)) == 1 and not decisions(mem, EFFECT)


def chosen():
    mem = lemon()
    choose(mem)
    return mem


def _native_enemy_egg_before_any_menu():
    mem = kashu_opening()
    frames = [native('enemy-egg-1')] * 3 + [native('enemy-egg-2')] * 3 + [native('enemy-egg-3')] * 3
    assert [f.kind for f in frames] == ['text'] * 9
    return mem, frames


def _parent_chosen_but_list_never_read():
    mem = lemon()
    for n in ['parent-1', 'parent-2', 'parent-3', 'parent-4']:
        step(native(n), mem)
    assert mem['battle']['okunote_attempt']['phase'] == 'parent_selected'
    return mem, [native('effect-2')] * 9


def _scoped(**changes):
    def build():
        mem = chosen()
        mem['battle']['okunote_attempt']['scope'] = _scope(mem, {**mem['battle'], **changes})
        return mem, [native('effect-2')] * 9
    return build


def _attempt(value):
    def build():
        mem = chosen()
        mem['battle']['okunote_attempt'] = value
        return mem, [native('effect-2')] * 9
    return build


def _other_run():
    mem = chosen()
    scope = list(mem['battle']['okunote_attempt']['scope'])
    scope[0] = 'other-run'
    mem['battle']['okunote_attempt']['scope'] = scope
    return mem, [native('effect-2')] * 9


def _menu_stage_unknown_frames():
    mem = lemon()
    step(native('parent-1'), mem)
    assert mem['battle']['okunote_egg_preempt']['stage'] == 'menu'
    return mem, [Screen([], None, '', kind='unknown')] * 9


@pytest.mark.parametrize('build', [
    _native_enemy_egg_before_any_menu,
    _parent_chosen_but_list_never_read,
    _scoped(enemy='カシュー'),
    _scoped(castle='アルマムーン'),
    _attempt(True),
    _attempt({'phase': 'candidate_selected'}),
    _other_run,
    _menu_stage_unknown_frames,
], ids=['native-enemy-egg-first', 'parent-only', 'other-enemy', 'other-castle',
        'attempt-not-dict', 'attempt-without-scope', 'other-run', 'menu-stage-unknown'])
def test_without_this_battles_chosen_candidate_the_limit_stays_an_unread_menu(build):
    mem, frames = build()
    unread_after(mem, frames)


def test_combat_return_after_the_choice_never_reaches_the_text_limit():
    mem = chosen()
    live = Screen([], None, '', kind='battle', battle=Battle('ビシソワーズ', 9, 'レモン', 27))
    p.battle_step(live, mem)
    assert 'okunote_attempt' not in mem['battle']
    assert not decisions(mem, EFFECT) and not decisions(mem, UNREAD)
