"""docich#3505: an intact boss-adjacent camper marches on via いどう.

The monthly pass exempts a camp whose roster triangle lands on the boss
castle from the recall queue. Before this change the exemption only let the
general sit out the exempt months; a healthy one (アルテミス 49/49 in g1133)
lost the boss fight by idling. Healthy siege campers now get a ``boss_move``
task that opens the camp menu's いどう and Y-jumps the destination at the
boss castle.
"""
from docich.hanjuku.flows import post_month_status as post
from docich.hanjuku.flows import recall as recall_flow
from docich import hanjuku_chart as chart
from docich.hanjuku.os_layer import roster as receipts
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku.state.regions import flow as _flow


BOSS = chart.boss_castle(1)
CAMPED = {'location': 'camp', 'marker': {'castle_candidate': BOSS}}


def memory(**fields):
    return {'chapter': 1, 'month': '2-6', 'tick': 100, 'gold': 250,
            '_records': [], **fields}


def _mem_with_status(row, month='2-6'):
    mem = memory(month=month)
    mem['general_statuses'] = {
        'scope': [1, month], 'complete': True, 'dirty': {},
        'identity': receipts.run_identity(mem.get('_run_identity')),
        'statuses': {'ゼウス': dict(row)}}
    return mem


def test_a_healthy_boss_camper_gets_a_boss_move_task():
    mem = _mem_with_status({**CAMPED, 'hp': 49, 'max_hp': 49})
    post.arm(mem)
    assert mem['post_month_status']['tasks'] == [
        {'kind': 'boss_move', 'name': 'ゼウス'}]


def test_an_injured_boss_camper_keeps_the_plain_exemption():
    mem = _mem_with_status({**CAMPED, 'hp': 10, 'max_hp': 89})
    post.arm(mem)
    assert 'post_month_status' not in mem          # no tasks: exempt only


def test_an_unmeasured_boss_camper_keeps_the_plain_exemption():
    mem = _mem_with_status(CAMPED)                  # no hp/max_hp at all
    post.arm(mem)
    assert 'post_month_status' not in mem


def test_a_broken_boss_camper_is_recalled_not_moved():
    mem = _mem_with_status({**CAMPED, 'broken': True, 'hp': 3, 'max_hp': 82})
    post.arm(mem)
    assert mem['post_month_status']['tasks'] == [
        {'kind': 'recall', 'name': 'ゼウス'}]


def test_boss_move_task_marks_the_recall_state(monkeypatch):
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump',
                 job={'kind': 'boss_move', 'name': 'ゼウス'},
                 statuses={'ゼウス': {**CAMPED, 'hp': 49, 'max_hp': 49}})
    screen = Screen(lines=[], hand=None, text='', kind='map', cursor=(150, 100))
    monkeypatch.setattr(post, 'own_camps',
                        lambda _frame: [{'target': [150, 100], 'kind': 'tent'}])
    monkeypatch.setattr(post.recall_flow, 'camp_recall_step',
                        lambda _screen, _mem, _frame: [pad('a')])
    assert post.step(screen, mem, object()) == [pad('a')]
    assert _flow(mem)['recall']['boss_move'] is True
    assert _flow(mem)['recall']['stage'] == 'to_camp'


def _camp_menu_screen(selected):
    from docich.hanjuku_font import TextLine
    rows = ['いどう', 'ステータス', 'キャンプ', 'きかん']
    return Screen([TextLine(39 + 16 * i, tuple((80 + 8 * j, ch) for j, ch in enumerate(word)))
                   for i, word in enumerate(rows)], (58, 33 + 16 * selected, 75, 45 + 16 * selected),
                  ''.join(rows), kind='text')


def test_boss_move_menu_picks_the_move_row():
    """menu stage: boss_move selects いどう, not きかん."""
    mem = memory()
    mem['flow'] = {'recall': {'stage': 'menu', 'steps': 0,
                              'boss_move': True, 'target': [26, 66]}}
    # Cursor on きかん (row 3): one up-press starts walking toward いどう.
    assert recall_flow.camp_recall_step(
        _camp_menu_screen(3), mem, object()) == [pad('up')]
    # Cursor already on いどう (row 0): A confirms into the dest stage.
    mem['flow'] = {'recall': {'stage': 'menu', 'steps': 0,
                              'boss_move': True, 'target': [26, 66]}}
    assert recall_flow.camp_recall_step(
        _camp_menu_screen(0), mem, object()) == [pad('a')]
    assert _flow(mem)['recall']['stage'] == 'dest'


