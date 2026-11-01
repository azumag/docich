"""v352: camps are left idle no longer, whichever way the game draws them.

g1097 2026-11-01 (chapter 4): キッシュ, ヴィーナス, ソーピニヨン, エピィ and ゼウス sat
camped for hours at full HP while a dome stayed in view of an idle bot. Live
frames, and the isolated probe of that run's start state ("probe" fixtures):

- g1097-status-holds-camp.png       19:36:01 キッシュ, キャンプしています
- g1097-status-inside-fort.png      19:35:55 ゼウス, キャンプのなかにいます
- g1097-fort-menu-over-dome.png     19:33:04 the stray 砦 menu, 101 frozen observations
- g1097-dome-beside-house.png       19:24:50 the dome that grew out of the lost tent
- g1097-probe-dome-under-cursor.png the cursor on a 砦, door (123, 89, 0)
- g1097-probe-fort-list.png         A on しゅつげき: ヴィーナス / エピィ
- g1097-probe-fort-status.png       A on a name: the status panel
- g1097-probe-status-marching.png   after the sortie: しんぐんちゅうです
"""
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku import orchestrator
from docich.hanjuku.flows import camp_recheck, house, recall
from docich.hanjuku.os_layer import phase
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.readers import fort_list_open, is_camp_menu, is_fort_menu
from docich.hanjuku.os_layer.screen import own_camps, parse
from docich.hanjuku_screen import Screen

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'
IDENTITY = {'game': 'hanjuku-hero', 'generation': 1097, 'runtime_id': 'g1097-4ddae5c8',
            'lease_id': 'lease'}
MAP = Screen([], None, '', kind='map', cursor=(132, 109))
TARGET = Screen([], None, '', kind='map_target', marker=(132, 109))
DOME = {'target': (131, 110), 'clipped': False, 'kind': 'dome'}


def load(name):
    return read_png(FIXTURES / name).resized()  # live screenshots are 299 wide; the bot reads 256


def screen(name):
    frame = load(name)
    return parse(frame, phase=phase.classify(frame))


def memory(**extra):
    return {'chapter': 4, 'tick': 1000, '_records': [], '_run_identity': dict(IDENTITY), **extra}


def decisions(mem, name):
    return [r for r in mem['_records'] if r['decision'] == name]


@pytest.mark.parametrize('name,general', [('g1097-status-holds-camp.png', 'キッシュ'),
                                          ('g1097-status-inside-fort.png', 'ゼウス')])
def test_the_real_status_panel_reads_a_camp_beside_the_roster_column(name, general):
    # The roster's リースリング shares the location row, so the old whole-row
    # comparison could never match on a real frame.
    info = house.general_status(screen(name))
    assert (info['general'], info['camp_observed'], info['location_observed']) == (general, True, True)
    assert info['location'] == 'field'


def test_a_marching_general_has_an_observed_location_but_is_not_camped():
    info = house.general_status(screen('g1097-probe-status-marching.png'))
    assert info['general'] == 'ヴィーナス'
    assert info['location_observed'] is True and 'camp_observed' not in info


@pytest.mark.parametrize('name,general', [('g1097-status-holds-camp.png', 'キッシュ'),
                                          ('g1097-status-inside-fort.png', 'ゼウス')])
def test_the_monthly_status_read_queues_an_offscreen_camp(name, general):
    mem = memory()
    camp_recheck.observe_camp_status(screen(name), mem)
    assert mem['retreat_rechecks'][general]['status'] == 'needs_observation'
    assert decisions(mem, 'camp_status_recall_pending')


def test_the_fort_menu_is_neither_a_castle_menu_nor_a_tent_menu():
    menu = screen('g1097-fort-menu-over-dome.png')
    assert is_fort_menu(menu) and not fort_list_open(menu) and not is_camp_menu(menu)
    assert fort_list_open(screen('g1097-probe-fort-list.png'))
    castle = Screen([], None, 'しゅつげきステータス', kind='castle_menu')
    assert not is_fort_menu(castle)
    tent = Screen([], None, 'いどうステータスキャンプきかん', kind='text')
    assert not is_fort_menu(tent)


def test_a_stray_fort_menu_is_closed_at_once():
    frame = load('g1097-fort-menu-over-dome.png')
    actions, state = orchestrator.decide(frame, {'policy': {'chapter': 4, 'tick': 10}},
                                         run_identity=dict(IDENTITY))
    assert actions == [p.pad('b')]
    assert [r for r in state['_records'] if r['decision'] == 'close_panel']


@pytest.mark.parametrize('name,target,cursor', [
    ('g1097-dome-beside-house.png', (142, 117), None),
    ('g1097-fort-menu-over-dome.png', (126, 117), None),
    ('g1097-probe-dome-under-cursor.png', (131, 110), (132, 109))])
