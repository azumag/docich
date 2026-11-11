"""A camped general is recalled before the month's repairs and locates.

Owner 2026-11-11: 間違った座標にむかったしょうぐんがずっと崖でキャンプしてるよ、戻る口もない.
g1163 queued ジェラート's repair (~3 min, failing) ahead of シュゼット's recall in
name order; the month rolled over each time (12:32, 12:38, 12:44) and the
recall never started. The camp itself came from a resumed target picker that
kept オシロガ's cell after the month menu (12:24:03 resumed, 12:24:05 aligned
on a roof nearby, 12:24:06 sent).
"""
from docich.hanjuku.flows import post_month_status as post
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import Screen


def test_a_camp_recall_runs_before_an_earlier_named_repair_and_locates():
    statuses = {
        'ジェラート': {'location': 'castle', 'castle': 'オシロガ', 'broken': True},
        'ライム': {'location': 'castle'},
        'シュゼット': {'location': 'camp', 'marker': {'castle_candidate': 'サヌト'}},
    }
    assert post._tasks(statuses, list(statuses)) == [
        {'kind': 'recall', 'name': 'シュゼット'},
        {'kind': 'repair', 'name': 'ジェラート'},
        {'kind': 'locate', 'name': 'ライム'}]


def test_a_resumed_target_picker_forgets_the_old_marker_cell():
    marker = Screen(lines=[], hand=None, text='', kind='map_target', marker=(139, 124))
    mem = {'chapter': 9, 'orders': {'9-Z1': 'launched_unconfirmed'}, '_records': [],
           'sortie_attempt': {'step': '9-Z1', 'target_seen': True},
           'cursor': [280, 178], 'near_goal': {'step': '9-Z1', 'goal': 'オシロガ', 'mode': 'target'}}
    policy.observe_sortie_transition(marker, mem, 'month_menu')
    assert [r for r in mem['_records'] if r.get('decision') == 'sortie_target_resumed']
    assert 'cursor' not in mem and 'near_goal' not in mem and mem['uncertain'] is True
