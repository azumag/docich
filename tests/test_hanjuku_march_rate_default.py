"""Every chapter marches at the measured game rate (v597).

g1163 chapter 8 2026-11-09 23:22: three あるまむーん marches to
おだわら/まつやま were declared overdue at 1675-1813 observations because
only chapter 6 carried a ticks-per-cell rate and everything else fell back to
the fixed 800-observation default.
"""
from docich.hanjuku.os_layer import world


def test_a_chapter_without_its_own_rate_uses_the_measured_one():
    long_march = world.march_arrival_limit({'chapter': 8},
                                           {'source': 'あるまむーん', 'target': 'おだわら'})
    assert long_march > world.BOSS_ARRIVAL_LIMIT
    assert long_march == world.MARCH_ARRIVAL_CAP


def test_a_short_march_scales_with_the_distance():
    short = world.march_arrival_limit({'chapter': 8},
                                      {'source': 'あるまむーん', 'target': 'おかざき'})
    long_march = world.march_arrival_limit({'chapter': 8},
                                           {'source': 'あるまむーん', 'target': 'おだわら'})
    assert world.BOSS_ARRIVAL_LIMIT <= short < long_march


def test_the_measured_chapter_6_rate_is_unchanged():
    rate = world.MARCH_TICKS_PER_CELL[6]
    assert rate == world.DEFAULT_MARCH_TICKS_PER_CELL == 3.2
