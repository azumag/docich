"""Post-month roster-first policy: sequence ordering and conservative evidence."""
from unittest.mock import patch

from docich.hanjuku.flows import post_month_status as post
from docich.hanjuku.os_layer import roster as receipts
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import Screen, parse

from test_hanjuku_house import Canvas, roster, status


def memory(**fields):
    return {'chapter': 1, 'month': '2-6', 'tick': 100, 'gold': 250,
            '_records': [], **fields}


def test_month_exit_arms_one_scoped_roster_first_owner():
    mem = memory()
    post.arm(mem)
    assert mem['post_month_status']['stage'] == 'await_map'
    assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('x')]
    assert mem['post_month_status']['stage'] == 'open_menu'
    previous = mem['post_month_status']
    post.arm(mem)
    assert mem['post_month_status'] is previous


def test_individual_statuses_are_read_before_any_repair_or_recall():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state['stage'] = 'roster'
    first = parse(roster(names=('どうし', 'ゼウス'), selected=0))
    second = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    assert post.step(first, mem, None) == []  # settle highlighted sprite
    assert post.step(first, mem, None) == [pad('a')]
    assert post.step(parse(status('どうし', egg='エラベルエッグ4')), mem, None) == [pad('b')]
    assert post.step(first, mem, None) == [pad('down')]
    assert post.step(second, mem, None) == []
    assert post.step(second, mem, None) == [pad('a')]
    assert post.step(parse(status('ゼウス', egg='こわれている')), mem, None) == [pad('b')]
    assert post.step(second, mem, None) == [pad('down')]
    # Short final roster page is a visible last-page proof.
    assert post.step(second, mem, None) == [pad('b')]
    assert mem['general_statuses']['complete'] is True
    assert list(mem['general_statuses']['statuses']) == ['どうし', 'ゼウス']
    assert state['tasks'] == [{'kind': 'locate', 'name': 'どうし'},
                              {'kind': 'locate', 'name': 'ゼウス'},
                              {'kind': 'repair', 'name': 'ゼウス'}]
    assert state['stage'] == 'process'


def _status_with_location(text):
    c = Canvas()
    c.text(24, 151, text)
    return parse(c.frame())


def test_exact_native_location_lines_remain_distinct():
    for text, expected in [
        ('キャンプしています', 'camp'),
        ('しろのなかにいます', 'castle'),
        ('いまこちらにむかっています', 'march'),
        ('しんぐんちゅうです', 'march'),
    ]:
        assert post._location(_status_with_location(text)) == expected
    assert post._location(_status_with_location('????')) == 'unknown'


def test_roster_triangle_needs_a_unique_fresh_castle_position():
    from docich import hanjuku_chart as chart
    castle, cell = next(iter(chart.castles(1).items()))
    mem = memory(cursor=list(cell), uncertain=False,
                 nav_last={'kind': 'map', 'screen': [63, 53], 'tick': 99})
    with patch.object(post, 'world_markers', return_value=[(60, 50, 66, 56)]):
        result = post._marker(mem, object())
        assert result['castle_candidate'] == castle
        mem['uncertain'] = True
        assert 'castle_candidate' not in post._marker(mem, object())


def test_known_garrison_does_not_need_redundant_select():
    mem = memory(garrison={'アルマムーン': ['ゼウス']})
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='status', selected='ゼウス')
    assert post.step(parse(status('ゼウス', egg='こわれている')), mem, None) == [pad('b')]
    assert state['statuses']['ゼウス']['castle'] == 'アルマムーン'
    assert state['statuses']['ゼウス']['castle_evidence'] == 'previous_named_roster'


def test_castle_location_is_confirmed_by_native_status_name():
    mem = memory(garrison={'グリン': ['ゼウス']})
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='locate_info', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}})
    with patch.object(post.map_registration, '_label', return_value='アルマムーン'):
        assert post.step(Screen([], None, '', kind='castle_info'), mem, None) == [pad('b')]
    assert mem['garrison']['アルマムーン'] == ['ゼウス']
    assert 'ゼウス' not in mem['garrison']['グリン']


