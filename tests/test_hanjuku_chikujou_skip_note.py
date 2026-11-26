"""docich#5278: 築城 skipped の理由記録が無い月を診断できるようにする。"""
from docich.hanjuku.flows import month as m


def _mem(shop):
    return {'chapter': 10, 'tick': 100, '_records': [], 'shop': shop}


def test_an_unexplained_skip_is_recorded_once():
    mem = _mem({'key': '1-1', 'chikujou': 'skipped', 'chikujou_tried': ['A']})
    m._note_chikujou_skip_unexplained(mem, mem['shop'])
    decisions = [r.get('decision') for r in mem['_records']]
    assert decisions.count('chikujou_skip_unexplained') == 1
    m._note_chikujou_skip_unexplained(mem, mem['shop'])
    decisions = [r.get('decision') for r in mem['_records']]
    assert decisions.count('chikujou_skip_unexplained') == 1


def test_a_recruit_hold_is_not_called_unexplained():
    mem = _mem({'key': '1-1', 'chikujou': 'skipped', 'chikujou_held': True})
    m._note_chikujou_skip_unexplained(mem, mem['shop'])
    assert not mem['_records']


def test_an_explained_reserve_eval_is_not_called_unexplained():
    mem = _mem({'key': '1-1', 'chikujou': 'skipped',
                'chikujou_last_eval': {'month': '1-1', 'affordable': False}})
    m._note_chikujou_skip_unexplained(mem, mem['shop'])
    assert not mem['_records']


def test_a_hold_that_gave_up_names_the_hold_not_unexplained():
    # docich#5326: 画面遷移の待ちが上限で断念した月は held の理由を名指しする。
    mem = _mem({'key': '1-1', 'chikujou': 'skipped', 'chikujou_tried': ['A']})
    mem['_records'] = [{'decision': 'chikujou_held', 'month': '1-1',
                        'deviation_reason': 'castle_list_unread'}]
    m._note_chikujou_skip_unexplained(mem, mem['shop'])
    decisions = [r.get('decision') for r in mem['_records']]
    assert 'chikujou_skip_after_hold' in decisions
    assert 'chikujou_skip_unexplained' not in decisions


def test_a_hold_survives_the_observation_record_buffer_reset():
    # docich#5704: chikujou_held と月終了は別観測になりうる。永続化した同月の
    # hold があれば、観測バッファが空でも unexplained と誤記しない。
    mem = _mem({'key': '1-1', 'chikujou': 'skipped', 'chikujou_tried': ['A'],
                'chikujou_last_hold': {'month': '1-1',
                                       'deviation_reason': 'castle_scan_exhausted'}})
    m._note_chikujou_skip_unexplained(mem, mem['shop'])
    decisions = [r.get('decision') for r in mem['_records']]
    assert 'chikujou_skip_after_hold' in decisions
    assert 'chikujou_skip_unexplained' not in decisions
    assert mem['_records'][-1]['observed_metric']['held_reason'] == 'castle_scan_exhausted'


def test_a_built_month_is_not_called_unexplained():
    mem = _mem({'key': '1-1', 'chikujou': 'done'})
    m._note_chikujou_skip_unexplained(mem, mem['shop'])
    assert not mem['_records']
