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


def test_deploy_backs_out_and_finally_cancels():
    mem = _memory('attack')
    with chart.map_context(mem):
        for _ in range(3):
            assert policy.deploy_step(_screen(), mem) == [policy.pad('b')]
        assert policy.deploy_step(_screen(), mem) == [policy.pad('b'), policy.pad('b')]
    assert 'じょう' not in str(mem.get('garrison') or mem.get('world', {}).get('garrison'))
    assert [r for r in mem['_records'] if r.get('decision') == 'castle_status_not_list']
