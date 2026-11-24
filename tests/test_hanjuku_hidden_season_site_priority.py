"""A 四季王 whose skull vanished from the Y view is a defeated king (g1342).

2026-11-24, chapter 4 on the production runtime: the owner confirmed the king
the bot fought at 北東 was defeated, and that the game's only evidence is the
skull disappearing from the Y view (no closing dialogue). The 21:11-21:15 fight
ended unclassified, so ``boss_victories`` never learned it and boss orders kept
going there while our camp stood on the site.

Measured on that runtime's 21:20 Y view (resized to the bot's 256x224):
北西 25 / 南西 25 light pixels (full skull; 北西 even has ブカティーニ marching
on it), 南東 8 (a partial skull behind a portrait), 北東 0. The 22:29 capture
of the same map is a dimmed/transition frame: every box reads ~6/0 and nothing
is terrain, so it must never close anything.

The rule is per site, not per compass point: every test that matters runs for
all four sites.
"""
import json
from pathlib import Path

import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.os_layer.pixels import Frame, read_png
from docich.hanjuku.os_layer.readers import season_site_readings, season_sites
from docich.hanjuku.os_layer.world import _apply_season_sites
from docich.hanjuku.strategy import orders
from docich.hanjuku_bot import decide

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'
SITES = chart.boss_sites(4)
NE, NW, SW, SE = ('四季王城(北東)', '四季王城(北西)', '四季王城(南西)', '四季王城(南東)')
ANCHOR = chart.CASTLES[3][chart.boss_castle(3)]
# 21:20 Y view of g1342 (299x224 screenshot; the bot reads it at 256x224).
NE_DEFEATED = read_png(FIXTURES / 'g1342-ch4-y-view-northeast-defeated-camp.png').resized(256, 224)
# 23:50 live survey of the same runtime (after the fight): 北東 shows only our
# army's pennant, the other three kings show the full skull.
LIVE_2350 = read_png(FIXTURES / 'g1342-ch4-y-view-2350-live.png').resized(256, 224)
DIMMED = read_png(FIXTURES / 'g1342-ch4-y-view-dimmed-transition.png').resized(256, 224)
STATE = json.loads((FIXTURES / 'g1342-ch4-bot-state-20261124-2120.json').read_text())
ID = STATE['policy']['_run_identity']


