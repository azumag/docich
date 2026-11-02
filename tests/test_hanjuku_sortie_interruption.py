"""Interrupted sorties must be reconciled from observations, not dispatched twice."""
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_screen import Screen
from docich.hanjuku_font import TextLine, UNKNOWN


def memory():
    return {'chapter': 1, 'active': '1-A2', 'orders': {'1-A2': 'pending'},
            'general_override': {'1-A2': 'ゼウス'}, 'source_override': {'1-A2': 'アルマムーン'},
            'garrison': {'アルマムーン': ['どうし', 'ゼウス']},
            'order_context': {'1-A2': {'actual_general': 'ゼウス'}},
            'sortie_attempt': {'step': '1-A2', 'target_seen': True}}


def screen(kind, words=()):
    lines = [TextLine(47 + 16*i, tuple((144 + 8*j, c) for j,c in enumerate(w)))
             for i,w in enumerate(words)]
    return Screen(kind=kind, text=''.join(words), lines=lines, hand=(122,41,139,53))


@pytest.mark.parametrize('kind', ['defense_started', 'unknown', 'map', 'text', 'battle'])
def test_interrupt_records_unknown_destination_once_and_blocks_same_general(kind):
    mem = memory()
    p.observe_sortie_transition(screen(kind), mem, 'map_target')
    assert mem['orders']['1-A2'] == 'launched_unconfirmed'
    assert mem['active'] is None
    assert mem['sorties']['1-A2']['target'] is None
    assert p._en_route(mem) == ({'ゼウス'}, set())
    assert p.next_order(mem)['step'] != '1-A2'
    first = deepcopy(mem['sorties'])
    mem['active'] = '1-A2'  # later source verification must not count as a new interruption
    p.observe_sortie_transition(screen('general_list'), mem, 'castle_menu')
    assert mem['sorties'] == first
    assert len([r for r in mem['_records'] if r['decision']=='order_launched_unconfirmed']) == 1


def test_hotload_migrates_verified_old_target_without_attempt():
    mem = memory(); mem.pop('sortie_attempt')
    p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    assert mem['orders']['1-A2'] == 'launched_unconfirmed'


@pytest.mark.parametrize('active', [None, '1-A2'])
def test_marker_return_resumes_same_target(active):
    mem = memory()
    p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    mem['active'] = active
    p.observe_sortie_transition(screen('map_target'), mem, 'unknown')
    assert mem['active'] == '1-A2' and mem['orders']['1-A2'] == 'pending'
    assert not mem['sorties']
    assert not mem['sortie_attempt'].get('interrupted')


def test_confirm_before_any_target_is_not_departure():
    mem = memory(); mem['sortie_attempt']['target_seen'] = False
    p.observe_sortie_transition(screen('unknown'), mem, 'sortie_confirm')
    assert not mem.get('sorties')


def test_real_entry_confirms_actual_not_planned_destination():
    mem = memory(); p.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
    p.message_step(screen('attack_started', ['ゼウスしょうぐんがジョンリギじょうにのりこんだ']), mem)
    assert mem['sorties']['1-A2']['target'] == 'ジョンリギ'
    assert mem['sorties']['1-A2']['status'] == 'arrived'
    assert mem['attack']['step'] == '1-A2'
    assert mem['orders']['1-A2'] == 'launched'
    assert mem['garrison']['アルマムーン'] == ['どうし']
    assert not mem.get('sortie_attempt')


def test_ambiguous_general_does_not_bind_unknown_destination():
    mem = memory(); p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    mem['sorties']['other'] = dict(mem['sorties']['1-A2'])
    assert p._match_sortie(mem, 'ジョンリギ', 'ゼウス') == (None, 'ambiguous')


def test_a_stale_unread_arrival_no_longer_makes_the_next_entry_ambiguous():
    """g749 2026-10-14: sixty unread ``en_route`` records, two of them ヴィーナス
    to けっかい and a キッシュ/ゴーメン pair, made every later entry message
    ambiguous, so the arrival was never bound and no sortie could start."""
    mem = memory(); mem['tick'] = 100000
    stale = {'general': 'ゼウス', 'target': 'ジョンリギ', 'status': 'en_route',
             'source': 'アルマムーン', 'tick': mem['tick'] - p.SORTIE_BUSY_TICKS - 1}
    fresh = {**stale, 'tick': mem['tick'] - 1}
    mem['sorties'] = {'old': stale, 'new': fresh}
    assert p._match_sortie(mem, 'ジョンリギ', 'ゼウス') == ('new', 'matched')


