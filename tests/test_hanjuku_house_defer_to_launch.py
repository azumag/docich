"""owner 2026-11-27: 発射待ちの出撃がある間は家来・状態の探索を見送る。"""
from pathlib import Path

from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from docich.hanjuku.flows import house as house_flow
from docich.hanjuku.state.regions import plan as _plan_region

FRAME = Path(__file__).parent / 'fixtures/hanjuku/g684-chikujou-four-rows.png'


def test_a_pending_launch_defers_the_house_survey():
    screen = parse(read_png(FRAME))
    step = 'A:3:M1'
    order = {'step': step, 'purpose': 'retake', 'source': 'ヘリオポリス',
             'target': 'ラグナグ', 'general': 'デュオニソス', 'cards': [], 'note': '奪還'}
    mem = {'chapter': 3, 'month': '3-9', 'sortie_launch_pending': step}
    _plan_region(mem)['chart_plan'] = {'orders': [order]}
    _plan_region(mem)['orders'] = {step: 'pending'}
    _plan_region(mem)['active'] = step
    actions = house_flow.step(screen, mem, read_png(FRAME))
    assert actions is None
    records = mem.get('_records') or []
    assert records and records[-1]['decision'] == 'house_deferred_sortie'
    assert records[-1]['observed_metric']['step'] == 'A:3:M1'
