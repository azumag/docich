"""Interrupted sorties must be reconciled from observations, not dispatched twice."""
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pytest
from docich import hanjuku_policy as p
from docich.hanjuku.strategy.orders import SORTIE_CANCELLED_OBSERVED_LIMIT
from docich.hanjuku_screen import Screen
from docich.hanjuku_font import TextLine, UNKNOWN
from docich.hanjuku.state.regions import world as _world, plan as _plan_region, sorties as _sorties, flow as _flow, journal as _journal


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
    assert _plan_region(mem)['orders']['1-A2'] == 'launched_unconfirmed'
    assert _plan_region(mem)['active'] is None
    assert _sorties(mem)['1-A2']['target'] is None
    assert p._en_route(mem) == ({'ゼウス'}, set())
    next_order = p.next_order(mem)
    # No other departure is required to exist; the interrupted actor stays busy.
    assert next_order is None or next_order['step'] != '1-A2'
    first = deepcopy(_sorties(mem))
    _plan_region(mem)['active'] = '1-A2'  # later source verification must not count as a new interruption
    p.observe_sortie_transition(screen('general_list'), mem, 'castle_menu')
    assert _sorties(mem) == first
    assert len([r for r in mem['_records'] if r['decision']=='order_launched_unconfirmed']) == 1


def test_hotload_migrates_verified_old_target_without_attempt():
    mem = memory(); _flow(mem).pop('sortie_attempt')
    p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    assert _plan_region(mem)['orders']['1-A2'] == 'launched_unconfirmed'


@pytest.mark.parametrize('active', [None, '1-A2'])
def test_marker_return_resumes_same_target(active):
    mem = memory()
    p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    _plan_region(mem)['active'] = active
    p.observe_sortie_transition(screen('map_target'), mem, 'unknown')
    assert _plan_region(mem)['active'] == '1-A2' and _plan_region(mem)['orders']['1-A2'] == 'pending'
    assert not _sorties(mem)
    assert not _flow(mem)['sortie_attempt'].get('interrupted')


def test_confirm_before_any_target_is_not_departure():
    mem = memory(); _flow(mem)['sortie_attempt']['target_seen'] = False
    p.observe_sortie_transition(screen('unknown'), mem, 'sortie_confirm')
    assert not mem.get('sorties')


def test_real_entry_confirms_actual_not_planned_destination():
    mem = memory(); p.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
    p.message_step(screen('attack_started', ['ゼウスしょうぐんがジョンリギじょうにのりこんだ']), mem)
    assert _sorties(mem)['1-A2']['target'] == 'ジョンリギ'
    assert _sorties(mem)['1-A2']['status'] == 'arrived'
    assert _plan_region(mem)['attack']['step'] == '1-A2'
    assert _plan_region(mem)['orders']['1-A2'] == 'launched'
    assert _world(mem)['garrison']['アルマムーン'] == ['どうし']
    assert not _flow(mem).get('sortie_attempt')


def test_ambiguous_general_does_not_bind_unknown_destination():
    mem = memory(); p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    mem['world']['sorties']['other'] = dict(mem['world']['sorties']['1-A2'])
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
    _plan_region(mem)['chart_plan'] = {'request_id': 'x', 'orders': [order]}
    assert p._continue_transit_sorties(mem, 'ジョンリギ', ['ゼウス', 'リーキ']) == ['A:1:J1']
    sortie = _sorties(mem)['A:1:J1']
    assert sortie['status'] == 'arrived' and sortie['target'] == 'ジョンリギ'
    assert sortie['transit_to'] == 'けっかい'
    assert _plan_region(mem)['source_override']['A:1:J1'] == 'ジョンリギ'
    assert _plan_region(mem)['orders']['A:1:J1'] == 'pending'
    assert _world(mem)['garrison']['ジョンリギ'] == ['ゼウス']
    assert any(r['decision'] == 'sortie_transit_continue' for r in mem['_records'])
    # The actual list still holds only the transit actor. Even after the
    # boss path is unlocked, the castle must keep its last defender.
    mem['captured'] = list(set(p.chart.CASTLE_NAMES[1]) - {'アルマムーン', 'けっかい'})
    assert p.next_order(mem) != order
    # Once the other observed defender is in the garrison, continuing the
    # original route is allowed from the newly observed source.
    _world(mem)['garrison']['ジョンリギ'] = ['ゼウス', 'リーキ']
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
    assert _sorties(mem)['A:1:J1']['status'] == 'arrived'
    assert _sorties(mem)['A:1:J1']['transit_to'] == 'けっかい'


