"""Deferred-goal ledger: observed needs stay queued until a receipt closes them."""
import pytest

from docich.hanjuku.flows import house, month
from docich.hanjuku.strategy import campaign, goals
from docich.hanjuku.strategy.goals import GOAL_LIMIT, STARVE_ATTEMPTS
from docich.hanjuku_screen import Screen


def mem(**extra):
    return {'chapter': 1, 'tick': 100, 'month': '1-6', '_records': [], **extra}


def kinds(mem):
    return [g['kind'] for g in goals.pending(mem)]


def records(mem, decision):
    return [r for r in mem['_records'] if r['decision'] == decision]


def test_refresh_queues_each_observed_need_once():
    m = mem(lost=['ゴーメン'],
            house_eggs={'ゼウス': {'broken': True}},
            egg_uses={'どうし': 0},
            captured=['カストーラ'],
            garrison={'カストーラ': []})
    goals.refresh(m)
    assert kinds(m) == ['retake', 'repair', 'egg_recover', 'move']
    repair = goals._find(m, 'repair', actor='ゼウス')
    assert repair['attempts'] == 0 and repair['queued'] == 100
    assert goals._find(m, 'egg_recover')['targets'] == ['どうし']
    assert len(records(m, 'goal_queued')) == 4


def test_lost_castle_outside_the_chapter_map_is_not_a_goal():
    m = mem(lost=['スペランザ'])            # a chapter-2 castle while chapter is 1
    goals.refresh(m)
    assert 'retake' not in kinds(m)


def test_refresh_is_idempotent_and_keeps_queue_order():
    m = mem(lost=['ゴーメン'], house_eggs={'ゼウス': {'broken': True}})
    goals.refresh(m)
    goals.note_attempt(m, 'repair', 'deferred', actor='ゼウス')
    goals.refresh(m)
    assert kinds(m) == ['retake', 'repair']
    assert len(records(m, 'goal_queued')) == 2
    assert goals._find(m, 'repair', actor='ゼウス')['attempts'] == 1


def test_egg_recover_goal_survives_an_active_boss_sortie():
    # The motivating case: a launched sortie must not erase the recovery want.
    m = mem(active='1-B1',
            sorties={'1-B1': {'status': 'en_route', 'target': 'ゴーメン', 'tick': 90}},
            captured=['ゴーメン', 'スペンソニア'],
            garrison={'ゴーメン': ['ゼウス'], 'スペンソニア': ['ヴィーナス']},
            egg_uses={'どうし': 0})
    goals.refresh(m)
    assert 'egg_recover' in kinds(m)
    # An en-route march already covers an empty castle: no duplicate move goal.
    m['garrison']['ゴーメン'] = []
    goals.refresh(m)
    assert 'move' not in kinds(m)
    assert 'egg_recover' in kinds(m)


def test_resolved_goal_leaves_the_ledger_with_its_attempt_history():
    m = mem(house_eggs={'ゼウス': {'broken': True}})
    goals.refresh(m)
    goals.note_attempt(m, 'repair', 'deferred', actor='ゼウス')
    m['house_eggs']['ゼウス']['broken'] = False
    goals.refresh(m)
    assert not goals.pending(m)
    resolved = records(m, 'goal_resolved')
    assert len(resolved) == 1 and resolved[0]['observed_metric']['attempts'] == 1


def test_note_attempt_counts_each_outcome_and_records_starvation():
    m = mem(house_eggs={'ゼウス': {'broken': True}})
    goals.refresh(m)
    for _ in range(STARVE_ATTEMPTS):
        goals.note_attempt(m, 'repair', 'deferred', actor='ゼウス')
    goal = goals._find(m, 'repair', actor='ゼウス')
    assert goal['attempts'] == STARVE_ATTEMPTS and goal['outcome'] == 'deferred'
    assert len(records(m, 'goal_starved')) == 1


def test_note_attempt_is_a_noop_when_the_want_is_gone():
    m = mem()
    assert goals.note_attempt(m, 'repair', 'deferred', actor='ゼウス') is None
    assert m['_records'] == []


