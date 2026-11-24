"""An eggless general is never sent as a boss wave.

ch9 2026-11-17 (local driver g1190, seedless): the boss fight's damage is the
egg's (owner 2026-11-05). リースリング, カペリーニ and ヘラ (status ``egg: None``)
left the boss at HP 500 / 129 and were lost; シフォン (まねっこ) and エピィ
(スロット) took it 500 -> 129 -> 0. Waves now come from egg carriers only.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan

CELLS = chart.castles(6)
NORMAL = [c for c in CELLS if c != 'ボス']
SOURCE = 'オケアナ'


def memory(garrison_at_source, rows):
    garrison = {c: ['ゼウス'] for c in NORMAL}
    garrison[SOURCE] = list(garrison_at_source)
    return {'chapter': 6, 'tick': 1000, 'month': '50-11', 'gold': 500, '_records': [],
            'orders': {}, 'world': {'sorties': {}}, 'captured': list(NORMAL),
            'garrison': garrison, 'general_statuses': {'statuses': rows}}


def boss_attacks(mem):
    return [c for c in replan.interim_candidates(mem, allow_sole=False).values()
            if c.get('target') == 'ボス' and c.get('purpose') == 'attack']


def row(egg, hp=60, **extra):
    return {'hp': hp, 'egg': egg, 'uses': 4 if egg else None, 'broken': False, **extra}


def test_an_egg_carrier_goes_before_an_eggless_general_that_sorts_first():
    mem = memory(['アマンディーヌ', 'ゼウス'],
                 {'アマンディーヌ': row(None), 'ゼウス': row('まねっこエッグ')})
    attacks = boss_attacks(mem)
    assert attacks and attacks[0]['source'] == SOURCE
    assert attacks[0]['general'] == 'ゼウス'


def test_a_castle_of_eggless_generals_sends_no_boss_wave():
    mem = memory(['アマンディーヌ', 'ヘラ'], {'アマンディーヌ': row(None), 'ヘラ': row(None)})
    assert [a for a in boss_attacks(mem) if a['source'] == SOURCE] == []


def test_an_unmeasured_general_stays_a_candidate():
    mem = memory(['アマンディーヌ', 'ヘラ'], {})
    assert [a for a in boss_attacks(mem) if a['source'] == SOURCE]


def test_a_row_without_hp_is_not_proof_of_no_egg():
    mem = memory(['アマンディーヌ', 'ヘラ'],
                 {'アマンディーヌ': {'egg': None}, 'ヘラ': {'egg': None}})
    assert [a for a in boss_attacks(mem) if a['source'] == SOURCE]


def test_a_freshly_observed_egg_type_keeps_the_general():
    from docich.hanjuku.state import egg_memory
    mem = memory(['アマンディーヌ', 'ヘラ'], {'アマンディーヌ': row(None), 'ヘラ': row(None)})
    egg_memory.observe(mem, 'アマンディーヌ', egg='スロットエッグ', uses=4,
                       present=True, source=egg_memory.STATUS)
    attacks = [a for a in boss_attacks(mem) if a['source'] == SOURCE]
    assert attacks and attacks[0]['general'] == 'アマンディーヌ'


def test_a_stale_egg_type_no_longer_keeps_an_eggless_row_a_candidate():
    # #4415: egg_types is written only when an egg shows and never cleared,
    # and the other writers carry no timestamp. With no dated observation a
    # measured "no egg" row now wins, so an old egg type cannot keep a general
    # who lost the egg in the boss-wave candidate list.
    mem = memory(['アマンディーヌ', 'ヘラ'], {'アマンディーヌ': row(None), 'ヘラ': row(None)})
    mem['egg_types'] = {'アマンディーヌ': 'スロットエッグ', 'ヘラ': 'スロットエッグ'}
    assert [a for a in boss_attacks(mem) if a['source'] == SOURCE] == []