def test_an_unlisted_general_is_not_a_transit_arrival():
    mem = memory(); mem['tick'] = 5000
    sortie = {'general': 'ゼウス', 'target': 'けっかい', 'status': 'en_route',
              'source': 'スペンソニア', 'tick': mem['tick'] - 1}
    mem['sorties'] = {'A:1:J1': dict(sortie)}
    assert p._continue_transit_sorties(mem, 'ジョンリギ', ['リーキ']) == []
    assert mem['world']['sorties']['A:1:J1'] == sortie


@pytest.mark.parametrize('words,expected', [(['ゼウス','どうし'], 'pending'),
                                          (['しょうぐんはおりません'], 'launched')])
def test_source_evidence_allows_retry_only_when_general_is_still_there(words, expected):
    mem = memory(); p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    _plan_region(mem)['active'] = '1-A2'
    actions = p.deploy_step(screen('general_list', words), mem)
    assert actions == [p.pad('b'), p.pad('b')]
    assert _plan_region(mem)['orders']['1-A2'] == expected
    assert _sorties(mem)['1-A2']['target'] is None
    assert _plan_region(mem)['active'] is None


@pytest.mark.parametrize('words', [['どうし'], ['ゼウス'+UNKNOWN], [UNKNOWN]])
def test_partial_list_does_not_prove_absence_or_select_replacement(words):
    mem = memory(); p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    _plan_region(mem)['active'] = '1-A2'
    assert p.deploy_step(screen('general_list', words), mem) == []
    assert _plan_region(mem)['orders']['1-A2'] == 'launched_unconfirmed'
    assert _plan_region(mem)['source_override'] == {'1-A2': 'アルマムーン'}


def test_unreachable_verification_does_not_fail_or_resend_sortie():
    mem = memory(); p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    _plan_region(mem)['active'] = '1-A2'
    p._give_up_source(mem, p._order(mem))
    assert _plan_region(mem)['orders']['1-A2'] == 'launched_unconfirmed'
    assert _sorties(mem)['1-A2']['verification_unavailable']
    assert _plan_region(mem)['active'] is None


def test_unknown_source_list_does_not_freeze_forever_or_retry():
    mem = memory(); p.observe_sortie_transition(screen('unknown'), mem, 'map_target')
    _plan_region(mem)['active'] = '1-A2'
    for _ in range(2):
        assert p.deploy_step(screen('general_list', ['どうし']), mem) == []
    assert p.deploy_step(screen('general_list', ['どうし']), mem) == [p.pad('b'),p.pad('b')]
    assert _plan_region(mem)['active'] is None and _plan_region(mem)['orders']['1-A2'] == 'launched_unconfirmed'


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
    assert _world(mem)['garrison']=={'アルマムーン':['どうし']}
    assert _world(mem)['general_location_unknown']==['ゼウス']
    assert not mem.get('retries')
    assert p._interim_source(mem,'ジョンリギ',{'general':'ゼウス','source':'キカンドン'}, {'キカンドン'},set()) is None
    mem.update(active='1-A2',source_override={'1-A2':'アルマムーン'})
    p._observe_garrison(screen('general_list',['ゼウス']),mem,p._order(mem))
    assert _world(mem)['general_location_unknown']==[]


def test_decide_hooks_interruption_before_dispatch(monkeypatch):
    from docich import hanjuku_bot,hanjuku_screen
    from docich.hanjuku_pixels import Frame
    monkeypatch.setattr(hanjuku_bot,'classify',lambda f:'event')
    monkeypatch.setattr(hanjuku_screen,'parse',lambda f,**k:screen('unknown'))
    _,state=hanjuku_bot.decide(Frame(256,224,bytes(256*224*3)),
                             {'policy':memory(),'screen_kind':'map_target'})
    assert _plan_region(state['policy'])['orders']['1-A2']=='launched_unconfirmed'


def test_real_general_row_geometry_ignores_hand_and_distant_border():
    row=TextLine(39,((120,UNKNOWN),(128,UNKNOWN),(144,'ゼ'),(152,'ウ'),(160,'ス'),(208,UNKNOWN)))
    sc=Screen(lines=[row],text=row.known,kind='general_list',hand=(122,33,142,46))
    assert p._general_visible(sc,'ゼウス')


