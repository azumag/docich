"""When A on the tent's flag cell does not open the menu, retry its body.

g1190 2026-11-15: the hero's cliff-side camp answered A on the flag cell with
the bare map three times per recall and the recall gave up every month.
"""
from test_hanjuku_chart_bot import _camp_frame

from docich import hanjuku_policy as policy
from docich.hanjuku.state.regions import flow as _flow
from docich.hanjuku_screen import Screen
from docich.hanjuku.os_layer.screen import own_camps


def memory():
    return {'chapter': 1, '_records': []}


def test_the_camp_body_is_not_the_flag_target():
    camp = own_camps(_camp_frame())[0]
    assert camp['body'] != camp['target']


def test_two_failed_reopens_switch_the_retry_to_the_tent_body():
    frame = _camp_frame()
    mem = memory()
    tent = Screen([], None, '', kind='map', cursor=(27, 67))
    policy.camp_recall_step(tent, mem, frame)
    state = _flow(mem)['recall']
    assert state.get('camp_body') and state['camp_body'] != state['target']
    flag_target = list(state['target'])
    body = list(state['camp_body'])
    bare = Screen([], None, '', kind='map', cursor=(50, 50))
    # The A on the flag cell leaves the bare map; each reopen costs 3 observations.
    for _ in range(3):
        state['stage'] = 'menu'
        for _ in range(3):
            policy.camp_recall_step(bare, mem, frame)
    assert state['target'] == body and body != flag_target
    assert any(r.get('decision') == 'camp_enter_body_retry' for r in mem['_records'])
