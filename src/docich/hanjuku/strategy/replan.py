"""戦略層: off-chart replanning (interim orders and adjusted charts).

Builds interim candidates when the chart has no ready order, starts the first
of them without waiting for a model, adopts a validated adjusted chart when
one answers, and records every deviation.
"""
from __future__ import annotations
from ..state.regions import world as _world, sorties as _sorties

import hashlib

from ... import (hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust,
                 hanjuku_reference as reference)
from ..decision_log import _record, _tally
from ..os_layer.world import NAME, SORTIE_BUSY_TICKS, _en_route, _owned
from . import routes
from .orders import (_arrival_castle, _boss_companion_source, _boss_egg_depleted,
                               _distance, _hero_egg_broken, _holds_egg,
                               _hero_source_alternative, _orders, _plan_pending, _ready, _reserved_targets, _source,
                               _defense_move_pick, _defense_priority_castles, _defense_score,
                               _garrison_plan, _post_conquest_wait,
                               _source_list_open, _source_location_recheck, _source_location_recheck_due,
                               _unplaced_castle_generals)


def _march_blocked(chapter, source, target, owned):
    # Chapter 3 marches follow the road table (``routes``), not the straight
    # line — an own castle off the road is no blocker even when it sits on
    # the segment.
    return not routes.is_road(chapter) and chart.march_blocked(chapter, source, target, owned)


def _march_blocker(chapter, source, target, owned):
    return chart.march_blocker(chapter, source, target, owned) if not routes.is_road(chapter) else None


def _interim_source(mem, target, chart_order, owned, busy, allow_sole=False):
    """(source, general) for an interim sortie to ``target``, or None.

    A castle whose general list we last read is used as measured: an empty one
    is never a source (g401: the chart general had already left キカンドン and
    the bot pressed A there for 14 minutes). Without a reading, fall back to
    the chart's source, then the home castle, as before.

    ``allow_sole`` is the monthly fallback of the defence wait: a castle with a
    single general may send it, except a castle already lost or blocked after
    an earlier break (g401: an emptied castle fell).
    """
    chapter = mem.get('chapter') or 0
    home = chart.home_castle(chapter)
    garrison = _world(mem).get('garrison') or {}
    busy = busy | set(_world(mem).get('general_location_unknown') or ())
    unavailable_for_attack = {NAME} if _hero_egg_broken(mem) else set()
    sole = len(owned) == 1          # owner: a single castle may leave the hero alone
    free = {c: [g for g in garrison.get(c) or () if g not in busy] for c in owned}
    if routes.is_road(chapter):
        owned = {c for c in owned if routes.allowed(mem, c, target)}
    # Emptying a castle is how the undefended ジョンリギ fell (g401), so a
    # castle that keeps somebody behind is required. Owner 2026-11-02: an
    # own castle lying on the march stops the army inside (アルマムーン →
    # クロチェット enters オトラント; every measured アルマムーン-launch
    # stalled while the only arrival was オトラント-launched 5-Z2), so a
    # source whose path crosses another own castle ranks behind a clear one.
    need = chart.sender_min(chapter)    # never empty a castle (owner 2026-11-06); 1 on chapter 9
    excluded = set()
    # Owner 2026-11-03 (g1097): generals moved into a castle recorded empty
    # stood there unseen while アルマムーン kept sending the sorties. The
    # castle's own list, opened once by a probe, is the arrival receipt; the
    # read then lets them sortie from it like any staffed castle.
    arrivals = sorted((c for c in owned if routes.arrival_probe_due(mem, c, cooldown=True)),
                      key=lambda c: (_distance(chapter, c, target), c))
    if arrivals:
        return arrivals[0], MOVE_ANY_GENERAL
    staffed = sorted((c for c in owned if len(set(free[c])) >= need
                      and _spares(free[c], unavailable_for_attack, sole) and c not in excluded),
                     key=lambda c: (_march_blocked(chapter, c, target, owned),
                                    _distance(chapter, c, target)))
    if staffed:
        best = staffed[0]
        if _march_blocked(chapter, best, target, owned):
            # A march from the only staffed source just stops inside the
            # blocking own castle — sortie from it instead (owner 2026-11-02:
            # オトラントから出ないとかいけつにならない). Its spare leaves even
            # when the list is thin: the march would only deliver the general
            # there anyway. An unread list is opened as a probe.
            blocker = _march_blocker(chapter, best, target, owned)
            if blocker:
                # A blocking own castle is a real departure source, not merely
                # a waypoint. With one measured defender it cannot sortie,
                # even when the distant castle has many available generals.
                blocker_free = [g for g in free.get(blocker) or ()
                                if g not in unavailable_for_attack]
                if len(set(blocker_free)) >= need:
                    spares = _strongest_first(mem, _spares(blocker_free, unavailable_for_attack, sole))
                    if spares:
                        return blocker, spares[0]
                if garrison.get(blocker) is None:
                    return blocker, MOVE_ANY_GENERAL
                return None  # reinforce the blocker before trying this target
        spares = _strongest_first(mem, _spares(free[best], unavailable_for_attack, sole))
        return best, spares[0]
    probes = sorted(c for c in owned if routes.probe_due(mem, c))
    if probes:
        return probes[0], MOVE_ANY_GENERAL
    recheck = sorted((c for c in owned if _source_location_recheck_due(mem, c)),
                     key=lambda c: (_distance(chapter, c, target), c))
    if recheck:
        return recheck[0], MOVE_ANY_GENERAL
    unplaced = [g for g in _unplaced_castle_generals(mem)
                if g not in busy and g not in unavailable_for_attack]
    arrival = _arrival_castle(mem, owned)
    if (unplaced and arrival in owned and _source_location_recheck(mem, arrival) is None
            and not chart_adjust.hero_guard_short([*(garrison.get(arrival) or ()), *unplaced],
                                                  unplaced[0], busy, sole_castle=sole)):
        # A joiner has no verified source-list membership yet. Open the
        # list as a probe, rather than naming a possibly absent actor.
        return arrival, MOVE_ANY_GENERAL
    general = chart_order['general'] if chart_order else NAME
    if general == NAME:
        # Owner 2026-11-08: 主人公はアルマムーンから動かさない。A chart order that
        # names the hero still runs, because deploy answers it with a live
        # companion, but an interim copy of it must not stand as a hero sortie
        # plan. Send a spare measured at the chart source, else let the live
        # list choose the actor.
        source = (chart_order or {}).get('source')
        spare_pool = [g for g in (garrison.get(source) or ()) if g not in busy]
        if source in owned and len(set(spare_pool)) >= need:
            spares = _spares(spare_pool, unavailable_for_attack, sole)
            if spares:
                return source, spares[0]
        general = MOVE_ANY_GENERAL
    if general in unavailable_for_attack:
        return None
    if general in busy:
        # A marching or unlocated general is waited for. One missing from this
        # month's complete roster has left the army: g1075 02:34-02:43, 4-C1's
        # ココット kept the retake of アルカ off the unread hero castle and a
        # sole defender left マラコビア instead. The read list picks a spare.
        roster = mem.get('recruit_roster') or {}
        if not (roster.get('complete') is True and roster.get('chapter') == chapter
                and roster.get('month') == mem.get('month')
                and general not in (roster.get('names') or ())):
            return None
        general = MOVE_ANY_GENERAL
    for source in ((chart_order or {}).get('source'), home):
        if source in owned and garrison.get(source) is None:
            # The chart name is not evidence that its actor occupies this
            # unread castle. Identify the actor only from the opened list.
            return source, MOVE_ANY_GENERAL
    # Refuting home does not refute every other owned castle. g684 4-2:
    # four known lists each held one defender, while ジョンリギ's list was
    # never opened and several current castle-status generals were unplaced.
    # Inspect the nearest unread source through the existing verified
    # castle/list route; only its real list may permit a spare to leave.
    refuted = mem.get('unplaced_refuted_castles') or {}
    unplaced_names = _unplaced_castle_generals(mem)
    def probe_due(c):
        if _source_location_recheck(mem, c) is not None:
            return _source_location_recheck_due(mem, c)
        if garrison.get(c) is None:
            return True
        if garrison.get(c) == []:
            return False
        old = refuted.get(c) or (mem.get('unplaced_refuted') or {})
        disproved = (old.get('castle') == c and old.get('month') == mem.get('month')
                      and old.get('names') == unplaced_names)
        # A defender/arrival updates garrison with one known name, which
        # does not prove that the castle's complete list contains only one.
        return bool(unplaced_names) and not disproved and len(set(free[c])) < 2
    unread = sorted((c for c in owned if probe_due(c)),
                    key=lambda c: (_distance(chapter, c, target), c))
    if unread:
        return unread[0], MOVE_ANY_GENERAL
    return None


MOVE_ANY_GENERAL = chart_adjust.PROBE_GENERAL   # move from an unread list: the spare is picked there
INTERIM_WAVE_LIMIT = 4         # extra sorties to the first target when spares allow
# Owner 2026-11-05: the boss dies to volume, not strength. Half-ripe eggs are
# so weak that a strong general and a weak one remove about the same HP, so
# every spare goes at the boss, from as many castles as possible (each keeps
# one defender), with no per-plan wave bound. Owner 2026-11-05 (later the same
# day): BOSS_WAVE_LIMIT is unnecessary — keep sending. The loop still ends
# when no castle can send and every unread list has been opened once.
# Owner 2026-11-04: a boss step that failed its launch-marker retries must not
# end the chapter's sorties. The mechanical plan is rebuilt every observation
# under the same request id and offers the same step, and next_order skips a
# 'failed' step forever, so the boss was never attacked again (g1133). Re-arm
# it with a small per-month budget and a cooldown; the ordinary readiness,
# source and kit checks still gate every launch.
BOSS_STEP_REARM_LIMIT = 3            # failed boss steps re-armed per game month
BOSS_STEP_REARM_COOLDOWN = 100       # observations between re-arms (~2.5 min at 1.5 s)
# Owner 2026-11-04 (g1133): the mechanical boss wave waits until every other
# castle is owned, so one lost castle whose retake never launches (クロチェット:
# the march from アルマムーン is absorbed by オトラント, the only オトラント
# general must stay) held the whole army at home for an hour while the boss
# got no sortie. After this many failed retake launches for a castle the
# retake stops gating the boss; it stays in the plan as an ordinary candidate.
RETAKE_STALL_FAILS = 3
RETAKE_FAILURE_KEEP = 8              # failed step ids remembered per lost castle


