"""Chapter 4's cleared 四季王 sites stay closed (g1163 2026-11-09).

The owner reported 北西・北東・南西 cleared while the bot kept sortieing
there. A king fight that ends on the closing dialogue leaves no measured 0 HP,
so the defeat never reached ``boss_victories``. The Y view draws a cleared
site as an orange pennant instead of the skull; that marker is the measurement
this module reads.
"""
from pathlib import Path

import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.os_layer.pixels import Frame, read_png
from docich.hanjuku.os_layer.readers import season_sites
from docich.hanjuku.os_layer.world import _apply_season_sites
from docich.hanjuku.strategy.orders import _site_king_down

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'
# 2026-10-30 07:44:16 JST: all four kings still hold their sites.
ALL_SKULLS = read_png(FIXTURES / 'g1075-ch4-y-view-season-skulls.png')
# 2026-11-09 02:45 JST live g1163: 北西/南西 have no marker (cleared), 南東 keeps
# the skull, and 北東 is covered by a general's portrait (unreadable).
LIVE_CLEARED = read_png(FIXTURES / 'g1163-ch4-y-view-cleared-sites.png')
SITES = chart.boss_sites(4)
ANCHOR = chart.CASTLES[3][chart.boss_castle(3)]
GRASS = (24, 141, 24)          # plain island terrain
PENNANT = (255, 181, 0)        # a marching army's own marker


def icon_origin(site):
    cell = chart.castles(4)[site]
    return (101 + (cell[0] - ANCHOR[0]) // 8, 63 + (cell[1] - ANCHOR[1]) // 8)


def painted(closed, colour=GRASS):
    """The fixture with the named sites' markers replaced by ``colour``."""
    pixels = bytearray(ALL_SKULLS.rgb)
    for site in closed:
        x, y = icon_origin(site)
        for px in range(x - 2, x + 12):
            for py in range(y - 2, y + 13):
                i = (py * ALL_SKULLS.width + px) * 3
                pixels[i:i + 3] = bytes(colour)
    return Frame(ALL_SKULLS.width, ALL_SKULLS.height, bytes(pixels))


def memory(**extra):
    return {'chapter': 4, 'tick': 100, 'boss_defeated': None, **extra}


def records(mem, decision):
    return [r for r in mem.get('_records') or [] if r.get('decision') == decision]


def test_the_2026_10_30_fixture_shows_all_four_kings():
    assert season_sites(ALL_SKULLS, 4) == {site: 'open' for site in SITES}


def test_the_2026_11_09_live_view_reads_the_owner_reported_state():
    assert season_sites(LIVE_CLEARED, 4) == {
        '四季王城(北西)': 'closed', '四季王城(南西)': 'closed',
        '四季王城(南東)': 'open'}          # 北東 is covered by a portrait


def test_no_marker_left_is_the_cleared_state():
    closed = '四季王城(北西)'
    read = season_sites(painted({closed}), 4)
    assert read[closed] == 'closed'
    assert {site: state for site, state in read.items() if site != closed} == {
        site: 'open' for site in SITES if site != closed}


def test_an_army_standing_on_a_site_is_unreadable_not_cleared():
    """g1163 2026-11-09: three orange pennants were armies, not closures."""
    occupied = '四季王城(北西)'
    read = season_sites(painted({occupied}, colour=PENNANT), 4)
    assert occupied not in read


def test_a_generals_portrait_on_a_site_is_unreadable():
    covered = '四季王城(北東)'
    skin = (255, 206, 164)
    read = season_sites(painted({covered}, colour=skin), 4)
    assert covered not in read


def test_a_site_under_the_cursor_is_not_read():
    read = season_sites(ALL_SKULLS, 4, cursor=icon_origin(SITES[0]))
    assert SITES[0] not in read and len(read) == 3


def test_another_chapter_never_reads_season_sites():
    assert season_sites(ALL_SKULLS, 3) == {}


def test_two_readings_close_the_site_and_stop_its_sorties():
    mem = memory()
    sites = season_sites(painted({'四季王城(北東)'}), 4)
    _apply_season_sites(mem, sites)
    assert not _site_king_down(mem, '四季王城(北東)')      # one reading is not enough
    assert not records(mem, 'season_site_closed')
    _apply_season_sites(mem, sites)
    assert _site_king_down(mem, '四季王城(北東)')
    assert not _site_king_down(mem, '四季王城(南東)')
    assert records(mem, 'season_site_closed')[0]['observed_metric']['site'] == '四季王城(北東)'


def test_a_skull_reading_reopens_a_closed_site():
    mem = memory()
    closed = season_sites(painted({'四季王城(南西)'}), 4)
    for _ in range(2):
        _apply_season_sites(mem, closed)
    assert _site_king_down(mem, '四季王城(南西)')
    open_site = {'四季王城(南西)': 'open'}
    for _ in range(2):
        _apply_season_sites(mem, open_site)
    assert not _site_king_down(mem, '四季王城(南西)')
    assert records(mem, 'season_site_open')


def test_an_all_closed_reading_cannot_park_the_whole_chapter():
    mem = memory()
    everything = {site: 'closed' for site in SITES}
    for _ in range(3):
        _apply_season_sites(mem, everything)
    assert not _site_king_down(mem, SITES[0])
    assert records(mem, 'season_sites_all_closed_refused')
    assert not records(mem, 'season_site_closed')


def test_the_three_cleared_sites_of_the_owner_report_stay_closed():
    """The live 2026-11-09 01:47 view: 北西/北東/南西 pennants, 南東 skull."""
    mem = memory()
    live = {'四季王城(北西)': 'closed', '四季王城(北東)': 'closed',
            '四季王城(南西)': 'closed', '四季王城(南東)': 'open'}
    for _ in range(2):
        _apply_season_sites(mem, live)
    assert {site for site in SITES if _site_king_down(mem, site)} == {
        '四季王城(北西)', '四季王城(北東)', '四季王城(南西)'}
    assert not _site_king_down(mem, '四季王城(南東)')
