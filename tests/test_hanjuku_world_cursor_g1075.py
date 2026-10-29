from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku_pixels import read_png
from docich import hanjuku_policy as policy

FIXTURES = Path(__file__).parent / 'fixtures' / 'hanjuku'


def test_g1075_world_cursor_blue_dot_ring():
    frame = read_png(FIXTURES / 'g1075-world-map-cursor.png')
    cursor = policy.world_cursor(frame)
    assert cursor is not None
    assert abs(cursor[0] - 137) <= 1 and abs(cursor[1] - 128) <= 1


def test_chapter2_boss_world_cursor_unchanged():
    frame = read_png(FIXTURES / 'chapter-2-boss-y-focus.png')
    assert policy.world_cursor(frame) == (128.0, 72.5)
