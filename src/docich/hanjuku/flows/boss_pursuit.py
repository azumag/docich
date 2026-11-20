"""Re-aim a boss-bound general at the moving boss via the roster SELECT jump.

Owner 2026-11-16: 「ボス出撃した将軍はリストに覚えておいて、Xメニューの
「しょうぐん」からSELECTでジャンプして、ボスへの出撃に向かわせるべき」.

A boss order launches a general out of the castle; the unit then marches on
the field while the boss keeps moving. A fixed waypoint Y jump keeps landing
in the cliff/forest walls, so the pursuit instead jumps the field view to the
launched general (X -> しょうぐん -> name -> SELECT), opens its unit menu and
re-issues いどう at the boss's freshly measured cell — the same destination
step a camp redirect takes.
"""
from __future__ import annotations

import time

from ... import hanjuku_chart as chart
from ..decision_log import _record
from ..os_layer import roster as _roster
from ..os_layer.commands import fort_list_batch, menu_to, pad
from ..os_layer.readers import (
    FORT_MENU_COMMANDS, _current, _options, fort_list_open, is_camp_menu, is_fort_menu)
from ..state.regions import plan as _plan_region, world as _world
from . import house

PURSUIT_READ_LIMIT = 90
ROSTER_WAIT_LIMIT = 6         # unparsed roster frames before closing and retrying
BOSS_GENERAL_MISS_LIMIT = 2   # absent-from-menu sightings before dropping a stale wave
PURSUIT_LIST_LIMIT = 48
PURSUIT_COOLDOWN = 240        # ticks before the same general is pursued again
PURSUIT_MARCH_MIN = 400       # a just-launched march is left alone this long
# Live g1288 2026-11-20 12:00-13:30: the pursuit restarted on nearly every map
# frame (77 queued / 65 redirected in 1 h 40 m) and no redirect ever entered the
# boss. The map flow therefore never got the field to start a wave from a
# castle or to hand a visible camp to the recall owner, and the month closed
# with zero sorties (month 64-3 audit: launches 0, departures 0). Leave a gap
# between pursuit starts so the ordinary owners get the map frames back.
PURSUIT_START_GAP = 90        # ticks between two pursuit starts (~1.5 min)
# Live g1288 2026-11-20: the seven wave entries were launched at ticks
# 1475..23839 and were still being re-aimed past tick 32000. No redirect ever
# entered the boss, and because the generals never came home no castle had a
# spare and no second wave could start (month audit 64-3..65-x: launches 0).
# A wave this old is not making progress: stop chasing, let the camp recall
# take the generals back to a castle and launch fresh sorties from there.
PURSUIT_STALE_TICKS = 3600    # one hour without a fresh wave entry
# Owner 2026-11-16: the army reaches the moving boss from the north waypoint.
# Chasing the boss's last measured cell lets it walk away during the march
# (live g1288 2026-11-20: boss (938,461) -> (762,629) in ten minutes while a
# march takes about six). Aim one measured displacement ahead instead, clamped
# so the lead cell stays on the island.
PURSUIT_LEAD_TICKS = 360      # march time the lead covers (~6 min)
PURSUIT_LEAD_MAX = 240        # px, total offset cap
# A general placed on the orbit must be allowed to wait for the boss to come
# round: re-aiming it every PURSUIT_COOLDOWN (4 min) would move it before the
# boss arrives. Owner 2026-11-20: the track is fixed, so waiting on it is the
# plan. Measured: the first boss contact (g1288 2026-11-20 20:46, battle_count
# 1) came about 46 minutes after the placement; 20 minutes was too short for
# a full lap, so the wait covers an hour.
AMBUSH_WAIT_TICKS = 3600
# Live g1288 2026-11-20 17:1x-17:2x: the roster route failed repeatedly
# (roster_unreadable / an event dialogue where the roster should be) while the
# camp recall was told to stand down for the "flying" waves. Nothing reached a
# general. After two failures in a row the pursuit steps aside for a bounded
# window so the visible-camp recall (a different menu route) can re-arm the
# wave; the pursuit resumes afterwards.
PURSUIT_FAIL_SUSPEND = 2
PURSUIT_SUSPEND_TICKS = 300
ROSTER_FAILURES = frozenset({'roster_unreadable', 'general_menu_missing',
                             'x_not_open', 'read_limit', 'roster_top_unconfirmed'})
