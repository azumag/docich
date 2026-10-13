"""Foreground greeting and named zero must survive stale controller context."""
from pathlib import Path

import pytest
from docich import hanjuku_bot as bot, hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import EggRow, parse
from test_hanjuku_empty_eggs import menu

F=Path(__file__).parent / 'fixtures/hanjuku/g713-pink-concert-greeting.png'


def test_original_pink_greeting_does_not_consume_the_house_scan_budget():
    frame=read_png(F)
    assert frame.digest()=='c88a6606651e91118e90da1557123bbde467141c50ecb4ddccfd6d63705d1709'
    screen=parse(frame)
    assert screen.kind=='text' and 'こんばんは' in screen.text
    assert bot.classify(frame)=='concert'
    state={'policy': {'chapter':1,'month':'1-7',
        'house':{'phase':'leave_roster','chapter':1,'age':0,'total':0,'seen':[],'pending':[]}}}
    for _ in range(4):
        actions,state=bot.decide(frame,state)
        assert actions==[p.pad('a')]
        assert state['policy']['house']['age']==0 and state['policy']['house']['total']==0
        assert not any(r['decision']=='house_deferred' for r in state['_records'])


@pytest.mark.parametrize('context', [None, {}, {'ally':'ゼウス','ally_hp':85,'enemy':'シナモン','enemy_hp':39}])
def test_actual_zero_owner_cannot_borrow_another_or_missing_battle_context(context,monkeypatch):
    mem={'chapter':1,'egg_uses':{'ココット':0,'ゼウス':3}}
    if context is not None:mem['battle']=context
    screen=menu('egg_battle_menu',cursor=208)
    screen.egg_rows=[EggRow('ココット',24,'ally',128),EggRow('シナモン',39,'enemy',112)]
    monkeypatch.setattr(p.experience,'preferred',lambda *a,**k:'use_egg')
    assert p._screen_egg_empty(screen,mem,mem.get('battle'))
    actions=p.egg_battle_step(screen,mem)
    assert actions!=[p.pad('a')]  # current command cursor is on the egg
    assert mem['egg_action']=='attack' and mem['egg_uses']['ココット']==0


def test_unknown_bulk_quantity_does_not_release_a_named_zero_status():
    mem={'egg_uses':{},'house_eggs':{'ココット':{'uses':0}}}
    assert p._own_egg_empty(mem,{'ally':'ココット'})
    mem['egg_uses']['ゼウス']=4
    assert p._own_egg_empty(mem,{'ally':'ココット'})
    mem['egg_uses']['ココット']=1
    assert not p._own_egg_empty(mem,{'ally':'ココット'})


@pytest.mark.parametrize('invalid', [None, False, True, '1', 1.0, 6, -1])
def test_invalid_quantity_cannot_release_the_last_observed_zero(invalid):
    mem={'egg_uses':{'ココット':invalid},'house_eggs':{'ココット':{'uses':0}}}
    assert p._own_egg_empty(mem,{'ally':'ココット'})


def test_aggregate_receipt_cannot_release_a_sortie_zero_without_house_cache():
    mem={'egg_uses':{'ココット':0,'ゼウス':3}}
    p._egg_quantities_unknown(mem)
    assert mem['egg_uses']=={} and mem['egg_zero_owners']==['ココット']
    assert p._own_egg_empty(mem,{'ally':'ココット'})
    mem['egg_uses']['ゼウス']=1
    assert p._own_egg_empty(mem,{'ally':'ココット'})
    mem['egg_uses']['ココット']=1
    assert not p._own_egg_empty(mem,{'ally':'ココット'})
    p._egg_quantities_unknown(mem)
    assert 'ココット' not in mem['egg_zero_owners']
