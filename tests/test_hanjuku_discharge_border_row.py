"""The forced-discharge list ignores its status panel's border rows.

Local chapter 9, 2026-11-15 (gold -246G): the list's five candidates were
readable but the y=127 border/status row contributed two unreadable tiles, so
``discharge_candidates`` returned None and the bot held the screen forever
(``discharge_unverified``, one no-input fallback every 101 observations while
game months passed). A wage-bearing row with a partial name must still
invalidate the list.
"""
import sys
from pathlib import Path

from docich.hanjuku.flows.shop import discharge_candidates
from docich.hanjuku.os_layer.font import UNKNOWN
from docich.hanjuku.os_layer.screen import Screen, TextLine, parse
from docich.hanjuku_pixels import read_png

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'


def test_border_only_row_does_not_invalidate_the_candidate_list():
    frame = read_png(FIXTURES / 'local-ch9-discharge-menu.png')
    screen = parse(frame)
    assert screen.kind == 'discharge_menu'
    assert discharge_candidates(screen) == [
        'ピオーネ', 'シフォン', 'ヘスティア', 'ポワソン', 'ジキタリス',
    ]


def test_wage_row_with_an_unreadable_name_still_invalidates_the_list():
    screen = Screen(
        [TextLine(47, ((24, 'シ'), (32, 'フ'), (40, 'ォ'), (48, 'ン'), (96, '1'), (104, '0'), (112, 'G'))),
         TextLine(63, ((24, UNKNOWN), (96, '9'), (104, 'G')))],
        None, '', kind='discharge_menu')
    assert discharge_candidates(screen) is None
