"""owner 2026-11-28: 第4話はボス城が最初からあり、攻めるのは経由城だけでよい。"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan


def test_bridge_sources_are_the_boss_leg_sources():
    bridges = set(chart.boss_bridge_sources(4))
    assert bridges == {'アルカ', 'ネクスドリア', 'フヌイヌム', 'ミヒラギアン'}


def _mem(captured):
    return {'chapter': 4, 'tick': 100, 'month': '7-1', 'captured': list(captured),
            'lost': [], 'world': {'garrison': {}, 'sorties': {}},
            'plan': {'orders': {}}, '_records': []}


def test_chapter_four_skips_enemy_castles_that_are_not_bridges():
    mem = _mem(['アルマムーン'])
    with chart.map_context(mem):
        cands = replan.interim_candidates(mem)
    targets = {c.get('target') for c in cands.values() if c.get('purpose') == 'attack'}
    assert 'ビクトリア' not in targets
    assert 'マラコビア' not in targets
