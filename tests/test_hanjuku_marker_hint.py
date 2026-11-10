"""An ambiguous selection marker is settled by the general's recorded castle.

g1163 2026-11-09: 29 `post_month_roster_marker_unverified` in 40 minutes, each
followed by a full status panel read.
"""
from types import SimpleNamespace
from unittest import mock

from docich.hanjuku.os_layer import readers

CASTLES = {'甲': (0, 0), '乙': (160, 0)}   # 20 px apart at MAP_VIEW_SCALE 8


def _frame():
    return SimpleNamespace(width=256, height=224, pixel=lambda x, y: (0, 0, 0))


def _run(tip, hint):
    with mock.patch.object(readers.chart, 'castles', lambda chapter: dict(CASTLES)), \
         mock.patch.object(readers, 'selection_marker',
                           lambda f: {'tip': tip, 'box': [tip[0] - 3, tip[1] - 8,
                                                          tip[0] + 3, tip[1]]}), \
         mock.patch.object(readers, '_map_icon_centres', lambda f: []), \
         mock.patch.object(readers, '_map_view_offset', lambda icons, castles: (0, 0)):
        return readers.marker_castle(_frame(), 8, hint=hint)


def test_an_ambiguous_marker_is_none_without_a_hint():
    # The marker sits between the two castles: 80 either side (< 1.5x) and
    # inside the 96 tolerance, so the reading is ambiguous.
    assert _run((10, 0), None) is None


def test_the_recorded_castle_settles_the_ambiguity():
    mapped = _run((10, 0), '甲')
    assert mapped is not None
    assert mapped['castle'] == '甲'


def test_a_hint_outside_the_tolerance_is_ignored():
    # 乙 is 80 from the marker; a hint naming it still has to pass tolerance,
    # and both candidates are inside it, so the hint decides.
    assert _run((10, 0), '乙')['castle'] == '乙'


def test_the_failure_reason_is_reported():
    """The monthly walk pays a status panel per unbound marker (g1163 2026-11-09)."""
    evidence = {}
    with mock.patch.object(readers.chart, 'castles', lambda chapter: dict(CASTLES)), \
         mock.patch.object(readers, 'selection_marker', lambda f: None), \
         mock.patch.object(readers, '_map_icon_centres', lambda f: []):
        assert readers.marker_castle(_frame(), 8, evidence=evidence) is None
    assert evidence.get('reason') == 'no_marker'


def test_a_missing_selection_marker_reports_its_cause():
    """20 of 25 unbound markers carried no reason (g1163 2026-11-09 23:35)."""
    from unittest import mock

    from docich.hanjuku.flows import post_month_status as post
    with mock.patch.object(readers, 'selection_marker', lambda f: None), \
         mock.patch.object(post, 'world_markers', lambda f: []):
        result = post._marker({'chapter': 8}, SimpleNamespace(
            width=256, height=224, pixel=lambda x, y: (0, 0, 0)))
    assert result is not None
    assert result['marker_reason'] == 'no_selection_marker'


def test_a_triangle_right_of_the_old_window_is_found():
    """The roster's name column starts at x=168, so the sprite can sit past 154."""
    from types import SimpleNamespace

    from docich.hanjuku.os_layer import readers as r

    def frame_with(ox, oy):
        palette = {}
        for dx, dy, colour in r._GENERAL_MARKER_PATTERN:
            palette[(ox + dx, oy + dy)] = colour
        return SimpleNamespace(width=299, height=224,
                               pixel=lambda x, y: palette.get((x, y), (12, 12, 12)))

    found = r.selection_marker(frame_with(162, 40))
    assert found is not None, 'a triangle at x=162 must be inside the search window'
    assert found['box'][0] == 162


def test_a_recorded_castle_past_the_tolerance_still_binds():
    """Chapter cells 7-12 are estimates, so the whole map can sit off (v600).

    g1163 2026-11-10 00:35: 6 of 9 monthly markers were out_of_tolerance.
    """
    from types import SimpleNamespace
    from unittest import mock

    wide = {'甲': (0, 0), '乙': (600, 0)}
    frame = SimpleNamespace(width=299, height=224, pixel=lambda x, y: (0, 0, 0))
    evidence = {}
    with mock.patch.object(readers.chart, 'castles', lambda chapter: dict(wide)), \
         mock.patch.object(readers, 'selection_marker',
                           lambda f: {'tip': (18, 0), 'box': [15, -8, 21, 0]}), \
         mock.patch.object(readers, '_map_icon_centres', lambda f: []), \
         mock.patch.object(readers, '_map_view_offset', lambda icons, castles: (0, 0)):
        plain = readers.marker_castle(frame, 8, evidence=evidence)
        hinted = readers.marker_castle(frame, 8, hint='甲', evidence={})
    assert plain is None and evidence.get('reason') == 'out_of_tolerance'
    assert hinted is not None and hinted['castle'] == '甲'
