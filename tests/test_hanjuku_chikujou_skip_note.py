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


def test_a_built_month_is_not_called_unexplained():
    mem = _mem({'key': '1-1', 'chikujou': 'done'})
    m._note_chikujou_skip_unexplained(mem, mem['shop'])
    assert not mem['_records']