def test_normal_recall_menu_still_picks_the_return_row():
    mem = memory()
    mem['flow'] = {'recall': {'stage': 'menu', 'steps': 0,
                              'target': [26, 66]}}
    assert recall_flow.camp_recall_step(
        _camp_menu_screen(3), mem, object()) == [pad('a')]
    assert _flow(mem)['recall']['stage'] == 'dest'


def test_boss_move_dest_confirms_a_measured_jump():
    """After a Y jump landed the marker on the boss cell, A confirms."""
    mem = memory()
    mem['y_jumped'] = {'RECALL': BOSS}
    mem['flow'] = {'recall': {'stage': 'dest', 'steps': 0,
                              'boss_move': True, 'goal': BOSS,
                              'target': [26, 66]}}
    screen = Screen(lines=[], hand=None, text='', kind='map_target',
                    marker=(100, 100))
    result = recall_flow.camp_recall_step(screen, mem, object())
    assert result == [pad('a')]
    assert _flow(mem)['recall']['stage'] == 'await_dispatch'
    last = mem['_records'][-1]
    assert last['decision'] == 'camp_recall_requested'
    assert last['observed_metric']['castle'] == BOSS


def test_boss_move_dest_jumps_at_the_boss_without_a_receipt(monkeypatch):
    import types
    from docich.hanjuku.flows import map as map_flow
    monkeypatch.setattr(map_flow, '_target_roof_under_marker',
                        lambda *_a, **_k: False)
    mem = memory()
    mem['flow'] = {'recall': {'stage': 'dest', 'steps': 0,
                              'boss_move': True, 'goal': BOSS,
                              'target': [26, 66]}}
    screen = Screen(lines=[], hand=None, text='', kind='map_target',
                    marker=(100, 100))
    result = recall_flow.camp_recall_step(screen, mem, types.SimpleNamespace(height=10))
    assert result == [pad('y')]
    assert mem['y_jump']['goal'] == BOSS
    assert mem['y_jump']['mode'] == 'target'


def test_recall_queue_runs_the_most_injured_first():
    """docich#3008: a badly hurt camper is recalled before a healthy one."""
    statuses = {
        'ヘラ': {'location': 'camp', 'hp': 20, 'max_hp': 59},
        'ゼウス': {'location': 'camp', 'hp': 85, 'max_hp': 85},
        'ミーティア': {'location': 'camp'},          # unmeasured: last
    }
    names = ['ミーティア', 'ヘラ', 'ゼウス']         # name order ≠ injury order
    assert [job['name'] for job in post._tasks(statuses, names)] == [
        'ヘラ', 'ゼウス', 'ミーティア']


def test_count_mismatch_runs_tasks_without_membership_proof():
    """docich#3751: a drifted headcount keeps tasks, drops departures."""
    mem = _mem_with_status({'location': 'camp', 'hp': 20, 'max_hp': 59})
    state = mem['post_month_status'] = {
        'scope': [1, '2-6'], 'identity': None, 'stage': 'roster',
        'names': ['ゼウス'], 'statuses': mem['general_statuses']['statuses'],
        'dirty': [], 'top_confirmed': True, 'steps': 0,
        'started_tick': 50, 'complete': False}
    mem['country_general_count'] = {
        'scope': [1, '2-6'], 'identity': None, 'count': 32}
    assert post._complete_scan(mem, state) is True
    assert state['complete'] is False
    assert state['count_mismatch'] == 32
    assert state['stage'] == 'process'
    assert state['tasks'] == [{'kind': 'recall', 'name': 'ゼウス'}]
    assert mem['general_statuses']['complete'] is False
    assert 'recruit_roster' not in mem
    assert any(r['decision'] == 'roster_count_mismatch'
               for r in mem['_records'])