def _failed_retakes(mem, record=False):
    """{lost castle: failed retake step ids} from the book and the stored plan.

    The mechanical plan is rebuilt (and its failed steps may drop out) every
    time it is exhausted, so failures are copied into ``retake_failures``
    when ``record`` is set — the rebuild calls it just before clearing the
    plan. Readers pass ``record=False`` and merge the live plan on top, so a
    caller that never rebuilds still sees the failures the plan holds.
    """
    chapter = mem.get('chapter') or 0
    lost = {c for c in mem.get('lost') or () if not chart.is_boss_castle(c)}
    book = mem.get('retake_failures')
    stored = book.get('castles') if isinstance(book, dict) and book.get('chapter') == chapter else {}
    # A castle retaken (or a chapter change) clears its failures.
    found = {c: list(steps) for c, steps in (stored or {}).items()
             if c in lost and isinstance(steps, list)}
    plan = mem.get('chart_plan') or {}
    status = mem.get('orders') or {}
    if plan.get('chapter') in (None, chapter):
        for order in plan.get('orders') or ():
            target, step = order.get('target'), order.get('step')
            if target in lost and status.get(step) == 'failed':
                steps = found.setdefault(target, [])
                if step not in steps:
                    steps.append(step)
    found = {c: steps[-RETAKE_FAILURE_KEEP:] for c, steps in found.items()}
    if record:
        mem['retake_failures'] = {'chapter': chapter, 'castles': found}
    return found


def _retakes_stalled(mem, blocking):
    """True when every castle blocking the boss wave is lost and keeps failing."""
    if not blocking:
        return False
    lost = set(mem.get('lost') or ())
    failed = _failed_retakes(mem)
    return all(c in lost and len(failed.get(c) or ()) >= RETAKE_STALL_FAILS
               for c in blocking)


def _boss_wave_in_flight(mem, boss):
    """Recent sorties already heading for the boss castle.

    Counts the sortie records that still reserve a target plus a launched
    plan order whose sortie record has not been written yet, so the bound
    cannot be passed by the gap between the launch and its receipt.
    """
    now = int(mem.get('tick') or 0)
    sorties = _world(mem).get('sorties') or {}
    steps = set()
    for step, sortie in sorties.items():
        if (not isinstance(sortie, dict)
                or (sortie.get('target') or sortie.get('planned_target')) != boss
                or sortie.get('status') not in ('en_route', 'launched_unconfirmed')
                or type(sortie.get('tick')) is not int
                or not 0 <= now - sortie['tick'] < SORTIE_BUSY_TICKS):
            continue
        steps.add(step)
    status = mem.get('orders') or {}
    for order in (mem.get('chart_plan') or {}).get('orders') or ():
        if (isinstance(order, dict) and order.get('target') == boss
                and status.get(order.get('step')) == 'launched'
                and order.get('step') not in sorties):
            steps.add(order.get('step'))
    return len(steps)


def _boss_wave_open(mem, target):
    """The boss keeps taking waves while sorties to it are still marching.

    Owner 2026-11-05: no in-flight budget. Every spare goes at the boss, and
    the plan bounds itself by the generators and castles it can still send.
    """
    return chart.is_boss_castle(target)


def _wave_pick(mem, target, owned, busy, allow_sole):
    """(source, general) of a measured spare for a same-target wave.

    Unlike ``_interim_source`` this never picks an unread castle: an unread
    list is a probe for the first sortie, not a known spare for a wave.
    Sources still keep one defender; the home castle is preferred.

    A source must also be able to reach the target: chapter 3 keeps only the
    measured castle-to-castle legs (``routes.allowed``), so a wave named from
    any other castle is unstartable. g1153 2026-11-08 (ドンダム the only enemy
    castle) filled the plan with パタゴーヌ→ドンダム and メガパタ→ドンダム
    waves while only イーピヨル→ドンダム is a measured leg; ``orders._ready``
    rejected them every tick and the month advanced with no sortie.
    """
    chapter = mem.get('chapter') or 0
    home = chart.home_castle(chapter)
    garrison = _world(mem).get('garrison') or {}
    unavailable = set(_world(mem).get('general_location_unknown') or ())
    need = chart.sender_min(chapter)    # never empty a castle (owner 2026-11-06); 1 on chapter 9
    for source in sorted(owned, key=lambda c: (c != home, c)):
        if not routes.allowed(mem, source, target):
            continue
        names = garrison.get(source)
        if not names:
            continue
        free = [g for g in names if g not in busy and g not in unavailable]
        if len(free) < need:
            continue
        spares = [g for g in _spares(free) if g != NAME]
        if spares:
            return source, spares[0]
    return None


GENERAL_LABEL = '一覧の余剰'   # how a MOVE_ANY_GENERAL order reads inside a note


def _general_label(general):
    """A human-readable actor for an order note.

    ``しょうぐん`` is the internal sentinel for "pick the spare on the live
    list" (MOVE_ANY_GENERAL), not a general named しょうぐん and not the hero
    (the hero is どうし and never sorties). Notes that printed the sentinel
    read as if a hero-like しょうぐん were attacking (owner report
    2026-11-09 16:24).
    """
    return GENERAL_LABEL if general == MOVE_ANY_GENERAL else general


def _current_hp(mem, general):
    """The general's measured current HP, or None when unread."""
    row = ((mem.get('general_statuses') or {}).get('statuses') or {}).get(general)
    if not isinstance(row, dict):
        return None
    hp = row.get('hp')
    return hp if type(hp) is int and hp >= 0 else None


def _strongest_first(mem, generals):
    """Order a castle's spare generals by known current HP, strongest first.

    g1163 chapter 5 (2026-11-09): プトレマイス was attacked five times with
    expiring weak generals (ビシソワーズ 27, セミヨン 36) while カルーン held
    ポセイドン 85. The garrison list is the game's roster order, not strength,
    so the first spare was not the best one. A general whose HP is unread keeps
    its list position after the measured ones.

    Current HP is what a sortie fights with: g1163 chapter 7 2026-11-09 15:53
    sent アルテミス (hp 1/49) and リーキ (hp 10/30) and both battles ended at
    ally 0 before a single egg command. A general measured at 0 HP is left for
    the repair pass instead of being sent to die.
    """
    statuses = ((mem.get('general_statuses') or {}).get('statuses') or {})
    rows = statuses if isinstance(statuses, dict) else {}

    def rank(item):
        index, name = item
        hp = _current_hp(mem, name)
        row = rows.get(name)
        max_hp = row.get('max_hp') if isinstance(row, dict) else None
        value = hp if hp is not None else max_hp
        dead = 1 if hp == 0 else 0
        return (dead, 0 if type(value) is int else 1,
                -(value if type(value) is int else 0), index)

    return [name for _, name in sorted(enumerate(generals), key=rank)]


def _spares(free, unavailable=(), sole=False):
    """Generals a castle may send; no castle is ever left empty.

    The hero's castle keeps ``HERO_CASTLE_GUARD`` other generals (owner
    2026-10-29). Owner 2026-11-06 dropped the sole-castle exception: an
    emptied castle falls to the next enemy and ends the game, so even our
    only castle keeps one general (the hero included).
    """
    return [g for g in free if g != NAME and g not in unavailable
            and not chart_adjust.hero_guard_short(free, g)]


def _boss_egg_rank(mem, general):
    """How usable the general's egg is as boss-fight damage: 0 best, 2 worst.

    Owner 2026-11-05: the boss fight's damage comes from eggs, not general
    strength. A general already measured with a broken/spent egg adds almost
    nothing and may be lost, so when one castle has several spares the live
    egg goes first. Only same-castle ordering changes; the castle choice and
    every other rule stay as they are.
    """
    row = ((mem.get('general_statuses') or {}).get('statuses') or {}).get(general)
    if isinstance(row, dict):
        if row.get('broken') is True:
            return 2
        uses = row.get('uses')
        if type(uses) is int and uses <= 0:
            return 2
        egg = row.get('egg')
        if isinstance(egg, str) and egg:
            return 2 if egg == 'くさってる' else 0
    uses = (mem.get('egg_uses') or {}).get(general)
    if type(uses) is int and uses <= 0:
        return 2
    if isinstance((mem.get('egg_types') or {}).get(general), str):
        return 0 if type(uses) is int and uses > 0 else 1
    return 1


def _defense_rebalance(mem, owned, busy, heading):
    """Boss-wait moves that staff a weak priority castle (#2985).

    Only a measured list can qualify a castle, and only a spare strictly
    stronger than its best defender is sent, so the moves stop once every
    priority castle holds the best available general. A donor's remaining
    best must not drop below the target it relieves, which keeps two weak
    castles from trading generals forever. The hero is not counted as a
    defender: it may sortie at any time, so weak non-hero garrisons still
    qualify for reinforcement.
    """
    chapter = mem.get('chapter') or 0
    garrison = _world(mem).get('garrison') or {}
    home = chart.home_castle(chapter)
    priority = _defense_priority_castles(mem)
    unlocated = set(_world(mem).get('general_location_unknown') or ())
    free = {c: [g for g in (garrison.get(c) or ())
                if g not in busy and g not in unlocated] for c in owned}
    out = []
    for target in sorted(priority, key=lambda c: _distance(chapter, home, c)):
        if (target in heading or garrison.get(target) is None
                or routes.unread_dispatches(mem, target)):
            continue
        best = max((_defense_score(mem, g) for g in free[target] if g != NAME),
                   default=None)
        donors = []
        for source in sorted(owned, key=lambda c: _distance(chapter, c, target)):
            if source == target or len(free[source]) < 2:
                continue
            sendable = [g for g in _spares(free[source]) if g != NAME]
            if not sendable:
                continue
            top = sorted(sendable, key=lambda g: _defense_score(mem, g),
                         reverse=True)[0]
            top_score = _defense_score(mem, top)
            if best is not None and top_score <= best:
                continue
            rest_best = max((_defense_score(mem, g)
                             for g in free[source] if g != top),
                            default=(0, 0, 0))
            if best is not None and rest_best < best:
                continue
            donors.append((top_score, -_distance(chapter, source, target),
                           source, top))
        if not donors:
            continue
        _, _, source, general = max(donors)
        out.append({'general': general, 'target': target, 'cards': [],
                    'source': source, 'after': None, 'purpose': 'move',
                    'defense_rebalance': True,
                    'note': f'暫定: ボス出撃待ちの防衛優先として{_general_label(general)}を{source}から{target}へ移動'})
    return out


GARRISON_ROTATION_LIMIT = 3