def test_a_dome_is_found_in_every_palette_and_under_the_cursor(name, target, cursor):
    frame = load(name)
    assert own_camps(frame) == [{'target': target, 'clipped': False, 'kind': 'dome'}]
    if cursor:
        assert parse(frame, phase=phase.classify(frame)).cursor == cursor


def fort_recall(**extra):
    return {'stage': 'menu', 'steps': 0, 'target': [131, 110], 'camp_kind': 'dome', **extra}


def test_a_general_leaves_the_fort_by_sortie_and_reaches_the_destination_picker(monkeypatch):
    monkeypatch.setattr(recall, '_recall_goal', lambda _mem: 'アルマムーン')
    mem = memory(recall=fort_recall())
    frame = object()
    assert p.camp_recall_step(screen('g1097-fort-menu-over-dome.png'), mem, frame) == [p.pad('a')]
    assert mem['recall']['stage'] == 'fort_list'
    assert p.camp_recall_step(screen('g1097-probe-fort-list.png'), mem, frame) == [p.pad('a')]
    assert mem['recall']['stage'] == 'fort_status' and mem['recall']['general'] == 'ヴィーナス'
    status = screen('g1097-probe-fort-status.png')
    assert p.camp_recall_step(status, mem, frame) == [p.pad('a')]
    # One press per settled panel: a repeated A on the picker would cancel the sortie.
    assert p.camp_recall_step(status, mem, frame) == []
    assert decisions(mem, 'camp_fort_sortie')[-1]['observed_metric']['members'] == ['ヴィーナス', 'エピィ']


def test_the_named_general_is_the_one_who_leaves():
    mem = memory(recall=fort_recall(stage='fort_list', fort_actor='エピィ'))
    assert p.camp_recall_step(screen('g1097-probe-fort-list.png'), mem, object()) == [p.pad('down')]
    assert mem['recall']['stage'] == 'fort_list'


def test_the_picker_opens_the_y_jump_because_a_fort_has_no_castle_in_view(monkeypatch):
    monkeypatch.setattr(recall, '_recall_goal', lambda _mem: 'アルマムーン')
    monkeypatch.setattr(recall, 'castle_roofs', lambda _frame: [])
    mem = memory(recall=fort_recall(stage='fort_status', general='ヴィーナス'))
    assert p.camp_recall_step(TARGET, mem, object()) == [p.pad('y')]
    assert mem['recall']['stage'] == 'dest' and mem['recall']['fort'] is True
    assert mem['y_jump']['goal'] == 'アルマムーン' and mem['y_jump']['mode'] == 'target'


def test_the_next_general_follows_soon_after_a_fort_sortie():
    mem = memory(recall=fort_recall(stage='await_dispatch', fort=True, general='ヴィーナス', steps=6,
                                    camp_kind='dome'))
    assert p.camp_recall_step(MAP, mem, object()) == []
    assert 'recall' not in mem
    assert mem['recall_skip_ticks'] == recall.FORT_RETRY_TICKS
    assert decisions(mem, 'camp_recall_unconfirmed')[-1]['observed_metric']['general'] == 'ヴィーナス'


def test_a_failing_fort_backs_off_to_the_full_hold():
    mem = memory()
    state = fort_recall(fort=True)
    seen = []
    for _ in range(7):
        recall._arm_recall_skip(mem, state, recall.FORT_RETRY_TICKS)
        seen.append(mem['recall_skip_ticks'])
    assert seen == [20, 40, 80, 160, 320, 400, 400]


def test_a_tent_lost_for_a_moment_is_retried_within_a_minute_or_two(monkeypatch):
    camps = []
    monkeypatch.setattr(p, 'own_camps', lambda _frame: camps)
    mem = memory(recall={'stage': 'to_camp', 'steps': 0, 'target': [150, 120]})
    for _ in range(3):
        assert p.camp_recall_step(MAP, mem, object()) == []
    assert mem['recall_skip_ticks'] == recall.LOST_CAMP_RETRY_TICKS
    camps.append({'target': (150, 120), 'clipped': False})
    mem['tick'] += recall.LOST_CAMP_RETRY_TICKS - 1
    assert p.camp_recall_step(MAP, mem, object()) is None
    mem['tick'] += 1
    assert p.camp_recall_step(MAP, mem, object())
    assert 'recall_skip_ticks' not in mem


def test_losing_the_same_tent_again_doubles_the_wait(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [])
    mem = memory()
    waits = []
    for _ in range(5):
        mem['recall'] = {'stage': 'to_camp', 'steps': 0, 'target': [150, 120]}
        for _ in range(3):
            p.camp_recall_step(MAP, mem, object())
        waits.append(mem['recall_skip_ticks'])
    assert waits == [60, 120, 240, 400, 400]