# Chapter 9's boss sweeps north-south (y 157 <-> 853 in about 330 s, measured
# 2026-11-17) while a camped general walks ~1.3 px/s. v990 tried to commit a
# redirect only while the boss was in the northern band; the next run refuted
# it (a southern reading, y 637, was followed by the first seedless boss
# battle 16 s later), so the band is gone. What the redirect record now carries
# is what the next decision needs: the general's cell, the boss cell, how old
# that reading is and the distance between them.
PURSUIT_FRESH_TICKS = 90      # a skull reading this young was taken by this very Y view (~6 ticks/s)
LIST_KEY = 'boss_wave_generals'
ABSENT_END_READS = 3          # identical roster reads at the list end = full pass
PURSUIT_STEP = 'PURSUIT'      # step id of the pursuit's own Y jump (y_jump['step'])
CLOSE_LIMIT = 12              # observations a closing probe waits for the map
UNIT_MENU_WAIT = 14           # map reads waiting for the unit's menu to draw
FORT_LIST_SEEK_LIMIT = 8      # list steps spent looking for the boss general in a 砦


def _drop_probe(mem):
    """End the probe and leave no half-finished PURSUIT Y jump behind.

    A remembered y_jump keeps the map flow steering a view nobody owns; a
    remembered y_jump_return / y_jumped record reads as a finished jump.
    """
    mem.pop('boss_pursuit', None)
    mem.pop('pursuit_lead', None)
    mem.pop('pursuit_aim', None)
    jump = mem.get('y_jump')
    if isinstance(jump, dict) and jump.get('step') == PURSUIT_STEP:
        mem.pop('y_jump', None)
    near = mem.get('near_goal')
    if isinstance(near, dict) and near.get('step') == PURSUIT_STEP:
        # map.py skips the periodic hero SELECT while any near_goal remains.
        mem.pop('near_goal', None)
    if PURSUIT_STEP in (mem.get('y_jumped') or {}):
        mem['y_jumped'].pop(PURSUIT_STEP, None)
        (mem.get('y_jumped_res') or {}).pop(PURSUIT_STEP, None)
        mem.pop('y_jump_return', None)


def _mark_lost(mem, general):
    """Record a general the full country roster no longer lists.

    Nothing else wrote generals_lost, so the wave kept the dead generals and
    every later pursuit closed general_not_found (ch9 2026-11-17, 15 of 15).
    """
    lost = mem.get('generals_lost')
    lost = list(lost) if isinstance(lost, list) else []
    if general not in lost:
        lost.append(general)
    mem['generals_lost'] = lost
    # The castles whose garrison record still lists the general now are stale
    # evidence (the march record's write-back, orders._expire_spent_sorties):
    # only a listing somewhere else may later call him back home.
    garrison = _world(mem).get('garrison') or {}
    stale = sorted(c for c, residents in garrison.items()
                   if isinstance(residents, list) and general in residents)
    mem.setdefault('generals_lost_garrison', {})[general] = stale
    # #4415: remember when the loss was confirmed on the same clock as
    # garrison_observed_at. A march write-back after this instant is bookkeeping,
    # not a fresh roster read that could call the general back home.
    mem.setdefault('generals_lost_at', {})[general] = time.time()


def track(mem):
    """Merge boss-target sorties into the remembered pursuit list (owner rule).

    Entries stay while their general is out: a garrison record of the same
    name means the unit came home, and a lost general can never be jumped to.
    """
    sorties = _world(mem).get('sorties') or {}
    garrison = _world(mem).get('garrison') or {}
    lost = set(mem.get('generals_lost') or ())
    wave = mem.setdefault(LIST_KEY, {})
    for step, s in sorties.items():
        general = (s or {}).get('general')
        # An unconfirmed launch keeps its site in planned_target only (target
        # stays None until the march is bound): ch9 2026-11-17 main line, two
        # boss launches never reached this list and no pursuit ever started.
        target = (s or {}).get('target') or (s or {}).get('planned_target')
        if target == chart.boss_castle(mem.get('chapter') or 0) \
                and isinstance(general, str) and general:
            wave.setdefault(general, {'step': step, 'launched': s.get('tick')})
    home = {g for residents in garrison.values()
            if isinstance(residents, list) for g in residents}
    stale = mem.get('generals_lost_garrison') or {}
    lost_at = mem.get('generals_lost_at') or {}
    observed = _world(mem).get('garrison_observed_at') or {}
    back = set()
    for g in lost & home:
        # A name seen back in a castle garrison was a misread, not a loss -
        # unless only the castles that listed him when the roster walk found
        # him absent still do (a march record's write-back, not a return).
        # #4415: for a dated loss, a listing only counts when that castle's
        # page was actually read after the loss. _expire_spent_sorties' write-
        # back drops the page timestamp, so it can never heal the loss.
        castles = {c for c, residents in garrison.items()
                   if isinstance(residents, list) and g in residents}
        if g in lost_at:
            at = lost_at.get(g)
            evidence = {c for c in castles
                        if isinstance(observed.get(c), (int, float))
                        and isinstance(at, (int, float)) and observed[c] >= at}
            evidence -= set(stale.get(g) or ())
        else:
            evidence = castles - set(stale.get(g) or ())
        if evidence or g not in stale:
            back.add(g)
    if back:
        mem['generals_lost'] = [g for g in (mem.get('generals_lost') or ()) if g not in back]
        for g in back:
            stale.pop(g, None)
        lost = set(mem['generals_lost'])
    for general in list(wave):
        if (general in home and not _camped_since(mem, general, wave[general].get('launched'))) \
                or general in lost:
            wave.pop(general, None)
    return wave