def _garrison_rotation(mem, owned, busy, heading, taken=()):
    """Owner 2026-11-04: rotate idle generals toward the enemy-facing castles.

    ``_garrison_plan`` sets the roster each owned castle should hold from its
    distance to the nearest enemy castle; this emits the moves that close a
    gap. A castle farther from every enemy sends one spare forward (its own
    floor may be zero in the rear), while the front ring fills to
    ``GARRISON_FRONT_TARGET``. Only a measured list can send or receive, a
    target with an unread transfer waits for its own list (the arrival
    receipt, #3037), and a castle already claimed by another candidate or an
    outgoing march is not targeted twice in one plan.
    """
    chapter = mem.get('chapter') or 0
    plan = _garrison_plan(mem)
    if not plan:
        return []
    garrison = _world(mem).get('garrison') or {}
    unavailable = set(busy) | set(_world(mem).get('general_location_unknown') or ())
    free = {c: [g for g in garrison.get(c) or () if g not in unavailable] for c in plan}
    sole = len(plan) == 1
    claimed = set(taken) | set(heading)
    used = set()
    out = []
    # The thinnest castle first: a castle holding one defender loses it (and
    # the castle) to the first enemy stack (g1163 chapter 8 2026-11-09 21:33:
    # ながはま held only アテナ when キッシュ attacked). Distance only breaks
    # ties between equal deficits.
    def _deficit(castle):
        return plan[castle][1] - len(set(free.get(castle) or ()))

    for target in sorted(plan, key=lambda c: (-_deficit(c), plan[c][0], c)):
        if len(out) >= GARRISON_ROTATION_LIMIT:
            break
        distance, want, _floor = plan[target]
        if target in claimed or routes.unread_dispatches(mem, target):
            continue
        # An unread garrison (no row at all) still gets a second defender: the
        # castle that fell last (g1163 2026-11-09 19:49 フィロメラ) had a
        # defender fighting while the bookkeeping held no row, so "unread" is
        # exactly the state that loses castles. The arrival also reads the
        # list, so the next pass knows what is really there.
        if len(set(free[target])) >= want:
            continue
        donors = []
        for source in sorted(plan, key=lambda c: (-plan[c][0],
                                                  _distance(chapter, c, target), c)):
            if source == target or source in used:
                continue
            source_distance, _source_want, source_floor = plan[source]
            if source_distance <= distance or not routes.allowed(mem, source, target):
                continue
            # Even a rear-castle floor of zero cannot override the actual
            # game's minimum two-general requirement for a departure.
            if (garrison.get(source) is None or len(set(free[source])) < 2
                    or len(free[source]) - 1 < source_floor):
                continue
            spares = [g for g in _spares(free[source]) if g != NAME]
            if not spares:
                continue
            general = sorted(spares, key=lambda g: _defense_score(mem, g),
                             reverse=True)[0]
            # A castle already above its own target donates first; rear
            # castles may thin down to one, but never dispatch their last.
            surplus = 1 if len(free[source]) > _source_want else 0
            donors.append((surplus, source_distance,
                           -_distance(chapter, source, target), source, general))
        if not donors:
            continue
        _, _, _, source, general = max(donors)
        out.append({'general': general, 'target': target, 'cards': [],
                    'source': source, 'after': None, 'purpose': 'move',
                    'garrison_rotate': True,
                    'note': f'暫定: 敵城に近い{target}を厚くするため{_general_label(general)}を{source}から移動'})
        used.add(source)
        claimed.add(target)
    return out


INTERIM_CARDS = ('イッテツーン', 'イッテツーン')   # same opener as a lost melee's retry


