"""A castle ステータス panel read as the sortie list is closed, not mined for names.

g1163 2026-11-11 14:28:39-44: the castle menu's ステータス opened instead of
しゅつげき, a sprite near the bottom was read as the hand, the screen was
classified ``general_list`` and its words (ハヤク, じょう, しゅうにゅう, G, レベル,
しょうぐん, めい) were recorded as ハヤク's garrison; ハヤク was then picked as a
substitute general and the bot pressed up toward it for over five minutes.
"""
from pathlib import Path

from PIL import Image

from docich import hanjuku_chart as chart
from docich import hanjuku_policy as policy
from docich.hanjuku.os_layer.pixels import Frame
from docich.hanjuku.os_layer.readers import _present_generals
from docich.hanjuku.os_layer.screen import parse

from test_hanjuku_chapter9_cursor_order_sortie import _memory

FIXTURE = Path(__file__).parent / 'fixtures' / 'hanjuku' / 'g1163-castle-status-panel-read-as-general-list.png'


def _screen():
    im = Image.open(FIXTURE).convert('RGB')
    return parse(Frame(im.width, im.height, im.tobytes()))


def test_the_live_frame_is_classified_as_a_list_but_yields_no_names():
    screen = _screen()
    assert screen.kind == 'general_list'          # the misclassification this guards
    assert _present_generals(screen) is None


OSHIROGA = Path(__file__).parent / 'fixtures' / 'hanjuku' / 'g1163-source-check-status-panel-oshiroga.png'


def _frame(path):
    im = Image.open(path).convert('RGB')
    return parse(Frame(im.width, im.height, im.tobytes()))


def test_the_source_check_panel_verifies_the_castle_instead_of_looping():
    """g1163 15:28-15:30: v667 pressed B on オシロガ's source-check panel without
    verifying it, so the check reopened it and ten orders were cancelled."""
    from docich.hanjuku.strategy import corridor
    from test_hanjuku_off_chart import _move_memory
    road = chart.corridor(9)
    mem = _move_memory()
    mem.update(chapter=9, captured=list(road[1:5]))       # ハヤク..オシロガ: オシロガ attacks
    with chart.map_context(mem):
        order = corridor.orders(mem)[0]
        assert order['source'] == 'オシロガ'
        mem.update(active=order['step'], chart_adjust={'request_id': 'm', 'interim_order': order})
        screen = _frame(OSHIROGA)
        assert screen.kind == 'general_list'
        assert policy.deploy_step(screen, mem) == [policy.pad('b')]
    assert mem['castle_verified'] == order['step']
    assert not [r for r in mem['_records'] if r.get('decision') == 'order_failed']


def test_a_panel_returning_after_verification_still_cancels():
    mem = _memory('attack')
    step = mem['_test_step']
    with chart.map_context(mem):
        for _ in range(policy.CASTLE_STATUS_AS_LIST_LIMIT):
            mem['castle_verified'] = step
            policy.deploy_step(_screen(), mem)
        mem['castle_verified'] = step
        assert policy.deploy_step(_screen(), mem) == [policy.pad('b'), policy.pad('b')]
