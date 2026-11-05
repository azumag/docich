"""Owner 2026-11-05: the boss takes volume, from many castles at once."""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import world as _world


def memory():
    owned = [c for c in chart.castles(6) if not chart.is_boss_castle(c)]
    mem = {
        'chapter': 6, 'month': '55-6', 'tick': 80000,
        'captured': list(owned), 'lost': [], 'orders': {},
        'garrison': {c: [] for c in owned},
        'sorties': {}, 'launched_orders': {}, '_records': [],
        'house_eggs': {}, 'general_location_unknown': [],
    }
    _world(mem)['garrison']['アブダレ'] = ['ドリアン', 'マスカット']
    _world(mem)['garrison']['セレーネ'] = ['ソーピニヨン', 'リーキ']
    _world(mem)['garrison']['カバルッサ'] = ['ガルバンゾー', 'ココット']
    return mem


def boss_waves(mem):
    return [o for o in replan.interim_candidates(mem).values()
            if o.get('target') == 'ボス' and o.get('purpose') == 'attack']


def test_every_known_spare_joins_the_boss_in_one_plan():
    waves = boss_waves(memory())
    assert len(waves) >= 3
    assert all(o['cards'] == [] for o in waves)


def test_the_waves_come_from_different_castles():
    waves = boss_waves(memory())
    sources = [o['source'] for o in waves]
    # Waves prefer a castle that has not sent one yet, so the plan spreads
    # over the map instead of spending one stack.
    assert len(set(sources)) >= 2


def test_a_weak_general_is_not_held_back_for_a_stronger_one():
    # Owner 2026-11-05: strength does not decide the boss fight, so the plan
    # must not reserve the strong spare while a weak one is available.
    mem = memory()
    _world(mem)['garrison']['アブダレ'] = ['ドリアン', 'ヘルメス']
    waves = boss_waves(mem)
    assert any(o['general'] == 'ドリアン' for o in waves)