def interim_candidates(mem, allow_sole=False) -> dict:
    """Deterministic off-chart sorties while a chart is pending.

    Retake castles we lost, then staff owned castles last seen empty (the home
    castle first) with a spare general from a castle that keeps somebody,
    then attack every other uncaptured non-boss castle (chart targets first).
    Moves come before attacks because the first candidate is also the
    fallback: listed last they never ran (g403-g407), while undefended
    castles fell without a battle and g407 left the home castle empty.
    Sources and generals come from measured general lists when known
    (``garrison``), so an idle general at any castle is used instead of only
    the chart's general. Attacks carry cards (INTERIM_CARDS). Only candidates
    passing ``chart_adjust.startable`` are returned, and the first one is the
    order that runs; no model chooses among them.
    """
    chapter = mem.get('chapter') or 0
    castles = chart.castles(chapter)
    home = chart.home_castle(chapter)
    owned = _owned(mem) & set(castles)
    busy, _ = _en_route(mem)
    heading = _reserved_targets(mem)
    chart_orders = {}
    for order in chart.orders(chapter):
        chart_orders.setdefault(order['target'], order)
    # A road chapter runs without chart orders, but its encoded chart still
    # says which cards each castle wants (chapter 9's soldiers have HP100).
    card_orders = {}
    if chart.corridor(chapter):
        for order in chart.all_orders(chapter):
            card_orders.setdefault(order['target'], order)
    lost = [c for c in mem.get('lost') or () if c in castles]
    # Owner 2026-11-02: a lost castle that gates the boss path must come back
    # before unrelated retakes — otherwise the bot keeps re-taking minor
    # castles while the boss chain's `after` preconditions can never fire
    # (g1097: 5-C4 バスカビル→プトレマイス対チキータ waited on captured
    # バスカビル while interim orders took アイアイエー/ボッサール/ジュメルズ).
    # The boss order (5-B1 プトレマイス→ボス) is gated out of ``orders()``, so
    # the path is walked on ``all_orders``: seed from its boss targets, then
    # back through each boss-path order's source (ボス ← プトレマイス ←
    # バスカビル ← アイアイエー).
    mechanical = not chart.speedrun_enabled(chapter)
    all_orders = chart.all_orders(chapter)
    boss_path = {o.get('target') for o in all_orders
                 if chart.is_boss_castle(o.get('target'))}
    while True:
        grow = {o.get('source') for o in all_orders
                if o.get('target') in boss_path and o.get('source') not in boss_path}
        if not grow:
            break
        boss_path |= grow
    if mechanical:
        # No chart chain: the boss assault waits for every other castle.
        boss_path = {c for c in castles if not chart.is_boss_castle(c)} | {chart.boss_castle(chapter)}
    boss_gate = {after[1] for order in all_orders
                 if order.get('target') in boss_path
                 for after in (order.get('after'),)
                 if isinstance(after, (list, tuple)) and len(after) == 2
                 and after[0] == 'captured' and after[1] in lost}
    if mechanical:
        # No chart chain to gate on: the home castle first, then nearest an owned castle.
        lost.sort(key=lambda c: (c != home,
                                 min((_distance(chapter, o, c) for o in owned), default=0), c))
    else:
        lost.sort(key=lambda c: (c != home, c not in boss_gate))  # home first, then the boss gate
    targets = [c for c in (*lost, *chart_orders, *castles)
               if c in castles and c not in owned and not chart.is_boss_castle(c)]
    targets = list(dict.fromkeys(targets))
    if mechanical:
        # Owner 2026-11-02 (スピードランチャート廃止): lost castles first (home,
        # then the rest), then the others nearest an owned castle first.
        rest = sorted((c for c in targets if c not in lost),
                      key=lambda c: (min((_distance(chapter, o, c) for o in owned), default=0), c))
        targets = [*(c for c in targets if c in lost), *rest]
    if chart.corridor(chapter):
        # A single-road chapter (owner 2026-11-09, chapter 9): only the next
        # castle on the road can be attacked; the others are unreachable
        # (impassable terrain) or would be marched at across owned castles.
        # A lost castle in the middle of the road is the next one again.
        road_next = chart.corridor_next(chapter, owned)
        targets = [c for c in targets if c == road_next]
    # Owner 2026-11-02 (ボス城へ余剰兵力を連続投入): while the boss chain is
    # held and the boss unbeaten, send every spare general at it in successive
    # waves — the charted single sortie is not the only assault. The in-flight
    # guard below exempts this target (owner 2026-11-04) so a wave can follow
    # the previous one without waiting out SORTIE_BUSY_TICKS.
    boss = chart.boss_castle(chapter)
    blocking = {c for c in boss_path - {boss} if c not in owned and c != home}
    chain_held = not blocking
    # Owner 2026-11-04: a castle whose retake keeps failing to launch must
    # not hold the boss waves forever (g1133 17:48-18:50, クロチェット).
    retakes_stalled = (not chain_held) and _retakes_stalled(mem, blocking)
    boss_open = bool(boss in castles and boss_path and (chain_held or retakes_stalled)
                     and not (mem.get('boss_victories') or {}))
    if boss_open:
        targets = [*targets, boss]
    # An interim sortie that has already gone out (launched/en route) must not be
    # re-issued while it is still in flight: otherwise the same target would be
    # attacked twice and the candidate list would never advance. Only recent
    # sortie receipts count; a launched order or a lost sortie never observed
    # to arrive must not hold its target forever (launched/launched_orders are
    # never pruned).
    now = int(mem.get('tick') or 0)
    inflight = {sortie['target'] for step, sortie in _sorties(mem).items()
                if isinstance(step, str) and step.startswith(chart_adjust.INTERIM_PREFIX)
                and isinstance(sortie, dict)
                and sortie.get('status') in ('en_route', 'launched_unconfirmed', 'launched', 'pending')
                and sortie.get('target') is not None and sortie.get('tick') is not None
                and 0 <= now - int(sortie['tick']) < SORTIE_BUSY_TICKS}
    # Owner 2026-11-04: the boss is the one target that keeps taking waves
    # while a sortie to it is still in flight (the 2026-11-02 design above).
    # Every other target keeps the one-expedition rule.
    targets = [c for c in targets if c not in inflight or _boss_wave_open(mem, c)]
    # One recent expedition per target. Unobserved old arrivals stop reserving
    # after SORTIE_BUSY_TICKS; they must not empty every castle in the meantime.
    targets = [c for c in targets if c not in heading or _boss_wave_open(mem, c)]
    sorties = {'retake': [], 'attack': []}
    # #3266: a concrete general chosen for one target is already unavailable
    # to later targets in the same plan; otherwise every retake names the first spare.
    planned_busy = set()
    planned_probe_sources = set()
    def _boss_wave_pick(mem, owned, busy, allow_sole, prefer_not=(),
                        allow_probe=True):
        """(source, general) of the next sendable spare for the boss.

        Owner 2026-11-05: strength does not decide the boss fight (half-ripe
        eggs make a weak general and a strong one remove about the same HP),
        so the pick spreads waves over castles that have not sent one yet
        instead of spending the strongest stack first. prefer_not holds the
        sources this plan already used.

        Within those equal sources the shortest march to the boss goes first.
        The old tie-break was the castle's name, which put アルマムーン (428
        cells) before the far closer オケアナ (203), so g1133 2026-11-06 only
        ever entered the boss castle from オケアナ while the 306-1015-cell
        marches were intercepted and camped on the road.
        """
        chapter = mem.get('chapter') or 0
        home = chart.home_castle(chapter)
        cells = chart.castles(chapter)
        boss_xy = next((xy for name, xy in cells.items() if chart.is_boss_castle(name)), None)
        garrison = _world(mem).get('garrison') or {}
        unavailable = set(busy) | set(_world(mem).get('general_location_unknown') or ()) | {NAME}
        if _hero_egg_broken(mem):
            unavailable |= {NAME}
        sole = len(owned) == 1
        need = chart.sender_min(chapter)    # never empty a castle (owner 2026-11-06); 1 on chapter 9
        # The one-general exception for post-conquest boss waves was left
        # behind by #3567. The game refuses the sortie when the castle has
        # only one general (e.g. ポセイドン at ボッサール), so it must not
        # become a planned attack even if every normal castle is owned.
        # docich#3515: a blind/kept boss launch actively asks for one roster
        # read of its source castle. Mint that probe first so the read
        # settles the phantom (still-listed rolls the launch back) instead
        # of waiting for the arrival window. Only on the probe-allowed
        # call: one roster probe per plan, throttled per castle.
        if allow_probe:
            try:
                from ..state.regions import flow as _flow
                probes = _flow(mem).get('launch_probe') or {}
                due = mem.get('roster_read_due') or {}
                now = int(mem.get('tick') or 0)
                for c in sorted(due):
                    if c not in owned or c in prefer_not:
                        continue
                    waiting = any((p.get('source') == c and (p.get('blind') or p.get('kept')))
                                  for p in probes.values())
                    if not waiting:
                        continue
                    asked = (mem.get('arrival_probe_tick') or {}).get(c)
                    if type(asked) is int and 0 <= now - asked < routes.PROBE_INTERVAL:
                        continue
                    return c, MOVE_ANY_GENERAL
            except Exception:
                pass
        choices = []
        for c in owned:
            free = [g for g in garrison.get(c) or () if g not in busy]
            if len(set(free)) < need:
                continue
            spare = _spares(free, {NAME} if _hero_egg_broken(mem) else (), sole)
            for g in spare:
                if g != MOVE_ANY_GENERAL and g not in unavailable:
                    xy = cells.get(c)
                    distance = (round(((xy[0] - boss_xy[0]) ** 2
                                       + (xy[1] - boss_xy[1]) ** 2) ** 0.5)
                                if boss_xy and xy else 0)
                    # The egg is the boss fight's damage (owner 2026-11-05), so
                    # its rank outranks march distance: g1163 2026-11-09 13:36
                    # sent an eggless ヘーゼル at ハードロボ (152) and lost
                    # without a point of damage while egg-carrying generals
                    # waited in the same chapter.
                    # A general measured at 0 HP cannot fight: the boss wave
                    # would end at ally 0 before any egg command (g1163
                    # chapter 7 2026-11-09 15:53-16:02: five waves lost that
                    # way). Rank him last within every other preference.
                    dead = 1 if _current_hp(mem, g) == 0 else 0
                    # A general whose boss wave left the monster's HP unchanged
                    # is ranked behind one that has not been tried there.
                    hurtless = 1 if g in (mem.get('boss_no_damage') or ()) else 0
                    choices.append((c in prefer_not, c == home, dead, hurtless,
                                    _boss_egg_rank(mem, g), distance, c, g))
        if choices:
            choices.sort()
            _, _, _, _, _, _, source, general = choices[0]
            return source, general
        # Owner 2026-11-05 (差分管理): when no castle knows a spare, read one
        # castle's list instead of idling. The sortie menu opens the roster
        # (general_list_scanned) and the deploy step then picks a measured
        # general, so this both refreshes the bookkeeping and sends the wave.
        # The oldest unread list goes first and the same castle is not probed
        # twice in one plan.
        # Probe at most one castle per plan, and only when the bookkeeping
        # knows nobody at all: a castle that is merely unread while other
        # generals are known is not worth a blind sortie.
        if allow_probe and not any(garrison.get(c) for c in owned):
            for c in sorted((c for c in owned
                             if garrison.get(c) is None and c not in prefer_not),
                            key=lambda c: (c == home, c)):
                return c, MOVE_ANY_GENERAL
        return None
    wave_target = None
    for target in targets:
        picked = (_boss_wave_pick(mem, owned, busy | planned_busy, allow_sole)
                  if chart.is_boss_castle(target) else
                  _interim_source(mem, target, chart_orders.get(target), owned,
                                  busy | planned_busy, allow_sole))
        if picked is None:
            continue
        source, general = picked
        roster_probe = False
        if general == MOVE_ANY_GENERAL:
            # An unread source is evidence gathering; one probe per source is enough.
            if source in planned_probe_sources:
                continue
            planned_probe_sources.add(source)
            # docich#3515: a blind/kept boss launch's source is a read-only
            # roster probe: it opens the list and settles the phantom, then
            # backs out without sending. An unread-castle probe still sends.
            try:
                from ..state.regions import flow as _flow
                _probes = _flow(mem).get('launch_probe') or {}
                roster_probe = (source in (mem.get('roster_read_due') or {})
                                and any(p.get('source') == source and (p.get('blind') or p.get('kept'))
                                        for p in _probes.values()))
            except Exception:
                roster_probe = False
        else:
            # Reserve only an actor measured in this source castle. Unread chart
            # actors are alternative future candidates, not simultaneous commitments.
            present = (_world(mem).get('garrison') or {}).get(source) or ()
            if general in present:
                planned_busy.add(general)
        purpose = 'retake' if target in lost else 'attack'
        note = ((f"暫定: {source}の将軍一覧を読んでボス出撃の成立を確認する" if roster_probe
                 else f"暫定: {_general_label(general)}で奪われた{target}を奪還"
                 if purpose == 'retake'
                 else f"暫定: {_general_label(general)}で{target}を攻撃"))
        # Owner (2026-09-28): 切り札は持たせたほうが良い. The chart's cards for
        # this castle, else the retry opener (two イッテツーン). Cards out of
        # stock are dropped at the card list (card_drop), never waited for.
        base = chart_orders.get(target) or card_orders.get(target)
        cards = list(base['cards']) if base and base.get('cards') else list(INTERIM_CARDS)
        if chart.is_boss_castle(target):
            cards = []   # 波: strict kit waits for nobody — carry what is there
        sorties[purpose].append({
            'general': general, 'target': target, 'cards': cards, 'source': source,
            'after': None, 'purpose': purpose, 'note': note,
            **({'source_probe': True} if general == MOVE_ANY_GENERAL else {}),
            **({'roster_probe': True} if roster_probe else {})})
    # Owner 2026-11-05: keep throwing the known spares at the boss inside one
    # plan, from as many castles as possible. Strength does not matter (a
    # half-ripe egg removes about the same HP whoever carries it), so this
    # only stops when no castle can send; each castle keeps its hero guard.
    # Owner 2026-11-05: this runs before the staging/rotation moves, so a
    # castle with ten generals sends seven (hero + two guards stay), not
    # whoever the moves happen to leave.
    boss_wave_orders = []
    if boss in targets:
        wave_busy = busy | planned_busy
        used_sources = {o['source'] for o in sorties['attack']
                        if chart.is_boss_castle(o.get('target'))}
        extra = 0
        while True:
            more = _boss_wave_pick(
                mem, owned, wave_busy, allow_sole, prefer_not=used_sources,
                allow_probe=(extra == 0
                             and not any(o.get('source_probe') for o in sorties['attack'])))
            if more is None:
                break
            source2, general2 = more
            probe = general2 == MOVE_ANY_GENERAL
            roster_probe = False
            if probe:
                try:
                    from ..state.regions import flow as _flow
                    _probes = _flow(mem).get('launch_probe') or {}
                    roster_probe = (source2 in (mem.get('roster_read_due') or {})
                                    and any(p.get('source') == source2 and (p.get('blind') or p.get('kept'))
                                            for p in _probes.values()))
                except Exception:
                    roster_probe = False
            boss_wave_orders.append({
                'general': general2, 'target': boss, 'cards': [], 'source': source2,
                'after': None, 'purpose': 'attack',
                'note': ((f'暫定: {source2}の将軍一覧を読んでボス出撃の成立を確認する' if roster_probe
                          else f'暫定: {source2}の将軍一覧を読んでボスへ送る' if probe
                          else f'暫定: {_general_label(general2)}でボスへ波状攻撃')),
                **({'source_probe': True} if probe else {}),
                **({'roster_probe': True} if roster_probe else {})})
            used_sources.add(source2)
            if not probe:
                wave_busy = wave_busy | {general2}
                planned_busy.add(general2)
            extra += 1
    # #3266: a general already committed to a sortie above is busy for the
    # staging/refill moves below as well — one plan must not book him twice.
    busy = busy | planned_busy
    garrison = _world(mem).get('garrison') or {}
    moves = []
    if routes.is_road(chapter):
        # Stage spare generals along chart legs rather than aiming a distant
        # enemy straight across a cliff. Every stage ends at an owned castle.
        seen_legs = set()
        for target in targets:
            options = []
            for donor in sorted(owned):
                free = [g for g in garrison.get(donor) or () if g not in busy
                        and g not in (_world(mem).get('general_location_unknown') or ())]
                # A staging move out of a castle with fewer than two generals
                # is only ever an arrival check. Wait until the transfer into
                # it has left the busy window: while it is still marching the
                # list cannot show the second general, and the move would be
                # planned against a one-general castle (g1153 2026-11-08
                # 13:30 moved from メガパタ while ヴィーナス was still walking).
                probe = routes.arrival_probe_due(mem, donor, cooldown=True)
                stage_min = chart.sender_min(chapter)
                if len(free) < stage_min and not probe:
                    continue
                if len(free) >= stage_min and not [g for g in _spares(free) if g != NAME]:
                    continue
                route = routes.path(mem, donor, target, owned)
                if route and len(route) > 2 and route[1] not in heading:
                    options.append((len(route), donor, route, free, probe))
            if not options:
                continue
            _, donor, route, free, probe = min(options, key=lambda v: (v[0], v[1]))
            leg = (donor, route[1])
            if leg in seen_legs:
                continue
            seen_legs.add(leg)
            general = (MOVE_ANY_GENERAL if len(free) < chart.sender_min(chapter)
                       else (_defense_move_pick(mem, donor, route[1],
                                                _spares(free)) or _spares(free)[0]))
            moves.append({'general': general, 'source': donor, 'target': route[1],
                          'cards': [], 'after': None, 'purpose': 'move',
                          'note': f'暫定: {target}への中継として{route[1]}へ移動',
                          **({'source_probe': True} if general == MOVE_ANY_GENERAL else {})})
    # A castle that was sent a move after its list was last read is not known
    # to be empty: past the busy window it was picked again and again (#3037).
    # Owner 2026-11-05: while the boss is open the spare generals are the
    # next boss waves, not a garrison for an empty rear castle (armies were
    # seen marching to ユージア/トウトウ/バビラリー while the boss was the only
    # target). Normal chapters keep the fill.
    # Chapter 9 (owner 2026-11-09): rear castles may stand empty behind the
    # front, so nothing is pulled off the road to fill them.
    empty = [] if boss_open or chart.guard_free(chapter) else [
        c for c in sorted(owned, key=lambda c: _distance(chapter, home, c))
        if garrison.get(c) == [] and c not in heading
        and not routes.unread_dispatches(mem, c)]
    for target in empty:
        # A castle whose list was never read may hold several generals: the
        # bot reads lists only when it sorties, so g407 knew one castle while
        # the stream showed crowded ones. Opening its list for the move is
        # the reading; with fewer than two there the move is cancelled
        # (deploy_step), which records the list and drops the donor.
        known = [c for c in owned if c != target
                 and routes.allowed(mem, c, target)
                 and len(spare := [g for g in garrison.get(c) or () if g not in busy]) >= 2
                 and [g for g in _spares(spare) if g != NAME]]
        unread = [c for c in owned if c != target and garrison.get(c) is None
                  and routes.allowed(mem, c, target)]
        donors = (sorted(known, key=lambda c: _distance(chapter, c, target))
                  + sorted(unread, key=lambda c: _distance(chapter, c, target)))
        if not donors:
            continue
        spare = [g for g in garrison.get(donors[0]) or () if g not in busy]
        spare = _spares(spare) if len(spare) >= 2 else []
        general = _defense_move_pick(mem, donors[0], target, spare) or MOVE_ANY_GENERAL
        moves.append({
            'general': general, 'target': target, 'cards': [], 'source': donors[0],
            'after': None, 'purpose': 'move',
            'note': f"暫定: {_general_label(general)}を{donors[0]}から空の{target}へ移動"})
    # A pending 四季王 site order with no companion needs an egg pair at one
    # castle: move a spare general to join a lone egg-holder (or the hero's
    # castle) so one can sortie while an egg-holder stays. g1075 held every
    # site order pending for an hour while a lone egg-holder guarded each
    # castle (owner 2026-10-30: 城に戻って出撃できる人員を探す).
    pending_sites = [o for o in chart.orders(chapter)
                     if o['target'] in chart.boss_sites(4)
                     and (mem.get('orders') or {}).get(o['step']) in (None, 'pending')]
    if pending_sites and all(_boss_companion_source(o, mem) is None for o in pending_sites):
        unavailable = busy | set(_world(mem).get('general_location_unknown') or ())
        present = {c: [g for g in garrison.get(c) or () if g not in unavailable]
                   for c in owned}
        paired = False
        for site in sorted(pending_sites, key=lambda o: o['step']):
            if paired:
                break
            for sink in sorted(present, key=lambda c: _distance(chapter, c, site['target'])):
                if paired or sink in heading or not present.get(sink):
                    continue
                egg_sink = sink != home and any(_holds_egg(mem, g) for g in present[sink])
                home_sink = (sink == home and NAME in present[sink]
                             and len(present[sink]) - 1 >= chart_adjust.HERO_CASTLE_GUARD)
                if not (egg_sink or home_sink):
                    continue
                for donor in sorted(present, key=lambda c: _distance(chapter, c, sink)):
                    if donor == sink or len(present[donor]) < 2:
                        continue
                    free = [g for g in present[donor] if g != NAME
                            and (home_sink or _holds_egg(mem, g))]
                    if not free:
                        continue
                    general = free[0]
                    moves.append({
                        'general': general, 'target': sink, 'cards': [], 'source': donor,
                        'after': None, 'purpose': 'move',
                        'note': f"暫定: {general}を{donor}から{sink}へ移動しボス出撃の同伴者を揃える"})
                    paired = True
                    break
    # Post-conquest boss wait: reinforce weak priority castles with the
    # strongest sendable spare instead of whoever the list happens to hold
    # (#2985: the home castle waited out a boss sortie with its weakest
    # general while stronger ones sat at border castles). Targets already
    # covered by an ordinary move above are not duplicated.
    if _post_conquest_wait(mem):
        moved = {o['target'] for o in moves}
        moves.extend(o for o in _defense_rebalance(mem, owned, busy, heading)
                     if o['target'] not in moved)
    # Owner 2026-11-04: 人数に余裕があるときは一つの城へ波状攻撃.
    # Generate the first attempt for every target (and the moves that staff
    # empty castles) before spending any surplus, so a wave never steals the
    # general another retake/move needs. The first non-boss target gets the
    # remaining measured spares, each source still keeping one defender.
    wave_orders = []
    wave_committed = set()
    wave_target = next((o['target'] for o in (*sorties['retake'], *sorties['attack'])
                        if not chart.is_boss_castle(o['target'])), None)
    if wave_target is not None:
        committed = set()
        for order in (*sorties['retake'], *sorties['attack'], *moves):
            general = order.get('general')
            if general and general != MOVE_ANY_GENERAL:
                committed.add(general)
        wave_committed = committed
        wave_busy = busy | planned_busy | committed
        purpose = 'retake' if wave_target in lost else 'attack'
        base = chart_orders.get(wave_target) or card_orders.get(wave_target)
        cards = list(base['cards']) if base and base.get('cards') else list(INTERIM_CARDS)
        extra = 0
        while extra < INTERIM_WAVE_LIMIT:
            more = _wave_pick(mem, wave_target, owned, wave_busy, allow_sole)
            if more is None:
                break
            source2, general2 = more
            committed.add(general2)
            wave_busy = wave_busy | {general2}
            wave_orders.append({
                'general': general2, 'target': wave_target, 'cards': cards,
                'source': source2, 'after': None, 'purpose': purpose,
                'note': (f"暫定: {general2}で{wave_target}へ波状攻撃" if purpose == 'attack'
                         else f"暫定: {general2}で奪われた{wave_target}へ波状奪還")})
            extra += 1
    # Owner 2026-11-04: 人数の少ない城は陥落しやすいので、敵城に近い城ほど
    # 厚くし、遠い城からは積極的に将軍を回す（遠い城は1人か0人でよい）。
    # 攻撃・奪還・空城の移動・波状を先に確保した余剰だけを使い、候補の
    # 後ろに置く。
    committed = {o['general'] for o in (*moves, *sorties['retake'], *sorties['attack'])}
    rotation = _garrison_rotation(
        mem, owned, busy | planned_busy | wave_committed
        | (committed - {MOVE_ANY_GENERAL}), heading,
        taken={o['target'] for o in (*sorties['retake'], *sorties['attack'], *moves)})
    # An owned castle recorded empty loses itself to the next enemy stack, and
    # the attack generator books every spare before the rotation can run (g1163
    # chapter 8 2026-11-09: しらかわ sat at 0 defenders while attacks kept the
    # front slot). Drop the lowest-priority attack of this cycle and send its
    # general there instead: nothing is duplicated, retakes keep their booked
    # generals (owner 2026-11-05 #3266), and a boss-only endgame is exempt
    # (owner 2026-11-05: ボスだけが残った局面での空城移動は廃止).
    empty_targets = [c for c in sorted(owned)
                     if isinstance((_world(mem).get('garrison') or {}).get(c), list)
                     and not (_world(mem).get('garrison') or {}).get(c)
                     and c not in heading
                     # Someone is already on the way: the arrival receipt is
                     # that castle's own list, not another dispatch.
                     and not routes.unread_dispatches(mem, c)]
    normal_left = [c for c in castles if c != boss and c not in _owned(mem)]
    staffing = []
    if empty_targets and normal_left:
        covered = {o.get('target') for o in (*moves, *rotation)}
        for castle in empty_targets:
            if castle in covered or not sorties['attack']:
                continue
            donor = sorties['attack'][-1]
            general = donor.get('general')
            source = donor.get('source')
            if not isinstance(general, str) or general == MOVE_ANY_GENERAL:
                continue
            sorties['attack'].pop()
            staffing.append({'general': general, 'target': castle, 'cards': [],
                             'source': source, 'after': None, 'purpose': 'move',
                             'garrison_rotate': True,
                             'note': f'暫定: 空の{castle}へ{_general_label(general)}を{source}から移動'})
            covered.add(castle)
    # Every candidate must be startable from the recorded garrisons, so a
    # chart general named in another castle never reaches the castle menu.
    situation = _adjust_situation(mem)
    out = {}
    known_retakes = [o for o in sorties['retake'] if not o.get('source_probe')]
    unread_retakes = [o for o in sorties['retake'] if o.get('source_probe')]
    # Owner 2026-11-02 (余剰兵力をボスへ連続投入): boss waves ride right after
    # the front candidate so the boss is hit in parallel rather than waiting
    # behind the whole retake/move queue — listing attacks last starved it.
    waves = [o for o in sorties['attack'] if chart.is_boss_castle(o.get('target'))]
    attacks = [o for o in sorties['attack'] if not chart.is_boss_castle(o.get('target'))]
    lead = [*known_retakes, *moves, *unread_retakes]
    # A stalled retake no longer gates the boss, so the boss wave leads: the
    # failing retake would otherwise take the front slot every cycle.
    front = [] if retakes_stalled else lead[:1]
    for order in (*front, *staffing, *waves, *boss_wave_orders,
                  *(lead if retakes_stalled else lead[1:]),
                  *attacks, *rotation, *wave_orders):
        # A generic source-list probe at a measured one-general castle is
        # not a feasible sortie. Only an arrival or a new joiner expected at
        # the hero's castle justifies opening that singleton, and even then
        # the order is read-only: the game refuses to dispatch the last
        # defender, so a departure from that castle would contradict the
        # leave-one rule (owner 2026-11-08: 将軍が一人しかいない城からの
        # 出撃計画がたつ).
        # docich#3515: a roster probe for a blind launch must open even a
        # one-general castle: that singleton is exactly the phantom's source.
        # It reads and settles without sending.
        present = (_world(mem).get('garrison') or {}).get(order['source'])
        if (isinstance(present, (list, tuple)) and len(set(present)) == 1
                and not chart.guard_free(chapter)       # chapter 9: the last general may go
                and order.get('roster_probe') is not True):
            # A new joiner is unconfirmed until the castle's live list is
            # opened. Since #3578's confirmed-source policy names the generic
            # probe actor (しょうぐん), test for that evidence-gathering probe,
            # not a guessed recruit name. Never allow a named singleton sortie.
            fresh_joiner = (order['source'] == _arrival_castle(mem, owned)
                            and bool(_unplaced_castle_generals(mem))
                            and order.get('source_probe') is True
                            and order['general'] == MOVE_ANY_GENERAL)
            arrival_probe = (order.get('source_probe') is True
                             and routes.arrival_probe_due(mem, order['source'],
                                                          cooldown=True))
            if not (fresh_joiner or arrival_probe):
                continue
            # The list may now hold the arrival, but the recorded garrison is
            # still one general: open it read-only and replan from the receipt.
            order['roster_probe'] = True
            order['note'] = (f"暫定: {order['source']}の将軍一覧を読んで"
                             "増援の到着を確認する")
        if (routes.is_road(chapter) and not chart.is_boss_castle(order.get('target'))
                and not routes.order_allowed(mem, order, order['source'])):
            continue        # a road chapter's ``_ready`` would reject it on every tick
        if chart_adjust.startable(order, situation) is not None:
            continue
        out[f"{order['purpose']}_{len(out) + 1}"] = order
    return out