def test_a_general_listed_in_an_intervening_own_castle_continues_his_order():
    """Owner (2026-10-14): our own castles print no entry message, so the
    general list of the castle he stopped in is the only measurement."""
    mem = memory(); mem['tick'] = 5000
    mem['captured'] = ['アルマムーン', 'ジョンリギ']
    mem['sorties'] = {'A:1:J1': {'general': 'ゼウス', 'target': 'けっかい', 'status': 'en_route',
                                 'source': 'スペンソニア', 'tick': mem['tick'] - 10}}
    order = {'step': 'A:1:J1', 'general': 'ゼウス', 'source': 'スペンソニア', 'target': 'けっかい',
             'cards': [], 'after': None, 'note': 't'}
    mem['chart_plan'] = {'request_id': 'x', 'orders': [order]}
    assert p._continue_transit_sorties(mem, 'ジョンリギ', ['ゼウス', 'リーキ']) == ['A:1:J1']
    sortie = mem['sorties']['A:1:J1']
    assert sortie['status'] == 'arrived' and sortie['target'] == 'ジョンリギ'
    assert sortie['transit_to'] == 'けっかい'
    assert mem['source_override']['A:1:J1'] == 'ジョンリギ'
    assert mem['orders']['A:1:J1'] == 'pending'
    assert mem['garrison']['ジョンリギ'] == ['ゼウス']
    assert any(r['decision'] == 'sortie_transit_continue' for r in mem['_records'])
    # The same order runs again, now from the castle he actually stopped in.
    # A new leg to the boss must also satisfy the current unlock prerequisite.
    mem['captured'] = list(set(p.chart.CASTLE_NAMES[1]) - {'アルマムーン', 'けっかい'})
    assert p.next_order(mem) == order


def test_a_stale_general_listed_mid_route_is_a_transit_arrival():
    """Owner 2026-11-02 (g1097): no sortie-age bound — a roster is only read
    when the castle is visited, so a general listed mid-route is a transit
    arrival no matter how long ago the sortie launched."""
    mem = memory(); mem['tick'] = 5000
    sortie = {'general': 'ゼウス', 'target': 'けっかい', 'status': 'en_route',
              'source': 'スペンソニア', 'tick': mem['tick'] - p.SORTIE_BUSY_TICKS - 1}
    mem['sorties'] = {'A:1:J1': dict(sortie)}
    assert p._continue_transit_sorties(mem, 'ジョンリギ', ['ゼウス']) == ['A:1:J1']
    assert mem['sorties']['A:1:J1']['status'] == 'arrived'
    assert mem['sorties']['A:1:J1']['transit_to'] == 'けっかい'


def test_an_unlisted_general_is_not_a_transit_arrival():
    mem = memory(); mem['tick'] = 5000
    sortie = {'general': 'ゼウス', 'target': 'けっかい', 'status': 'en_route',
              'source': 'スペンソニア', 'tick': mem['tick'] - 1}
    mem['sorties'] = {'A:1:J1': dict(sortie)}
    assert p._continue_transit_sorties(mem, 'ジョンリギ', ['リーキ']) == []
    assert mem['sorties']['A:1:J1'] == sortie


@pytest.mark.parametrize('words,expected', [(['ゼウス','どうし'], 'pending'),
                                          (['しょうぐんはおりません'], 'launched')])
def test_source_evidence_allows_retry_only_when_general_is_still_there(words, expected):
    mem = memory(); p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    mem['active'] = '1-A2'
    actions = p.deploy_step(screen('general_list', words), mem)
    assert actions == [p.pad('b'), p.pad('b')]
    assert mem['orders']['1-A2'] == expected
    assert mem['sorties']['1-A2']['target'] is None
    assert mem['active'] is None


@pytest.mark.parametrize('words', [['どうし'], ['ゼウス'+UNKNOWN], [UNKNOWN]])
def test_partial_list_does_not_prove_absence_or_select_replacement(words):
    mem = memory(); p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    mem['active'] = '1-A2'
    assert p.deploy_step(screen('general_list', words), mem) == []
    assert mem['orders']['1-A2'] == 'launched_unconfirmed'
    assert mem['source_override'] == {'1-A2': 'アルマムーン'}


def test_unreachable_verification_does_not_fail_or_resend_sortie():
    mem = memory(); p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    mem['active'] = '1-A2'
    p._give_up_source(mem, p._order(mem))
    assert mem['orders']['1-A2'] == 'launched_unconfirmed'
    assert mem['sorties']['1-A2']['verification_unavailable']
    assert mem['active'] is None