def _camped_since(mem, general, launched):
    """The latest observed status row says the general camps in the field.

    The monthly status read ends a march record on a camp or castle reading and
    writes the general back into the source garrison (orders._expire_spent_sorties).
    A camp reading after the launch means the unit stands in the field - a 砦 or
    a tent the pursuit can still send out - so the garrison entry must not drop
    it from the list (ch9 2026-11-17 main line: リースリング, launched tick 23868,
    status read camp at 24091, record ended at 24100).
    """
    rows = (mem.get('general_statuses') or {}).get('statuses')
    row = rows.get(general) if isinstance(rows, dict) else None
    return (isinstance(row, dict) and row.get('location_observed') is True
            and row.get('location') == 'camp' and type(row.get('tick')) is int
            and (type(launched) is not int or row['tick'] >= launched))


def wave_stale(mem):
    """True when even the newest remembered boss wave is past the stale bound.

    Uses the newest entry: one old record must not block fresh waves that a
    rebuilt sortie just added. An empty list is not stale (there is nothing to
    chase and camp recall keeps its ordinary rule).
    """
    now = int(mem.get('tick') or 0)
    launched = [e.get('launched') for e in (mem.get(LIST_KEY) or {}).values()
                if isinstance(e, dict)]
    launched = [t for t in launched if type(t) is int]
    if not launched:
        return False
    return now - max(launched) >= PURSUIT_STALE_TICKS


def wave_active(mem):
    """A boss wave still deserves the field: entries exist and are fresh.

    The house/roster/monthly/camp owners defer to the boss pursuit only while
    this is true. A stale wave (see :func:`wave_stale`) keeps its entries but
    no longer outranks them, otherwise stopping the pursuit would leave the
    field idle behind deferrals nothing serves (live g1288 2026-11-20 15:2x).
    """
    if not mem.get(LIST_KEY) or wave_stale(mem):
        return False
    return not pursuit_suspended(mem)


def pursuit_suspended(mem):
    """The roster route failed twice; let the camp recall own the field."""
    until = mem.get('boss_pursuit_suspended')
    return type(until) is int and int(mem.get('tick') or 0) < until


def ambush_contact(mem):
    """Record a boss reading that reaches a placed ambush cell.

    Owner 2026-11-20: generals wait on the measured orbit and the boss comes
    to them. This surfaces the arrival so the run log shows whether the track
    is really shared (contact) or the boss never comes (wrong point).
    """
    reading = mem.get('boss_cell') or {}
    cell = reading.get('cell')
    if not (isinstance(cell, (list, tuple)) and len(cell) == 2):
        return
    now = int(mem.get('tick') or 0)
    for general, entry in (mem.get(LIST_KEY) or {}).items():
        if not isinstance(entry, dict):
            continue
        aim = entry.get('ambush_cell')
        if not (isinstance(aim, (list, tuple)) and len(aim) == 2):
            continue
        if abs(aim[0] - cell[0]) + abs(aim[1] - cell[1]) > 16:
            continue
        last = entry.get('contact_tick')
        if type(last) is int and now - last < PURSUIT_COOLDOWN:
            continue
        entry['contact_tick'] = now
        _record(mem, 'boss_ambush_contact', general=general,
                observed_metric={'boss_cell': list(cell), 'aim': list(aim)},
                reason='待ち伏せセルにボスの実測が到達した')


def _ambush(mem, general=None):
    """A fixed point on the measured boss track for this general.

    Owner 2026-11-20: the boss looks random but orbits a fixed track, so
    generals placed *on* that track meet it as it comes round; chasing the
    current cell only follows it. The wave is spread over the track: each
    general takes a different tracked cell (its index in the sorted wave),
    so the orbit is covered instead of every unit marching to one point.
    None until the track has a real extent.
    """
    cur = (mem.get('boss_cell') or {}).get('cell')
    track = mem.get('boss_cell_track')
    if not (isinstance(cur, (list, tuple)) and len(cur) == 2 and isinstance(track, list)):
        return None
    cells = [tuple(e['cell']) for e in track
             if isinstance(e, dict) and isinstance(e.get('cell'), (list, tuple))
             and len(e['cell']) == 2 and all(type(v) is int for v in e['cell'])]
    distinct = sorted(set(cells))
    if len(distinct) < 3:
        return None
    far = max(distinct, key=lambda c: abs(c[0] - cur[0]) + abs(c[1] - cur[1]))
    if abs(far[0] - cur[0]) + abs(far[1] - cur[1]) < 160:
        return None
    if general is None:
        return far
    names = sorted((mem.get(LIST_KEY) or {}).keys())
    index = names.index(general) if general in names else 0
    return distinct[index % len(distinct)]