DEFENSE_WAIT_MAX_CYCLES = 2     # absolute cap on monthly evaluations before a break


def _sole_starved(mem):
    """True when no owned castle can spare a general and some castle holds one.

    Only the two-generals-stay rule blocks here: the castle data and the
    targets exist, and every owned castle has at most one free general.
    """
    chapter = mem.get('chapter') or 0
    castles = chart.castles(chapter)
    owned = _owned(mem) & set(castles)
    if not owned or not [c for c in castles if c not in owned and not chart.is_boss_castle(c)]:
        return False
    busy, _ = _en_route(mem)
    busy = busy | set(_world(mem).get('general_location_unknown') or ())
    garrison = _world(mem).get('garrison') or {}
    free = {c: {g for g in garrison.get(c) or () if g not in busy} for c in owned}
    return all(len(free[c]) <= 1 for c in owned) and any(len(free[c]) == 1 for c in owned)


def _joiner_prospects(mem):
    """Generals who may join a castle soon: unplaced at home, or marching."""
    return bool(_unplaced_castle_generals(mem)) or bool(_en_route(mem)[0])


def _defense_gate(mem):
    """'hold', 'break' or None for a sole-general starvation.

    None: the normal path runs (candidates exist, or the starvation is not the
    two-generals rule). 'hold': wait for a second general with no model request
    and no per-tick record. 'break': the monthly fallback lets one general
    leave its castle. Records are written on entry, on each monthly evaluation
    and on release only.
    """
    # A break whose source was lost afterwards is never repeated from there.
    sources = mem.get('defense_break_sources') or {}
    for source in [c for c in sources if c in (mem.get('lost') or ())]:
        blocked = list(mem.get('defense_break_blocked') or ())
        mem['defense_break_blocked'] = sorted({*blocked, source})
        _record(mem, 'defense_break_lost', chart_step=None, strategy_variant='chart_defense_break',
                observed_metric={'castle': source, 'target': sources[source]},
                expected_metric={'castle_kept': True},
                reason='1将軍出撃の出撃元が落ちたため、同じ城からの守備崩し出撃を禁止する')
        sources.pop(source)
    if interim_candidates(mem):
        if mem.pop('defense_wait', None):
            _record(mem, 'chart_defense_wait_released', chart_step=None,
                    strategy_variant='chart_defense_wait',
                    reason='2人目の将軍が使えるようになったため参入待ちを解除し通常運行に戻す')
        return None
    if not _sole_starved(mem):
        return None
    month = mem.get('month')
    owned = sorted(_owned(mem))
    garrison = _world(mem).get('garrison') or {}
    observed = {'owned': owned, 'garrison': {c: list(garrison.get(c) or ()) for c in owned},
                'month': month, 'prospects': _joiner_prospects(mem)}
    wait = mem.get('defense_wait')
    if not wait:
        wait = mem['defense_wait'] = {'since': month, 'eval_month': month, 'cycles': 0}
        _record(mem, 'chart_defense_wait', chart_step=None, strategy_variant='chart_defense_wait',
                observed_metric=observed, expected_metric={'generals_per_source': 2},
                reason='出撃元に将軍2人以上を残す制約で候補0件。2人目の参入を待つ（LLM要求なし）')
    elif month != wait['eval_month']:
        wait['eval_month'] = month
        wait['cycles'] += 1
        _record(mem, 'chart_defense_wait_eval', chart_step=None, strategy_variant='chart_defense_wait',
                observed_metric={**observed, 'cycles': wait['cycles']},
                reason='月次評価: 2人目の将軍がまだ使えない')
    cycles, prospects = wait['cycles'], observed['prospects']
    if cycles >= DEFENSE_WAIT_MAX_CYCLES or (cycles >= 1 and not prospects):
        return 'break'
    return 'hold'


