"""#4745: estimated-chapter (7-12) castle cells get a wider unique-nearest bound."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku.os_layer import readers


def test_out_of_tolerance_estimated_ch_accepts_unique_nearest(monkeypatch):
    # Two castles: the nearest lands past MAP_CASTLE_TOLERANCE but inside the
    # wider bound and the runner-up is clearly farther.
    castles = {'シマウ': (0, 0), 'アルマムーン': (1000, 0)}
    monkeypatch.setattr(readers.chart, 'castles', lambda _c: castles)
    monkeypatch.setattr(readers, 'selection_marker', lambda _f: {'box': (0, 0, 6, 6), 'tip': (3, 3)})
    monkeypatch.setattr(readers, '_map_icon_centres', lambda _f: [(3, 3)])
    scale = readers.MAP_VIEW_SCALE
    monkeypatch.setattr(readers, '_map_view_offset', lambda _i, _c: (0, 0))
    # Put the marker 1.5x tolerance from the castle cell.
    d = readers.MAP_CASTLE_TOLERANCE * 1.5
    monkeypatch.setattr(readers, 'selection_marker', lambda _f: {'box': (0, 0, 6, 6), 'tip': (d / scale, 0)})
    monkeypatch.setattr(readers, '_map_icon_centres', lambda _f: [])
    why = {}
    out = readers.marker_castle(object(), 9, evidence=why)
    assert out['castle'] == 'シマウ' and why['reason'] == 'out_of_tolerance_estimated_ch'
    out2 = readers.marker_castle(object(), 5, evidence={})
    assert out2 is None
