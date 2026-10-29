"""The strongest general with an egg goes to a 四季王 site (#3048, owner 2026-10-30).

g1075 08:20-08:27 (v328): each site order went from its chart source because
that castle held one fit companion, so the lone アルテミス (HP 49 / combat 7),
カペリーニ (63/7, no egg) and ミュスカデ (57/9, no egg) were sent. The 四季王
fights as a summoned monster from the first turn with no きりふだ row
(08:27:56 サマカンテ), and ミュスカデ lost a field summon battle with neither.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import orders

NORMAL = [c for c in chart.CASTLE_NAMES[4] if c not in ('アルマムーン', 'ボス')]
HOME = chart.home_castle(4)


def site_order(target='四季王城(南西)'):
    return next(o for o in chart.orders(4) if o['target'] == target)


def memory(**garrison):
    # 08:20 lists; the hero's castle, where joiners stand, was never read.
    lists = {'ミヒラギアン': ['アルテミス'], 'ネクスドリア': ['カペリーニ'],
             'フヌイヌム': ['ミュスカデ'], 'アルカ': ['キール'], **garrison}
    return {'chapter': 4, 'tick': 100, 'captured': list(NORMAL), 'lost': [], 'orders': {},
            'garrison': lists, 'egg_types': {'アルテミス': 'かどまつエッグ'},
            'egg_uses': {'アルテミス': 4}}


def test_g1075_lists_read_the_heros_castle_before_sending_a_weak_lone_general():
    mem = memory()
    assert orders._order_hero_alternatives(site_order(), mem, ['カペリーニ']) == []    # no egg
    assert orders._order_hero_alternatives(site_order(), mem, ['ミュスカデ']) == []
    assert orders._boss_companion_source(site_order(), mem) == HOME
    assert orders._ready(site_order(), mem)


def test_the_strongest_companion_of_every_read_list_goes():
    mem = memory(アルカ=['ヴィーナス', 'キール'])
    assert orders._boss_companion_source(site_order(), mem) == 'アルカ'
    mem['garrison'][HOME] = [chart.HERO, 'アポロン', 'ゼウス', 'ココット', 'アテナ']
    assert orders._boss_companion_source(site_order(), mem) == HOME
    assert orders._order_hero_alternatives(site_order(), mem, mem['garrison'][HOME])[:2] == ['ゼウス', 'アポロン']


def test_a_read_heros_castle_without_a_strong_spare_falls_back_to_the_best_read_list():
    mem = memory()
    mem['garrison'][HOME] = [chart.HERO, 'ゼウス', 'ココット']      # ゼウス would leave one guard
    assert orders._boss_companion_source(site_order(), mem) == 'ミヒラギアン'


def test_an_egg_is_the_screens_reading_else_the_rosters():
    mem = memory()
    mem['egg_types'].update({'キャラウェイ': 'いっぱつエッグ', 'ヘスティア': 'くさってる'})
    assert orders._holds_egg(mem, 'キャラウェイ')            # bought or given since
    assert not orders._holds_egg(mem, 'ヘスティア')
    assert orders._holds_egg(mem, 'ゼウス') and not orders._holds_egg(mem, 'カペリーニ')
    mem['egg_uses']['アルテミス'] = 0
    assert not orders._holds_egg(mem, 'アルテミス')


def test_other_chapters_keep_the_chart_source_first():
    boss = next(o for o in chart.orders(3) if o['target'] == 'ボス')
    mem = {'chapter': 3, 'captured': [c for c in chart.CASTLE_NAMES[3] if c not in ('アルマムーン', 'ボス')],
           'garrison': {boss['source']: ['カペリーニ'], 'グリン': ['ゼウス']}}
    assert orders._boss_companion_source(boss, mem) == boss['source']