def box_origin(site):
    cell = chart.castles(4)[site]
    return (101 + (cell[0] - ANCHOR[0]) // 8, 63 + (cell[1] - ANCHOR[1]) // 8)


def box(frame, site):
    x, y = box_origin(site)
    return {(dx, dy): frame.pixel(x + dx, y + dy)
            for dx in range(-2, 12) for dy in range(-2, 13)}


def with_boxes(frame, boxes):
    """``frame`` with each site's marker box replaced by ``boxes[site]``."""
    pixels = bytearray(frame.rgb)
    for site, content in boxes.items():
        x, y = box_origin(site)
        for (dx, dy), colour in content.items():
            i = ((y + dy) * frame.width + x + dx) * 3
            pixels[i:i + 3] = bytes(colour)
    return Frame(frame.width, frame.height, bytes(pixels))


def only_hidden(site):
    """The real 21:20 view with the skull gone from ``site`` alone.

    The vanished box is the real 北東 box (camp, no skull); every other site
    shows the real 北西 box (full skull).
    """
    hidden, skull = box(NE_DEFEATED, NE), box(NE_DEFEATED, NW)
    return with_boxes(NE_DEFEATED, {s: hidden if s == site else skull for s in SITES})


def skulls_everywhere():
    return only_hidden(None)


def seed():
    return json.loads(json.dumps(STATE))


def observe(frame, state, times):
    for _ in range(times):
        _, state = decide(frame, json.loads(json.dumps(state)), run_identity=ID)
    return state


def closed_sites(mem):
    return {s for s in SITES if orders._site_king_down(mem, s)}


def memory():
    return {'chapter': 4, 'tick': 100, 'boss_defeated': None}


def apply(mem, frame, times=1):
    for _ in range(times):
        mem['tick'] += 1
        _apply_season_sites(mem, season_site_readings(frame, 4))


def boss_order(site):
    return next(o for o in chart.orders(4) if o['target'] == site)


def unlocked(mem, site):
    return orders._boss_target_unlocked(boss_order(site), mem)


def test_real_frame_pair_separates_a_vanished_skull_from_a_live_king_with_our_army():
    read = season_site_readings(NE_DEFEATED, 4)
    kinds = {site: (row['kind'], row['light']) for site, row in read['readings'].items()}
    assert kinds == {NW: ('skull', 25), SW: ('skull', 25), SE: ('ambiguous', 8), NE: ('hidden', 0)}
    assert read['healthy']
    # 北西 is the live king with a general of ours already marching on it.
    assert STATE['policy']['world']['launched'][NW] == {'general': 'ブカティーニ', 'step': '4-B4'}
    # The old API keeps its contract: only definite skulls/terrain are a state.
    assert season_sites(NE_DEFEATED, 4) == {NW: 'open', SW: 'open'}


def test_the_later_live_survey_keeps_the_same_split_and_closes_northeast_only():
    read = season_site_readings(LIVE_2350, 4)
    kinds = {site: (row['kind'], row['light']) for site, row in read['readings'].items()}
    assert kinds == {NW: ('skull', 25), SW: ('skull', 25), SE: ('skull', 25), NE: ('hidden', 0)}
    assert read['healthy']
    mem = memory()
    apply(mem, LIVE_2350, 3)
    assert closed_sites(mem) == {NE}


def test_a_dimmed_transition_frame_reads_unhealthy_and_closes_nothing():
    read = season_site_readings(DIMMED, 4)
    assert not read['healthy']
    mem = memory()
    apply(mem, DIMMED, 6)
    assert closed_sites(mem) == set()
    assert {row['unreadable_streak'] for row in mem['season_sites'].values()
            if row['marker'] == 'unreadable'} >= {6}


def test_decide_closes_the_defeated_northeast_only_after_three_healthy_readings():
    state = seed()
    for n in (1, 2):
        state = observe(NE_DEFEATED, state, 1)
        mem = state['policy']
        assert closed_sites(mem) == set()
        assert mem['season_sites'][NE]['hidden_streak'] == n
        assert not [r for r in state['_records'] if r['decision'] == 'season_site_closed']
    state = observe(NE_DEFEATED, state, 1)
    mem = state['policy']
    assert closed_sites(mem) == {NE}
    assert mem['season_sites'][NE]['last_read_tick'] == mem['tick']
    # The live kings stay open, the partial skull is neither.
    assert mem['season_sites'][NW]['state'] == mem['season_sites'][SW]['state'] == 'open'
    assert mem['season_sites'][SE]['state'] == 'open'
    assert mem['season_sites'][SE]['marker'] == 'ambiguous'
    closed = [r for r in state['_records'] if r['decision'] == 'season_site_closed']
    assert [r['castle'] for r in closed] == [NE]
    assert closed[0]['observed_metric']['light'] == 0 and closed[0]['observed_metric']['healthy']


@pytest.mark.parametrize('site', SITES)
def test_decide_closes_whichever_site_lost_its_skull_and_only_that_one(site):
    state = observe(only_hidden(site), seed(), 3)
    mem = state['policy']
    assert closed_sites(mem) == {site}
    for other in SITES:
        assert mem['season_sites'][other]['marker'] == ('hidden' if other == site else 'skull')


@pytest.mark.parametrize('site', SITES)
def test_a_returning_skull_resets_the_streak_and_reopens_a_wrong_closure(site):
    mem = memory()
    apply(mem, only_hidden(site), 2)
    apply(mem, skulls_everywhere())                 # the skull is back: streak restarts
    apply(mem, only_hidden(site), 2)
    assert closed_sites(mem) == set()
    apply(mem, only_hidden(site))
    assert closed_sites(mem) == {site}
    apply(mem, skulls_everywhere(), 2)
    assert closed_sites(mem) == set()
    assert any(r.get('decision') == 'season_site_open' for r in mem['_records'])


@pytest.mark.parametrize('site', SITES)
def test_a_frame_without_any_live_skull_never_closes_a_hidden_site(site):
    """Nothing in the frame proves the skull palette is drawn: unreadable."""
    hidden = box(NE_DEFEATED, NE)
    frame = with_boxes(NE_DEFEATED, {s: hidden for s in SITES})
    assert not season_site_readings(frame, 4)['healthy']
    mem = memory()
    apply(mem, frame, 8)
    assert closed_sites(mem) == set()
    assert mem['season_sites'][site]['unreadable_streak'] == 8


def test_a_partial_skull_is_never_closed_however_often_it_is_read():
    mem = memory()
    apply(mem, NE_DEFEATED, 30)
    assert mem['season_sites'][SE]['marker'] == 'ambiguous'
    assert mem['season_sites'][SE]['ambiguous_streak'] == 30
    assert mem['season_sites'][SE]['state'] is None
    assert not orders._site_king_down(mem, SE)


@pytest.mark.parametrize('site', SITES)
def test_a_skullless_site_waits_while_a_visible_skull_remains(site):
    mem = observe(only_hidden(site), seed(), 1)['policy']      # one read: hidden, still open
    assert not orders._site_king_down(mem, site)
    assert not unlocked(mem, site)
    assert all(unlocked(mem, other) for other in SITES if other != site)


@pytest.mark.parametrize('site', SITES)
def test_a_skullless_site_is_a_candidate_once_no_visible_skull_is_left(site):
    mem = observe(only_hidden(site), seed(), 1)['policy']
    for other in SITES:
        if other != site:
            mem['season_sites'][other].update(marker='closed', state='closed')
    assert unlocked(mem, site)
    # Reversible: a skull on the site itself restores the normal rule.
    mem['season_sites'][site]['marker'] = 'skull'
    assert unlocked(mem, site)


def test_the_partial_skull_of_the_southeast_yields_to_the_visible_kings():
    mem = observe(NE_DEFEATED, seed(), 1)['policy']
    assert not unlocked(mem, SE) and not unlocked(mem, NE)
    assert unlocked(mem, NW) and unlocked(mem, SW)
    for site in (NW, SW):
        mem['season_sites'][site].update(marker='closed', state='closed')
    assert unlocked(mem, SE)                # no visible skull left: SE goes first


@pytest.mark.parametrize('site', SITES)
def test_a_closed_site_does_not_stall_the_plan_or_attract_boss_orders(site):
    mem = observe(only_hidden(site), seed(), 3)['policy']
    assert closed_sites(mem) == {site}
    assert not unlocked(mem, site) and not orders._ready(boss_order(site), mem)
    # Every other king's boss order stays startable: the closure removes one
    # site from the rotation and nothing else.
    assert all(unlocked(mem, other) and orders._ready(boss_order(other), mem)
               for other in SITES if other != site)
    follow = orders.next_order(mem)
    assert follow is not None and follow.get('target') != site


@pytest.mark.parametrize('site', SITES)
def test_a_wrong_closure_stores_nothing_permanent_and_a_reopen_restores_the_plan_step(site):
    """The hidden-x3 closure only sets season_sites; nothing else is written."""
    step = boss_order(site)['step']
    others = [o for o in chart.orders(4) if o.get('target') in SITES and o['target'] != site]
    mem = memory()
    mem['plan'] = {'chart_plan': {'source': 'mechanical', 'orders': [dict(o) for o in others]},
                   'orders': {step: 'pending'}}
    apply(mem, only_hidden(site), 3)
    assert closed_sites(mem) == {site}
    assert mem['boss_defeated'] is None and not mem.get('boss_victories')
    assert mem['plan']['orders'][step] == 'pending'
    assert step not in {o['step'] for o in mem['plan']['chart_plan']['orders']}
    apply(mem, skulls_everywhere(), 2)
    assert closed_sites(mem) == set()
    assert step in {o['step'] for o in mem['plan']['chart_plan']['orders']}
    assert mem['plan']['orders'][step] == 'pending'
    assert any(r.get('decision') == 'season_site_step_restored' for r in mem['_records'])
    apply(mem, skulls_everywhere(), 2)             # no duplicate on further skull reads
    assert [o['step'] for o in mem['plan']['chart_plan']['orders']].count(step) == 1


@pytest.mark.parametrize('frame', [NE_DEFEATED, LIVE_2350])
def test_a_hidden_site_with_our_flag_on_it_is_recorded_as_hidden_with_camp(frame):
    """An abandoned camp hides a live skull too: say why the skull is missing."""
    mem = memory()
    apply(mem, frame, 3)
    row = mem['season_sites'][NE]
    assert row['marker'] == 'hidden' and row['hidden_with_camp'] and row['camp'] >= 6
    assert all(not mem['season_sites'][s]['hidden_with_camp'] for s in SITES if s != NE)
    closed = [r for r in mem['_records'] if r['decision'] == 'season_site_closed']
    assert closed[0]['observed_metric']['hidden_with_camp'] is True
    # The annotation never changes the rule: the site is still just hidden-x3
    # closed and a returning skull reopens it with nothing else stored.
    apply(mem, skulls_everywhere(), 2)
    assert closed_sites(mem) == set() and not mem['season_sites'][NE]['hidden_with_camp']
    assert mem['boss_defeated'] is None and not mem.get('boss_victories')
