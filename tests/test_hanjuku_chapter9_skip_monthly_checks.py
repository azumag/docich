"""Chapter 9 skips the monthly locate and camp recall until its boss phase.

Owner 2026-11-11: 月次の位置チェックも必要がないので省略できる。キャンプになった
将軍は仕方ないのでこの章は放置。ただし、ボスフェーズになったときは、規定の動き、
ボスを追いかけるためにチェックが必要.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.flows import post_month_status as post

ROAD = chart.corridor(9)
STATUSES = {
    'ジェラート': {'location': 'castle', 'castle': 'オシロガ', 'broken': True},
    'ライム': {'location': 'castle'},
    'シュゼット': {'location': 'camp', 'marker': {'castle_candidate': 'サヌト'}},
}


def _mem(chapter, owned):
    return {'chapter': chapter, 'captured': list(owned), '_records': []}


def test_the_road_keeps_only_repairs():
    assert post._tasks(STATUSES, list(STATUSES), road=True) == [
        {'kind': 'repair', 'name': 'ジェラート'}]


def test_chapter_nine_before_the_boss_phase_skips_the_checks():
    mem = _mem(9, ROAD[1:4])
    with chart.map_context(mem):
        assert post._road_skips_checks(mem) is True


def test_the_boss_phase_keeps_the_checks():
    mem = _mem(9, ROAD[1:])
    with chart.map_context(mem):
        assert post._road_skips_checks(mem) is False


def test_other_chapters_keep_the_checks():
    mem = _mem(8, [])
    with chart.map_context(mem):
        assert post._road_skips_checks(mem) is False
