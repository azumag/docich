"""g692: the cursor stays on a dimmed upper row after the first selection."""
import json
from pathlib import Path

import pytest

from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from docich import hanjuku_starter as starter
from test_hanjuku_starter import state, keys


@pytest.mark.parametrize('restored', [False, True])
def test_native_selected_row_cursor_can_leave_the_dimmed_first_general(restored):
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku/g692-starter-one-selected.png')
    assert frame.digest() == '09182fc534a4ad4d32575435468111c3636a0da19f8bd296965217414217153d'
    screen = parse(frame)
    assert starter.rows(screen) == [(48, 63, 'ココット'), (48, 79, 'ビーツ'), (48, 95, 'チコリ')]
    assert starter.rows(screen, selected=True) == [(48, 119, 'ゼウス')]
    initial = state()
    if restored:
        initial['policy']['starter_party'] = {
            'chapter': 1, 'run_identity': None, 'selected': ['ゼウス'],
            'attempts': {'ゼウス': 1}, 'waits': 0}
        initial = json.loads(json.dumps(initial))
    actions, got = decide(frame, initial)
    assert keys(actions) == ['down']
    assert got['policy']['starter_party']['selected'] == ['ゼウス']
    assert not got['policy']['starter_party'].get('confirm_sent')
    assert not got['policy'].get('garrison') and not got['policy'].get('sorties')
    # A repeated still frame can retry arrows, but cannot select the dim row
    # or manufacture a second selected general/departure.
    for _ in range(30):
        actions, got = decide(frame, json.loads(json.dumps(got)))
        assert keys(actions) in ([], ['down'])
    assert got['policy']['starter_party']['selected'] == ['ゼウス']
    assert got['policy']['starter_party']['nav']['presses'] == starter.NAV_PRESS_LIMIT
    assert not got['policy']['starter_party'].get('confirm_sent')
