"""Observed actor handoff without invented location, death, or live input."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from docich.hanjuku.flows.battle import battle_step, battle_end
from docich.hanjuku_screen import Battle, Screen, parse
from docich.hanjuku_pixels import read_png

FIXTURES = Path(__file__).parent/'fixtures/hanjuku'
IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g747-0fd8cf05', 'generation': 747,
            'lease_id': '28284317-f2d0-488c-af16-2d8666389504'}


def panel(ally='シナモン', hp=23, enemy='マカデミア', ehp=34):
    return Screen(lines=[], hand=None, text='', kind='battle',
                  battle=Battle(enemy, ehp, ally, hp))


def step(m, s):
    m['tick'] = m.get('tick', 0) + 1
    actions = battle_step(s, m)
    return actions


def memory():
    m = {'chapter': 1, '_run_identity': dict(IDENTITY),
         'captured': ['アルマムーン'], 'garrison': {'アルマムーン': ['シナモン', 'どうし']}}
    step(m,panel());step(m,panel())
    return m


def defeated():
    m=memory();step(m,panel(hp=0));step(m,panel(hp=0))
    assert m['battle']['unlocated_zero']
    return json.loads(json.dumps(m))


def successor(m, ally='どうし', hp=86, enemy='マカデミア', ehp=34):
    return step(m,panel(ally,hp,enemy,ehp))


def test_native_sequence_tracks_new_actor_and_never_claims_castle_or_death():
    evidence=json.loads((FIXTURES/'g747-succession-evidence.json').read_text())
    native={}
    for row in evidence['frames']:
        f=read_png(FIXTURES/row['frame'])
        assert f.digest()==row['rgb_sha256']==row['decision']['frame_sha256']
        native[row['uid']]=parse(f)
    m={'chapter':1,'_run_identity':dict(IDENTITY),'captured':['アルマムーン'],
       'garrison':{'アルマムーン':['シナモン','どうし']}}
    # Minimal state and the duplicate opening are explicit replay setup.
    for tick,uid in [(15494,15495),(15495,15495),(15502,15502),(15503,15503),(15509,15509),(15510,15510),(15517,15517)]:
        m=json.loads(json.dumps(m));m['tick']=tick
        actions=battle_step(native[uid],m)
        if uid==15509:
            assert not actions and m['battle']['ally']=='シナモン'
        if uid==15510:
            assert actions and m['battle']['ally']=='どうし'
            assert m['battle']['ally_hp']==82
            assert m['battle']['castle'] is None and m['battle']['side'] is None
    battle_end(m,'map');battle_end(m,'map')
    results=[r for r in m['_records'] if r['decision']=='battle_result']
    assert [(r['ally'],r['outcome']) for r in results]==[('シナモン','loss'),('どうし','win')]
    assert all(r['castle'] is None and r['side'] is None for r in results)
    assert all(r['observed_metric']['general_loss']=='unclassified' for r in results)
    assert m['captured']==['アルマムーン'] and not m.get('lost')
    assert m['general_location_unknown']==['シナモン']
    assert m['stats']['generals_lost'] is None


def test_saved_handoff_drops_predecessor_tactics_and_stale_entry():
    m=defeated();cur=m['battle'];cur.update(step='old',survival={'exhausted':True},cards_used=['イッテツーン'],planned_cards=['キャトルミュ'])
    m['attack']={'side':'defense','castle':'ジョンリギ','step':'old'}
    m.update(egg_choice={'old':True},egg_key='old',indep_menu='old')
    assert successor(m)==[]
    m=json.loads(json.dumps(m))
    assert successor(m,hp=82)
    cur=m['battle']
    assert cur['step'] is None and cur['planned_cards']==[] and cur['cards_used']==[]
    assert cur['side'] is None and cur['castle'] is None and 'survival' not in cur
    assert all(k not in m for k in ('egg_choice','egg_key','indep_menu'))
    assert cur['melee_opening_scope'][1:]==['どうし','マカデミア',82,34]
    assert m['stats']['losses']==1


@pytest.mark.parametrize('mutation', ['missing_identity','other_run','other_gen','other_lease','chapter','no_opening','opening_actor','opening_hp','no_receipt','receipt_future','receipt_old','receipt_bool','alive','enemy_zero','known_attack','known_castle','event_duel','monster','summon','hero_predecessor'])
def test_unbound_changed_or_unsafe_record_cannot_handoff(mutation):
    m=defeated();c=m['battle']
    if mutation=='missing_identity':m.pop('_run_identity')
    elif mutation=='other_run':m['_run_identity']['runtime_id']='g748-other'
    elif mutation=='other_gen':m['_run_identity']['generation']=748
    elif mutation=='other_lease':m['_run_identity']['lease_id']='different'
    elif mutation=='chapter':m['chapter']=2
    elif mutation=='no_opening':c.pop('melee_opening_scope')
    elif mutation=='opening_actor':c['melee_opening_scope'][1]='クミン'
    elif mutation=='opening_hp':c['start_ally_hp']=22
    elif mutation=='no_receipt':c.pop('unlocated_zero')
    elif mutation=='receipt_future':c['unlocated_zero']['tick']=999
    elif mutation=='receipt_old':m['tick']+=33
    elif mutation=='receipt_bool':c['unlocated_zero']['tick']=True
    elif mutation=='alive':c['ally_hp']=1
    elif mutation=='enemy_zero':c['enemy_hp']=0
    elif mutation=='known_attack':c['side']='attack'
    elif mutation=='known_castle':c['castle']='アルマムーン'
    elif mutation=='event_duel':c['event_duel']=True
    elif mutation=='monster':m['egg_battle']=True
    elif mutation=='summon':c['card_summon_observed']=True
    elif mutation=='hero_predecessor':c['ally']='どうし'
    previous=c['ally']
    new_actor = 'ヴィーナス' if mutation == 'hero_predecessor' else 'どうし'
    assert successor(m,ally=new_actor,hp=82)==[] and successor(m,ally=new_actor,hp=80)==[]
    assert m['battle']['ally']==previous and not m.get('stats')


@pytest.mark.parametrize('changes',[{'ally':'ダークエルフ','hp':216},{'ally':'未確認'},{'hp':True},{'hp':None},{'hp':0},{'hp':999},{'enemy':'クミン'},{'ehp':0},{'ehp':35}])
def test_new_panel_must_be_complete_known_live_and_same_enemy(changes):
    m=defeated()
    assert successor(m,**changes)==[] and successor(m,**changes)==[]
    assert m['battle']['ally']=='シナモン'


def test_one_zero_or_saved_zero_without_observation_is_not_enough():
    for observed in (False,True):
        m=memory()
        if observed:step(m,panel(hp=0))
        else:m['battle']['ally_hp']=0
        assert successor(m)==[] and successor(m,hp=82)==[]
        assert m['battle']['ally']=='シナモン'


@pytest.mark.parametrize('interruption',[panel(hp=None),panel(hp=0),panel(ally='ミント',hp=32)])
def test_new_actor_panels_must_be_consecutive(interruption):
    m=defeated();successor(m);step(m,interruption)
    assert successor(m)==[] and m['battle']['ally']=='シナモン'
    assert successor(m,hp=82) and m['battle']['ally']=='どうし'


def test_intervening_tick_and_hp_recovery_invalidate_observation_chain():
    m=defeated();successor(m);m['tick']+=1
    assert successor(m)==[]
    step(m,panel(hp=4))
    assert 'unlocated_zero' not in m['battle']
    assert successor(m)==[] and successor(m)==[]


def test_zero_panels_need_consecutive_ticks_and_nonbattle_clears_candidate(monkeypatch):
    m=memory();step(m,panel(hp=0));m['tick']+=1;step(m,panel(hp=0))
    assert not m['battle'].get('unlocated_zero')
    from docich.hanjuku_bot import decide
    from docich.hanjuku_pixels import Frame
    from docich import hanjuku_screen
    monkeypatch.setattr(hanjuku_screen,'parse',lambda *args,**kw:Screen(lines=[],hand=None,text='',kind='text'))
    m=defeated();successor(m)
    _,state=decide(Frame(256,224,bytes(256*224*3)),{'policy':m},run_identity=IDENTITY)
    m=state['policy'];assert 'unlocated_successor_seen' not in m['battle']
    assert successor(m)==[]


def test_nonhero_successor_uses_own_record_and_can_finish_once():
    m=defeated()
    assert successor(m,ally='ヴィーナス',hp=82)==[]
    assert successor(m,ally='ヴィーナス',hp=80)
    assert m['battle']['ally']=='ヴィーナス'
    step(m,panel('ヴィーナス',hp=75,ehp=0))
    battle_end(m,'map');battle_end(m,'map');battle_end(m,'map')
    assert m['stats']['wins']==1 and m['stats']['losses']==1
    assert m['captured']==['アルマムーン'] and not m.get('lost')


def test_duplicate_tick_is_not_a_second_observation():
    m=memory();step(m,panel(hp=0))
    battle_step(panel(hp=0),m)
    assert not m['battle'].get('unlocated_zero')
    step(m,panel(hp=0));assert m['battle']['unlocated_zero']
    assert successor(m)==[]
    assert battle_step(panel('どうし',86),m)==[]
    assert m['battle']['ally']=='シナモン'
