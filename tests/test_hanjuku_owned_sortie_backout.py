"""Partial confirmation must keep the bounded cancellation owner, never A."""
import json
from copy import deepcopy
from pathlib import Path
from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import Frame,read_png
from docich.hanjuku_screen import parse,Screen
from test_hanjuku_chapter_two_stock_kit import memory


def confirmation(partial=False):
    frame=read_png(Path(__file__).parent/'fixtures/hanjuku/g700-balmung-kit-confirm.png')
    if not partial:return frame
    # Delete only the measured 「つ」 cell, copying the blank cell on this row.
    pixels=bytearray(frame.rgb)
    for y in range(95,103):
        for x in range(160,168):
            i=(y*frame.width+x)*3
            pixels[i:i+3]=bytes(frame.pixel(184,y))
    return Frame(frame.width,frame.height,bytes(pixels))


def state():
    mem=memory();mem['name']={'done':True}
    mem['boss_stock_restart']={'step':'2-B1','readings':0}
    return {'policy':mem}


def test_native_and_same_partial_panel_both_back_out_through_normal_dispatch():
    for partial in (False,True):
        frame=confirmation(partial);s=state()
        assert parse(frame).kind == ('yes_no' if partial else 'sortie_confirm')
        actions,s=decide(frame,s)
        assert actions==[p.pad('b')]
        assert s['policy']['boss_stock_restart']['readings']==1


def test_partial_panel_retains_deadline_through_json_roundtrips_and_never_approves():
    frame=confirmation(True);s=state()
    for reading in range(1,14):
        actions,s=decide(frame,json.loads(json.dumps(s)))
        assert not any('a' in a.get('buttons',[]) for a in actions)
        if reading<12:
            assert actions==[p.pad('b')]
            assert s['policy']['boss_stock_restart']['readings']==reading
        elif reading==12:
            assert actions==[p.pad('b'),p.pad('b')]
            assert not s['policy'].get('boss_stock_restart') and s['policy']['active'] is None
        else:assert actions==[] and s['policy']['boss_stock_cancelled']


def test_unowned_partial_confirmation_keeps_existing_yes_no_behavior():
    s=state();s['policy'].pop('boss_stock_restart')
    actions,_=decide(confirmation(True),s)
    assert actions==[p.pad('a')]


def test_battle_month_and_unrelated_confirmation_do_not_consume_cancellation_budget():
    mem=state()['policy'];before=deepcopy(mem)
    for kind in ('battle','battle_menu','month_menu','shop_quantity_prompt','yes_no'):
        assert p.boss_stock_restart_step(Screen([],None,'うむッ!いかんッ!',kind=kind),mem) is None
        assert mem==before


def test_capped_guard_yields_to_foreground_and_clears_only_after_actual_backout():
    mem=state()['policy'];mem.pop('boss_stock_restart');mem['boss_stock_cancelled']=True
    assert p.boss_stock_restart_step(Screen([],None,'',kind='battle_menu'),mem) is None
    assert mem['boss_stock_cancelled']
    assert p.boss_stock_restart_step(parse(confirmation(True)),mem)==[]
    assert p.boss_stock_restart_step(Screen([],None,'',kind='castle_menu'),mem) is None
    assert not mem.get('boss_stock_cancelled')
