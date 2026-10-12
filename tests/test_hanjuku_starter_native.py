"""Unmodified g684 chapter-one completion frame, RGB bound to its decision."""
from pathlib import Path

from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from docich.hanjuku_bot import decide
from docich import hanjuku_starter as starter
from test_hanjuku_starter import state, keys, picture

FRAME = Path(__file__).parent / 'fixtures/hanjuku/g684-starter-empty.png'


def test_native_empty_starter_reads_names_without_panel_scroll_arrow():
    frame = read_png(FRAME)
    assert frame.digest() == 'dc3c6cb95f0c11c587f586bab1d3030e6eb9b2f620aaac5bce08f619bc10cd20'
    screen = parse(frame)
    assert screen.kind == 'starter_list'
    assert starter.rows(screen) == [(48, 47, 'ゼウス'), (48, 63, 'アマンディーヌ'),
                                    (48, 79, 'ヴィーナス'), (48, 95, 'マカデミア')]
    assert starter.rows(screen, selected=True) == []
    actions, got = decide(frame, state())
    assert keys(actions) == ['a']
    assert got['_records'][-1]['decision'] == 'starter_select'
    assert got['_records'][-1]['general'] == 'ゼウス'
    assert got['policy']['starter_party']['selected'] == []
    assert not got['policy'].get('garrison') and not got['policy'].get('sorties')


def test_unread_tile_inside_measured_name_pane_is_still_rejected():
    c = picture()
    c.tile(104, 47, 0xfffffff0fffffff0)
    frame = c.frame()
    assert starter.rows(parse(frame)) is None
    actions, got = decide(frame, state())
    assert actions == []
    assert got['_records'][-1]['decision'] == 'starter_unread'