def test_the_ledger_is_bounded_and_reports_overflow():
    m = mem(house_eggs={f'将軍{i}': {'broken': True} for i in range(GOAL_LIMIT + 6)})
    goals.refresh(m)
    assert len(goals.pending(m)) == GOAL_LIMIT
    assert m['goals_overflow'] == 6
    assert len(records(m, 'goal_queue_full')) == 1


def test_chapter_change_discards_the_ledger():
    m = mem(lost=['ゴーメン'])
    goals.refresh(m)
    assert goals.pending(m)
    campaign._enter_chapter(m, 2, reason='test chapter advance')
    assert 'goals' not in m


def test_interrupted_unpaid_egg_sub_requeues_for_the_month():
    m = mem(shop={'key': '1-6', 'egg': 'opened'},
            month_sub={'kind': 'egg', 'key': '1-6', 'presses': 3},
            egg_uses={'どうし': 0})
    goals.refresh(m)
    assert month.month_sub_step(Screen([], None, '', kind='battle'), m) is None
    assert m['shop']['egg'] == 'pending'          # retryable this month
    assert 'month_sub' not in m
    assert records(m, 'month_sub_requeued') and records(m, 'month_sub_lost')
    goal = goals._find(m, 'egg_recover')
    assert goal['attempts'] == 1 and goal['outcome'] == 'interrupted'


def test_interrupted_paid_egg_sub_is_not_silently_requeued():
    m = mem(shop={'key': '1-6', 'egg': 'opened'},
            month_sub={'kind': 'egg', 'key': '1-6', 'stage': 'recovering'})
    assert month.month_sub_step(Screen([], None, '', kind='battle'), m) is None
    assert m['shop']['egg'] == 'opened'           # payment may have happened
    assert not records(m, 'month_sub_requeued')
    assert records(m, 'month_sub_lost')


def field_scan_mem(**extra):
    return mem(house_eggs={'ゼウス': {'broken': True, 'month': '1-6'}},
               gold=500,
               recruit_field_scan_attempts={'scope': [1, '1-6'], 'count': 2},
               house_field_scan={'scope': [1, '1-6'], 'tick': 95},
               captured=['カストーラ'],
               garrison={'カストーラ': ['ゼウス']}, **extra)


def test_repair_goal_bypasses_the_field_scan_budget_when_idle():
    m = field_scan_mem()
    goals.refresh(m)
    goals.note_map_idle(m)
    assert house.step(Screen([], None, '', kind='map'), m, None) is not None
    assert m['house']['phase'] == 'open_roster'
    assert records(m, 'house_goal_dispatch')


def test_exhausted_field_budget_still_blocks_without_a_goal():
    m = field_scan_mem()
    m['house_eggs'] = {}
    goals.refresh(m)
    assert not goals.repair_due(m)
    assert house.step(Screen([], None, '', kind='map'), m, None) is None
    assert 'house' not in m


def test_repair_goal_sleeps_after_attempt_limit_until_location_changes():
    """Unreachable repair targets sleep instead of burning every idle frame."""
    m = mem(house_eggs={'ソーピニヨン': {'broken': True, 'location': 'castle', 'month': '38-3'}},
            garrison={'アルマムーン': ['どうし']})
    goals.refresh(m)
    for _ in range(goals.REPAIR_SLEEP_ATTEMPTS):
        goals.note_attempt(m, 'repair', 'interrupted', actor='ソーピニヨン')
    assert goals.repair_due(m) is False
    goal = goals._find(m, 'repair', actor='ソーピニヨン')
    assert goal['sleep_signature'] == ['castle', '38-3', False, None]
    assert len(records(m, 'repair_goal_sleeping')) == 1
    assert goals.repair_due(m) is False
    m['garrison']['アルマムーン'].append('ソーピニヨン')
    assert goals.repair_due(m) is True
    assert goal['attempts'] == 0
    assert 'sleep_signature' not in goal


