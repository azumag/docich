"""A chapter-6 boss march may use the whole bindable window (g1133 2026-11-05).

Two boss armies (ヘルメス, リースリング) left オケアナ at ticks 39917/39943 for the
boss castle. The distance-scaled wait was the 800-observation floor (203 chart
cells x 2.4), so at 819 observations the first order was declared lost and sent
again while the army was still walking. The same chapter's measured marches ran
23-34 minutes (1380-2040 observations) for 603-740 cells, so the floor is below
a real boss march. The boss is the last march of the chapter: it may wait the
whole window in which its battle can still bind to the order.
"""
from docich.hanjuku.strategy import orders as O
from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import sorties as _sorties


def boss_sortie(tick=39917, target='ボス'):
    return {'general': 'ヘルメス', 'source': 'オケアナ', 'target': target,
            'status': 'en_route', 'tick': tick}


def memory(tick, target='ボス'):
    return {'chapter': 6, 'tick': tick, '_records': [],
            'orders': {'6-B1': 'launched'},
            'sorties': {'6-B1': boss_sortie(target=target)},
            'launched_orders': {}}


def decisions(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def test_a_ch6_boss_march_waits_for_the_whole_bindable_window():
    assert O._arrival_limit({'chapter': 6}, boss_sortie()) == O.MARCH_ARRIVAL_CAP


def test_a_ch6_march_to_a_normal_castle_keeps_the_distance_limit():
    limit = O._arrival_limit({'chapter': 6}, boss_sortie(target='トウトウ'))
    assert limit == O.BOSS_ARRIVAL_LIMIT
    assert limit < O.MARCH_ARRIVAL_CAP


def test_the_live_boss_march_is_not_declared_lost_at_819_observations():
    mem = memory(39917 + 819)
    O._relaunch_overdue_boss(mem)
    assert mem['orders']['6-B1'] == 'launched'
    assert _sorties(mem)['6-B1']['status'] == 'en_route'
    assert not decisions(mem, 'boss_sortie_overdue')


def test_the_same_march_is_sent_again_after_the_full_window():
    mem = memory(39917 + O.MARCH_ARRIVAL_CAP)
    O._relaunch_overdue_boss(mem)
    assert mem['orders']['6-B1'] == 'pending'
    assert _sorties(mem)['6-B1']['status'] == 'overdue'
    assert mem['boss_relaunch'] == {'6-B1': 1}
    (record,) = decisions(mem, 'boss_sortie_overdue')
    assert record['observed_metric']['marching'] == O.MARCH_ARRIVAL_CAP


def test_a_chapter_without_a_measured_rate_keeps_the_fixed_limit():
    assert O._arrival_limit({'chapter': 3}, boss_sortie()) == O.BOSS_ARRIVAL_LIMIT


def test_a_plan_rebuild_does_not_release_a_boss_march_at_900_observations():
    # g1133 2026-11-05: every plan rebuild released a boss march at the 800
    # floor while _relaunch_overdue_boss waited 1600, so a second general was
    # sent after an army still walking (plan_step_released age 872, ポセイドン).
    mem = memory(39917 + 900)
    replan._release_stale_steps(mem, ['6-B1'])
    assert mem['orders']['6-B1'] == 'launched'
    assert not decisions(mem, 'plan_step_released')


def test_a_normal_march_is_still_released_after_its_distance_limit():
    limit = O._arrival_limit({'chapter': 6}, boss_sortie(target='トウトウ'))
    mem = memory(39917 + limit + 1, target='トウトウ')
    replan._release_stale_steps(mem, ['6-B1'])
    assert mem['orders']['6-B1'] == 'pending'
    assert decisions(mem, 'plan_step_released')


def test_a_boss_march_is_released_after_the_whole_bindable_window():
    mem = memory(39917 + O.MARCH_ARRIVAL_CAP + 1)
    replan._release_stale_steps(mem, ['6-B1'])
    assert mem['orders']['6-B1'] == 'pending'
    (record,) = decisions(mem, 'plan_step_released')
    assert record['observed_metric']['target'] == 'ボス'