def test_repair_uses_roster_select_then_verified_castle_entry():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump', job={'kind': 'repair', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle', 'broken': True,
                                     'castle': 'アルマムーン'}})
    with patch.object(post, '_roof_under_cursor', return_value=True):
        assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('a')]
    assert state['stage'] == 'wait_repair'
    assert mem['house']['phase'] == 'castle_verify'
    assert mem['house']['general'] == 'ゼウス'


def test_unknown_status_never_allows_planning():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='status', selected='ゼウス', phase_reads=post.STATUS_RETRIES)
    assert post.step(Screen([], None, '', kind='unknown'), mem, None) == [pad('b')]
    assert state['stage'] == 'blocked'
    assert post.step(Screen([], None, '', kind='map'), mem, None) == []
    assert 'general_statuses' not in mem


def test_select_locate_opens_castle_even_when_roof_is_hidden_by_cursor():
    """SELECT's named position is evidence; roof recognition can be occluded."""
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}})
    with patch.object(post, '_roof_under_cursor', return_value=False):
        assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('a')]
    assert state['stage'] == 'locate_open'
    assert not mem.get('general_location_unknown')
    assert post.step(Screen([], None, '', kind='castle_menu'), mem, None) == []
    assert state['stage'] == 'locate_info'
    menu = Screen([], (40, 184), 'ステータス', kind='castle_menu')
    with patch.object(post, 'menu_to', return_value='here'):
        assert post.step(menu, mem, None) == [pad('a')]
    with patch.object(post.map_registration, '_label', return_value='アルマムーン'):
        assert post.step(Screen([], None, '', kind='castle_info'), mem, None) == [pad('b')]
    assert state['stage'] == 'process'
    assert mem['garrison']['アルマムーン'] == ['ゼウス']
    assert state['statuses']['ゼウス']['castle_evidence'] == 'native_castle_name'


def test_castle_menu_header_cannot_be_mistaken_for_verified_status():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='locate_info', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}})
    with patch.object(post.map_registration, '_label', return_value='アルマムーン'):
        assert post.step(Screen([], None, '', kind='castle_menu'), mem, None) == []
    assert state['stage'] == 'locate_info'
    assert 'アルマムーン' not in mem.get('garrison', {})


def test_missing_castle_status_retries_same_general_before_advancing():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='locate_info', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}},
                 phase_reads=post.STATUS_RETRIES - 1, locate_retries=0)
    assert post.step(Screen([], None, '', kind='castle_menu'), mem, None) == [pad('b')]
    assert state['stage'] == 'locate_reset'
    assert state['job']['name'] == 'ゼウス'
    assert state['locate_retries'] == 1
    assert not mem.get('general_location_unknown')
    assert post.step(Screen([], None, '', kind='map'), mem, None) == [pad('x')]
    assert state['stage'] == 'task_menu'
    assert state['job']['name'] == 'ゼウス'
    # Only after the bounded retry budget is exhausted can an unknown
    # general be excluded from planning; it is never attributed to a castle.
    state.update(stage='locate_info', phase_reads=post.STATUS_RETRIES - 1,
                 locate_retries=post.LOCATE_RETRIES)
    assert post.step(Screen([], None, '', kind='castle_menu'), mem, None) == [pad('b')]
    assert state['stage'] == 'process'
    assert mem['general_location_unknown'] == ['ゼウス']
    assert 'castle' not in state['statuses']['ゼウス']


def test_select_lost_in_roster_resends_select_before_location_failure():
    mem = memory()
    post.arm(mem)
    state = mem['post_month_status']
    state.update(stage='task_jump', job={'kind': 'locate', 'name': 'ゼウス'},
                 statuses={'ゼウス': {'location': 'castle'}}, phase_reads=3)
    still_roster = parse(roster(names=('どうし', 'ゼウス'), selected=1))
    assert post.step(still_roster, mem, None) == [pad('select')]
    assert state['stage'] == 'task_jump'