def test_sleeping_repair_goal_wakes_when_the_purse_crosses_a_bracket():
    """A gold-deferred repair wakes once a gift is affordable again."""
    m = mem(gold=50,
            house_eggs={'ゼウス': {'broken': True, 'location': 'field', 'month': '37-2'}},
            garrison={'アルマムーン': ['どうし']})
    goals.refresh(m)
    for _ in range(goals.REPAIR_SLEEP_ATTEMPTS):
        goals.note_attempt(m, 'repair', 'deferred', actor='ゼウス')
    assert goals.repair_due(m) is False
    m['gold'] = 250
    assert goals.repair_due(m) is True


def test_sleeping_repair_goal_does_not_block_a_due_one():
    """One unreachable actor sleeps while a reachable repair stays due."""
    m = mem(house_eggs={'ソーピニヨン': {'broken': True, 'location': 'castle', 'month': '38-3'},
                        'ブカティーニ': {'broken': True, 'location': 'castle', 'month': '38-3'}},
            garrison={'アルマムーン': ['どうし', 'ブカティーニ']})
    goals.refresh(m)
    for _ in range(goals.REPAIR_SLEEP_ATTEMPTS):
        goals.note_attempt(m, 'repair', 'interrupted', actor='ソーピニヨン')
    assert goals.repair_due(m) is True


def test_repair_goal_stays_due_below_the_attempt_limit():
    m = mem(house_eggs={'ゼウス': {'broken': True, 'location': 'castle', 'month': '37-2'}},
            garrison={'アルマムーン': ['どうし', 'ゼウス']})
    goals.refresh(m)
    for _ in range(goals.REPAIR_SLEEP_ATTEMPTS - 1):
        goals.note_attempt(m, 'repair', 'interrupted', actor='ゼウス')
    assert goals.repair_due(m) is True


def test_a_general_a_complete_roster_no_longer_lists_is_not_a_repair_goal():
    """g1097: ゼウス left the army at 37-2 and kept ``repair_due`` true forever."""
    m = mem(house_eggs={'ゼウス': {'broken': True}, 'ヴィーナス': {'broken': True}})
    goals.refresh(m)
    assert goals.repair_due(m)

    m['recruit_roster'] = {'chapter': 1, 'month': '1-6', 'names': ['どうし', 'ヴィーナス'], 'complete': True}
    goals.refresh(m)
    assert [g['actor'] for g in goals.pending(m, 'repair')] == ['ヴィーナス']
    assert goals.repair_due(m)


def test_a_roster_scan_in_progress_does_not_requeue_a_departed_general():
    m = mem(house_eggs={'ゼウス': {'broken': True}},
            recruit_roster={'chapter': 1, 'month': '1-6', 'names': ['どうし'], 'complete': True})
    goals.refresh(m)
    assert not goals.repair_due(m)

    m['recruit_roster'] = {'chapter': 1, 'month': '1-7', 'names': [], 'complete': False}
    goals.refresh(m)
    assert not goals.repair_due(m) and not records(m, 'goal_queued')


def test_an_incomplete_roster_proves_nothing_and_a_rejoined_general_returns():
    m = mem(house_eggs={'ゼウス': {'broken': True}},
            recruit_roster={'chapter': 1, 'month': '1-6', 'names': ['どうし'], 'complete': False})
    goals.refresh(m)
    assert goals.repair_due(m)

    m['recruit_roster'] = {'chapter': 1, 'month': '1-6', 'names': ['どうし'], 'complete': True}
    goals.refresh(m)
    assert not goals.repair_due(m)
    m['recruit_roster'] = {'chapter': 1, 'month': '1-7', 'names': ['ゼウス'], 'complete': False}
    goals.refresh(m)
    assert goals.repair_due(m)


def test_another_chapters_roster_proves_nothing():
    m = mem(house_eggs={'ゼウス': {'broken': True}},
            recruit_roster={'chapter': 2, 'names': ['どうし'], 'complete': True})
    goals.refresh(m)
    assert goals.repair_due(m)
