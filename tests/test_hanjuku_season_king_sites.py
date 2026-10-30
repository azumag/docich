"""Chapter 4's 四季王 sites (#3048, owner 2026-10-30).

g1075 took the seventh ordinary castle at 01:33:56 and sent nothing for hours:
the base 4-B1 aimed at the legacy single 「ボス」 point, which never unlocks in
chapter 4, and the four sites had no cells. Owner: register the sites from the
live screen, send the strongest general but never the hero, keep two beside
the hero, and take back a lost castle before the next site.
"""
from pathlib import Path

import pytest

from docich import hanjuku_chart as chart, hanjuku_policy as policy
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.readers import world_flags
from docich.hanjuku.strategy import orders
from docich.hanjuku_screen import Screen, classify_text

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'
# 2026-10-30 07:44:16 JST world_map_owners: every ordinary castle ours.
CH4 = read_png(FIXTURES / 'g1075-ch4-y-view-season-skulls.png')
CH3 = read_png(FIXTURES / 'g1075-ch3-y-view-boss-skull.png')
CH3_SKULL = (101, 63)          # its top rows; アポロン's face covers row 68 on
SKULL_COLOURS = {(164, 165, 164), (255, 254, 255), (32, 32, 32)}
SITES = chart.boss_sites(4)
NORMAL = [c for c in chart.CASTLE_NAMES[4] if c not in (chart.home_castle(4), chart.boss_castle(4))]


def skull_template():
    x0, y0 = CH3_SKULL
    return [(x - x0, y - y0, CH3.pixel(x, y)) for y in range(y0, y0 + 5)
            for x in range(x0 - 1, x0 + 9) if CH3.pixel(x, y) in SKULL_COLOURS]


def skulls(frame):
    template = skull_template()
    return {(x, y) for y in range(frame.height - 5) for x in range(1, frame.width - 9)
            if all(frame.pixel(x + dx, y + dy) == c for dx, dy, c in template)}


def site_order(target='四季王城(南西)'):
    return next(o for o in chart.orders(4) if o['target'] == target)


def all_captured(**extra):
    return {'chapter': 4, 'tick': 100, 'captured': list(NORMAL), 'lost': [], 'orders': {}, **extra}


