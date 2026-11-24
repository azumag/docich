"""The Y view's own cursor is not a skull (g1342 2026-11-25 00:24).

The owner confirmed both northern kings are defeated (only 南西 and 南東
live). At 00:24 the Y-view cursor (blue square, white G, orange corner
brackets) sat on the 北西 box: its dark outline and white pixels counted as
skull light, so a light/dark pixel count read 24 and called a dead king alive.
A skull is now matched by its shape; a box under the cursor reads 'cursor' and
changes nothing. 南西's box is a live king with a general's face over it.
"""
import json
from pathlib import Path

import pytest

from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.readers import season_site_readings, season_sites, world_cursor
from docich.hanjuku.strategy import orders
import test_hanjuku_hidden_season_site_priority as base
from test_hanjuku_hidden_season_site_priority import NE, NW, SE, SITES, SW

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'
# 2026-11-25 00:24 production Y view (299x224 screenshot, read at 256x224).
LIVE_0024 = read_png(FIXTURES / 'g1342-ch4-y-view-0024-cursor-on-northwest.png').resized(256, 224)


def kinds(frame, cursor=None):
    read = season_site_readings(frame, 4, cursor)
    return {site: row['kind'] for site, row in read['readings'].items()}


def northern_kings_gone():
    """00:24 with the cursor off 北西: 北西 gets 北東's real flag-only box."""
    return base.with_boxes(LIVE_0024, {NW: base.box(LIVE_0024, NE)})


def test_the_real_00_24_view_does_not_read_the_cursor_box_as_a_skull():
    read = season_site_readings(LIVE_0024, 4)
    assert read['readings'][NW]['kind'] == 'cursor'
    assert read['readings'][NW]['blue'] >= 60 and read['readings'][NW]['shape'][0] <= 6
    assert {s: read['readings'][s]['kind'] for s in (NE, SW, SE)} == {
        NE: 'hidden', SW: 'skull', SE: 'skull'}
    assert read['healthy']


def test_the_production_cursor_also_keeps_the_box_out_of_the_reading():
    cursor = world_cursor(LIVE_0024)
    assert cursor is not None
    assert NW not in season_site_readings(LIVE_0024, 4, cursor)['readings']
    assert NW not in season_sites(LIVE_0024, 4, cursor)


def test_a_general_face_over_a_live_king_never_reads_hidden():
    """南西 at 00:24 sits at the old light threshold (15): shape, not count."""
    row = season_site_readings(LIVE_0024, 4)['readings'][SW]
    assert row['light'] == 15 and row['kind'] == 'skull'
    mem = base.memory()
    base.apply(mem, LIVE_0024, 12)
    assert mem['season_sites'][SW]['marker'] == 'skull'
    assert base.closed_sites(mem) == {NE}
    assert mem['season_sites'][SW]['hidden_streak'] == 0


@pytest.mark.parametrize('site', SITES)
def test_the_cursor_on_any_site_reads_cursor_and_changes_nothing(site):
    frame = base.with_boxes(LIVE_0024, {s: base.box(LIVE_0024, NW) if s == site
                                        else base.box(base.NE_DEFEATED, NW) for s in SITES})
    assert kinds(frame)[site] == 'cursor'
    assert all(kind == 'skull' for s, kind in kinds(frame).items() if s != site)
    mem = base.memory()
    base.apply(mem, base.skulls_everywhere(), 2)
    before = json.loads(json.dumps(mem['season_sites'][site]))
    base.apply(mem, frame, 5)
    after = mem['season_sites'][site]
    assert base.closed_sites(mem) == set()
    assert (after['state'], after['marker']) == (before['state'], before['marker'])
    assert after['cursor_streak'] == 5


def test_decide_keeps_the_covered_kings_state_and_closes_the_flag_only_north_east():
    state = base.observe(LIVE_0024, base.seed(), 3)
    mem = state['policy']
    assert base.closed_sites(mem) == {NE}
    assert mem['season_sites'][NW]['state'] == 'open'          # unread under the cursor
    assert mem['season_sites'][NW].get('marker') != 'closed'
    assert mem['season_sites'][SW]['marker'] == mem['season_sites'][SE]['marker'] == 'skull'


def test_repeated_clean_frames_close_both_north_kings_and_orders_go_south():
    frame = northern_kings_gone()
    assert kinds(frame) == {NW: 'hidden', NE: 'hidden', SW: 'skull', SE: 'skull'}
    state = base.observe(frame, base.seed(), 2)
    assert base.closed_sites(state['policy']) == set()
    state = base.observe(frame, state, 1)
    mem = state['policy']
    assert base.closed_sites(mem) == {NW, NE}
    assert {site for site in SITES if base.unlocked(mem, site)} == {SW, SE}
    assert all(orders._ready(base.boss_order(site), mem) for site in (SW, SE))
    assert not any(orders._ready(base.boss_order(site), mem) for site in (NW, NE))
    follow = orders.next_order(mem)
    assert follow is not None and follow.get('target') not in (NW, NE)


def test_a_site_not_read_cleanly_for_long_yields_to_freshly_seen_skulls():
    """The cursor can sit on a site for hours: its old skull is not evidence."""
    mem = base.observe(LIVE_0024, base.seed(), 1)['policy']
    now = mem['tick']
    mem['season_sites'][NW].update(marker='skull', last_read_tick=now - 2000)
    assert not base.unlocked(mem, NW)                          # SW/SE are freshly visible
    assert base.unlocked(mem, SW) and base.unlocked(mem, SE)
    mem['season_sites'][NW]['last_read_tick'] = now - 100
    assert base.unlocked(mem, NW)                              # recently read: normal
    mem['season_sites'][NW]['last_read_tick'] = now - 2000
    for site in (SW, SE):
        mem['season_sites'][site]['last_read_tick'] = now - 2000
    assert base.unlocked(mem, NW)                              # nothing fresh left: no wait
