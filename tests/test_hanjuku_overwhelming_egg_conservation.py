"""Synthetic battle-state regressions, not captured wins or delivered inputs."""
from copy import deepcopy
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.judgment.battle import _melee_continuation_evidence
from test_hanjuku_survival import menu


IDENTITY = {
    'game': 'hanjuku-hero', 'runtime_id': 'g713-58fb2ff1', 'generation': 713,
    'lease_id': 'b21bde1f-f8bf-49a6-95da-9288f9605f69',
}


def memory(*, hero=False, hp=55, enemy_hp=5, soldiers=(5, 0)):
    ally = p.NAME if hero else 'ゼウス'
    cur = {
        'ally': ally, 'enemy': 'クミン', 'ally_hp': hp, 'enemy_hp': enemy_hp,
        'start_ally_hp': hp, 'start_enemy_hp': enemy_hp,
        'ally_soldiers': soldiers[0], 'enemy_soldiers': soldiers[1],
        'card_soldiers_current': True, 'card_evidence_version': 1,
        'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
        'planned_cards': [], 'tactics_done': [], 'step': None,
        'side': 'defense', 'castle': 'スペンソニア',
        'melee_opening_scope': [deepcopy(IDENTITY), ally, 'クミン', hp, enemy_hp],
        'melee_opening_army': list(soldiers),
    }
    if hero:
        cur['ref_ally_hp'] = 90
    mem = {'chapter': 1, 'tick': 10, '_run_identity': deepcopy(IDENTITY),
           'battle': cur, 'egg_uses': {ally: 1}}
    p._survival_state(mem, cur).update(cards_checked=3, cards_exhausted=True)
    return mem


@pytest.mark.parametrize('hp', [55, 40, 13])
def test_wounded_opening_preserves_a_scoped_overwhelming_advantage(hp):
    mem = memory(hp=hp)
    cur = mem['battle']
    assert hp * 10 <= 85 * 7
    evidence = _melee_continuation_evidence(mem, cur)
    assert evidence['rule'] == 'melee_advantage_at_opening'
    assert evidence['enemy_progress'] == 'not_observed'
    assert p._hold_melee_egg(mem, cur, {'たまごをつかう'})
    assert p.battle_menu_step(menu(('たまごをつかう', 'たいきゃく')), mem) == [p.pad('b')]
    assert cur['survival']['egg_attempted'] is False
    assert mem['egg_uses']['ゼウス'] == 1
    assert not mem.get('egg_recheck')


def test_progress_after_a_wounded_opening_keeps_conservation():
    mem = memory(hp=55, enemy_hp=10)
    cur = mem['battle']
    cur.update(ally_hp=40, enemy_hp=5)
    evidence = _melee_continuation_evidence(mem, cur)
    assert evidence['rule'] == 'melee_finish_after_damage'
    assert evidence['enemy_progress'] == 'hp'
    assert p._hold_melee_egg(mem, cur, {'たまごをつかう'})


def test_our_hp_falling_without_enemy_progress_still_ends_conservation():
    mem = memory(hp=55, enemy_hp=10)
    mem['battle']['ally_hp'] = 40
    assert _melee_continuation_evidence(mem, mem['battle']) is None
    assert not p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'})


@pytest.mark.parametrize('change', [
    {'ally_hp': 12}, {'ally_hp': True}, {'enemy_hp': 30}, {'enemy_hp': 0},
    {'ally_soldiers': None}, {'enemy_soldiers': None}, {'ally_soldiers': 7},
    {'enemy_soldiers': True}, {'card_soldiers_current': False},
    {'card_hp_unread': True}, {'card_context_unclassified': True},
    {'card_summon_observed': True}, {'egg_battle': True},
    {'card_flow': {'stage': 'list'}}, {'card_damage_rescue_needed': True},
])
def test_wounded_opening_does_not_override_missing_evidence_or_danger(change):
    mem = memory()
    mem['battle'].update(change)
    assert not p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'})


@pytest.mark.parametrize('change', ['missing', 'runtime', 'generation', 'lease', 'ally'])
def test_old_or_foreign_openings_cannot_authorize_wounded_conservation(change):
    mem = memory()
    scope = mem['battle']['melee_opening_scope']
    if change == 'missing':
        mem['battle'].pop('melee_opening_scope')
    elif change == 'runtime':
        scope[0]['runtime_id'] = 'foreign'
    elif change == 'generation':
        scope[0]['generation'] += 1
    elif change == 'lease':
        scope[0]['lease_id'] = 'foreign'
    else:
        scope[1] = 'ヴィーナス'
    assert not p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'})


def test_json_restore_preserves_evidence_but_not_a_permanent_permission():
    mem = json.loads(json.dumps(memory()))
    before = deepcopy(mem)
    assert p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'})
    assert mem == before
    mem['battle']['card_summon_observed'] = True
    assert not p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'})


def test_hero_without_cards_saves_egg_only_with_a_large_healthy_margin():
    mem = memory(hero=True, hp=90)
    assert p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'})
    mem['battle']['ally_hp'] = 60
    assert not p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'})


@pytest.mark.parametrize('change', [
    {'ally_hp': 12}, {'card_summon_observed': True}, {'egg_battle': True},
    {'enemy': 'クイーン'}, {'enemy_soldiers': None},
    {'card_flow': {'stage': 'list'}}, {'card_damage_rescue_needed': True},
])
def test_hero_no_card_conservation_keeps_game_over_protections(change):
    mem = memory(hero=True, hp=90)
    mem['battle'].update(change)
    assert not p._hold_melee_egg(mem, mem['battle'], {'たまごをつかう'})


def test_critical_hp_still_selects_an_available_egg_after_conservation():
    mem = memory()
    assert p.battle_menu_step(menu(('たまごをつかう', 'たいきゃく')), mem) == [p.pad('b')]
    mem['battle']['ally_hp'] = 12
    assert p.battle_menu_step(menu(('たまごをつかう', 'たいきゃく')), mem) == [p.pad('a')]
    assert mem['battle']['survival']['egg_attempted'] is True
    # Selection is not a consumption receipt.
    assert mem['egg_uses']['ゼウス'] == 1


def test_positive_egg_does_not_reopen_after_current_conservation():
    from test_hanjuku_card_damage_gate import panel
    mem = memory()
    cur = mem['battle']
    assert p.battle_menu_step(menu(('たまごをつかう', 'たいきゃく')), mem) == [p.pad('b')]
    opens = cur['survival']['opens']
    for _ in range(3):
        p.battle_step(panel(mem, soldiers=(5, 0)), mem)
    assert cur['survival']['opens'] == opens
    assert not any(r.get('decision') == 'battle_egg_before_retreat' for r in mem['_records'])
    assert cur['survival']['egg_attempted'] is False
    cur['ally_hp'] = 12
    assert p._egg_before_retreat(mem, cur) is True


def test_diagnostics_accepts_exact_new_version_without_allowing_unknown_text():
    import importlib.util
    from pathlib import Path
    from docich.hanjuku.version import BOT_VERSION
    path = Path(__file__).resolve().parents[1] / 'ops/vm_actions/collect_diagnostics.py'
    spec = importlib.util.spec_from_file_location('egg_conservation_diagnostics', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._project_corner_state({'bot_version': BOT_VERSION})['bot_version'] == BOT_VERSION
    for value in ('not-allowlisted-token', BOT_VERSION + '-extra', None):
        assert module._project_corner_state({'bot_version': value})['bot_version'] is None