def _lead(mem, probe):
    """One measured boss displacement ahead, or None when it is unusable."""
    cur = mem.get('boss_cell') or {}
    prev = probe.get('boss_cell0') or {}
    cell, tick = cur.get('cell'), cur.get('tick')
    pcell, ptick = prev.get('cell'), prev.get('tick')
    if not (isinstance(cell, (list, tuple)) and len(cell) == 2
            and all(type(v) is int for v in cell) and type(tick) is int):
        return None
    if not (isinstance(pcell, (list, tuple)) and len(pcell) == 2
            and all(type(v) is int for v in pcell) and type(ptick) is int):
        return None
    gap = tick - ptick
    if gap <= 0 or gap > PURSUIT_STALE_TICKS:
        return None
    lx = int(round((cell[0] - pcell[0]) * PURSUIT_LEAD_TICKS / gap))
    ly = int(round((cell[1] - pcell[1]) * PURSUIT_LEAD_TICKS / gap))
    span = abs(lx) + abs(ly)
    if span < 24:
        return None
    if span > PURSUIT_LEAD_MAX:
        lx = int(round(lx * PURSUIT_LEAD_MAX / span))
        ly = int(round(ly * PURSUIT_LEAD_MAX / span))
    return (lx, ly)


def _note_stale_wave(mem):
    now = int(mem.get('tick') or 0)
    if mem.get('boss_wave_stale_noted') is not None:
        return
    mem['boss_wave_stale_noted'] = now
    launched = [e.get('launched') for e in (mem.get(LIST_KEY) or {}).values()
                if isinstance(e, dict)]
    launched = [t for t in launched if type(t) is int]
    newest = max(launched) if launched else None
    _record(mem, 'boss_wave_stale',
            observed_metric={'newest_launched': newest,
                             'age': now - newest if type(newest) is int else None},
            reason='ボス波の全隊が上限時間を超えて入城していないため追撃を止め、'
                   '野営回収で城へ戻して次の波を編成する')


def _candidate(mem):
    """The oldest boss-bound general that has marched long enough to re-aim."""
    now = int(mem.get('tick') or 0)
    wave = mem.get(LIST_KEY) or {}
    best = None
    for general, e in wave.items():
        launched = e.get('launched')
        if type(launched) is int and now - launched < PURSUIT_MARCH_MIN:
            continue
        # A fresh wave must not be queued behind the abandoned ones: the oldest
        # entry used to win the pick, so the new sortie waited while the stale
        # generals were re-aimed again (live g1288 2026-11-20 15:33: エストラゴン
        # launched, カシス from tick 1475 was still selected first).
        if type(launched) is int and now - launched >= PURSUIT_STALE_TICKS:
            continue
        last = e.get('pursuit_tick')
        if type(last) is int and now - last < PURSUIT_COOLDOWN:
            continue
        wait = e.get('ambush_until')
        if type(wait) is int and now < wait:
            continue
        if best is None or (launched or 0) < (best[1].get('launched') or 0):
            best = (general, e)
    return best


def _redirect_metric(mem, probe, marker):
    """What a redirect aimed at, for the run log: unit, boss, reading age, distance."""
    metric = {'target_cell': list(marker)}
    reading = mem.get('boss_cell') or {}
    cell = reading.get('cell')
    unit = probe.get('unit_cell')
    if isinstance(cell, (list, tuple)) and len(cell) == 2:
        metric['boss_cell'] = list(cell)
        if type(reading.get('tick')) is int:
            metric['boss_cell_age'] = int(mem.get('tick') or 0) - reading['tick']
    if isinstance(unit, (list, tuple)) and len(unit) == 2:
        metric['unit_cell'] = list(unit)
        if 'boss_cell' in metric:
            metric['distance'] = [metric['boss_cell'][0] - unit[0], metric['boss_cell'][1] - unit[1]]
    return metric


