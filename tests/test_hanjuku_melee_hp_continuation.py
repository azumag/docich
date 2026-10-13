"""Wounded melee continuation; later panels are counterfactual, not wins."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.judgment.battle import _melee_continuation_evidence
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_card_damage_gate import panel
from test_hanjuku_survival import menu

IDENTITY={'game':'hanjuku-hero','runtime_id':'g713-58fb2ff1','generation':713,
          'lease_id':'b21bde1f-f8bf-49a6-95da-9288f9605f69'}


def opened():
    # This historical raw panel really shows Zeus85/Cumin23. The following
    # HP55/5 panel is explicitly synthetic, never a captured continuation.
    screen=parse(read_png(Path(__file__).parent/'fixtures/hanjuku/g713-zeus-cumin-healthy.png'))
    mem={'chapter':1,'tick':10,'_run_identity':deepcopy(IDENTITY),
         'attack':{'side':'defense','castle':'スペンソニア'}}
    assert screen.battle.ally_hp==85 and screen.battle.enemy_hp==23
    p.battle_step(screen,mem);p.battle_step(screen,mem)
    cur=mem['battle']
    assert cur['melee_opening_scope']==[IDENTITY,'ゼウス','クミン',85,23]
    assert cur['melee_opening_army']==list(screen.field_soldiers)
    p._survival_state(mem,cur).update(cards_checked=3,cards_exhausted=True)
    assert p.battle_menu_step(menu(('たまごをつかう','たいきゃく')),mem)==[p.pad('b')]
    return mem


def continuing():
    mem=opened();cur=mem['battle'];cur.update(ally_hp=55,enemy_hp=5)
    p.battle_step(panel(mem,soldiers=(5,0)),mem)
    assert cur['clashed'] is True
    return mem


def test_hp_loss_below_seventy_percent_keeps_current_one_sided_melee():
    mem=continuing();cur=mem['battle']
    assert cur['ally_hp']*10<=85*7
    assert p._hold_melee_egg(mem,cur,{'たまごをつかう'})
    assert p.battle_menu_step(menu(('たまごをつかう','たいきゃく')),mem)==[p.pad('b')]
    assert cur['survival']['egg_attempted'] is False
    evidence=mem['_records'][-1]['observed_metric']['conservation']
    assert evidence['rule']=='melee_finish_after_damage'
    assert (evidence['ally_opening_hp'],evidence['enemy_opening_hp'])==(85,23)
    opens=cur['survival']['opens']
    for _ in range(3):
        actions=p.battle_step(panel(mem,soldiers=(5,0)),mem)
        assert not any('b' in a.get('buttons',[]) for a in actions)
    assert cur['survival']['opens']==opens


@pytest.mark.parametrize('hp',[54,30,13])
def test_remaining_force_can_finish_without_an_absolute_full_hp_percentage(hp):
    mem=continuing();mem['battle']['ally_hp']=hp
    assert p._hold_melee_egg(mem,mem['battle'],{'たまごをつかう'})


@pytest.mark.parametrize('change',[{'ally_hp':12},{'ally_hp':0},{'ally_hp':True},
    {'enemy_hp':28},{'enemy_hp':23,'enemy_soldiers':2},{'enemy_hp':0},{'enemy_hp':True},
    {'card_soldiers_current':False},{'card_soldiers_current':1},
    {'ally_soldiers':None},{'enemy_soldiers':None},{'ally_soldiers':7},
    {'enemy_soldiers':True},{'enemy_soldiers':6},
    {'card_hp_unread':True},{'card_context_unclassified':True},
    {'card_summon_observed':True},{'egg_battle':True},
    {'card_damage_rescue_needed':True},{'card_flow':{'stage':'list'}},
    {'ally':p.NAME},{'ally':'しゅじんこう'},{'enemy':'クイーン'},
    {'ally':'未確認'},{'start_ally_hp':30},{'start_enemy_hp':5}])
def test_lost_advantage_or_protection_cannot_borrow_the_continuation(change):
    mem=continuing();mem['battle'].update(change)
    assert _melee_continuation_evidence(mem,mem['battle']) is None
    assert not p._hold_melee_egg(mem,mem['battle'],{'たまごをつかう'})


@pytest.mark.parametrize('change',['missing','run','generation','lease','ally','enemy','start'])
def test_opening_receipt_is_bound_to_this_run_and_same_battle(change):
    mem=continuing();scope=mem['battle']['melee_opening_scope']
    if change=='missing':mem['battle'].pop('melee_opening_scope')
    elif change=='run':scope[0]['runtime_id']='g714-foreign'
    elif change=='generation':scope[0]['generation']=714
    elif change=='lease':scope[0]['lease_id']='foreign'
    elif change=='ally':scope[1]='ヴィーナス'
    elif change=='enemy':scope[2]='タピオカ'
    else:scope[3]=84
    assert not p._hold_melee_egg(mem,mem['battle'],{'たまごをつかう'})


def test_saved_scope_reuses_observation_and_never_changes_egg_quantity():
    mem=json.loads(json.dumps(continuing()));mem['egg_uses']={'ゼウス':1}
    before=deepcopy(mem)
    assert p._hold_melee_egg(mem,mem['battle'],{'たまごをつかう'})
    assert mem==before
    mem['egg_uses']['ゼウス']=0
    assert not p._hold_melee_egg(mem,mem['battle'],{'たまごをつかう'})


def test_later_critical_hp_or_enemy_summon_reopens_real_rescue():
    for change in ({'ally_hp':12},{'card_summon_observed':True}):
        mem=continuing();mem['battle'].update(change)
        if 'ally_hp' in change:
            p.battle_step(panel(mem,soldiers=(5,0)),mem)
        assert not p._hold_melee_egg(mem,mem['battle'],{'たまごをつかう'})
        assert p.battle_menu_step(menu(('たまごをつかう','たいきゃく')),mem)==[p.pad('a')]
        assert mem['battle']['survival']['egg_attempted']


def test_no_new_receipt_is_created_when_hotloading_an_existing_battle():
    mem=continuing();mem['battle'].pop('melee_opening_scope')
    p.battle_step(panel(mem,soldiers=(5,0)),mem)
    assert 'melee_opening_scope' not in mem['battle']
    assert not p._hold_melee_egg(mem,mem['battle'],{'たまごをつかう'})


def test_saved_boolean_generation_cannot_match_integer_one_by_python_equality():
    mem=continuing()
    mem['_run_identity'].update(runtime_id='g1-replay',generation=1)
    mem['battle']['melee_opening_scope'][0].update(runtime_id='g1-replay',generation=True)
    assert not p._hold_melee_egg(mem,mem['battle'],{'たまごをつかう'})


def test_malformed_opening_scope_is_not_a_continuation_permission():
    mem=continuing()
    for value in (None,True,{},[],[IDENTITY],['not_identity','ゼウス','クミン',85,23]):
        mem['battle']['melee_opening_scope']=value
        assert not p._hold_melee_egg(mem,mem['battle'],{'たまごをつかう'})


def test_enemy_troop_loss_can_prove_progress_before_general_hp_starts_falling():
    mem=continuing();cur=mem['battle'];cur.update(enemy_hp=23,clashed=False)
    assert p._hold_melee_egg(mem,cur,{'たまごをつかう'})
    evidence=_melee_continuation_evidence(mem,cur)
    assert evidence['enemy_progress']=='soldiers'
    assert evidence['opening_soldiers']==[5,2]
    assert mem['battle']['cards_used']==[]


@pytest.mark.parametrize('opening',[None,[],[5,None],[5,True]])
def test_unknown_opening_troops_cannot_invent_enemy_progress(opening):
    mem=continuing();cur=mem['battle'];cur.update(enemy_hp=23,clashed=False,melee_opening_army=opening)
    assert not p._hold_melee_egg(mem,cur,{'たまごをつかう'})