def _mechanical_interim(mem, state, rid, digest, allow_sole=False):
    """Start the next deterministic sortie while no adjusted plan is pending.

    No model answers this: the first candidate from ``interim_candidates``
    goes out once the previous interim order is no longer waiting to start.
    An adjusted chart from the worker still replaces it when it is adopted.
    """
    interim = state.get('interim_order')
    status = mem.get('orders') or {}
    busy = (interim and status.get(interim['step']) in (None, 'pending')
            and _source(interim, mem) in _owned(mem)
            and routes.order_allowed(mem, interim, _source(interim, mem))
            # ``next_order`` never starts an order whose source list is closed
            # (read empty, no probe due); waiting on it held every other
            # candidate (g1075 2026-10-29 11:38-11:47, リグア retake).
            and _source_list_open(interim, mem))
    if busy:
        return
    candidates = interim_candidates(mem, allow_sole)
    if not candidates:
        # A hold nobody can act on used to be silent (g530: sortie
        # stopped with zero evidence); record the starvation once per
        # request revision so diagnostics can see it.
        if state.get('interim_hold_digest') != digest:
            state['interim_hold_digest'] = digest
            _record(mem, 'chart_interim_hold', chart_step=None,
                    strategy_variant='chart_adjust_pending',
                    request_id=rid, choice=None, confidence=None,
                    jev_status='no_candidates',
                    deviation_reason='interim_no_candidates',
                    reason='奪還・攻撃・移動のいずれも作れないため暫定出撃を持てず調整チャートを待つ')
            from . import goals
            for goal in goals.pending(mem, {'retake', 'move'}):
                goals.note_attempt(mem, goal['kind'], 'unavailable', target=goal.get('target'))
        return
    label, order = next(iter(candidates.items()))
    state['interim_count'] = state.get('interim_count', 0) + 1
    step = f"{chart_adjust.INTERIM_PREFIX}{rid[:6]}:{state['interim_count']}"
    state['interim_order'] = {**order, 'step': step}
    _record(mem, 'chart_interim_order', chart_step=step, strategy_variant='chart_interim_mechanical',
            request_id=rid, choice=label, confidence=None, general=order['general'],
            source=order['source'], target=order['target'], purpose=order.get('purpose'),
            deviation_reason='mechanical_chart',
            reason='LLM応答を待たず決定的候補の先頭から暫定出撃')


def _suppress_recent_failures(mem, orders):
    """Do not re-issue, at once, an order that just failed with that content.

    A rebuilt plan carries a new execution id for the same (source>target:
    purpose) step, so it looked new and was retried immediately; g1133
    2026-11-05 retried the boss order 96 times in ten minutes while no castle
    menu would open. The content stays failed until PLAN_STALE_TICKS passes.
    """
    now = int(mem.get('tick') or 0)
    recent = mem.get('failed_local_step') or {}
    if not recent:
        return
    status = mem.setdefault('orders', {})
    for order in orders:
        at = recent.get(order.get('step'))
        if type(at) is not int or now - at >= PLAN_STALE_TICKS:
            continue
        status[order['step']] = 'failed'
        _tally(mem, 'retry_suppressed')


NONBOSS_STEP_REARM_LIMIT = 3     # failed non-boss attempts re-armed per step per month


def _release_stale_failures(mem, orders, rid):
    """A failed non-boss step the rebuilt plan offers again is retried once the
    suppression window has passed.

    ``next_order`` skips a 'failed' step forever, and a rebuilt mechanical plan
    keeps the same execution id while the request id stays the same. Nothing
    ever cleared that status, so a single failed attempt removed the order —
    often the only route to the last enemy castle — for the rest of the
    situation. g1153 2026-11-08 (chapter 3, ドンダム the only enemy castle):
    the relay move that staffs イーピヨル failed once; イーピヨル then kept a
    single general, the only measured leg to ドンダム (イーピヨル→ドンダム)
    could never launch, and months advanced with no sortie at all.

    The retry waits out ``PLAN_STALE_TICKS`` (the window
    ``_suppress_recent_failures`` already uses) and is capped per month and
    step, so a genuinely impossible order cannot drain the army.
    """
    now = int(mem.get('tick') or 0)
    recent = mem.get('failed_local_step') or {}
    month = mem.get('month')
    status = mem.setdefault('orders', {})
    book = mem.setdefault('plan_step_rearm', {})
    for order in orders:
        if chart.is_boss_castle(order.get('target')):
            continue          # the boss keeps its own bounded re-arm below
        step = chart_adjust.execution_step(rid, order['step'])
        if status.get(step) != 'failed':
            continue
        at = recent.get(order['step'])
        if type(at) is int and 0 <= now - at < PLAN_STALE_TICKS:
            continue          # just failed: the suppression window spaces it
        entry = book.get(step)
        if not isinstance(entry, dict) or entry.get('month') != month:
            entry = {'month': month, 'count': 0}
        if entry['count'] >= NONBOSS_STEP_REARM_LIMIT:
            continue
        entry['count'] += 1
        book[step] = entry
        status[step] = 'pending'
        # The failed attempt's actor/kit/source choices are not receipts for
        # this one: a stale ``general_override`` (e.g. the ココット that was
        # recorded elsewhere) must not steer the retry. Sortie records stay —
        # an overdue move is still the only arrival evidence for its target.
        for key in RELEASE_KEYS:
            (mem.get(key) or {}).pop(step, None)
        # ``launched_orders`` lives in the world region, not at the top level.
        (_world(mem).get('launched_orders') or {}).pop(step, None)
        _record(mem, 'plan_step_rearmed', chart_step=step, general=order.get('general'),
                target=order.get('target'),
                observed_metric={'count': entry['count'], 'month': month},
                reason='計画に残った失敗指示が再発射されず、出撃できないまま月が進むため、月内の上限付きで未発射に戻す')
    if len(book) > 32:
        for key in list(book)[:-32]:
            book.pop(key, None)
    return book


def _mechanical_chart(mem, state, rid, digest):
    """Construct and adopt a full chart mechanically (no LLM answer)."""
    if _plan_pending(mem):
        return
    allow_sole = False   # no castle is ever emptied (owner 2026-11-06)
    stored = mem.get('chart_plan') or {}
    if stored.get('request_id') == rid and stored.get('source') == 'mechanical':
        # Even within the same month, rebuild an exhausted mechanical chart
        # against the current garrison and in-flight sorties (keep defenders).
        mem['chart_plan'] = None
    # A previously adopted LLM chart with no startable orders must also be
    # replaced by a new full mechanical plan, not just one interim sortie.
    # Keep it until a valid replacement exists; the fallback can still act
    # when candidate generation fails.
    # The same situation rebuilds the same candidate list on every call, and
    # re-adopting it recorded the same plan over and over (g1133 2026-11-05:
    # 487 identical chart_adjust_applied records). Keep the stored plan when
    # the rebuilt content is identical: nothing about it changed. The stored
    # orders carry their execution id, so the comparison uses each order's
    # ``local_step`` — the execution id is derived from the request and never
    # equals the local id the rebuild produces (#3729).
    previous_local = [o.get('local_step') for o in stored.get('orders') or ()]
    candidates = interim_candidates(mem, allow_sole=allow_sole)
    if not candidates:
        _mechanical_interim(mem, state, rid, digest, allow_sole=allow_sole)
        return
    chapter = mem.get('chapter') or 0
    orders = []
    used = set()
    for label, order in candidates.items():
        orders.append({
            'step': _mechanical_step(order, used, mem),
            'general': order['general'],
            'source': order['source'],
            'target': order['target'],
            'cards': list(order.get('cards') or ()),
            'after': order.get('after'),
            'purpose': order.get('purpose'),
            'note': order.get('note') or '',
            **({'source_probe': True} if order.get('source_probe') is True else {}),
            **({'roster_probe': True} if order.get('roster_probe') is True else {}),
        })
    _suppress_recent_failures(mem, orders)
    # Failed steps the plan still offers are retried after their bounded
    # windows. This runs even when the rebuilt plan is identical: that is the
    # stuck case, and the early return below would otherwise skip both re-arms
    # and leave the one failing order (often the only route left) dead for as
    # long as the situation lasts.
    _release_stale_failures(mem, orders, rid)
    _rearm_failed_boss_steps(mem, orders, rid)
    # A kept plan still needs its stale 'launched' steps released: the early
    # return below must not make an in-flight marker permanent (#3729).
    _release_stale_steps(mem, [chart_adjust.execution_step(rid, o['step']) for o in orders])
    if (stored.get('source') == 'mechanical' and previous_local
            and None not in previous_local
            and [o['step'] for o in orders] == previous_local):
        mem['chart_plan'] = stored
        mem.setdefault('chart_plan_tick', int(mem.get('tick') or 0))
        state.pop('interim_order', None)
        return
    doc = {'schema': chart_adjust.SCHEMA, 'chapter': chapter, 'request_id': rid,
           'source': 'mechanical', 'orders': orders, 'purchases': None,
           'recruitment': None}
    try:
        validated = chart_adjust.validate(doc)
    except ValueError:
        _mechanical_interim(mem, state, rid, digest, allow_sole=allow_sole)
        return
    validated = dict(validated, request_digest=digest)
    _adopt_plan(mem, validated, rid)