@pytest.mark.parametrize('kind', ['defense_started', 'boss_attack_started', 'battle_intro'])
def test_battle_interruption_is_recorded_apart_from_other_picker_closes(kind):
    """docich#5020: 6 of 9 sorties in a battle-heavy month closed the picker as
    unconfirmed. The cause (a battle taking the frame) must be countable, or the
    monthly audit cannot tell it from an input the game ignored."""
    mem = memory()
    p.observe_sortie_transition(screen(kind), mem, 'map_target')
    counted = [r for r in mem['_records'] if r['decision'] == 'launch_interrupted_by_battle']
    assert len(counted) == 1
    assert counted[0]['general'] == 'ゼウス'
    assert counted[0]['observed_metric']['screen'] == kind
    assert counted[0]['chart_step'] == '1-A2'
    # The unconfirmed departure itself still closes the attempt as before.
    assert _plan_region(mem)['orders']['1-A2'] == 'launched_unconfirmed'


@pytest.mark.parametrize('kind', ['unknown', 'map', 'general_list'])
def test_other_picker_closes_are_not_counted_as_battle_interruptions(kind):
    mem = memory()
    p.observe_sortie_transition(screen(kind), mem, 'map_target')
    assert _plan_region(mem)['orders']['1-A2'] == 'launched_unconfirmed'
    assert not [r for r in mem['_records'] if r['decision'] == 'launch_interrupted_by_battle']


def test_unknown_name_does_not_reestablish_defeated_general_location():
    mem=memory();_world(mem)['general_location_unknown']=['ゼウス']
    p._observe_garrison(screen('general_list',['ゼウス'+UNKNOWN]),mem,p._order(mem))
    assert _world(mem)['general_location_unknown']==['ゼウス']


def _verify_cancel(mem, words=('ゼウス', 'どうし')):
    """Drive the source-roster reconciliation of the unconfirmed launch."""
    _plan_region(mem)['active'] = '1-A2'
    return p.deploy_step(screen('general_list', list(words)), mem)


def _reopen_picker(mem):
    """The re-driven dispatch confirms the sortie and the picker re-opens."""
    _flow(mem)['sortie_attempt'] = {'step': '1-A2', 'target_seen': False}
    p.observe_sortie_transition(screen('map_target'), mem, 'map')


def _outcomes(mem):
    return [r['resulting_event'] for r in mem['_records']
            if r['decision'] == 'launch_interrupted_by_battle']


def test_battle_interrupted_picker_resumes_same_month_then_establishes():
    """docich#5020 改善案1・2: a battle that closes the destination picker owes
    one same-month re-open. The roster verify sees the cancelled general, the
    same order keeps driving (将軍・切り札据え置き), the picker re-opens for
    real (resumed) and a real send closes the incident (established)."""
    mem = memory(); mem['month'] = 4
    p.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
    assert _plan_region(mem)['orders']['1-A2'] == 'launched_unconfirmed'
    first = [r for r in mem['_records'] if r['decision'] == 'launch_interrupted_by_battle']
    assert len(first) == 1
    assert first[0]['resulting_event'] is None    # incident opened, not yet resolved
    assert first[0]['observed_metric']['resume'] == {'armed': True, 'used': 0, 'limit': 2}
    # The roster still lists the general: the launch cancelled and the
    # same-month picker re-open is owed -- the step keeps driving.
    assert _verify_cancel(mem) == [p.pad('b'), p.pad('b')]
    assert _plan_region(mem)['orders']['1-A2'] == 'pending'
    assert _plan_region(mem)['active'] == '1-A2'
    # The re-driven dispatch re-opens the destination picker: one spent re-open.
    _reopen_picker(mem)
    # Repeated frames of the same open picker spend nothing more.
    p.observe_sortie_transition(screen('map_target'), mem, 'map_target')
    resumed = [r for r in mem['_records'] if r['decision'] == 'launch_interrupted_by_battle'
               and r['resulting_event'] == 'resumed']
    assert len(resumed) == 1
    assert resumed[0]['observed_metric']['resume'] == {'outcome': 'resumed', 'driven': True,
                                                      'used': 1, 'limit': 2}
    # The resumed sortie's observed send closes the incident as established.
    p._finish_order(mem, 'launched')
    assert _outcomes(mem) == [None, 'resumed', 'established']
    assert not (_flow(mem).get('sortie_resume') or {})