def test_an_old_hold_without_a_window_keeps_the_full_hold(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (120, 100)}])
    mem = {'chapter': 1, 'tick': 100 + recall.RECALL_SKIP_TICKS - 1, 'recall_skip': {'tick': 100},
           'recall_skip_target': {'chapter': 1, 'target': [120, 100]}}
    assert p.camp_recall_step(MAP, mem, object()) is None


def test_a_dome_is_not_held_back_by_the_tent_it_grew_from(monkeypatch):
    # g1097 19:24:00 lost the tent at (150, 120) as ゼウス walked in; the dome at
    # (142, 117) then stayed on screen under the tent's hold.
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (142, 117), 'clipped': False,
                                                         'kind': 'dome'}])
    mem = memory(recall_skip={'tick': 999}, recall_skip_target={'chapter': 4, 'target': [150, 120]})
    assert p.camp_recall_step(Screen([], None, '', kind='map', cursor=(142, 117)), mem, object()) == [p.pad('a')]
    assert mem['recall']['camp_kind'] == 'dome'


def test_a_visible_camp_goes_before_a_new_survey(monkeypatch):
    monkeypatch.setattr(camp_recheck, 'recall_ready', lambda _mem, _frame: True)
    monkeypatch.setattr(house, '_field_budget', lambda _mem, _scope: (0, False))
    mem = memory(month='35-8', house_scan_month='35-7', house_scan_tick=0,
                 goals=[{'kind': 'repair', 'actor': 'ゼウス', 'target': None, 'attempts': 1}])
    assert house.step(Screen([], None, '', kind='map', cursor=(100, 100)), mem, object()) is None
    assert 'house' not in mem
    assert not decisions(mem, 'scan_started') and not decisions(mem, 'goal_dispatch')


def test_recall_ready_stops_once_the_camp_is_on_hold():
    frame = load('g1097-dome-beside-house.png')
    mem = memory()
    assert camp_recheck.recall_ready(mem, frame) is True
    mem.update(recall_skip={'tick': 999}, recall_skip_target={'chapter': 4, 'target': [142, 117],
                                                              'kind': 'dome'})
    assert camp_recheck.recall_ready(mem, frame) is False
    assert camp_recheck.recall_ready({**mem, 'recall': {'stage': 'to_camp'}}, frame) is False


def test_a_due_named_camp_also_goes_before_a_survey():
    identity = dict(IDENTITY)
    mem = memory(retreat_rechecks={'エピィ': {'identity': identity, 'chapter': 4, 'tick': 900, 'attempts': 0,
                                              'retry_after': 900, 'status': 'needs_observation'}})
    assert camp_recheck.recall_ready(mem, load('g1097-probe-fort-status.png')) is True


def test_a_named_camp_the_recall_would_not_start_does_not_hold_a_survey_back():
    identity = dict(IDENTITY)
    row = {'identity': identity, 'chapter': 4, 'tick': 900, 'attempts': 0, 'retry_after': 900,
           'status': 'needs_observation'}
    frame = load('g1097-probe-fort-status.png')
    waiting = memory(retreat_rechecks={'エピィ': {**row, 'retry_after': 2000}})
    assert camp_recheck.recall_ready(waiting, frame) is False
    spent = memory(retreat_rechecks={'エピィ': {**row, 'attempts': camp_recheck.ATTEMPT_LIMIT}})
    assert camp_recheck.recall_ready(spent, frame) is False
    busy = memory(attack={'target': 'アルカ'}, retreat_rechecks={'エピィ': dict(row)})
    assert camp_recheck.recall_ready(busy, frame) is False


def test_the_named_recheck_hands_a_fort_to_the_sortie_route():
    identity = dict(IDENTITY)
    mem = memory(retreat_rechecks={'エピィ': {'identity': identity, 'chapter': 4, 'tick': 900, 'attempts': 1,
                                              'retry_after': 900, 'status': 'needs_observation'}})
    state = {'stage': 'recheck_status', 'steps': 0, 'reads': 0, 'general': 'エピィ', 'target': [131, 110]}
    mem['recall'] = state
    actions = camp_recheck._hand_over_to_fort(screen('g1097-fort-menu-over-dome.png'), mem, state,
                                              object(), recall.enter_fort)
    assert actions == [p.pad('a')]
    assert mem['recall']['stage'] == 'fort_list' and mem['recall']['fort_actor'] == 'エピィ'
    assert 'エピィ' not in (mem.get('retreat_rechecks') or {})
    assert decisions(mem, 'retreat_recheck_fort')
