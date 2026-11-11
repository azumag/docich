"""Chapter 9's road castle keeps taking waves while sorties are marching.

Owner 2026-11-11: 早く倒さないとどんどん敵城のレベルが上がるステージなので、
ひとりづつ出撃していたら間に合わない. g1163 09:40-10:14 launched seven
sorties at ハヤク and every launch was followed by ``chart_interim_hold``: the
one-expedition rule removed the only target until that battle ended.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401  (package import side effects)
from docich.hanjuku.state.regions import sorties as _sorties
from docich.hanjuku.strategy import corridor
from docich.hanjuku.strategy import replan

HOME = chart.home_castle(9)
ROAD = chart.corridor(9)


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(marching):
    mem = {'chapter': 9, 'tick': 27000, 'month': '17-4', 'captured': [], 'orders': {},
           'garrison': {HOME: ['どうし', 'リーキ', 'ラズベリー', 'タルタル', 'ロックフォール',
                               'ヘルメス', 'アマンディーヌ']},
           'sorties': {f'I:wave{i}': {'general': general, 'source': HOME, 'target': 'ハヤク',
                                      'status': 'en_route', 'tick': 26900 + i}
                       for i, general in enumerate(marching)},
           '_records': []}
    assert len(_sorties(mem)) == len(marching)
    return mem


def attacks(mem):
    with chart.map_context(mem):
        return [o for o in replan.interim_candidates(mem).values()
                if o['purpose'] in ('attack', 'retake')]


@pytest.mark.parametrize('marching', [['リーキ'], ['リーキ', 'ラズベリー'],
                                       ['リーキ', 'ラズベリー', 'タルタル', 'ロックフォール', 'ヘルメス']])
def test_another_wave_goes_while_fewer_than_the_bound_are_marching(marching):
    got = attacks(memory(marching))
    assert got, 'the road castle must keep taking waves'
    assert {o['target'] for o in got} == {ROAD[1]}
    assert not {o['general'] for o in got} & set(marching), 'a marching general is not sent again'


def test_the_bound_stops_further_waves():
    # #3944: the bound is SQUAD (v671, six), shared with the planner.
    marching = ['リーキ', 'ラズベリー', 'タルタル', 'ロックフォール', 'ヘルメス', 'アマンディーヌ'][:corridor.SQUAD]
    assert len(marching) == corridor.SQUAD
    assert attacks(memory(marching)) == []


def test_old_marches_no_longer_count():
    mem = memory(['リーキ', 'ラズベリー', 'タルタル'])
    mem['tick'] = 27000 + 5000          # far beyond every arrival limit
    assert attacks(mem)


def test_other_chapters_keep_one_expedition_per_target():
    mem = {'chapter': 8, 'tick': 27000, 'month': '15-1', 'orders': {}, '_records': []}
    assert replan._corridor_wave_open(mem, 'ひめじ') is False