def test_two_failed_picker_reopens_cut_off_to_launched_unconfirmed():
    """docich#5020: at most two same-month re-opens per step. The interruption
    that finds the budget spent closes as exhausted and the order returns to
    the existing launched_unconfirmed terminal path."""
    mem = memory(); mem['month'] = 4
    for _ in range(2):
        p.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
        assert _verify_cancel(mem) == [p.pad('b'), p.pad('b')]
        _reopen_picker(mem)
    # The picker is open again (third dispatch) when another battle interrupts.
    _flow(mem)['sortie_attempt'] = {'step': '1-A2', 'target_seen': True}
    _plan_region(mem)['active'] = '1-A2'
    p.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
    assert _plan_region(mem)['orders']['1-A2'] == 'launched_unconfirmed'
    assert _plan_region(mem)['active'] is None
    assert _outcomes(mem) == [None, 'resumed', None, 'resumed', 'exhausted']
    # Exhausted: the cancelled sortie verifies but no re-open is owed, so the
    # order releases to the ordinary unconfirmed path like any other cancel.
    assert _verify_cancel(mem) == [p.pad('b'), p.pad('b')]
    assert _plan_region(mem)['orders']['1-A2'] == 'pending'
    assert _plan_region(mem)['active'] is None


def test_picker_resume_never_crosses_the_month():
    """docich#5020: the re-open budget belongs to the interruption's month. A
    sortie verified cancelled in a later month follows the ordinary
    unconfirmed path (pending, released) and no resume is recorded."""
    mem = memory(); mem['month'] = 4
    p.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
    assert (_flow(mem).get('sortie_resume') or {})['1-A2']['armed'] is True
    # The month turns before the source roster is read.
    mem['month'] = 5
    p.observe_sortie_transition(screen('map'), mem, 'map')
    assert '1-A2' not in (_flow(mem).get('sortie_resume') or {})
    assert _verify_cancel(mem) == [p.pad('b'), p.pad('b')]
    assert _plan_region(mem)['orders']['1-A2'] == 'pending'
    assert _plan_region(mem)['active'] is None
    # The new month's own dispatch may still send this order, but it is a fresh
    # sortie, not a resume of the interruption.
    _reopen_picker(mem)
    assert 'resumed' not in _outcomes(mem)


def test_marker_return_records_resumed_without_spending_the_budget():
    """docich#5020: the existing sortie_target_resumed path is preserved -- the
    game's own marker return resolves a battle interruption's resume without
    consuming one of the two driven re-opens."""
    mem = memory(); mem['month'] = 4
    p.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
    p.observe_sortie_transition(screen('map_target'), mem, 'defense_started')
    assert any(r['decision'] == 'sortie_target_resumed' for r in mem['_records'])
    resumed = [r for r in mem['_records'] if r['decision'] == 'launch_interrupted_by_battle'
               and r['resulting_event'] == 'resumed']
    assert len(resumed) == 1
    assert resumed[0]['observed_metric']['resume']['driven'] is False
    assert resumed[0]['observed_metric']['resume']['used'] == 0


def test_resume_source_verification_skips_normal_cancel_suppression_before_map_step():
    """A resume-owed source read must keep the same order startable."""
    mem = memory(); mem['month'] = 4
    p.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
    assert _verify_cancel(mem) == [p.pad('b'), p.pad('b')]
    assert _plan_region(mem)['orders']['1-A2'] == 'pending'
    assert _plan_region(mem)['active'] == '1-A2'
    assert (_flow(mem).get('sortie_resume') or {})['1-A2']['owed'] is True
    assert not (mem.get('sortie_cancelled_observed_counts') or {}).get('1-A2')
    # The factual roster observation remains in the decision log; only the
    # ordinary retry counter/suppression is bypassed for this resume.
    assert [r for r in mem['_records'] if r['decision'] == 'sortie_cancelled_observed']
    assert [r for r in mem['_records'] if r['decision'] == 'sortie_resume_source_confirmed']

    # Exercise the real map gate: it must not clear the resumed order as a
    # normal cancellation suppressed by the generic retry guard.
    p.map_step(screen('map'), mem, None)
    assert _plan_region(mem)['active'] == '1-A2'
    assert _plan_region(mem)['orders']['1-A2'] == 'pending'
    assert not [r for r in mem['_records'] if r['decision'] == 'order_precondition_lost']