def test_unknown_source_list_does_not_freeze_forever_or_retry():
    mem = memory(); p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    mem['active'] = '1-A2'
    for _ in range(2):
        assert p.deploy_step(screen('general_list', ['どうし']), mem) == []
    assert p.deploy_step(screen('general_list', ['どうし']), mem) == [p.pad('b'),p.pad('b')]
    assert mem['active'] is None and mem['orders']['1-A2'] == 'launched_unconfirmed'


def test_goal_anchor_survives_cursor_occluding_the_roof(monkeypatch):
    # Actual g403 23:19:03/05 roof geometry; synthetic Screen/roof inputs.
    roofs = [{'kind':'own','target':(195,13),'clipped':False},
             {'kind':'own','target':(51,93),'clipped':False},
             {'kind':'own','target':(206,173),'clipped':False}]
    monkeypatch.setattr(p, 'castle_roofs', lambda *a, **k: roofs)
    mem = {'chapter':1,'cursor':[741,804]}
    a = p.nav_step(Screen(lines=[],hand=None,text='',kind='map',cursor=(216,172)),mem,None,(731,805),'アルマムーン')
    assert a == [p.pad('left',10)] and mem['goal_anchor_lock'] == 'アルマムーン'
    roofs.pop()  # cursor at 205 removes the goal roof; other roofs vote x=536
    assert p.nav_step(Screen(lines=[],hand=None,text='',kind='map',cursor=(205,172)),mem,None,(731,805),'アルマムーン') == 'arrived'
    assert mem['cursor'] == [730,804]


@pytest.mark.parametrize('change', ['uncertain','edge','goal','marker'])
def test_goal_lock_does_not_survive_unmeasured_camera_or_changed_goal(monkeypatch,change):
    monkeypatch.setattr(p,'castle_roofs',lambda *a,**k: [])
    mem={'chapter':1,'cursor':[741,804],'goal_anchor_lock':'アルマムーン',
         'nav_last':{'screen':[216,172],'expected':[-10,0]}}
    sc=Screen(lines=[],hand=None,text='',kind='map',cursor=(205,172));goal='アルマムーン'
    if change=='uncertain':mem['uncertain']=True
    if change=='edge':sc.cursor=(232,172)
    if change=='goal':goal='キカンドン'
    if change=='marker':sc.marker=(205,172)
    p.update_world(sc,mem,None,goal)
    assert not mem.get('goal_anchor_lock')
    assert mem['anchor'] is None


def test_unbound_attack_loss_invalidates_old_garrison_without_guessing_a_home():
    mem={'chapter':1,'garrison':{'アルマムーン':['ゼウス','どうし']},
         'battle':{'ally':'ゼウス','ally_hp':0,'enemy_hp':21,'castle':'ジョンリギ',
                   'side':'attack','step':None,'away':1}}
    p.battle_end(mem,'map')
    assert mem['garrison']=={'アルマムーン':['どうし']}
    assert mem['general_location_unknown']==['ゼウス']
    assert not mem.get('retries')
    assert p._interim_source(mem,'ジョンリギ',{'general':'ゼウス','source':'キカンドン'}, {'キカンドン'},set()) is None
    mem.update(active='1-A2',source_override={'1-A2':'アルマムーン'})
    p._observe_garrison(screen('general_list',['ゼウス']),mem,p._order(mem))
    assert mem['general_location_unknown']==[]


def test_decide_hooks_interruption_before_dispatch(monkeypatch):
    from docich import hanjuku_bot,hanjuku_screen
    from docich.hanjuku_pixels import Frame
    monkeypatch.setattr(hanjuku_bot,'classify',lambda f:'event')
    monkeypatch.setattr(hanjuku_screen,'parse',lambda f,**k:screen('unknown'))
    _,state=hanjuku_bot.decide(Frame(256,224,bytes(256*224*3)),
                             {'policy':memory(),'screen_kind':'map_target'})
    assert state['policy']['orders']['1-A2']=='launched_unconfirmed'


def test_real_general_row_geometry_ignores_hand_and_distant_border():
    row=TextLine(39,((120,UNKNOWN),(128,UNKNOWN),(144,'ゼ'),(152,'ウ'),(160,'ス'),(208,UNKNOWN)))
    sc=Screen(lines=[row],text=row.known,kind='general_list',hand=(122,33,142,46))
    assert p._general_visible(sc,'ゼウス')


def test_unknown_name_does_not_reestablish_defeated_general_location():
    mem=memory();mem['general_location_unknown']=['ゼウス']
    p._observe_garrison(screen('general_list',['ゼウス'+UNKNOWN]),mem,p._order(mem))
    assert mem['general_location_unknown']==['ゼウス']
