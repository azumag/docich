"""A cold checkpoint restore cancels the menu and never copies the old route."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse,Screen,resume_sortie_panel
from test_hanjuku_owned_sortie_backout import confirmation


def cold_state():
    return {'policy':{'name':{'done':True},'resume_chapter_pending':True,
                      'resume_sortie_cancel':{'readings':0}}}


def test_real_saved_remaining_two_panel_is_cancelled_from_fresh_policy():
    frame=read_png(Path(__file__).parent/'fixtures/hanjuku/g706-resume-card-select.png')
    assert resume_sortie_panel(parse(frame))
    actions,state=decide(frame,cold_state())
    assert actions==[p.pad('b')]
    mem=state['policy']
    assert mem['resume_sortie_cancel']['readings']==1
    assert not any(mem.get(k) for k in ('chapter','active','orders','garrison','captured'))


@pytest.mark.parametrize('partial',[False,True])
def test_next_full_or_partial_confirmation_cannot_be_approved(partial):
    frame=confirmation(partial);state=cold_state()
    assert resume_sortie_panel(parse(frame))
    for _ in range(13):
        actions,state=decide(frame,json.loads(json.dumps(state)))
        assert not any('a' in a.get('buttons',[]) for a in actions)
    assert state['policy']['resume_sortie_cancel']['readings']==12
    assert actions==[]


def test_general_and_castle_menu_backout_has_no_new_sortie_or_actor_binding():
    mem=cold_state()['policy']
    assert p.resume_sortie_step(Screen([],None,'',kind='general_list'),mem)==[p.pad('b')]
    assert p.resume_sortie_step(Screen([],None,'',kind='castle_menu'),mem)==[]
    assert not mem.get('orders') and not mem.get('active')


def test_only_freshly_resolved_chapter_releases_cold_owner_and_requires_new_map_survey():
    mem=cold_state()['policy'];mem.update(chapter=2);mem.pop('resume_chapter_pending')
    assert p.resume_sortie_step(Screen([],None,'',kind='map'),mem) is None
    assert not mem.get('resume_sortie_cancel') and mem['world_map_due'] is True


def test_battle_month_and_regular_nonresume_menus_keep_their_owners():
    mem=cold_state()['policy'];before=deepcopy(mem)
    for kind in ('battle','battle_menu','month_menu','field_encounter_started'):
        assert p.resume_sortie_step(Screen([],None,'',kind=kind),mem) is None
        assert mem==before
    assert p.resume_sortie_step(parse(confirmation(True)),{}) is None


def test_unclassified_restoration_is_not_a_sortie_permission():
    assert not resume_sortie_panel(Screen([],None,'きりふだセレクトあと2こ',kind='card_select'))
    mem=cold_state()['policy']
    assert p.resume_sortie_step(Screen([],None,'うむッ!いかんッ!',kind='yes_no'),mem)==[]


def test_native_cold_backout_reads_castle_status_before_rebuilding_route():
    root=Path(__file__).parent/'fixtures/hanjuku'
    frames=[read_png(root/'g706-resume-card-select.png'),read_png(root/'g706-resume-sortie-confirm.png'),
            None,read_png(root/'g706-resume-castle-menu.png'),
            read_png(root/'g706-resume-status-selected.png'),
            read_png(root/'g706-resume-castle-status.png')]
    state=cold_state()
    for frame,expected in zip(frames[:2],([p.pad('b')],[p.pad('b')])):
        actions,state=decide(frame,json.loads(json.dumps(state)))
        assert actions==expected and not state['policy'].get('chapter')
    # This intervening general list has no chapter or route evidence.
    assert p.resume_sortie_step(Screen([],None,'',kind='general_list'),state['policy'])==[p.pad('b')]
    for frame,expected in zip(frames[3:],([p.pad('down')],[p.pad('a')],[p.pad('b')])):
        actions,state=decide(frame,json.loads(json.dumps(state)))
        assert actions==expected
    mem=state['policy']
    assert mem['chapter']==2 and mem['chapter_evidence']=='スペランザ'
    assert not mem.get('resume_sortie_cancel') and not mem.get('resume_chapter_pending')
    assert mem['world_map_due'] is True
    assert not any(mem.get(k) for k in ('active','orders','sorties','captured'))