def _miss_resumed_picker_target(mem):
    order = p._order(mem)
    assert order and order['step'] == '1-A2'
    actions = None
    for _ in range(p.TARGET_MISS_LIMIT):
        actions = p._unverified_target(screen('map_target'), mem, order, None)
    return actions


def test_resumed_picker_target_misses_use_resume_budget_not_target_cancel_limit():
    """Two target-confirmation failures exhaust the two reopens without a new battle."""
    mem = memory(); mem['month'] = 4
    p.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
    assert _verify_cancel(mem) == [p.pad('b'), p.pad('b')]

    _reopen_picker(mem)
    row = (_flow(mem).get('sortie_resume') or {})['1-A2']
    assert row['used'] == 1 and row['picker_active'] is True
    assert _miss_resumed_picker_target(mem) == [p.pad('b')]
    row = (_flow(mem).get('sortie_resume') or {})['1-A2']
    assert row['used'] == 1 and row['owed'] is True
    assert _plan_region(mem)['orders']['1-A2'] == 'pending'
    assert _plan_region(mem)['active'] == '1-A2'
    assert not (mem.get('target_cancel') or {}).get('1-A2')

    # No second battle occurs: the second failed target confirmation itself
    # consumes the remaining picker-reopen budget.
    _reopen_picker(mem)
    row = (_flow(mem).get('sortie_resume') or {})['1-A2']
    assert row['used'] == 2 and row['picker_active'] is True
    assert _miss_resumed_picker_target(mem) == [p.pad('b')]
    assert '1-A2' not in (_flow(mem).get('sortie_resume') or {})
    assert _plan_region(mem)['orders']['1-A2'] == 'launched_unconfirmed'
    assert _plan_region(mem)['active'] is None
    assert not (mem.get('target_cancel') or {}).get('1-A2')
    assert _outcomes(mem) == [None, 'resumed', 'resumed', 'exhausted']



def test_closed_resumed_picker_does_not_recreate_an_attempt_on_map_return():
    mem = memory()
    mem['month'] = 4
    p.observe_sortie_transition(screen('defense_started'), mem, 'map_target')
    assert _verify_cancel(mem) == [p.pad('b'), p.pad('b')]
    _reopen_picker(mem)
    assert _miss_resumed_picker_target(mem) == [p.pad('b')]
    resume = (_flow(mem).get('sortie_resume') or {})['1-A2']
    assert resume['owed'] is True and resume.get('picker_active') is not True
    assert not _flow(mem).get('sortie_attempt')

    # The B that closed the resumed picker is not a new unsent departure.
    p.observe_sortie_transition(screen('map'), mem, 'map_target')
    assert not _flow(mem).get('sortie_attempt')
    assert _plan_region(mem)['orders']['1-A2'] == 'pending'
    assert _plan_region(mem)['active'] == '1-A2'
    assert not (mem.get('sortie_cancelled_observed_counts') or {}).get('1-A2')

    # The owed step is selected ahead of newer chart orders, but still passes
    # the same source/defender/readiness gates as an ordinary dispatch.
    p.map_step(screen('map'), mem, None)
    assert _plan_region(mem)['active'] == '1-A2'


def test_normal_source_cancel_limit_is_not_overwritten_back_to_pending():
    mem = memory()
    _flow(mem).pop('sortie_resume', None)
    order = {'step': '1-A2', 'source': 'アルマムーン', 'target': 'ジョンリギ',
             'general': 'ゼウス', 'purpose': 'attack'}
    for count in range(1, SORTIE_CANCELLED_OBSERVED_LIMIT + 1):
        _plan_region(mem)['orders']['1-A2'] = 'launched_unconfirmed'
        _plan_region(mem)['active'] = '1-A2'
        _sorties(mem)['1-A2'] = {
            'general': 'ゼウス', 'source': 'アルマムーン', 'target': None,
            'planned_target': 'ジョンリギ', 'purpose': 'attack',
            'status': 'launched_unconfirmed',
        }
        _flow(mem)['sortie_attempt'] = {'step': '1-A2', 'target_seen': True}
        _verify_cancel(mem)
        expected = ('failed' if count == SORTIE_CANCELLED_OBSERVED_LIMIT else 'pending')
        assert _plan_region(mem)['orders']['1-A2'] == expected
    assert mem['sortie_cancelled_observed_counts']['1-A2'] == SORTIE_CANCELLED_OBSERVED_LIMIT
