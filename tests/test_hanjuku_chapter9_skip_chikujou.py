"""Chapter 9's march does not stop to upgrade castles.

Owner 2026-11-11: ９話は機械的に次の城にすべての将軍を送る、それ以外はdisable.
g1163 2026-11-12 16:1x-16:4x spent 74 month steps and repeated castle-list
scrolls on 築城 while the road was still being taken.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.strategy import economy

ROAD = chart.corridor(9)


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(chapter, owned):
    return {'chapter': chapter, 'tick': 5000, 'month': f'23-{chapter}',
            'captured': [c for c in owned if c != chart.home_castle(chapter)],
            'lost': [], 'gold': 9999, 'world': {}, '_records': []}


def plan(mem):
    shop = {'key': mem['month'], 'gold_start': 9999}
    with chart.map_context(mem):
        economy._plan_extras(mem, shop, 0, False)
    return shop


def test_the_march_skips_the_castle_upgrade():
    mem = memory(9, [ROAD[0], ROAD[1]])
    shop = plan(mem)
    assert shop['chikujou'] == 'skipped'
    assert [r for r in mem['_records'] if r.get('decision') == 'month_chikujou_skipped']


def test_the_boss_phase_keeps_the_upgrade():
    mem = memory(9, list(ROAD))
    assert plan(mem)['chikujou'] == 'check'


def test_another_chapter_keeps_the_upgrade():
    home = chart.home_castle(8)
    other = [c for c in sorted(chart.castles(8)) if c != home][0]
    assert plan(memory(8, [home, other]))['chikujou'] == 'check'
