"""g684 Castora: a measured melee follow-up is a real option after クースカン."""
from pathlib import Path
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_card_damage_gate import cards, memory, panel

FIXTURES = Path(__file__).parent / 'fixtures/hanjuku'


def castora():
    return memory('クースカン', enemy='ヘーゼル', enemy_hp=33,
                  ally='マカデミア', ally_hp=34, soldiers=(6, 6))


def test_native_castora_list_selects_control_card_without_a_second_card():
    battle = read_png(FIXTURES / 'g684-castora-macadamia-panel.png')
    listing = read_png(FIXTURES / 'g684-castora-kooskan-list.png')
    assert battle.digest() == '10f8e7c3735de335e1d308aa2448cf7529ee2ca822ff0025c2b40a9366fa7f93'
    assert listing.digest() == '73a75bb07093860c018c371c8220f8956012ba64ca533dc2bc63d95afeabd681'
    mem = castora()
    p._card_battle_reading(parse(battle), mem['battle'])
    assert p.card_list_step(parse(listing), mem) == [p.pad('a')]
    cur = mem['battle']
    assert cur['card_assessment']['reason'] == 'control_then_melee_not_a_kill'
    assert cur['cards_selected'] == ['クースカン']
    assert cur['cards_used'] == []
    assert cur['enemy_hp'] == 33 and cur['enemy_soldiers'] == 6
    assert cur['card_assessment']['enemy_soldier_hp_upper'] == 60
    assert cur['card_assessment']['lethal'] is False
    projection = cur['card_assessment']['melee_followup']
    assert projection['enemy_hp_after_upper'] == 17
    assert projection['enemy_soldier_hp_after_upper'] == 30
    assert projection['effect_confirmation'] == 'unclassified'


@pytest.mark.parametrize('change', [
    {'ally_hp': 12}, {'ally_hp': 33}, {'ally_soldiers': 5},
    {'ally_soldiers': 0, 'enemy_soldiers': 0}, {'enemy_soldiers': None},
    {'ally_soldiers': 7}, {'card_soldiers_current': False},
    {'card_hp_unread': True}, {'enemy': 'ヴィーナス', 'enemy_hp': 33},
    {'ally': 'だれか'}, {'enemy': 'プリンス'},
])
def test_control_melee_permission_needs_healthy_hp_current_army_and_combat(change):
    mem = castora()
    mem['battle'].update(change)
    gate = p._card_damage_gate(mem, mem['battle'], 'クースカン', listed_cards=['クースカン'])
    assert gate['allowed'] is False


def test_an_unseen_card_or_other_control_effect_cannot_license_melee():
    mem = castora()
    gate = p._card_damage_gate(mem, mem['battle'], 'クースカン')
    assert gate['allowed'] is False and gate['needs_live_list'] is True
    assert p._card_damage_gate(mem, mem['battle'], 'ブレイコウ',
                               listed_cards=['ブレイコウ'])['allowed'] is False


def test_survival_route_can_select_control_then_melee_from_the_real_list():
    mem = castora()
    cur = mem['battle']
    cur['planned_cards'] = []
    cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
    rescue = p._survival_state(mem, cur)
    assert p.card_list_step(cards(['クースカン']), mem) == [p.pad('a')]
    assert cur['card_melee_followup']['bursts'] == 0
    assert rescue['cards_attempted'] == ['クースカン']


def test_missing_list_cursor_cannot_create_a_followup_watch():
    mem = castora()
    screen = cards(['クースカン'])
    screen.hand = screen.menu_cursor = None
    assert p.card_list_step(screen, mem) == []
    assert not mem['battle'].get('card_melee_followup')
    assert not mem['battle']['cards_selected']


def selected():
    mem = castora()
    assert p.card_list_step(cards(['クースカン']), mem) == [p.pad('a')]
    mem['battle']['planned_cards'] = []
    return mem


def test_observed_hp_drop_allows_immediate_bounded_power_bursts_without_fake_receipts():
    mem = selected()
    cur = mem['battle']
    cur['enemy_hp'] = 17
    for _ in range(p.MELEE_HOLD_LIMIT):
        assert p.battle_step(panel(mem, soldiers=(6, 6)), mem) == p._power_mash(mem, cur)
    assert cur['card_melee_followup']['bursts'] == p.MELEE_HOLD_LIMIT
    assert p._card_melee_ready(mem, cur) is False
    assert cur['cards_used'] == [] and cur['cards_unclassified'] == ['クースカン']
    assert not cur.get('enemy_egg_dropped')
    assert cur['enemy_soldiers'] == 6


@pytest.mark.parametrize('change', [
    {'enemy_hp': 33}, {'ally_hp': 12}, {'enemy': 'クミン'},
    {'card_soldiers_current': False}, {'egg_battle': True},
    {'ally_soldiers': 3},
])
def test_selection_alone_or_new_danger_cannot_force_melee(change):
    mem = selected()
    cur = mem['battle']
    cur.update({'enemy_hp': 17, **change})
    assert p._card_melee_ready(mem, cur) is False


def test_expired_selection_cannot_disable_survival_on_a_later_panel():
    mem = selected()
    mem['battle']['enemy_hp'] = 17
    mem['tick'] += p.CARD_ANNOUNCE_LIMIT + p.MELEE_HOLD_LIMIT + 1
    assert p._card_melee_ready(mem, mem['battle']) is False


@pytest.mark.parametrize('hp',[1,33])
def test_unchanged_hp_after_selection_never_claims_measured_drop_or_followup(hp):
    mem=castora()
    mem['battle'].update(enemy_hp=hp,start_enemy_hp=hp)
    assert p.card_list_step(cards(['クースカン']),mem)==[p.pad('a')]
    mem['battle']['planned_cards']=[]
    for _ in range(3):
        mem=json.loads(json.dumps(mem))
        mem['tick']+=1
        control=json.loads(json.dumps(mem))
        control['battle'].pop('card_melee_followup')
        expected=p.battle_step(panel(control,soldiers=(6,6)),control)
        assert p.battle_step(panel(mem,soldiers=(6,6)),mem)==expected
        assert not any(r['decision']=='battle_card_melee_followup' for r in mem['_records'])
        assert mem['battle']['cards_used']==[]
        assert mem['battle']['card_melee_followup']['bursts']==0


def test_hotloaded_refusal_can_recheck_once_at_the_existing_probe_boundary():
    mem = castora()
    cur = mem['battle']
    old_key = p._card_damage_key(mem, cur)[1:]  # v151 had no policy revision
    cur.update(card_damage_rescue_needed=True, melee_forced=True,
               card_damage_list_blocked={'key': old_key, 'cards': ['クースカン']})
    rescue = p._survival_state(mem, cur)
    rescue.update(exhausted=True, cards_exhausted=True, cards_uncertain=False)
    p._card_damage_recheck_resources(mem, cur)
    assert cur['card_damage_final_recheck'] is True
    assert rescue['exhausted'] is False
    p._card_damage_recheck_resources(mem, cur)
    assert len([r for r in mem['_records'] if r['decision'] == 'battle_card_damage_recheck']) == 1