def _close(mem, probe, reason):
    probe['phase'] = 'close'
    probe['outcome'] = reason
    entry = (mem.get(LIST_KEY) or {}).get(probe.get('general'))
    if entry is not None:
        entry['pursuit_tick'] = int(mem.get('tick') or 0)
    _note_failure(mem, probe, reason)
    _record(mem, 'boss_pursuit_unverified', chart_step=probe.get('step'),
            general=probe.get('general'), resulting_event=reason,
            reason='将軍へのSELECTジャンプが成立しないため位置を推測せずメニューを閉じる')


def _note_failure(mem, probe, reason):
    if reason not in ROSTER_FAILURES:
        return
    streak = int(mem.get('boss_pursuit_fail_streak') or 0) + 1
    mem['boss_pursuit_fail_streak'] = streak
    if streak < PURSUIT_FAIL_SUSPEND:
        return
    now = int(mem.get('tick') or 0)
    mem['boss_pursuit_suspended'] = now + PURSUIT_SUSPEND_TICKS
    mem['boss_pursuit_fail_streak'] = 0
    _record(mem, 'boss_pursuit_suspended', chart_step=probe.get('step'),
            general=probe.get('general'),
            observed_metric={'reason': reason, 'until': now + PURSUIT_SUSPEND_TICKS},
            reason='名簿ジャンプが連続で成立しないため、一定時間は野営メニュー経由の再照準へ明け渡す')


ACTIONABLE = frozenset({'map', 'main_menu', 'general_list', 'castle_menu',
                        'castle_info', 'map_target', 'card_select', 'text',
                        'world_map', 'unknown'})


