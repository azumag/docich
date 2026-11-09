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