def _mechanical_step(order, used, mem=None):
    """Local step id named by what the order does, not by its list position.

    g1097 2026-11-03: the plan is rebuilt whenever gold, month or an order
    state changes, under the same request id while the captures are unchanged
    (and again hours later when the situation recurs). Positional ids M1..Mn
    then named different orders each time: M3 (クロチェット retake) inherited
    'launched' from an earlier M3 (a move to アイアイエー) and never ran, M1
    (カルーン) stayed 'launched' for 6.5 hours, so only the one order whose id
    was unused went out at a time. The id must identify the order's content.
    """
    key = '%s>%s:%s' % (order.get('source'), order.get('target'), order.get('purpose') or '')
    if order.get('roster_probe') is True:
        # A read-only roster probe verifies an arrival and then finishes
        # 'done'; the sortie it verifies must not share that execution id or
        # it would inherit the finished status and never start. The dispatch
        # tick separates one arrival check from the next, so a later transfer
        # into the same castle is probed again.
        key += ':probe'
        if mem is not None:
            ticks = [s.get('tick') for _, s in routes.unread_dispatches(mem, order.get('source'))
                     if isinstance(s, dict)]
            key += ':' + str(max((t for t in ticks if type(t) is int), default=0))
    if (mem is not None and chart.is_boss_castle(order.get('target'))
            and _boss_wave_in_flight(mem, order.get('target')) >= 1):
        # 章末のボスは行軍中でも次の隊を出す（owner 2026-11-04）。同じ出撃元と
        # 目的のままだと、1隊目の行軍中に作った次の隊が同じstep名になり、
        # launchedのまま再発行されない。2隊目以降だけ将軍まで含めて別の指示に
        # する（1隊目は従来どおり位置に依存しないidを保つ）。
        key += ':' + str(order.get('general') or '')
    step = 'M' + hashlib.sha1(key.encode()).hexdigest()[:6]
    while step in used:
        step = 'M' + hashlib.sha1((step + key).encode()).hexdigest()[:6]
    used.add(step)
    return step


RELEASE_KEYS = ('general_override', 'source_override', 'card_override', 'rare_card_kit',
                'strong_card_kit', 'dropper_card_kit', 'order_context', 'sortie_general',
                'sortie_actor_miss', 'launched_orders', 'target_cancel')


def _release_stale_steps(mem, steps):
    """An order the plan offers again is not in flight: forget its old 'launched'.

    The candidates exclude every target with a recent unit en route, so a step
    marked launched whose sortie is not marching any more (arrived, overdue,
    never recorded) describes an earlier attempt. Keeping it made the order
    unstartable for as long as the situation id stayed the same.

    The wait is the same one the overdue path uses (_arrival_limit). It was
    BOSS_ARRIVAL_LIMIT (800) here while a chapter-6 boss march is allowed
    MARCH_ARRIVAL_CAP (1600), so every plan rebuild released a boss march at
    20 minutes and sent a second general after an army still walking (g1133
    2026-11-05: plan_step_released for ポセイドン's boss march at age 872,
    then ブレッツェル was sent on the same step).
    """
    from .orders import (_arrival_limit, _drop_spent_sortie_attempt,
                         _queue_overdue_location_recheck)
    now = int(mem.get('tick') or 0)
    status = mem.setdefault('orders', {})
    for step in steps:
        if status.get(step) != 'launched':
            continue
        sortie = _sorties(mem).get(step) or {}
        if (sortie.get('status') in ('en_route', 'launched_unconfirmed')
                and type(sortie.get('tick')) is int
                and 0 <= now - sortie['tick'] < _arrival_limit(mem, sortie)):
            continue
        for key in RELEASE_KEYS:
            (mem.get(key) or {}).pop(step, None)
        _drop_spent_sortie_attempt(mem, step)
        # Owner 2026-11-05: a released attempt must not leave its march behind.
        # グレナデン stayed launched_unconfirmed for 5971 observations while
        # every plan kept the step, so the expiry's "planned" guard never let
        # it go and his name stayed out of every wave.
        if isinstance(sortie, dict) and sortie.get('status') in (
                'en_route', 'launched_unconfirmed', 'overdue'):
            # A released chart step does not prove that the old actor
            # returned to its source. Recheck the actor's actual location at
            # the next monthly named-status pass instead of adding a phantom
            # garrison member (the newly pending step can choose a real spare).
            _queue_overdue_location_recheck(mem, sortie)
            _sorties(mem).pop(step, None)
            general, source = sortie.get('general'), sortie.get('source')
            _record(mem, 'sortie_record_expired', chart_step=step, general=general,
                    observed_metric={'general': general, 'source': source,
                                     'age': now - sortie['tick']
                                     if type(sortie.get('tick')) is int else None},
                    reason='解放した指示の行軍記録を破棄し、将軍の所在地を月次確認で再取得する')
        status[step] = 'pending'
        _record(mem, 'plan_step_released', chart_step=step,
                observed_metric={'sortie_status': sortie.get('status'), 'general': sortie.get('general'),
                                 'target': sortie.get('target'),
                                 'age': (now - sortie['tick']) if type(sortie.get('tick')) is int else None},
                reason='計画が再び出した指示に、行軍中でない過去の「出撃済み」が残っていたため未発射に戻す')


def _rearm_failed_boss_steps(mem, orders, rid):
    """Re-arm a failed boss step so the chapter end keeps taking sorties.

    g1133 2026-11-04: the departure marker was never observed after アテナ's
    boss sortie (16:18) and the retry hit LAUNCH_MISS_LIMIT at 16:22, so the
    mechanical step stayed 'failed'. The plan is rebuilt every observation
    under the same request id and offers that step again; ``next_order``
    skips a failed step, so no boss sortie was planned for 40+ minutes while
    ten castles sat owned and the army waited at home.

    Only the boss is re-armed, and only within a per-month budget and
    cooldown: a genuinely unreachable target must not drain the army forever.
    """
    status = mem.setdefault('orders', {})
    now = int(mem.get('tick') or 0)
    month = mem.get('month')
    rearmed = mem.get('boss_step_rearm')
    if not isinstance(rearmed, dict):
        rearmed = mem['boss_step_rearm'] = {}
    for order in orders:
        if not chart.is_boss_castle(order.get('target')):
            continue
        step = chart_adjust.execution_step(rid, order['step'])
        if status.get(step) != 'failed':
            continue
        entry = rearmed.get(step)
        if not isinstance(entry, dict) or entry.get('month') != month:
            entry = {'month': month, 'count': 0, 'tick': now - BOSS_STEP_REARM_COOLDOWN}
        if entry['count'] >= BOSS_STEP_REARM_LIMIT:
            continue
        if now - entry['tick'] < BOSS_STEP_REARM_COOLDOWN:
            continue
        entry['count'] += 1
        entry['tick'] = now
        rearmed[step] = entry
        status[step] = 'pending'
        _record(mem, 'plan_step_rearmed', chart_step=step, general=order.get('general'),
                target=order.get('target'),
                observed_metric={'count': entry['count'], 'month': month},
                reason='ボス指示が発射未確認の上限で失敗のまま再計画され続け、出撃が止まるため、月内の上限付きで未発射に戻す')
    if len(rearmed) > 32:
        for key in list(rearmed)[:-32]:
            rearmed.pop(key, None)
    return rearmed


def _adjust_situation(mem):
    """Where the generals are, for the adjusted-chart request.

    g421 (e7df88f1): the LLM chart sent F2/F3/F5 with generals that were not
    in their source castles (it was never told who stood where) and each
    order failed at the castle menu. The request now carries the recorded
    garrisons, marching generals and lost castles so the worker can prompt
    with them and drop orders that cannot start.
    """
    now = int(mem.get('tick') or 0)
    # Same bound as _en_route: an unread arrival stops counting as marching.
    marching = sorted({(s['general'], s.get('target')) for s in _sorties(mem).values()
                       if s.get('status') in ('en_route', 'launched_unconfirmed') and s.get('general')
                       and s.get('tick') is not None and now - int(s['tick']) < SORTIE_BUSY_TICKS},
                      key=str)
    # An unread castle may be stored as None; the situation digest still has
    # to serialise (owner 2026-11-05: probing unread lists must not crash it).
    return {'garrison': {castle: sorted(names or ())
                         for castle, names in sorted((_world(mem).get('garrison') or {}).items())},
            'owned': sorted(_owned(mem)),
            'lost': sorted(mem.get('lost') or []), 'home_lost': bool(mem.get('home_lost')),
            'en_route': [{'general': g, 'target': t} for g, t in marching],
            'card_stock': dict(sorted((mem.get('card_stock') or {}).items()))}


def _supersede_unstarted_steps(mem, new_orders):
    """Retire the previous plan's steps that never started.

    A replaced plan must not leave its unstarted steps standing: they would
    keep naming a general who already sortied under the new plan (g1163
    chapter 8 2026-11-09: A:046e819d:M5f4f03 stayed pending while
    A:8e54a3af:M5f4f03 was already launched). Only a step with no sortie of its
    own is superseded; anything launched keeps its own lifecycle.
    """
    previous = (mem.get('chart_plan') or {}).get('orders') or []
    statuses = mem.setdefault('orders', {})
    sorties_now = _sorties(mem)
    keep = {o.get('step') for o in new_orders if isinstance(o, dict)}
    superseded = []
    for old_order in previous:
        step = old_order.get('step') if isinstance(old_order, dict) else None
        if not isinstance(step, str) or step in keep:
            continue
        if statuses.get(step) in (None, 'pending') and step not in sorties_now:
            statuses[step] = 'superseded'
            superseded.append(step)
    if superseded:
        _record(mem, 'plan_steps_superseded', observed_metric={'steps': superseded[:8]},
                expected_metric={'pending_generals': 0},
                reason='計画が差し替わったため、未発射の旧手順を破棄して同じ将軍の二重計画を防ぐ')
    return superseded