def step(screen, mem, frame):
    """Owner the screen only while a pursuit is queued or may be started."""
    if (mem.get('chapter') or 0) != 9:
        return None
    track(mem)
    ambush_contact(mem)
    if pursuit_suspended(mem):
        return None
    kind = screen.kind
    shop = mem.get('shop')
    busy = (mem.get('house') or mem.get('post_month_status')
            or mem.get('month_sub') or mem.get('month_nav')
            or mem.get('month_blind_nav')
            # The shop dict survives month end with closed=True for purchase
            # reuse, so presence alone is not screen ownership.
            or (isinstance(shop, dict) and not shop.get('closed')))
    probe = mem.get('boss_pursuit')
    if isinstance(probe, dict):
        # A menu/battle/shop phase owns the screen now: wait it out without
        # spending the read budget, so the probe resumes on the map instead
        # of starving at read_limit inside someone else's phase.
        if busy or kind not in ACTIONABLE:
            return None
        probe['reads'] += 1
        if probe['reads'] > PURSUIT_READ_LIMIT and probe.get('phase') != 'close':
            _close(mem, probe, 'read_limit')
    else:
        # No other order may be running: stealing the field mid-deploy breaks
        # the active sortie, and the pursuit only starts from the field map.
        if (kind != 'map' or _plan_region(mem).get('active') or busy):
            return None
        # #4558: don't gate on a fresh boss cell here — the pursuit's own
        # SELECT whole-map jump re-measures it (_apply_skull_boss). Requiring
        # a fresh cell made a stale/expired reading self-reinforcing: no
        # pursuit -> no re-measure -> cell stays stale -> marches overdue.
        # _candidate still needs a marched wave general, so non-ch9 phases
        # never reach this code anyway.
        if wave_stale(mem):
            _note_stale_wave(mem)
            return None
        mem.pop('boss_wave_stale_noted', None)
        pick = _candidate(mem)
        if pick is None:
            return None
        now = int(mem.get('tick') or 0)
        last = mem.get('boss_pursuit_start_tick')
        if type(last) is int and 0 <= now - last < PURSUIT_START_GAP:
            # The owner is not this flow for the moment: the map belongs to
            # the camp recall and the sortie plan (see PURSUIT_START_GAP).
            return None
        general, entry = pick
        # A fresh pursuit gets a fresh jump budget; three spent jumps would
        # otherwise end every later pursuit in boss_jump_failed.
        (mem.get('y_jumps') or {}).pop(f'{PURSUIT_STEP}:target', None)
        probe = mem['boss_pursuit'] = {
            'general': general, 'step': entry.get('step'), 'phase': 'open',
            'reads': 0, 'seeks': 0, 'x_retries': 0}
        mem['boss_pursuit_start_tick'] = now
        reading = mem.get('boss_cell') or {}
        probe['boss_cell0'] = {'cell': list(reading.get('cell') or ()),
                               'tick': reading.get('tick')}
        mem.pop('pursuit_lead', None)
        mem.pop('pursuit_aim', None)
        _record(mem, 'boss_pursuit_queued', chart_step=entry.get('step'), general=general,
                observed_metric={'since_last_start': now - last if type(last) is int else None},
                reason='ボスへ出撃した将軍へX→しょうぐん→SELECTでジャンプし、動くボスの現在セルへいどうを出し直す')
        return []

    phase = probe['phase']
    if phase == 'open':
        if kind == 'map':
            probe['phase'] = 'roster'
            return [pad('x')]
        if kind == 'main_menu':
            probe['phase'] = 'roster'
        elif kind in ('general_list', 'castle_menu', 'castle_info', 'map_target', 'card_select'):
            return [pad('b')]
        else:
            return None

    if probe['phase'] == 'roster':
        if kind == 'map':
            probe['x_retries'] += 1
            if probe['x_retries'] > 2:
                _close(mem, probe, 'x_not_open')
                return None
            return [pad('x')]
        if kind == 'main_menu':
            move = menu_to(screen, 'しょうぐん')
            if move == 'here':
                probe['phase'] = 'pick'
                return [pad('a')]
            if move:
                return [move]
            _close(mem, probe, 'general_menu_missing')
            # A general that stays absent from the 将軍 menu has left the
            # field (defeated or withdrawn); keeping the stale wave record
            # made the pursuit loop on it and skip the live waves
            # (live g1288 2026-11-20 13:0x, ギー/コリアンダー).
            general = probe.get('general')
            if general:
                misses = mem.setdefault('boss_pursuit_misses', {})
                misses[general] = int(misses.get(general) or 0) + 1
                if misses[general] >= BOSS_GENERAL_MISS_LIMIT:
                    (mem.get(LIST_KEY) or {}).pop(general, None)
                    misses.pop(general, None)
                    _record(mem, 'boss_wave_general_missing', general=general,
                            reason='将軍メニューに将軍が現れないためボス波の記録を終了する')
            return [pad('b')]
        if house.roster(screen):
            probe['phase'] = 'pick'
        else:
            # A valid-looking global roster can still fail the wage/hand
            # checks for a frame or two; waiting forever inside the owner
            # guard held the field with no input (live g1288 2026-11-20
            # 10:37, ギー/バルムンク). Close and retry the X menu.
            waits = int(probe.get('roster_waits') or 0) + 1
            probe['roster_waits'] = waits
            if waits > ROSTER_WAIT_LIMIT:
                _close(mem, probe, 'roster_unreadable')
                return [pad('b')]
            return []

    if probe['phase'] == 'pick':
        names = house.roster(screen)
        if names is None:
            if kind == 'world_map':
                # The probe has no way to read a whole-island view mid-roster,
                # and waiting on it spends the read budget without input (live
                # g1288 2026-11-20 16:11: a battle and the month menus
                # interrupted the walk, the field returned as world_map and the
                # pursuit ended read_limit). Close the view; the next field
                # frame can start the X menu again.
                _close(mem, probe, 'world_map_returned')
                return [pad('y')]
            return []
        if probe['general'] not in names:
            # A loss needs a full top-to-end walk. A list opened mid-way
            # would otherwise condemn a general above the cursor unseen.
            if not probe.get('roster_top'):
                if names and names[0] == 'どうし' and screen.selected == names[0]:
                    probe['roster_top'] = True
                else:
                    probe['top_seeks'] = probe.get('top_seeks', 0) + 1
                    if probe['top_seeks'] > _roster.MAX_GENERALS:
                        _close(mem, probe, 'roster_top_unconfirmed')
                        return [pad('b')]
                    return [pad('up')]
            # A stopped cursor alone is not proof of the list end. Require
            # the physical scroll-end pixels (or a short final page) and
            # three identical reads: the general is off the country list.
            at_end = ((frame is not None and house._roster_scroll_end(screen, frame))
                      or (len(names) < _roster.VISIBLE_NAME_ROWS
                          and screen.selected == names[-1]))
            probe['same'] = probe.get('same', 0) + 1 if (at_end and names == probe.get('last')) else 0
            probe['last'] = names
            if probe['same'] >= ABSENT_END_READS:
                _mark_lost(mem, probe['general'])
                _close(mem, probe, 'general_absent_from_roster')
                return [pad('b')]
            probe['seeks'] += 1
            if probe['seeks'] > PURSUIT_LIST_LIMIT:
                _close(mem, probe, 'general_not_found')
                return [pad('b')]
            return [pad('down')]
        move = menu_to(screen, probe['general'])
        if move == 'here':
            # SELECT on the roster jumps the field view to this general —
            # a field SELECT alone would always focus the hero (house.py).
            # The jump moves the camera without the map flow measuring it, so
            # the previous cursor estimate (the boss cell from the last Y view)
            # must not survive: update_world dead-reckons the marker's screen
            # position (still near the centre) and would keep the boss cell,
            # letting the dest phase confirm the unit's own tile with no Y jump.
            # Live g1288 2026-11-20 13:48-13:56: every redirect pressed A right
            # after いどう (no Y), the marker stayed on the unit and no new ▲
            # ever appeared - 65 redirects, zero marches.
            mem.pop('cursor', None)
            mem['uncertain'] = True
            (mem.get('y_jumped') or {}).pop(PURSUIT_STEP, None)
            (mem.get('y_jumped_res') or {}).pop(PURSUIT_STEP, None)
            mem.pop('y_jump_return', None)
            probe['phase'] = 'field_open'
            return [pad('select')]
        return [move] if move else []

    if probe['phase'] == 'field_open':
        if kind == 'map':
            probe['phase'] = 'unit_move'
            return [pad('a')]
        if kind == 'text':
            return [pad('b')] if house.roster(screen) is not None else None
        if kind == 'world_map':
            return [pad('y')]
        if kind in ('main_menu', 'castle_menu', 'castle_info', 'general_list'):
            return [pad('b')]
        return []

    if probe['phase'] == 'unit_move':
        if probe.get('fort_stage') or is_fort_menu(screen):
            if kind == 'map_target':
                # The 砦's しゅつげき ended on the destination picker (a castle
                # sortie's): the dest phase below takes it from here.
                probe['phase'] = 'dest'
            else:
                return _fort_step(screen, mem, probe)
    if probe['phase'] == 'unit_move':
        if is_camp_menu(screen):
            move = menu_to(screen, 'いどう')
            if move == 'here':
                probe['phase'] = 'dest'
                return [pad('a')]
            if move:
                return [move]
            return []
        if kind in ('castle_menu', 'castle_info'):
            _close(mem, probe, 'unit_menu_unreadable')
            return [pad('b')]
        if kind == 'map':
            # The menu draws a few frames after the A; a map that stays bare
            # is no menu this probe reads (ch9 2026-11-17 s1: 6 pursuits sat
            # 90 reads in unit_move and ended read_limit).
            probe['menu_waits'] = int(probe.get('menu_waits') or 0) + 1
            if probe['menu_waits'] > UNIT_MENU_WAIT:
                _close(mem, probe, 'unit_menu_unreadable')
                return [pad('b')]
            return []
        if kind == 'text':
            return [pad('b')] if house.roster(screen) is not None else None
        if kind == 'world_map':
            return [pad('y')]
        return []

    if probe['phase'] == 'dest':
        if kind != 'map_target':
            if kind == 'world_map':
                # Our own Y jump opened this view. Closing it with B undid the
                # jump before its first step: ch9 2026-11-17 pressed y once and
                # b 51 times and never reached y_jump_confirm.
                jump = mem.get('y_jump')
                if isinstance(jump, dict) and jump.get('step') == PURSUIT_STEP:
                    if frame is None:
                        return []
                    from ..os_layer.navigation import _y_jump_step
                    from ..os_layer.world import _apply_skull_boss
                    # The map flow reads the skull on every Y view, but this
                    # jump is ours: without the read the northern-band check
                    # judged the reading of an earlier view (ch9 2026-11-17:
                    # the measurement landed after the redirect it should have
                    # gated).
                    _apply_skull_boss(mem, frame)
                    return _y_jump_step(mem, frame)
                return [pad('b')]
            if kind == 'map':
                _close(mem, probe, 'dest_picker_lost')
                return [pad('b')]
            return []
        from . import map as _map
        goal = chart.boss_castle(9)
        marker = screen.marker
        if not marker or frame is None or not chart.moving_boss_cell(9):
            probe['lost'] = int(probe.get('lost') or 0) + 1
            if probe['lost'] < 3:
                return []
            _close(mem, probe, 'boss_position_lost')
            return [pad('b')]
        if probe.get('unit_cell') is None and screen.cursor is not None:
            # The picker opens on the unit that is being re-aimed.
            probe['unit_cell'] = list(screen.cursor)
        pseudo = {'step': 'PURSUIT', 'target': goal, 'source': None}
        if _map._target_roof_under_marker(screen, mem, frame, pseudo):
            entry = (mem.get(LIST_KEY) or {}).get(probe['general'])
            if entry is not None:
                entry['pursuit_tick'] = int(mem.get('tick') or 0)
            mem['boss_pursuit_fail_streak'] = 0
            mem['boss_pursuit_suspended'] = None
            mem.pop('pursuit_lead', None)
            placed = mem.pop('pursuit_aim', None)
            if placed:
                if entry is None:
                    entry = (mem.get(LIST_KEY) or {}).get(probe['general'])
                if entry is not None:
                    entry['ambush_until'] = int(mem.get('tick') or 0) + AMBUSH_WAIT_TICKS
                    entry['ambush_cell'] = list(placed)
            metric = _redirect_metric(mem, probe, marker)
            if placed:
                metric['aim'] = list(placed)
            _record(mem, 'boss_pursuit_redirected', chart_step=probe.get('step'),
                    general=probe['general'],
                    observed_metric=metric,
                    reason='将軍ジャンプした部隊のいどう先を、実測ボスセルへ合わせて確定する')
            probe['phase'] = 'close'
            probe['outcome'] = 'redirected'
            return [pad('a')]
        from ..os_layer.navigation import _start_y_jump
        aim = _ambush(mem, probe.get('general'))
        if aim:
            # Fixed placement on the orbit: the general waits where the boss
            # will come round instead of following today's cell.
            mem['pursuit_aim'] = list(aim)
            mem.pop('pursuit_lead', None)
        else:
            mem.pop('pursuit_aim', None)
            lead = _lead(mem, probe)
            if lead:
                mem['pursuit_lead'] = list(lead)
        if (mem.get('y_jumps') or {}).get('PURSUIT:target', 0) < 3:
            return _start_y_jump(mem, pseudo, goal, 'target')
        _close(mem, probe, 'boss_jump_failed')
        return [pad('b')]

    if probe['phase'] == 'close':
        probe['closing'] = int(probe.get('closing') or 0) + 1
        if kind == 'map' or kind == 'world_map' or probe['closing'] > CLOSE_LIMIT:
            # A world map is closed by the map flow (_close_world_map), which
            # an empty answer here kept from ever running: ch9 2026-11-17
            # sat 2500 ticks in close with reads at 2168.
            _drop_probe(mem)
            mem['uncertain'] = True
            mem['nav_search'] = True
            return None
        if kind in ('main_menu', 'general_list', 'castle_menu', 'castle_info',
                    'map_target', 'card_select') or house.roster(screen):
            return [pad('b')]
        return []
    return None


