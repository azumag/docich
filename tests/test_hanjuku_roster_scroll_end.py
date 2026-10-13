"""Global list completion from native end markers, never a stalled input."""
from pathlib import Path
import pytest
from docich.hanjuku.flows import house
from docich.hanjuku.os_layer import roster as r
from docich.hanjuku_screen import parse, Screen
from docich.hanjuku_pixels import read_png, Frame
from test_hanjuku_house import memory, feed

ROOT = Path(__file__).parent / 'fixtures/hanjuku-roster-end'
NAMES = ['どうし','ココット','コリアンダー','リーキ','ヘルメス','アルテミス','ラズベリー','キッシュ','シャルドネ','リースリング','ビシソワーズ']

def native(uid=11093):
    return read_png(ROOT / f'g749-{uid}.png')

def scan(*, first=True, seen=None):
    mem=memory('roster_advance', selected='ビシソワーズ', seen=list(NAMES if seen is None else seen), roster_first_general='どうし' if first else None)
    r.begin(mem,mem['house']);r.page(mem,NAMES)
    mem['house']['roster_first_general']='どうし' if first else None
    return mem

def test_native_end_and_continuing_pages():
    assert house._roster_scroll_end(parse(native()), native())
    assert not house._roster_scroll_end(parse(native(11428)), native(11428))

@pytest.mark.parametrize('first,seen', [(False,None),(True,NAMES[:-1])])
def test_end_does_not_complete_walk_starting_midway_or_missing_status(first,seen):
    mem=scan(first=first,seen=seen)
    for _ in range(9):feed(mem,native())
    assert not mem['house'].get('roster_end_observed')
    house.step(Screen([],None,'',kind='map'),mem,None)
    assert not mem['recruit_roster']['complete']

def test_full_native_walk_completes_after_repeated_actual_end_frames():
    mem=scan()
    for _ in range(6):feed(mem,native())
    assert mem['house']['roster_end_observed']
    house.step(Screen([],None,'',kind='map'),mem,None)
    assert mem['recruit_roster']['complete']
    assert mem['recruit_roster']['names']==NAMES

@pytest.mark.parametrize('rgb', [(0,0,0),(8,36,28),(255,254,255)])
def test_missing_faded_or_present_down_marker_is_not_empty(rgb):
    f=native();data=bytearray(f.rgb)
    for y in range(161,165):
        for x in range(192,199):
            i=(y*f.width+x)*3;data[i:i+3]=bytes(rgb)
    changed=Frame(f.width,f.height,bytes(data))
    assert not house._roster_scroll_end(parse(changed),changed)

def test_changed_scope_invalidates_physical_end_receipt():
    mem=scan()
    for _ in range(6):feed(mem,native())
    mem['month']='3-1'
    assert not r.complete(mem,mem['house'])

def test_no_frame_or_wrong_cursor_cannot_prove_end():
    s=parse(native())
    assert not house._roster_scroll_end(s,None)
    s.selected='リーキ'
    assert not house._roster_scroll_end(s,native())

def test_first_general_is_recorded_only_on_opened_global_list():
    from test_hanjuku_house import roster
    mem=memory('roster', roster_opened=True)
    r.begin(mem,mem['house'])
    feed(mem,roster(NAMES[:8],0))
    assert mem['house']['roster_first_general']=='どうし'
    mem=memory('roster', roster_opened=True)
    r.begin(mem,mem['house'])
    feed(mem,roster(NAMES[3:11],0))
    assert mem['house']['roster_first_general'] is None

def test_continuation_frame_clears_end_confirmation():
    mem=scan()
    for _ in range(2):feed(mem,native())
    feed(mem,native(11428))
    assert mem['house']['roster_end_observations']==0
    assert not mem['house'].get('roster_end_observed')

def test_end_flag_does_not_override_invalidated_or_unread_membership():
    for invalidated in (False,True):
        mem=scan();state=mem['house'];state['roster_end_observed']=True
        state['roster_invalidated']=invalidated
        if not invalidated:state['seen']=NAMES[:-1]
        assert not r.complete(mem,state)


@pytest.mark.parametrize('bad', ['シャルトネ', 'シャルネ', 'シャルド'])
def test_unidentified_page_does_not_poison_same_scan(bad):
    from test_hanjuku_house import roster
    mem = scan()
    before = list(mem['recruit_roster']['names'])
    names = [*NAMES[-8:]]
    names[names.index('シャルドネ')] = bad
    screen = parse(roster(names, 7))
    assert house.roster(screen) == names  # selected row is known; no alias correction
    r.page(mem, names)
    assert mem['recruit_roster']['names'] == before
    # A later fully identified native page can finish the original scan;
    # no unknown spelling is folded into a known member.
    for _ in range(6):
        feed(mem, native())
    house.step(Screen([], None, '', kind='map'), mem, None)
    assert mem['recruit_roster']['complete']
    assert mem['recruit_roster']['names'] == NAMES


def test_known_members_and_hero_remain_readable():
    from test_hanjuku_house import roster
    names = ['どうし', 'シャルドネ', 'ヴィーナス', 'クイーン',
             'ヘルメス', 'ゼウス', 'クランベリー', 'タルタル']
    assert house.roster(parse(roster(names, 0))) == names


@pytest.mark.parametrize('bad', ['シャルトネ', 'シャルネ', 'シャルド'])
def test_unknown_selected_name_never_opens_status(bad):
    from test_hanjuku_house import roster
    mem = scan()
    names = list(NAMES[-8:])
    row = names.index('シャルドネ')
    names[row] = bad
    screen = parse(roster(names, row))
    mem['house']['phase'] = 'roster'
    assert house.roster(screen) is None
    assert house.step(screen, mem, None) == []
    assert mem['house']['phase'] == 'roster'


def test_native_unknown_unselected_row_does_not_block_known_next_member():
    from docich.hanjuku.os_layer.commands import pad
    frame = read_png(ROOT / 'g749-20298.png')
    screen = parse(frame)
    names = house._names_at(screen, 168, 39, 151)
    assert names[0] == 'シャルトネ'
    assert screen.selected == 'クランベリー'
    seen = ['どうし','ココット','リーキ','ヘルメス','アルテミス','ラズベリー',
            'キッシュ','シャルドネ','ビシソワーズ','シナモン','ビーツ','ガスパチョ',
            'クミン','ヴィーナス']
    mem = memory('roster_advance', selected='ヴィーナス', seen=seen)
    r.begin(mem, mem['house'])
    r.page(mem, seen)
    assert feed(mem, frame) == []
    assert mem['house']['phase'] == 'roster'
    assert feed(mem, frame) == [pad('a')]
    assert mem['house']['selected'] == 'クランベリー'
    assert mem['recruit_roster']['names'] == seen
    assert 'シャルトネ' not in mem['recruit_roster']['names']
    assert not mem['recruit_roster']['complete']