def _adopt_plan(mem, doc, rid):
    """Adopt a validated adjusted chart as the plan, with per-generation step ids."""
    orders = [{**o, 'step': chart_adjust.execution_step(rid, o['step']), 'local_step': o['step'],
               'cards': list(o['cards']), 'after': list(o['after']) if o['after'] else None}
              for o in doc['orders']]
    garrison = _world(mem).get('garrison') or {}
    for order in orders:
        present = garrison.get(order['source'])
        if (order.get('source_probe') is True and order.get('roster_probe') is not True
                and isinstance(present, (list, tuple)) and len(set(present)) == 1):
            # An adopted plan may only inspect a measured one-general castle:
            # the game refuses to dispatch its last defender, so the order is
            # a read-only roster probe even if the plan called it a sortie.
            order['roster_probe'] = True
    purchases = doc.get('purchases')
    _supersede_unstarted_steps(mem, orders)
    mem['chart_plan'] = {
        'request_id': rid, 'source': doc.get('source'), 'orders': orders,
        'recruitment': doc.get('recruitment'),
        'purchases': ({**purchases, 'month': list(purchases['month']),
                       'cards': [list(c) for c in purchases['cards']]} if purchases else None)}
    mem['chart_plan_tick'] = int(mem.get('tick') or 0)
    mem.pop('plan_stale_signature', None)
    # Several boss waves march at once, so each new boss order takes the next
    # probe candidate in rotation instead of every wave aiming at the same
    # tile (v404). Chapters without a probe list are untouched.
    probes = chart.BOSS_CELL_PROBES.get(mem.get('chapter') or 0) or ()
    if probes:
        assigned = mem.setdefault('boss_step_probe', {})
        rotation = mem.setdefault('boss_probe_rotation', {})
        # The policy round-trips through JSON between observations, so dict
        # keys reload as strings. g1133 2026-11-05: chapter arrived as int 6,
        # ``rotation.get(6)`` missed the stored "6" every time, and all sixteen
        # boss steps kept probe index 0 — every wave aimed at the same cell.
        chapter = str(mem.get('chapter') or 0)
        for order in orders:
            if not chart.is_boss_castle(order['target']) or order['step'] in assigned:
                continue
            assigned[order['step']] = int(rotation.get(chapter) or 0) % len(probes)
            rotation[chapter] = (int(rotation.get(chapter) or 0) + 1) % len(probes)
        for stale in list(assigned)[:-40]:   # del on the temporary list is a no-op
            assigned.pop(stale, None)

    state = mem.setdefault('chart_adjust', {})
    state.pop('interim_order', None)
    mem['variant'] = 'chart_adjusted'
    # Digest for commentary: grounded order content, not the full plan blob.
    order_digest = [{'general': o['general'], 'source': o['source'],
                     'target': o['target'], 'cards': list(o['cards']),
                     'roster_probe': bool(o.get('roster_probe'))} for o in orders]
    # A mechanical plan is rebuilt whenever its orders stop being runnable;
    # the same rebuild was recorded every observation (g1133 2026-11-05: 17
    # identical records in 30 minutes). Record only a different plan.
    # JSON round trip: the policy is reloaded between observations, so the
    # stored value must be lists; a tuple reloads as a list and never compares
    # equal (measured 2026-11-05: 24 records in 11 minutes, five of them the
    # very same plan).
    signature = [rid, [o['step'] for o in orders]]
    if mem.get('chart_adjust_signature') != signature:
        mem['chart_adjust_signature'] = signature
        _record(mem, 'chart_adjust_applied', chart_step=None, strategy_variant='chart_adjusted',
                request_id=rid, source=doc.get('source'), steps=[o['step'] for o in orders],
                local_steps=[o['local_step'] for o in orders], order_digest=order_digest,
                reason='調整チャートを受信したため独自の指示列で出撃を再開')


PLAN_STALE_TICKS = 600      # ~15 min of observations with no order started


def _drop_stale_plan(mem):
    """Rebuild a plan whose every order stayed pending for a long time.

    The rebuild used to wait for the situation digest to change (gold, month,
    order status). A plan whose orders cannot start changes none of them, so it
    sat pending forever: g1133 2026-11-05 had two staging moves pending for
    over an hour, and the plan was never rebuilt with the purpose field that
    would have let them run (v405). Dropping it here makes the next
    ``_mechanical_chart`` rebuild from the current situation.

    The drop is counted, not recorded: the planner is also run on copies (the
    chart worker) where no state signature survives, so a decision record
    repeated on every call (35 identical records a minute under v411).
    """
    plan = mem.get('chart_plan') or {}
    adopted = mem.get('chart_plan_tick')
    if not plan.get('orders') or type(adopted) is not int:
        return
    now = int(mem.get('tick') or 0)
    if now - adopted < PLAN_STALE_TICKS:
        return
    status = mem.get('orders') or {}
    if any(status.get(o.get('step')) not in (None, 'pending') for o in plan['orders']):
        return
    mem['chart_plan'] = None
    mem.pop('chart_plan_tick', None)
    mem.pop('plan_stale_signature', None)
    _tally(mem, 'plan_stale_rebuild')


def _off_chart_mechanical(mem, state, rid, gate):
    """Speedrun chart abolished: take castles mechanically, ask no model.

    The same stand-in the adjusted-chart path uses while an answer is pending,
    but nothing is requested and no model answer is ever adopted, so the next
    sortie is always the first deterministic candidate.
    """
    status = mem.get('orders') or {}
    fields = {'request_id': rid, 'off_chart_reason': 'mechanical',
              'captured': sorted(mem.get('captured') or []), 'orders': dict(status),
              'gold': mem.get('gold'), 'month': mem.get('month'), **_adjust_situation(mem)}
    digest = chart_adjust.request_digest(fields)
    _drop_stale_plan(mem)
    if state.get('request_id') != rid:
        state.clear()
        state['request_id'] = rid
    if state.get('request_digest') != digest:
        state['request_digest'] = digest
        if (mem.get('chart_plan') or {}).get('source') == 'mechanical':
            mem['chart_plan'] = None     # built for an older situation
    if _plan_pending(mem):
        return
    if gate == 'break':
        _mechanical_interim(mem, state, rid, digest, allow_sole=False)
    else:
        _mechanical_chart(mem, state, rid, digest)


def _off_chart(mem):
    """No order is ready: request an adjusted chart, or adopt one that answered.

    The base chart is never mutated. A request is recorded once per distinct
    situation (chapter, captures, order states); an adjusted chart is adopted
    only when it answers exactly that request, as a complete order list that
    replaces the previous plan. Until then the previous plan keeps waiting, or,
    with no plan waiting, the first deterministic interim candidate runs. No
    model answer is required for progress.
    """
    # Before any path below can drop the stored plan, keep its failed retake
    # steps: a castle whose retake keeps failing stops gating the boss wave.
    _failed_retakes(mem, record=True)
    # A deliberate recovery wait is not an exhausted chart. Do not ask the
    # planner to replace the boss order or send an interim sortie while the
    # next normal month advances. Other ready orders were considered first.
    waiting = next((o for o in _orders(mem) if _boss_egg_depleted(o, mem)
                    and not _hero_source_alternative(o, mem)
                    and _ready(o, {**mem, 'egg_uses': {}})
                    and (mem.get('orders') or {}).get(o['step']) in (None, 'pending')), None)
    if waiting:
        key = (waiting['step'], mem.get('month'), (mem.get('egg_uses') or {}).get(NAME))
        if mem.get('boss_egg_wait') != list(key):
            mem['boss_egg_wait'] = list(key)
            _record(mem, 'boss_egg_recovery_wait', chart_step=waiting['step'],
                    observed_metric={'general': NAME, 'egg_uses': key[2], 'month': key[1]},
                    expected_metric={'egg_uses': 4},
                    reason='第1話ボス出撃前に主人公の卵が消耗しているため、通常の月次回復を待つ')
        return
    mem.pop('boss_egg_wait', None)
    gate = _defense_gate(mem)
    if gate == 'hold' and not _plan_pending(mem):
        return  # wait for a second general: no model request, no per-tick churn
    state = mem.setdefault('chart_adjust', {})
    rid = chart_adjust.request_id(mem)
    if not chart.speedrun_enabled(mem.get('chapter') or 0):
        return _off_chart_mechanical(mem, state, rid, gate)
    orders = [o for o in _orders(mem) if not o['step'].startswith(chart_adjust.INTERIM_PREFIX)]
    status = mem.get('orders') or {}
    blocked = [{'step': o['step'], 'after': list(o['after'] or ())} for o in orders
               if status.get(o['step']) in (None, 'pending')]
    reason = ('chart_unavailable' if not orders
              else 'orders_locked' if blocked else 'orders_exhausted')
    fields = {'request_id': rid, 'off_chart_reason': reason, 'blocked': blocked,
              'captured': sorted(mem.get('captured') or []), 'orders': dict(status),
              'gold': mem.get('gold'), 'month': mem.get('month'), **_adjust_situation(mem)}
    digest = chart_adjust.request_digest(fields)
    new_request = state.get('request_id') != rid
    prior_digest = state.get('request_digest')
    if new_request:
        state.clear()
        state['request_id'] = rid
        state['request_digest'] = digest
        _record(mem, 'chart_adjust_request', chart_step=None, strategy_variant='chart_adjust_pending',
                **fields,
                reason='チャート外: 出撃可能な指示がないため調整チャートを非同期に要求し、決定的な暫定出撃を続ける')
    revision_changed = (not new_request) and prior_digest is not None and prior_digest != digest
    if (not new_request) and prior_digest != digest:
        # Same situation, newer payload (stock, gold, lost castles): republish
        # so the worker answers the current revision, then keep going so the
        # interim fallback still progresses this observation.
        state['request_digest'] = digest
        # A mechanical chart was built for the previous revision; discard it so
        # the normal chart / a fresh mechanical chart can take over.
        if (mem.get('chart_plan') or {}).get('source') == 'mechanical':
            mem['chart_plan'] = None
        _record(mem, 'chart_adjust_request', chart_step=None, strategy_variant='chart_adjust_pending',
                **fields,
                reason='チャート外: 状況が更新されたため同じ要求を最新の在庫・配置で再要求')
    doc = mem.get('_adjusted')
    plan = mem.get('chart_plan') or {}
    doc_digest = doc.get('request_digest') if doc else None
    if (doc and doc.get('request_id') == rid and doc.get('chapter') == mem.get('chapter')
            and (plan.get('request_id') != rid or (mem.get('chart_plan') or {}).get('source') == 'mechanical')
            and ((doc_digest == state.get('request_digest'))
                 # A digestless answer predates the revision field (old worker
                 # during a hot-load): accepted until a revision actually
                 # changed under it, then only a bound answer counts.
                 or (doc_digest is None and not revision_changed))):
        _adopt_plan(mem, doc, rid)
        return
    # An adopted answer may be locked or exhausted without changing its
    # request id (g514: retake Nakyume waited for Nakyume to be captured).
    # Adoption identity prevents replay above; only a live plan may block
    # the interim candidates from being evaluated again.
    if _plan_pending(mem):
        return
    # Never overwrite an adopted (non-mechanical) chart with a mechanical one:
    # the mechanical chart is only a stand-in while the LLM answer is pending.
    if (mem.get('chart_plan') or {}).get('source') not in (None, 'mechanical'):
        # Fall back to the single interim sortie for exhausted adopted plans.
        _mechanical_interim(mem, state, rid, digest, allow_sole=(gate == 'break'))
        return
    if gate == 'break':
        _mechanical_interim(mem, state, rid, digest, allow_sole=False)
    else:
        _mechanical_chart(mem, state, rid, digest)