def _fort_step(screen, mem, probe):
    """Leave a 砦 by しゅつげき: its list names who may go, the card, then the picker.

    Measured on ch9 2026-11-17 (isolated probe of the g1190 state): the general
    the pursuit selected sat in a 砦 with another, A on his unit opened
    しゅつげき/こうかん (no いどう), A on しゅつげき listed the two inside, A on
    the name showed his status card and the next A opened the destination
    picker on the 砦 (map_target). The same route recall.py takes for a 砦.
    The general missing from the list is not a loss: only close.
    """
    kind = screen.kind
    stage = probe.get('fort_stage') or 'menu'
    probe['fort_stage'] = stage
    if kind == 'map':
        probe['fort_waits'] = int(probe.get('fort_waits') or 0) + 1
        if probe['fort_waits'] > UNIT_MENU_WAIT:
            _close(mem, probe, 'fort_menu_closed')
            return [pad('b')]
        return []
    if stage == 'menu':
        if not is_fort_menu(screen):
            return []
        if not fort_list_open(screen):
            move = menu_to(screen, 'しゅつげき')
            if move is None:
                return []
            if move != 'here':
                return [move]
            probe['fort_stage'] = 'list'
            return [pad('a')]
        probe['fort_stage'] = stage = 'list'
    if stage == 'list':
        if not fort_list_open(screen):
            return []
        members = [word for x, _, word in _options(screen) if x > 100]
        wanted = probe['general']
        if wanted not in members:
            # A 砦 can hold more generals than the four rows its window draws;
            # the target may be on the next page. Live g1288 2026-11-20 06:13
            # selected カシス and landed on a 砦 whose first page listed
            # アポロン/マーマレード/ヘスティア/ナストリーニ; the old code closed
            # general_not_in_fort without ever scrolling. Move the hand into
            # the list (it can still be on しゅつげき) and scroll, bounded.
            current = _current(screen)
            if current is not None and current[2] in FORT_MENU_COMMANDS:
                return [pad('right')]
            seeks = int(probe.get('fort_seeks') or 0) + 1
            probe['fort_seeks'] = seeks
            if seeks > FORT_LIST_SEEK_LIMIT:
                _close(mem, probe, 'general_not_in_fort')
                return [pad('b')]
            return [pad('down')]
        move = menu_to(screen, wanted)
        if move is None:
            return []
        if move != 'here':
            return fort_list_batch(screen, wanted) or [move]
        probe['fort_stage'] = 'status'
        probe['fort_reads'] = 0
        return [pad('a')]
    # status card: one A opens the picker, a repeat only while the card stays
    probe['fort_reads'] = int(probe.get('fort_reads') or 0) + 1
    if probe['fort_reads'] > 40:
        _close(mem, probe, 'fort_picker_missing')
        return [pad('b')]
    if house.general_status(screen):
        return [pad('a')] if probe['fort_reads'] % 6 == 1 else []
    return []