def test_each_site_cell_sits_under_its_skull_like_chapter_three():
    assert skulls(CH3) == {CH3_SKULL}
    found = skulls(CH4)
    assert len(found) == 4
    ch3_cell = chart.CASTLES[3]['ボス']
    for site in SITES:
        x, y = chart.castles(4)[site]
        # Same sprite, same anchor: the cell moves 8 px per view px of skull.
        assert ((x - ch3_cell[0]) % 8, (y - ch3_cell[1]) % 8) == (0, 0)
        skull = (CH3_SKULL[0] + (x - ch3_cell[0]) // 8, CH3_SKULL[1] + (y - ch3_cell[1]) // 8)
        assert skull in found
        aim = (x / 8 + chart.Y_JUMP_OFFSET[4][0], y / 8 + chart.Y_JUMP_OFFSET[4][1])
        assert skull[0] <= aim[0] <= skull[0] + 8 and skull[1] <= aim[1] <= skull[1] + 6
    # The names follow the corners of the island.
    cells = chart.castles(4)
    assert cells['四季王城(北西)'][0] < cells['四季王城(北東)'][0]
    assert cells['四季王城(南西)'][0] < cells['四季王城(南東)'][0]
    assert cells['四季王城(北西)'][1] < cells['四季王城(南西)'][1]
    assert cells['四季王城(北東)'][1] < cells['四季王城(南東)'][1]


def test_the_skulls_are_never_read_as_castle_flags():
    flags = world_flags(CH4, 4)
    assert not set(flags) & set(SITES)
    assert set(flags.values()) == {'own'}


def test_the_four_sites_go_nearest_march_first_from_the_closest_castle():
    sites = [o for o in chart.orders(4) if o['target'] in SITES]
    assert [o['step'] for o in sites] == ['4-B1', '4-B2', '4-B3', '4-B4']
    assert {o['target'] for o in sites} == set(SITES)
    marches = [orders._distance(4, o['source'], o['target']) for o in sites]
    assert marches == sorted(marches)
    for order in sites:
        assert order['general'] == chart.HERO and order['after'] == ('all_captured',)
        assert order['source'] == min(NORMAL, key=lambda c: orders._distance(4, c, order['target']))
    assert not [o for o in chart.orders(4) if o['target'] == chart.boss_castle(4)]


def test_g1075_after_the_seventh_castle_has_a_site_sortie_ready():
    mem = all_captured()
    assert orders._ready(site_order(), mem)
    mem['orders'] = {o['step']: 'launched' for o in chart.orders(4) if o['target'] not in SITES}
    assert policy.next_order(mem)['step'] == '4-B1'


def test_a_single_lost_castle_still_lets_a_site_sortie_start():
    # Owner 2026-10-30 (全城条件は厳しいため緩和): one missing castle no
    # longer holds the sites; retakes run alongside the site march.
    mem = all_captured()
    mem['captured'].remove('アルカ')
    mem['lost'] = ['アルカ']
    assert orders._ready(site_order(), mem)


def test_two_lost_castles_are_taken_back_before_the_next_site():
    mem = all_captured()
    mem['captured'].remove('アルカ')
    mem['captured'].remove('ミヒラギアン')
    mem['lost'] = ['アルカ', 'ミヒラギアン']
    assert not orders._ready(site_order(), mem)
    # A lost hero castle holds the sites too.
    assert not orders._ready(site_order(), all_captured(lost=[chart.home_castle(4)]))


def test_a_won_site_is_not_attacked_again():
    won = {'サマカンテ': {'enemy': 'サマカンテ', 'chapter': 4, 'site': '四季王城(南西)'}}
    mem = all_captured(boss_victories=won)
    assert not orders._boss_target_unlocked(site_order(), mem)
    assert orders._boss_target_unlocked(site_order('四季王城(南東)'), mem)


def test_the_legacy_single_boss_point_stays_locked():
    assert not orders._boss_target_unlocked({**site_order(), 'target': 'ボス'}, all_captured())


def test_a_site_sortie_keeps_two_generals_beside_the_hero():
    order = site_order()
    mem = all_captured()
    hero = chart.HERO
    three = ['ゼウス', 'ヴィーナス', 'ココット']
    sent = orders._order_hero_alternatives(order, mem, three)
    assert sent and hero not in sent
    # With the hero, any one of three may leave; of two, none may.
    assert orders._order_hero_alternatives(order, mem, [hero, *three]) == sent
    # Owner 2026-10-30 (定常的にボス城へ進行): strict guards that leave no
    # sortie-able general relax to keeping one present general - never empty.
    relaxed = orders._order_hero_alternatives(order, mem, [hero, *three[:2]])
    assert relaxed == three[:2]
    # A marching general does not guard (owner rule's own count): two present
    # beside the hero still let one go, leaving him plus one defender.
    mem['sorties'] = {'x': {'general': 'ココット', 'status': 'en_route', 'tick': 90, 'target': 'アルカ'}}
    assert orders._order_hero_alternatives(order, mem, [hero, *three]) == relaxed
    # Other chapters' boss orders keep their own sequence.
    ch3 = next(o for o in chart.orders(3) if o['target'] == 'ボス')
    assert orders._order_hero_alternatives(ch3, {'chapter': 3}, [hero, *three[:2]])


def entry(text):
    screen = Screen(lines=[], hand=None, text=text)
    screen.kind = classify_text(screen)
    return screen


def test_the_boss_entry_binds_the_site_the_general_marches_on():
    mem = {'chapter': 4, 'tick': 200, 'orders': {'4-B2': 'launched'},
           'sorties': {'4-B2': {'general': 'ゼウス', 'target': '四季王城(南東)', 'source': 'ネクスドリア',
                                'status': 'en_route', 'tick': 150}}}
    assert policy.message_step(entry('ゼウスしょうぐんがボスじょうにせめこんだ!!'), mem) == [policy.pad('a')]
    assert mem['attack'] == {'general': 'ゼウス', 'castle': '四季王城(南東)', 'side': 'attack',
                             'step': '4-B2', 'entry_evidence': 'measured_boss_entry'}


def test_two_site_marches_of_one_general_name_no_site():
    mem = {'chapter': 4, 'tick': 200,
           'sorties': {s: {'general': 'ゼウス', 'target': t, 'status': 'en_route', 'tick': 150}
                       for s, t in (('4-B1', '四季王城(南西)'), ('4-B2', '四季王城(南東)'))}}
    policy.message_step(entry('ゼウスしょうぐんがボスじょうにせめこんだ!!'), mem)
    assert (mem.get('attack') or {}).get('castle') in (None, chart.boss_castle(4))
