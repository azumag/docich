"""Fresh cursor feedback and torn-HUD rejection, without guessed input."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from docich import hanjuku_bot as bot
from docich.hanjuku.os_layer import screen as view
from docich.hanjuku.os_layer.commands import menu_navigation_reading, menu_navigation_feedback, pad
from docich.hanjuku_pixels import Frame, read_png
from docich.hanjuku_font import TextLine, UNKNOWN

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/hanjuku'
spec=importlib.util.spec_from_file_location('menu_feedback_entry',ROOT/'brains/hanjuku/bot.py')
entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)


@pytest.mark.parametrize('uid,label,cursor',[(8064,'monster-row1',176),(8065,'monster-row2',192),
                                           (8128,'god-dialogue',None)])
def test_original_pixels_have_the_manually_inspected_cursor(uid,label,cursor):
    frame=read_png(FIX/f'g745-{label}-{uid}.png')
    evidence=next(e for e in json.loads((FIX/'g745-menu-reading-evidence.json').read_text())
                  if e['decision']['decision_id'].endswith(':'+str(uid)))
    assert frame.digest()==evidence['rgb_sha256']
    screen=view.parse(frame,phase=bot.classify(frame))
    assert screen.menu_cursor==cursor
    assert screen.kind==evidence['ground_truth']['screen_kind']
    if uid==8128:
        assert screen.header=={'chapter':None,'year':3,'month':6,'gold':160}
        assert menu_navigation_reading(screen) is None


def test_ambiguous_knight_scores_do_not_pick_the_first_row():
    rows=[TextLine(176,((176,'たまごをつかう'),)),TextLine(192,((176,'きりふだ'),))]
    with patch.object(view,'_knight_count',side_effect=[20,20]):
        assert view._menu_cursor(None,rows) is None
    with patch.object(view,'_knight_count',side_effect=[7,7]):
        assert view._menu_cursor(None,rows) is None


@pytest.mark.parametrize('name',['g684-castora-kooskan-list.png','g692-venus-artemis-kooskan-list.png'])
def test_native_card_picker_keeps_its_cursor_when_it_classifies_as_text(name):
    frame=read_png(FIX/name)
    screen=view.parse(frame,phase=bot.classify(frame))
    assert screen.kind=='text' and screen.menu_cursor==176
    assert view._card_picker_rows(screen)


@pytest.mark.parametrize('occlusion',['unknown','blank'])
def test_torn_middle_gold_digit_is_not_a_smaller_balance(occlusion):
    original=read_png(FIX/'g741-army-count-7458.png')
    assert view.parse(original).header['gold']==131
    data=bytearray(original.rgb)
    for y in range(15,23):
        for x in range(168,176):
            i=(y*256+x)*3;data[i:i+3]=b'\0\0\0'
    if occlusion=='unknown':
        i=(15*256+168)*3;data[i:i+3]=b'\xff\xff\xff'
    screen=view.parse(Frame(256,224,bytes(data)))
    # joined() discards an UNKNOWN or a blank between digits: 131 becomes 11.
    assert view.HEADER.search(screen.text).group(4)=='11'
    assert screen.header is None


def hud(text,y=15):
    return TextLine(y,tuple((32+i*8,ch) for i,ch in enumerate(text)))


@pytest.mark.parametrize('rows',[
    [hud('1ねん2のつき1'+UNKNOWN+'1G')],
    [hud(UNKNOWN+'2ねん2のつき131G')],
    [hud('1ねん2のつき131G'),hud('1ねん2のつき132G',23)],
    [hud('1ねん2のつき131G',151)],
    [hud('1ねん2のつき'),hud('131G',23)],
])
def test_unknown_duplicate_dialogue_and_cross_row_headers_are_not_receipts(rows):
    assert view._header(rows) is None


def test_legitimate_field_spacing_and_distant_border_unknowns_remain_readable():
    line=hud(UNKNOWN+'   3わ 2ねん 12のつき 131G   '+UNKNOWN)
    assert view._header([line])=={'chapter':3,'year':2,'month':12,'gold':131}


def navigation_state():
    frame=read_png(FIX/'g745-monster-row1-8064.png')
    screen=view.parse(frame,phase=bot.classify(frame))
    reading=menu_navigation_reading(screen)
    return {'step':7,'screen_kind':screen.kind,'menu_navigation_reading':{**reading,'step':7},'policy':{}}


def test_observed_single_menu_move_reobserves_at_five_hundred_ms():
    state=navigation_state();before=deepcopy(state)
    assert entry.observation_interval_ms(state,[pad('down')])==500
    assert state==before
    # This is a cadence change only: selection still needs the next raw frame.
    assert entry.observation_interval_ms(state,[pad('a')])==1500
    assert entry.observation_interval_ms(state,[])==1500
    assert entry.observation_interval_ms(state,[pad('b')])==1500


@pytest.mark.parametrize('actions',[[pad('up')],[pad('left')],[pad('down'),pad('down')],
    [pad('down'),pad('a')],[{**pad('down'),'hold_ms':200}],
    [{**pad('down'),'hold_ms':True}],[{**pad('down'),'buttons':['down','a']}],None])
def test_unobserved_direction_long_press_and_batches_keep_normal_cadence(actions):
    assert entry.observation_interval_ms(navigation_state(),actions)==1500


@pytest.mark.parametrize('change',['missing','previous_step','different_kind'])
def test_saved_or_foreign_menu_observation_cannot_keep_fast_cadence(change):
    state=navigation_state()
    if change=='missing':state.pop('menu_navigation_reading')
    elif change=='previous_step':state['menu_navigation_reading']['step']=6
    else:state['screen_kind']='text'
    assert not menu_navigation_feedback(state,[pad('down')])


def test_orchestrator_overwrites_menu_reading_even_when_the_next_frame_is_dialogue():
    frame=read_png(FIX/'g745-god-dialogue-8128.png')
    _,state=bot.decide(frame,navigation_state())
    assert state['menu_navigation_reading'] is None
    assert entry.observation_interval_ms(state,[pad('down')])==1500


@pytest.mark.parametrize('field,value',[('step',True),('cursor',None),('cursor',[176,True]),
    ('cursor',[256,176]),('directions',None),('directions',['a']),('kind','unknown')])
def test_corrupt_saved_readings_are_not_fast_feedback(field,value):
    state=navigation_state();state['menu_navigation_reading'][field]=value
    assert not menu_navigation_feedback(state,[pad('down')])


def test_feedback_plan_keeps_the_original_menu_rgb_and_current_step(tmp_path):
    frame=read_png(FIX/'g745-monster-row1-8064.png')
    evidence=json.loads((FIX/'g745-menu-reading-evidence.json').read_text())[0]
    state=navigation_state();actions=[pad('down')]
    with patch('docich.hanjuku_scene.observe'),patch('docich.hanjuku_progress.record'):
        entry.persist(tmp_path,state,[],{'hanjuku':evidence['runtime']},
                      actions=actions,frame_sha256=frame.digest(),frame=frame)
    rows=[json.loads(l) for l in (tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan=next(r for r in rows if r['event']=='action_plan')
    assert plan['observation_interval_ms']==500
    assert plan['planned_actions']==actions
    assert plan['menu_navigation_feedback']==state['menu_navigation_reading']
    assert plan['frame_sha256']==read_png(tmp_path/'hanjuku_frames'/plan['snapshot']).digest()


@pytest.mark.parametrize('channel',[0,1,2])
def test_colour_tolerance_is_inclusive_on_all_three_channels(channel):
    for delta,wanted in [(-11,False),(-10,True),(0,True),(10,True),(11,False)]:
        pixel=[100,110,120];pixel[channel]+=delta
        assert view._near(pixel,(100,110,120),10) is wanted
