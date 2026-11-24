"""戦略層: off-chart replanning (interim orders and adjusted charts).

Builds interim candidates when the chart has no ready order, starts the first
of them without waiting for a model, adopts a validated adjusted chart when
one answers, and records every deviation.
"""
from __future__ import annotations
from ..state import egg_memory
from ..state.regions import world as _world, sorties as _sorties, plan as _plan_region

import hashlib

from ... import (hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust,
                 hanjuku_reference as reference)
from ..decision_log import _record, _tally
from ..os_layer.world import NAME, SORTIE_BUSY_TICKS, _en_route, _owned
from . import routes
from .orders import (RETIRED_ORDER_STATES,
                               _arrival_castle, _boss_companion_source, _boss_egg_depleted,
                               _whitehouse_open,
                               drop_retired_step_context,
                               _distance, _hero_egg_broken, _holds_egg,
                               _hero_source_alternative, _orders, _plan_pending, _ready, _reserved_targets,
                               _site_king_down, _source,
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


def _too_hurt_to_attack(mem, general):
    """A measured general this close to 0 HP loses before any card lands.

    g1190 2026-11-17 20:4x (chapter 11): three of nine battles opened at ally
    HP 4/13/5 against 87/45/87 — the general was effectively dead on arrival
    and the sortie cost a month. Unread HP is not evidence either way.

    An unread maximum HP cannot justify a low absolute HP: the 1-HP floor
    let 7 HP attackers through (docich#4668).
    """
    hp = _current_hp(mem, general)
    if type(hp) is not int:
        return False
    row = ((mem.get('general_statuses') or {}).get('statuses') or {}).get(general)
    maximum = row.get('max_hp') if isinstance(row, dict) else None
    if type(maximum) is not int or maximum <= 0:
        # docich#4668: an unmeasured max HP left the floor at 1 HP, so
        # 7-HP generals were sent and died on arrival (ch1 3-11:
        # クランベリー 7 vs 37, コリアンダー 0 vs 24). Use an absolute
        # floor until a full status panel names the general's maximum.
        return hp < ATTACK_MIN_HP_ABS
    return hp < max(1, int(maximum * ATTACK_MIN_HP_RATIO))


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
    gate = chart.gateway(chapter)
    if gate and target in gate['back']:
        # The map's halves are joined only through the hub (owner 2026-11-17,
        # chapter 11): a back-half sortie from a front castle marches out and
        # back. Only the hub or an already-owned back castle may send it.
        owned = {c for c in owned if c == gate['hub'] or c in gate['back']}
    busy = busy | set(_world(mem).get('general_location_unknown') or ())
    unavailable_for_attack = {NAME} if _hero_egg_broken(mem) else set()
    sole = len(owned) == 1          # owner: a single castle may leave the hero alone
    # Owner 2026-11-15: with the whole road taken, only a march from the last
    # corridor castle reaches the moving boss; any other source marches into
    # the cliffs and camps (g1190 15-x: カラ→ボス re-issued every ~85 steps
    # without ever entering). The gather moves walk the army to イデ！ first.
    road = chart.corridor(chapter) or ()
    boss_gather = bool(chart.is_boss_castle(target) and road
                       and chart.corridor_next(chapter, owned) is None)
    if boss_gather:
        owned = {c for c in owned if c == road[-1]}
    free = {c: [g for g in _known_residents(mem, garrison, c) or () if g not in busy]
            for c in owned}
    if routes.is_road(chapter):
        owned = {c for c in owned if routes.allowed(mem, c, target)}
    # Emptying a castle is how the undefended ジョンリギ fell (g401), so a
    # castle that keeps somebody behind is required. Owner 2026-11-02: an
    # own castle lying on the march stops the army inside (アルマムーン →
    # クロチェット enters オトラント; every measured アルマムーン-launch
    # stalled while the only arrival was オトラント-launched 5-Z2), so a
    # source whose path crosses another own castle ranks behind a clear one.
    need = chart.sender_min(chapter)    # never empty a castle (owner 2026-11-06); 1 on chapter 9
    # The game refuses a sortie from a castle that keeps only one general. A
    # boss wave planned from the one-general last castle therefore never
    # launches and only starves the gather moves (g1190 16-x: イデ！ stayed at
    # one general while the boss attack order was re-issued every month).
    if boss_gather:
        need = 2
    excluded = set()
    # Owner 2026-11-03 (g1097): generals moved into a castle recorded empty
    # stood there unseen while アルマムーン kept sending the sorties. The
    # castle's own list, opened once by a probe, is the arrival receipt; the
    # read then lets them sortie from it like any staffed castle.
    arrivals = sorted((c for c in owned if routes.arrival_probe_due(mem, c, cooldown=True)),
                      key=lambda c: (_distance(chapter, c, target), c))
    if arrivals:
        return arrivals[0], MOVE_ANY_GENERAL
    def _hub_ready(c):
        # The shared hub must keep two defenders while it sends: g1190
        # 2026-11-17 lost タタール four times right after a sortie from it, and
        # each loss cut the far half off until the next retake. A back-half
        # target is exempt: the hub is the only castle that can send it.
        if not gate or c != gate['hub'] or target in gate['back']:
            return True
        return len(set(free[c])) >= GATEWAY_HUB_GUARD

    staffed = sorted((c for c in owned if len(set(free[c])) >= need and _hub_ready(c)
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
        if not spares:
            return None
        if _too_hurt_to_attack(mem, spares[0]):
            from ..decision_log import _record
            _record(mem, 'interim_attack_deferred_low_hp', chart_step=None, general=spares[0],
                    observed_metric={'source': best, 'target': target,
                                     'hp': _current_hp(mem, spares[0])},
                    reason='計測HPが最大の2割未満の将軍しか残っていないため、この目標への出撃を今月は見送る')
            return None
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
    if (unplaced and not boss_gather and arrival in owned
            and _source_location_recheck(mem, arrival) is None
            and not chart_adjust.hero_guard_short([*(garrison.get(arrival) or ()), *unplaced],
                                                  unplaced[0], busy, sole_castle=sole)):
        # A boss wave needs two measured generals at the last castle; an
        # unplaced joiner is not one (g1190 16-x: ブレッツェル re-issued from
        # the one-general イデ！ every ~85 steps and never launched).
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


def _hub_reinforce_pending(mem):
    """A promoted hub reinforcement is already queued or marching.

    Mirrors ``_hero_hub_move_pending``: the stored plan (or the live interim
    order) still carries an order flagged ``hub_first`` whose step has not
    reached a terminal status. A launched or failed step frees the month's
    promotion for the next thin-hub plan.
    """
    status = _plan_region(mem).get('orders') or {}
    for order in (_plan_region(mem).get('chart_plan') or {}).get('orders') or ():
        if (isinstance(order, dict) and order.get('hub_first') is True
                and status.get(order.get('step')) not in ('launched', 'failed')):
            return True
    interim = (mem.get('chart_adjust') or {}).get('interim_order')
    return isinstance(interim, dict) and interim.get('hub_first') is True


def _note_hub_reinforce(mem):
    """The month's promoted hub reinforcement is now planned (docich#4547).

    Only the issuance paths (an adopted/interim order, or a stored row that
    regained the flag) mark this; ``interim_candidates`` is also evaluated
    just to test whether any candidate exists, and that read must not spend
    the month's promotion before a real plan can carry it.
    """
    book = mem.get('hub_reinforce_eval')
    if not isinstance(book, dict):
        book = mem['hub_reinforce_eval'] = {}
    if book.get('month') != mem.get('month'):
        book.clear()
        book.update({'month': mem.get('month'), 'planned': False})
    book['planned'] = True


def _hub_reinforce_available(mem):
    """True while this month's promoted reinforcement has not been spent."""
    book = mem.get('hub_reinforce_eval')
    return (not isinstance(book, dict)
            or book.get('month') != mem.get('month')
            or not book.get('planned'))


MOVE_ANY_GENERAL = chart_adjust.PROBE_GENERAL   # move from an unread list: the spare is picked there
# Extra sorties to the first target when spares allow. A castle with several
# defenders needs more than four (g1163 2026-11-10 08:00-10:00: しまばら kept
# producing successor defenders while ながはま held eight spares).
INTERIM_WAVE_LIMIT = 6
GATEWAY_HUB_GUARD = 3        # the shared hub keeps two defenders while it sends
STAGE_LOST_HUB_PER_PLAN = 4  # spares moved forward while the hub is the enemy's
HUB_GATHER_PER_PLAN = 2      # spare generals moved into a chapter's hub per plan
HUB_GATHER_ALL_PER_PLAN = 8  # owner 2026-11-18: gather the whole army at the hub
HUB_REINFORCE_MAX = 2        # a hub holding this few is undermanned (#4495/#4547)
ATTACK_MIN_HP_RATIO = 0.20   # measured HP/max below this is a wasted attack sortie
ATTACK_MIN_HP_ABS = 20       # docich#4668: floor when max HP is unread
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
    plan = _plan_region(mem).get('chart_plan') or {}
    status = _plan_region(mem).get('orders') or {}
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
    status = _plan_region(mem).get('orders') or {}
    for order in (_plan_region(mem).get('chart_plan') or {}).get('orders') or ():
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


# Owner 2026-11-11 (chapter 9): 早く倒さないと敵城のレベルがどんどん上がる
# ステージなので、ひとりずつ出撃していたら間に合わない. The one-expedition rule
# kept exactly one general marching at the road's next castle: every launch was
# followed by ``chart_interim_hold`` until that battle ended (g1163 09:40-10:14,
# seven launches, seven holds). The road's next castle now keeps taking waves
# until this many sorties to it are in flight. v660 capped the wave at 3;
# v671 (owner 2026-11-11 17:55, 「攻撃中の先発隊は最大6人」) supersedes it, so
# the wave shares the planner's ``SQUAD`` budget (#3944: separate caps 6 vs 3
# minted retakes the gate then held every observation).
def _corridor_wave_open(mem, target) -> bool:
    """A single-road chapter keeps several sorties marching at its next castle."""
    from . import corridor
    chapter = mem.get('chapter') or 0
    if not chart.corridor(chapter):
        return False
    owned = _owned(mem) & set(chart.castles(chapter))
    if target != chart.corridor_next(chapter, owned):
        return False
    return corridor._attackers_marching(mem, target) < corridor.SQUAD


def _partial_residents(mem, castle):
    """Names a full page proved stand in ``castle``, as a lower bound.

    A full page of the general list is never the roster, so deploy's
    ``_observe_garrison`` keeps its names in ``garrison_partial`` and leaves
    ``garrison`` untouched (docich#3996). The names on the page are still
    confirmed residents: candidate selection may name them instead of
    treating the castle as unread. A name a complete read has since placed
    in another castle is stale evidence and is dropped; nobody else's
    absence is inferred, and no completeness stamp is implied (docich#3997).
    """
    world = _world(mem)
    partial = (world.get('garrison_partial') or {}).get(castle)
    if not isinstance(partial, list) or not partial:
        return None
    elsewhere = {name for other, members in (world.get('garrison') or {}).items()
                 if other != castle and isinstance(members, list)
                 for name in members}
    names = [g for g in partial if g not in elsewhere]
    return names or None


def _known_residents(mem, garrison, castle):
    """Residents candidate selection may name for ``castle``.

    The measured roster when a complete read is recorded; otherwise the
    full page's lower bound (``_partial_residents``); None only when the
    castle's list was never read at all.
    """
    names = garrison.get(castle)
    if names is not None:
        return names
    return _partial_residents(mem, castle)


# Owner 2026-11-11: 三人づつとかじゃなくて全員移動するようにしないとだし、主人公も
# 移動しないと新しく雇った将軍は後方からしか出れない. Chapter 9 sorties only
# leave the road castle beside the target and recruits join at the hero's
# castle, so every general behind the front walks one hop toward it each plan,
# the hero included. Superseded v661's four-general front.
def _corridor_forward_moves(mem, owned, garrison, busy, road_next, nearest_first=False):
    """(lead, rest) moves that walk every rear general one hop toward the front.

    The first move keeps the lead slot the staging move always had; the rest
    queue behind the front's attacks. The hero moves right after the first
    general of his castle, while others are still there to be listed with
    him; he is never an attacker (``_spares``/``_wave_pick`` keep excluding him).

    ``nearest_first`` puts the donors closest to the front first (the boss
    gather: two generals at the last castle unlock the first wave, so the
    nearest castle's troops must arrive before the far rear ones).
    """
    chapter = mem.get('chapter') or 0
    unknown = set(_world(mem).get('general_location_unknown') or ())
    lead, rest = [], []
    staged = set()
    donors = sorted(owned, key=(lambda c: (routes_len(mem, c, road_next, owned), c))
                    if nearest_first else
                    (lambda c: (-routes_len(mem, c, road_next, owned), c)))
    for donor in donors:
        route = routes.path(mem, donor, road_next, owned)
        if not route or (len(route) <= 2 and not nearest_first):
            # The front itself, or no road to it. The gather (nearest_first)
            # keeps the adjacent donor: its one hop IS the last castle
            # (g1190 22-x: イソ kept 17 generals while イデ！ stood empty
            # because the front-adjacent move was skipped).
            continue
        names = _known_residents(mem, garrison, donor)
        if names is None:
            # An unread rear list: open it with one probe move, as before.
            (rest if lead else lead).append({
                'general': MOVE_ANY_GENERAL, 'source': donor, 'target': route[1], 'cards': [],
                'after': None, 'purpose': 'move', 'source_probe': True,
                'note': f'暫定: {road_next}への中継として{route[1]}へ移動'})
            continue
        free = [g for g in names if g not in busy and g not in unknown and g not in staged]
        others = _strongest_first(mem, [g for g in free if g != NAME])
        order = others[:1] + ([NAME] if NAME in free else []) + others[1:]
        for general in order:
            staged.add(general)
            label = '主人公' if general == NAME else general
            (rest if lead else lead).append({
                'general': general, 'source': donor, 'target': route[1], 'cards': [],
                'after': None, 'purpose': 'move',
                'note': f'暫定: {road_next}への中継として{route[1]}へ{label}を移動'})
    return lead, rest, staged


def routes_len(mem, donor, target, owned):
    route = routes.path(mem, donor, target, owned)
    return len(route) if route else 0


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
    gate = chart.gateway(chapter)
    if gate and target in gate['back']:
        # Waves to a back-half castle can only start at the hub (owner
        # 2026-11-17, chapter 11): the halves are joined there alone.
        owned = {c for c in owned if c == gate['hub'] or c in gate['back']}
    unavailable = set(_world(mem).get('general_location_unknown') or ())
    need = chart.sender_min(chapter)    # never empty a castle (owner 2026-11-06); 1 on chapter 9
    # Nearest source first: a wave from あるまむーん to しまばら walks ~753 cells
    # (~40 minutes at the measured rate) while まつやま is 336 cells away
    # (g1163 2026-11-10 07:xx: six sorties, three battles). Home stays the
    # tiebreak so an equal-distance choice still prefers it.
    cells = chart.castles(chapter)
    goal = cells.get(target)
    def _reach(castle):
        cell = cells.get(castle)
        if not goal or not cell:
            return 0.0
        return ((cell[0] - goal[0]) ** 2 + (cell[1] - goal[1]) ** 2) ** 0.5
    for source in sorted(owned, key=lambda c: (round(_reach(c), 1), c != home, c)):
        if not routes.allowed(mem, source, target):
            continue
        names = _known_residents(mem, garrison, source)
        if not names:
            continue
        free = [g for g in names if g not in busy and g not in unavailable]
        if len(free) < need:
            continue
        spares = [g for g in _spares(free) if g != NAME]
        if spares:
            # Strongest first: a castle with a successor defender kept beating
            # the wave's weakest spare (g1163 2026-11-10 09:38: しまばら's
            # first defender fell at ally 18, the next one at 85 HP).
            pick = _strongest_first(mem, spares)[0]
            if _too_hurt_to_attack(mem, pick):
                continue
            return source, pick
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
    return egg_memory.egg_rank(mem, general, row)


def _boss_eggless(mem, general):
    """A general whose measured status shows no egg at all.

    The boss fight's damage is the egg's (owner 2026-11-05), so an eggless
    general takes the sortie, meets the boss at ally HP -> 0 and removes
    nothing: ch9 2026-11-17 (local driver, g1190) リースリング, カペリーニ and
    ヘラ, all ``egg: None`` rows, left the boss at HP 500 / 129 unchanged and
    were lost, while the egg carriers シフォン (まねっこ) and エピィ (スロット)
    took 500 -> 129 -> 0. Only a measured row counts (an HP and no egg); an
    unread general stays a candidate. #4415: a stale ``egg_types`` entry alone
    no longer keeps the general — a newer dated observation is required, and
    the same read port serves ``_holds_egg`` and the recovery budget.
    """
    row = ((mem.get('general_statuses') or {}).get('statuses') or {}).get(general)
    return egg_memory.eggless(mem, general, row)


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
            # A road chapter only reaches a castle from the one before it
            # (owner 2026-11-16): a donor two hops back marches into the
            # castle between and is absorbed — タオ→オシロガ over サヌト kept
            # settling as move_overdue. The donor must be a road neighbour.
            if (source == target or len(free[source]) < 2
                    or not routes.allowed(mem, source, target)):
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
    # ``_known_residents`` covers a castle whose full-page read aged out of
    # ``garrison`` but still holds partial-resident evidence — the same pool
    # ``_interim_source`` counts. Treating that row as empty froze the only
    # spare castle out of every donation (docich#4642 follow-up: the home
    # roster read at tick ~31892 vanished by ~32951 and no move was minted).
    free = {c: [g for g in _known_residents(mem, garrison, c) or ()
                if g not in unavailable] for c in plan}
    sole = len(plan) == 1
    claimed = set(taken) | set(heading)
    donated = {}
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
            if source == target:
                continue
            source_distance, _source_want, source_floor = plan[source]
            if source_distance <= distance or not routes.allowed(mem, source, target):
                continue
            # One donor may staff several targets in the same plan (docich#4642:
            # the single-donation rule let one spare castle send at most one
            # general a month, so every frontier castle stayed at one defender
            # and no attack source ever opened). Generals already committed to
            # another target this plan are subtracted before the floor check.
            sent = donated.get(source) or set()
            available = [g for g in free[source] if g not in sent]
            # Even a rear-castle floor of zero cannot override the actual
            # game's minimum two-general requirement for a departure. A
            # castle whose list was never read at all cannot be counted.
            if (_known_residents(mem, garrison, source) is None
                    or len(set(available)) < 2
                    or len(available) - 1 < source_floor):
                continue
            spares = [g for g in _spares(available) if g != NAME]
            if not spares:
                continue
            general = sorted(spares, key=lambda g: _defense_score(mem, g),
                             reverse=True)[0]
            # A castle already above its own target donates first; rear
            # castles may thin down to one, but never dispatch their last.
            surplus = 1 if len(available) > _source_want else 0
            donors.append((surplus, source_distance,
                           -_distance(chapter, source, target), source, general))
        if not donors:
            continue
        _, _, _, source, general = max(donors)
        out.append({'general': general, 'target': target, 'cards': [],
                    'source': source, 'after': None, 'purpose': 'move',
                    'garrison_rotate': True,
                    'note': f'暫定: 敵城に近い{target}を厚くするため{_general_label(general)}を{source}から移動'})
        donated.setdefault(source, set()).add(general)
        claimed.add(target)
    return out


INTERIM_CARDS = ('イッテツーン', 'イッテツーン')

def _strong_stock_kit(mem, stock):
    """docich#4895: spare strong cards for the interim opener, use order first.

    クースカン・ミックミーが潤沢にあるのに暫定攻撃注文が弱い再挑戦用開幕札
    (イッテツーンx2) のまま終盤の敵将軍に押し負けた。実在庫が余裕を持つ強い
    切り札で開幕札を組み替える: ``STRONG_CARDS`` の使用優先順に各1枚ずつ取り、
    残り携行枠はIDの小さい側 (同順の逆) からID合計48未満に収まる限り重ねる。
    クースカンとミックミーが潤沢なら推奨組の クースカン+ミックミーx2 (ID合計47)
    になる。
    未完了のボス注文が必要とする枚数は残し (``boss_kit``、g825)、携行ID合計は
    敵卵の閾値48未満を守る。
    """
    from .boss_cards import boss_kit
    from .kit import CARRY_SLOTS
    kit = boss_kit(mem)
    need = kit[1] if kit else {}
    spare = {name: stock[name] - int(need.get(name) or 0)
             for name in reference.STRONG_CARDS if type(stock.get(name)) is int}
    ids, limit = reference.ALL_CARD_IDS, reference.ENEMY_EGG_CARD_ID_SUM
    cards, total = [], 0
    for name in reference.STRONG_CARDS:
        if (len(cards) < CARRY_SLOTS and spare.get(name, 0) > cards.count(name)
                and total + ids.get(name, limit) < limit):
            cards.append(name)
            total += ids[name]
    for name in reversed(reference.STRONG_CARDS):
        while (len(cards) < CARRY_SLOTS and spare.get(name, 0) > cards.count(name)
               and total + ids.get(name, limit) < limit):
            cards.append(name)
            total += ids[name]
    # The opener is replaced only when every weak copy becomes a strong card.
    return cards if len(cards) >= len(INTERIM_CARDS) else None


def _stock_cards(mem, wanted):
    '''docich#4685 + #4895: carry cards the country actually holds; the
    interim opener upgrades to spare strong cards when they are abundant.

    The planned cards were dropped at the list when out of stock, so an
    attack could march with an empty kit: ch2 2026-11-19 sent 37/38 HP
    generals at アウスパジア (enemy HP 85) twice while ノリウツール 21 and
    バグストーム 25 sat in stock, and both waves retreated. Keep the
    wanted cards that are in stock, else carry the best-stocked ones.

    A ``None`` wanted is the generic interim opener (イッテツーンx2, the same
    kit a lost melee's retry carries): #4895 lost to the late enemy generals
    on it while クースカン・ミックミー sat abundant in stock, so it upgrades
    to ``_strong_stock_kit``. A pinned chart card list never upgrades, even
    when it happens to name the identical イッテツーンx2.
    '''
    generic = wanted is None
    wanted = list(INTERIM_CARDS) if generic else list(wanted)
    stock = mem.get('card_stock') or {}
    if not isinstance(stock, dict) or not stock:
        return wanted         # unread stock is not evidence of an empty one
    if generic:
        strong = _strong_stock_kit(mem, stock)
        if strong is not None:
            return strong
    # One planned copy per measured copy: planning イッテツーンx2 on a stock
    # of 1 stalled the list on the second, never-held copy before the drop.
    held = []
    for card in wanted:
        if type(stock.get(card)) is int and stock[card] > held.count(card):
            held.append(card)
    if held:
        return held
    # Generic fallback must not consume cards reserved for a pending boss kit.
    # The strong opener above already subtracts those copies; apply the same
    # constraint when no wanted card is held and we rank the remaining stock.
    if generic:
        from .boss_cards import boss_kit
        kit = boss_kit(mem)
        reserved = kit[1] if kit else {}
    else:
        reserved = {}
    ranked = sorted(((n, q) for n, q in stock.items()
                     if type(q) is int and q > int(reserved.get(n) or 0)
                     and isinstance(n, str)),
                    key=lambda row: -row[1])
    return [n for n, _ in ranked[:2]]
   # same opener as a lost melee's retry

# Generals the hub's latest complete roster must show before the hero joins
# it: a confirmed staffed hub, so he is never dropped into an unmeasured one.
HERO_HUB_MOVE_MIN = 1


def _hero_hub_move_pending(mem):
    """A stored/interim order already carries the hero's hub move."""
    plan = (_plan_region(mem).get('chart_plan') or {}).get('orders') or ()
    if any(isinstance(o, dict) and o.get('hero_hub_move') is True for o in plan):
        return True
    interim = (mem.get('chart_adjust') or {}).get('interim_order')
    return isinstance(interim, dict) and interim.get('hero_hub_move') is True


def _note_hero_hub_move(mem):
    """The month's hero-hub move is now planned: no second one this month.

    Only the issuance paths (an adopted/interim order, or a stored row that
    regained the flag) mark this; ``interim_candidates`` is also evaluated
    just to test whether any candidate exists, and that read must not spend
    the month's move before a real plan can carry it.
    """
    book = mem.get('hero_hub_move_eval')
    if not isinstance(book, dict):
        book = mem['hero_hub_move_eval'] = {}
    if book.get('month') != mem.get('month'):
        book.clear()
        book.update({'month': mem.get('month'), 'planned': False, 'skipped': []})
    book['planned'] = True
    # The hero's hub move is itself the month's reinforcement: it also spends
    # the promoted-gather slot so a thin hub never gets both (docich#4547).
    _note_hub_reinforce(mem)


def _hero_hub_move(mem, owned, garrison, busy, gate, home):
    """The once-a-month order that carries the hero to the gateway hub.

    Owner 2026-11-18 02:4x: recruits join the hero's castle, so the army
    gathers at the hub only once he stands there. docich#4546 (g1190 62-9):
    タタール was retaken and held six generals while どうし stayed home and
    the held decision left no trace — only gather orders ever started.

    The move fires only on complete rosters: a page (``garrison_partial``)
    proves the names on it, never who else stands there, so a month whose
    evidence is partial holds the order instead of guessing. Every held
    evaluation is recorded once a month as ``hero_hub_move_skipped``:
    ``partial 名簿`` when the complete rosters cannot prove the
    preconditions (the hero at home, a staffed hub), ``計画競合`` when the
    month already planned the move or the hero is marching.
    """
    hub = gate['hub']
    if hub not in owned:
        return None
    book = mem.get('hero_hub_move_eval')
    if not isinstance(book, dict):
        book = mem['hero_hub_move_eval'] = {}
    if book.get('month') != mem.get('month'):
        book.clear()
        book.update({'month': mem.get('month'), 'planned': False, 'skipped': []})
    skipped_list = book.setdefault('skipped', [])
    if not isinstance(skipped_list, list):
        skipped_list = book['skipped'] = []

    def hold(skip_reason, detail, **observed):
        if skip_reason not in skipped_list:
            skipped_list.append(skip_reason)
            _record(mem, 'hero_hub_move_skipped', chart_step=None,
                    skip_reason=skip_reason,
                    observed_metric={'month': mem.get('month'), 'hub': hub, **observed},
                    reason=detail)
        return None

    home_roster = garrison.get(home)
    hub_roster = garrison.get(hub)
    if isinstance(hub_roster, (list, tuple)) and NAME in hub_roster:
        return None            # the latest complete roster already places him at the hub
    if not (isinstance(home_roster, (list, tuple)) and NAME in home_roster):
        # A complete home read that misses him places him elsewhere; a page or
        # an unread list proves nothing, so an unproven hero is held, not fired.
        if (isinstance(home_roster, (list, tuple))
                and NAME not in (_partial_residents(mem, home) or ())
                and NAME not in (_world(mem).get('general_location_unknown') or ())):
            return None        # confirmed elsewhere by a complete roster
        return hold('partial 名簿',
                    '直近の完全な名簿で主人公の本城在城を確認できないため主人公のハブ移動を保留する',
                    castle=home)
    if not isinstance(hub_roster, (list, tuple)) or len(hub_roster) < HERO_HUB_MOVE_MIN:
        return hold('partial 名簿',
                    f'ハブの駐留将軍を直近の完全な名簿で{HERO_HUB_MOVE_MIN}人以上確認できないため'
                    '主人公のハブ移動を保留する',
                    hub_count=len(hub_roster) if isinstance(hub_roster, (list, tuple)) else None)
    if book.get('planned') or _hero_hub_move_pending(mem):
        return hold('計画競合', '当月の主人公ハブ移動が既に計画されているため再計画しない')
    if NAME in busy:
        return hold('計画競合', '主人公が他の指示で行軍中のため主人公のハブ移動を保留する')
    return {'general': NAME, 'target': hub, 'cards': [], 'source': home,
            'after': None, 'purpose': 'move', 'hero_hub_move': True,
            'note': f"暫定: 主人公を{hub}へ移して将軍募集の拠点にする"}


ARMY_FLOOR = 20   # docich#4681: below this the army refills before attacking

def _army_below_floor(mem):
    '''docich#4681: is the country's measured army too thin to attack?

    ch2 famine 2026-11-19: soldiers 2 with gold 0, three early sorties and
    then アルマムーン plus one castle left. An attack spends the army that
    keeps the castles, so below the refill floor (economy.LOW_ARMY, 20) the
    planner offers retakes and moves only.
    '''
    count = mem.get('soldiers_seen')
    if type(count) is not int:
        return False
    return count < ARMY_FLOOR

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
    gateway = chart.gateway(chapter)
    hub = gateway['hub'] if gateway else None
    if mechanical:
        # No chart chain to gate on: the home castle first, then nearest an owned castle.
        lost.sort(key=lambda c: (c != home,
                                 c != hub,
                                 min((_distance(chapter, o, c) for o in owned), default=0), c))
    else:
        # home first, then the boss gate, then the chapter's shared hub: while
        # the hub is the enemy's, the far half is unreachable (g1190
        # 2026-11-17: タタール lost and four more castles followed).
        lost.sort(key=lambda c: (c != home, c not in boss_gate, c != hub))
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
    gate = chart.gateway(chapter)
    if gate and gate['hub'] not in owned:
        # The hub separates two halves of the map (owner 2026-11-17, chapter
        # 11): while it is the enemy's, nothing beyond it can be reached, so a
        # sortie there just walks back. Keep the army on the hub and the front.
        targets = [c for c in targets if c not in gate['back']]
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
    # #4922: once chapter 10's White House is open (all castles held, or its
    # cell measured), a recapture must not stop the boss waves again.
    boss_open = bool(boss in castles and boss_path
                     and (chain_held or retakes_stalled or _whitehouse_open(mem))
                     and not (mem.get('boss_victories') or {}))
    if boss_open:
        # Owner 2026-11-21: そもそもボス攻撃を最優先に. The boss used to sit
        # last in the candidate list, so every retake/staffing order ran first
        # and the boss waves only started once the rest was done. Put the boss
        # first so the wave goes out even while other targets remain.
        targets = [boss, *targets]
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
    # Chapter 9's road castle likewise (owner 2026-11-11), up to
    # SQUAD sorties at once (#3944).
    targets = [c for c in targets if c not in inflight or _boss_wave_open(mem, c)
               or _corridor_wave_open(mem, c)]
    # One recent expedition per target. Unobserved old arrivals stop reserving
    # after SORTIE_BUSY_TICKS; they must not empty every castle in the meantime.
    targets = [c for c in targets if c not in heading or _boss_wave_open(mem, c)
               or _corridor_wave_open(mem, c)]
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
        # Owner 2026-11-15: with the whole road taken, only a march from the
        # last corridor castle reaches the moving boss (the cliffs stop every
        # other source), so the boss waves come from there and the rear
        # generals are gathered by the forward moves below instead.
        road = chart.corridor(chapter) or ()
        if road and chart.corridor_next(chapter, owned) is None:
            owned = [c for c in owned if c == road[-1]]
        boss_xy = next((xy for name, xy in cells.items() if chart.is_boss_castle(name)), None)
        garrison = _world(mem).get('garrison') or {}
        unavailable = set(busy) | set(_world(mem).get('general_location_unknown') or ()) | {NAME}
        if _hero_egg_broken(mem):
            unavailable |= {NAME}
        sole = len(owned) == 1
        # The game refuses a sortie from a one-general castle (e.g.
        # ポセイドン at ボッサール and the one-general last castle), so a boss
        # wave needs two generals at the source.
        need = 2
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
            free = [g for g in _known_residents(mem, garrison, c) or () if g not in busy]
            if len(set(free)) < need:
                continue
            spare = _spares(free, {NAME} if _hero_egg_broken(mem) else (), sole)
            for g in spare:
                if g != MOVE_ANY_GENERAL and g not in unavailable:
                    if _boss_eggless(mem, g):
                        continue          # an eggless wave is a pure loss
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
                    # #4922: chapter 10's White House opens only at the south
                    # gate's red carpet, so a wave from a castle south of its
                    # wall (y >= the gate) marches the open road instead of
                    # camping behind the ring. Other chapters are unaffected.
                    north_of_wall = bool(chapter == 10 and xy
                                         and xy[1] < chart.CH10_BOSS_WAYPOINT[1])
                    choices.append((c in prefer_not, north_of_wall, c == home, dead, hurtless,
                                    _boss_egg_rank(mem, g), distance, c, g))
        if choices:
            choices.sort()
            _, _, _, _, _, _, _, source, general = choices[0]
            return source, general
        # #4869: when transfers have reached a front castle whose recorded
        # garrison is still stale (e.g., 1 known defender at イデ！ while a
        # whole wave arrived from イソ), choices stays empty (need=2) and the
        # unread-only probe below is skipped because garrison[c] is non-empty.
        # Probe any eligible owned castle with arrival_probe_due like
        # _interim_source does, so the arrived generals become visible and launch.
        if allow_probe:
            boss = chart.boss_castle(chapter)
            arrivals = sorted(
                (c for c in owned if c not in prefer_not
                 and routes.arrival_probe_due(mem, c, cooldown=True)),
                key=lambda c: (c == home, _distance(chapter, c, boss), c),
            )
            if arrivals:
                return arrivals[0], MOVE_ANY_GENERAL
        # docich#5077: every measured castle fails ``need`` while an unread
        # castle may hold the pair — g1190 ch1 9-10 held six one-general
        # fronts and a never-read アルマムーン, so no wave could ever be
        # minted and no probe was armed either (roster_read_due and
        # arrival_probe_due both require their own trigger). When at least
        # one roster is known (the all-unread case is handled below), read
        # one unread castle: the sortie menu opens its list and the next
        # plan sends the wave from it if a pair is there. The last probe's
        # start tick throttles re-issue while a read is still walking.
        if allow_probe and any(_known_residents(mem, garrison, c) for c in owned):
            now = int(mem.get('tick') or 0)
            asked = mem.get('arrival_probe_tick') or {}
            unread = sorted(
                (c for c in owned
                 if _known_residents(mem, garrison, c) is None and c not in prefer_not
                 and (type(asked.get(c)) is not int
                      or now - int(asked[c]) >= routes.PROBE_INTERVAL)),
                key=lambda c: (c == home, c))
            if unread:
                return unread[0], MOVE_ANY_GENERAL
        # Owner 2026-11-05 (差分管理): when no castle knows a spare, read one
        # castle's list instead of idling. The sortie menu opens the roster
        # (general_list_scanned) and the deploy step then picks a measured
        # general, so this both refreshes the bookkeeping and sends the wave.
        # The oldest unread list goes first and the same castle is not probed
        # twice in one plan.
        # Probe at most one castle per plan, and only when the bookkeeping
        # knows nobody at all: a castle that is merely unread while other
        # generals are known is not worth a blind sortie.
        if allow_probe and not any(_known_residents(mem, garrison, c) for c in owned):
            for c in sorted((c for c in owned
                             if _known_residents(mem, garrison, c) is None and c not in prefer_not),
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
            # docich#4967: a general picked from a PARTIAL roster
            # (garrison_partial) was not registered in planned_busy here, so
            # every retake in the plan named the same general and the later
            # castles stalled after the first sortie. Use the same resident
            # resolution the actor picker uses.
            present = _known_residents(mem, _world(mem).get('garrison') or {}, source) or ()
            if (general != MOVE_ANY_GENERAL and general != NAME and present
                    and general not in present):
                # docich#4712: the chart names the general that took this castle
                # when the chapter was mapped. By the boss stage that general
                # has often moved or been lost, and the order then waits
                # forever: ch6 20-x held オケアナ→ボス (エシャロット) while
                # オケアナ's list read ビーツ/ポセイドン/ゼウス. Re-pick a
                # measured actor from the same list; the strict boss checks in
                # the deploy flow stay untouched because the plan now names
                # somebody who is really there.
                free = [g for g in present if g not in planned_busy and g != NAME]
                if free:
                    general = _strongest_first(mem, free)[0]
                    present = tuple(present)
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
        cards = _stock_cards(mem, list(base['cards']) if base and base.get('cards') else None)
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
    # A castle that already committed a concrete boss wave must keep its
    # remaining non-hero defender out of optional staging. Read-only source
    # probes do not reserve a general and are deliberately excluded.
    boss_wave_sources = {
        order.get('source')
        for order in (*sorties['attack'], *sorties['retake'], *boss_wave_orders)
        if chart.is_boss_castle(order.get('target'))
        and not order.get('source_probe')
        and isinstance(order.get('source'), str)
    }
    # #3266: a general already committed to a sortie above is busy for the
    # staging/refill moves below as well — one plan must not book him twice.
    busy = busy | planned_busy
    garrison = _world(mem).get('garrison') or {}
    moves = []
    stage_more = []
    road_next = chart.corridor_next(chapter, owned) if chart.corridor(chapter) else None
    if road_next is not None:
        lead_moves, stage_more, staged = _corridor_forward_moves(mem, owned, garrison, busy, road_next)
        moves.extend(lead_moves)
        busy = busy | staged
    elif chart.corridor(chapter):
        # Owner 2026-11-15: with the whole road taken and the moving boss
        # present, every general gathers at the last corridor castle; only a
        # march that starts there reaches the boss (the cliffs stop the rest).
        # Reuse the corridor's one-hop forward moves with the last castle as
        # the front (road_next is None once every road castle is ours).
        road = chart.corridor(chapter) or ()
        gather = road[-1] if road else None
        if gather in owned:
            lead_moves, stage_more, staged = _corridor_forward_moves(
                mem, owned, garrison, busy, gather, nearest_first=True)
            moves.extend(lead_moves)
            busy = busy | staged
    elif routes.is_road(chapter):
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
                spares = [g for g in _spares(free) if g != NAME]
                # Once the boss is open, a source that already sent a
                # concrete boss wave must not spend its last non-hero spare on
                # an optional staging move. Keep one defender at every source.
                if boss_open and donor in boss_wave_sources and len(spares) < 2:
                    continue
                if len(free) < stage_min and not probe:
                    continue
                if len(free) >= stage_min and not spares:
                    continue
                route = routes.path(mem, donor, target, owned)
                if route and len(route) > 2 and route[1] not in heading:
                    # Keep donor-specific spares paired with the option; the
                    # shortest route may not belong to the last donor visited.
                    options.append((len(route), donor, route, free, probe, spares))
            if not options:
                continue
            _, donor, route, free, probe, spares = min(options, key=lambda v: (v[0], v[1]))
            leg = (donor, route[1])
            if leg in seen_legs:
                continue
            seen_legs.add(leg)
            general = (MOVE_ANY_GENERAL if len(free) < chart.sender_min(chapter)
                       else (_defense_move_pick(mem, donor, route[1], spares) or spares[0]))
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
    # Owner 2026-11-17 (chapter 11): once the front is taken the army waits at
    # the hub タタール, because every back-half sortie starts there. Move one
    # spare general per plan from a front castle into the hub so the back-half
    # waves have depth instead of one defender at a time.
    gate = chart.gateway(chapter)
    stage_lost_hub = bool(gate and gate['hub'] not in owned)
    if gate and (gate['hub'] in owned or stage_lost_hub) and (
            stage_lost_hub or any(c not in owned for c in gate['back'])):
        hub = gate['hub']
        if stage_lost_hub:
            # The hub is the enemy's, so the army has nowhere to stage: g1190
            # 2026-11-18 kept 17 generals at home while every front castle held
            # one, and each hub retake marched the whole island. Stage the
            # spares at the own castle nearest the hub and START with that move
            # so it is not queued behind the retake waves.
            # Staging at the home castle would only move the army home: pick
            # the own front castle nearest the hub that is not the home one.
            front_owned = [c for c in owned if c in gate['front'] and c != home]
            if front_owned:
                hub = min(front_owned,
                          key=lambda c: (_distance(chapter, c, gate['hub']), c))
        hub_busy = busy | {g for o in moves for g in (o.get('general'),) if isinstance(g, str)}
        gather = []
        for source in (c for c in owned if c in gate['front'] and c != hub):
            if source in heading:
                continue
            free = [g for g in garrison.get(source) or () if g not in hub_busy]
            # Keep the castle defended: only a castle with a spare may send.
            spares = [g for g in _spares(free) if g != NAME]
            if spares:
                gather.append((source, spares))
        # docich#4547 (1): right after the retake the hub holds a guard or
        # two — the far home stack arrives after the next enemy attack, so
        # the nearest front castles send their spares first (chapter 11 lost
        # タタール again and again before the home march arrived).
        hub_roster = garrison.get(gate['hub'])
        undermanned = (not stage_lost_hub
                       and isinstance(hub_roster, (list, tuple))
                       and 0 < len(set(hub_roster)) <= HUB_REINFORCE_MAX)
        if undermanned:
            gather.sort(key=lambda item: (_distance(chapter, item[0], hub), item[0]))
        else:
            # The deepest stack goes first (g1190 2026-11-17 20:1x: 18 generals sat
            # at the home castle while the hub held one, and the back-half waves
            # could only start at the hub). Two moves per plan keep the month's
            # sortie slots available for the attacks themselves.
            gather.sort(key=lambda item: (-len(item[1]), _distance(chapter, item[0], hub), item[0]))
        # While the hub is lost the army sits at home (19 generals on
        # 2026-11-18) and every retake marches the island; stage more per plan
        # so the front castle can start sending its own sorties sooner. One
        # source may contribute several spares — the home stack is the donor.
        # Owner 2026-11-18 02:4x: the hero moves to the hub so recruiting
        # happens where the army is (new generals join the hub directly).
        # docich#4546 (62-9): the hub was retaken and held six generals while
        # どうし stayed home and only gathers started — the move now waits on
        # the latest complete rosters, fires once a month, and records every
        # hold (``hero_hub_move_skipped``).
        hero_move = None
        if not stage_lost_hub:
            hero_move = _hero_hub_move(mem, owned, garrison, busy, gate, home)
            if hero_move is not None:
                moves.insert(0, hero_move)
        # docich#4547 (2): while the hub is undermanned, one reinforcement
        # move a month is promoted ahead of the front attacks — but only
        # issuance marks the month spent (``_note_hub_reinforce``), never a
        # candidates-only evaluation. The hero's own once-a-month hub move is
        # itself the month's reinforcement, so it does not double up.
        promoted = (undermanned and hero_move is None
                    and _hub_reinforce_available(mem)
                    and not _hub_reinforce_pending(mem))
        # Owner 2026-11-18 02:4x: once the hub is ours, gather the WHOLE army
        # there (recruiting at the hub as well), instead of two spares a plan.
        if stage_lost_hub:
            stage_limit, per_source = STAGE_LOST_HUB_PER_PLAN, STAGE_LOST_HUB_PER_PLAN
        else:
            stage_limit, per_source = HUB_GATHER_ALL_PER_PLAN, HUB_GATHER_ALL_PER_PLAN
        staged = 0
        marked = False
        for source, spares in gather:
            for general in spares[:per_source]:
                if staged >= stage_limit:
                    break
                hub_first = promoted and not marked
                moves.append({'general': general, 'target': hub, 'cards': [], 'source': source,
                              'after': None, 'purpose': 'move',
                              **({'stage_first': True} if stage_lost_hub else {}),
                              **({'hub_first': True} if hub_first else {}),
                              'note': f"暫定: 後半へ備えて{_general_label(general)}を{source}から"
                                      f"中央の{hub}へ集める"})
                marked = marked or hub_first
                staged += 1
            if staged >= stage_limit:
                break
    # A pending 四季王 site order with no companion needs an egg pair at one
    # castle: move a spare general to join a lone egg-holder (or the hero's
    # castle) so one can sortie while an egg-holder stays. g1075 held every
    # site order pending for an hour while a lone egg-holder guarded each
    # castle (owner 2026-10-30: 城に戻って出撃できる人員を探す).
    pending_sites = [o for o in chart.orders(chapter)
                     if o['target'] in chart.boss_sites(4)
                     and (_plan_region(mem).get('orders') or {}).get(o['step']) in (None, 'pending')]
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
        cards = _stock_cards(mem, list(base['cards']) if base and base.get('cards') else None)
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
    # docich#5127: the recall flow registers ``roster_read_due`` in any
    # chapter, but only chapter 9's corridor probe consumed it — and the boss
    # wave only for a blind/kept launch_probe's own source. In every other
    # chapter the due expired unread and the recalled general stayed
    # unaccounted. Mint one read-only roster probe for the least-read due
    # castle: the sortie menu's list read is the arrival receipt. The due is
    # popped by the complete read (deploy._observe_garrison), never by the
    # mint, so a rebuilt plan keeps offering the same step until the roster
    # is actually read.
    # Any order out of the due castle already opens its list, which consumes
    # the due at the read — a second probe for the same source is noise.
    opened = {o.get('source') for o in (*sorties['retake'], *sorties['attack'],
                                      *moves, *staffing, *rotation,
                                      *boss_wave_orders, *wave_orders)}
    due_orders = []
    due_sources = sorted((c for c in routes.roster_read_due(mem)
                          if c in owned and c not in opened),
                         key=lambda c: ((mem.get('route_probe_read_tick') or {}).get(c, 0), c))
    if due_sources:
        source = due_sources[0]
        # The probe never launches: the boss castle label is legal from every
        # chapter's own castle (``order_allowed``), and 'move' keeps the read
        # out of the thin-army attack gate.
        target = chart.boss_castle(chapter)
        if target == source or target not in castles:
            target = next((c for c in castles if c != source), source)
        due_orders.append({
            'general': MOVE_ANY_GENERAL, 'source': source, 'target': target,
            'cards': [], 'after': None, 'purpose': 'move',
            'source_probe': True, 'roster_probe': True,
            'note': f'暫定: 帰還の到着確認として{source}の将軍一覧を読む'})
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
    # Staging and staffing can name the same leg in both directions (g1190
    # 2026-11-18 79-9: move ジャンセニア→タプロベイン and move タプロベイン→ジャンセニア
    # in one plan). They cancel out and spend two sortie slots, so keep the
    # first direction only.
    pairs = set()
    unique_moves = []
    for move_order in moves:
        key = (move_order.get('source'), move_order.get('target'))
        if (key[1], key[0]) in pairs:
            continue
        pairs.add(key)
        unique_moves.append(move_order)
    moves = unique_moves
    lead = [*known_retakes, *moves, *unread_retakes]
    # A lost hub leaves the army at home; the staging move leads so the next
    # month's wave starts from a castle near the hub instead of the home
    # castle (g1190 2026-11-18, #4495).
    stage_first = [o for o in moves if o.get('stage_first')]
    # docich#4547 (2): a hub holding only a guard gets one promoted
    # reinforcement a month, ahead of the front attacks (the two promotion
    # kinds are exclusive: hub_first requires the hub to be ours).
    promoted = stage_first or [o for o in moves if o.get('hub_first')]
    # A stalled retake no longer gates the boss, so the boss wave leads: the
    # failing retake would otherwise take the front slot every cycle.
    if promoted and not retakes_stalled:
        front = promoted[:1]
        tail = [o for o in lead if o is not promoted[0]]
    elif retakes_stalled:
        front, tail = [], lead
    else:
        front, tail = lead[:1], lead[1:]
    for order in (*due_orders, *front, *staffing, *waves, *boss_wave_orders,
                  *tail, *attacks, *stage_more, *rotation, *wave_orders):
        if order.get('purpose') == 'move':
            leg = (order.get('source'), order.get('target'))
            if (leg[1], leg[0]) in pairs:
                # The opposite leg is already planned: the two cancel out.
                continue
            pairs.add(leg)
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
                and order.get('hero_hub_move') is not True   # owner: the hero may leave alone
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
        if (_army_below_floor(mem) and order.get('purpose') == 'attack'
                and not order.get('retake')):
            # docich#4681: an attack with the army under the floor loses the
            # castle it marched from; refill first.
            continue
        if chart_adjust.startable(order, situation) is not None:
            continue
        out[f"{order['purpose']}_{len(out) + 1}"] = order
    # docich#4706: chapter 4's four boss sites unlock only when every other
    # castle is captured, and each boss order must be sent from its own castle
    # (ミヒラギアン→南西, ネクスドリア→南東, フヌイヌム→北東, アルカ→北西).
    # With all non-boss castles taken, those four sources held 2, 3, 1 and 1
    # generals: the one-general castles can send nobody and home cannot march
    # to the sites, so no candidate was ever offered and the chapter waited.
    # Move one spare into each starved boss source while the rest is settled.
    if not out and not _army_below_floor(mem) and _boss_source_staffing_needed(mem, chapter, owned):
        garrison_map = _world(mem).get('garrison') or {}
        boss_sources = []
        for order in chart.all_orders(chapter):
            site = order.get('target')
            src = order.get('source')
            if (chart.is_boss_castle(site) and src in owned
                    and src not in boss_sources):
                boss_sources.append(src)
        settled = all(c in owned for c in castles if not chart.is_boss_castle(c))
        donors = [c for c in sorted(owned, key=lambda c: (-len(set(garrison_map.get(c) or ())), c))
                  if len(set(garrison_map.get(c) or ())) >= 3
                  and not chart.march_blocked(chapter, c, home, owned)]
        for src in boss_sources:
            kept = set(garrison_map.get(src) or ())
            if len(kept) >= 2 or not settled:
                continue
            for donor in donors:
                names = [g for g in (garrison_map.get(donor) or ())
                         if g not in kept and g != NAME]
                if not names:
                    continue
                # docich#4709: _en_route(mem) returns (generals, targets); the
                # two-argument call added in #4706 raised TypeError whenever this
                # branch was reached (no candidate and a settled boss source).
                busy, _ = _en_route(mem)
                free = [g for g in names if g not in busy]
                if not free:
                    continue
                general = _strongest_first(mem, free)[0]
                out[f'move_{len(out) + 1}'] = {
                    'general': general, 'source': donor, 'target': src,
                    'cards': [], 'after': None, 'purpose': 'move',
                    'note': f'暫定: ボス注文の出撃元{src}が1将軍のため{donor}から増援を送る'}
                break
    # docich#4689: every castle holding exactly one general can neither donate
    # nor attack, so a stuck army needs one read of a castle list to see the
    # generals it recruited. ch3 month 11-1..12-3 waited ten months with 31
    # generals country-wide and only four located: next_order null, no
    # candidate ever offered, and the recruits never entered a castle list.
    # One read-only probe from the home castle to the nearest enemy castle
    # opens that list; whatever it shows, the next plan is built from it.
    if not out and not _army_below_floor(mem):
        home = chart.home_castle(chapter)
        garrison_map = _world(mem).get('garrison') or {}
        # Count each known general once. A general still marching is already
        # accounted for; it is not an unlocated recruit that warrants another
        # roster probe (docich#5153: 72 read-only probes while a single march
        # was still in flight).
        located_names = {
            general
            for names in garrison_map.values()
            if isinstance(names, (list, tuple))
            for general in names
            if isinstance(general, str) and general
        }
        in_flight, _ = _en_route(mem)
        located_names.update(general for general in in_flight
                             if isinstance(general, str) and general)
        located = len(located_names)
        totals = mem.get('country_general_count')
        total = totals.get('count') if isinstance(totals, dict) else None
        enemies = [c for c in castles if c not in owned and not chart.is_boss_castle(c)]
        if not enemies:
            busy_sources = {o.get('source') for o in (*sorties['retake'], *sorties['attack'], *moves)
                            if o.get('source')}
            probe = _stale_boss_roster_probe(mem, chapter, castles, owned, busy_sources)
            if probe is not None:
                out['probe_stuck_1'] = probe
        elif type(total) is int and total > located:
            # A due arrival is stronger evidence than a stale home roster:
            # open the castle whose unread dispatch is waiting to be reconciled.
            # Otherwise probe the stalest eligible roster, but never reread the
            # same castle inside the shared 300-tick cooldown.
            garrison_tick = _world(mem).get('garrison_tick') or {}
            now = int(mem.get('tick') or 0)
            candidates = []
            # Prefer a castle not already used by a planned order, because
            # that order may later be rejected by a source/defender guard.
            # Keep planned sources as a last-resort read-only fallback, though:
            # if every other castle is recent/unreachable, excluding the only
            # source entirely erases the only way to discover its roster
            # (Devin follow-up on #5182, 2026-11-23).
            already_sources = {
                order.get('source')
                for order in (*front, *staffing, *waves, *boss_wave_orders, *tail,
                              *attacks, *stage_more, *rotation, *wave_orders)
                if isinstance(order.get('source'), str)
            }
            for candidate_source in owned:
                planned_source = candidate_source in already_sources
                reachable = [
                    e for e in enemies
                    if routes.allowed(mem, candidate_source, e)
                    and (not routes.is_road(chapter)
                         or not chart.march_blocked(chapter, candidate_source, e, owned))
                ]
                no_attack_leg = not reachable
                if no_attack_leg:
                    # A read-only roster probe never marches (deploy backs out
                    # after opening the list), so a measured attack leg is not
                    # required to open it. Requiring one skipped the home
                    # castle, whose measured legs all lead to castles we
                    # already own: 2026-11-24 ch3 had 24 generals country-wide
                    # and four located, every castle kept one, the home list
                    # was never reopened, and months passed with no sortie
                    # (#5324, the same deadlock #4689 fixed before the probe
                    # was narrowed to reachable attack sources). Any enemy
                    # castle serves as the nominal target; no march uses it.
                    reachable = [min(enemies,
                                     key=lambda e: (_distance(chapter, candidate_source, e), e))]
                unread = routes.unread_dispatches(mem, candidate_source)
                arrival_due = bool(unread) and routes.arrival_probe_due(
                    mem, candidate_source, cooldown=True)
                # An unread transfer that is still marching, or whose arrival
                # probe cooldown has not elapsed, must not redirect this fallback.
                if unread and not arrival_due:
                    continue
                last_tick = garrison_tick.get(candidate_source)
                recently_read = (type(last_tick) is int
                                 and 0 <= now - last_tick < STALE_ROSTER_PROBE_INTERVAL)
                if recently_read and not arrival_due:
                    continue
                if arrival_due:
                    unread_ticks = [row.get('tick') for _, row in unread
                                    if isinstance(row, dict) and type(row.get('tick')) is int]
                    priority = (0, 1 if planned_source else 0,
                                min(unread_ticks, default=now),
                                candidate_source != home, candidate_source)
                else:
                    # Among castles with no measured attack leg the home
                    # castle comes first: joiners appear where the hero is
                    # (``_arrival_castle``), so its list is the one that
                    # actually hides the generals missing from the count.
                    home_first = 0 if (no_attack_leg and candidate_source == home) else 1
                    priority = (1, 1 if planned_source else 0, home_first,
                                last_tick if type(last_tick) is int else -1,
                                candidate_source != home, candidate_source)
                candidates.append((priority, candidate_source, reachable[0]))
            candidates.sort(key=lambda row: row[0])
            if candidates:
                _, source, target = candidates[0]
                out['probe_stuck_1'] = {
                    'general': MOVE_ANY_GENERAL, 'source': source, 'target': target,
                    'cards': [], 'after': None, 'purpose': 'attack',
                    'source_probe': True,
                    # docich#4696: mark it a read-only roster probe as well, so the
                    # deploy flow reads the source list with _observe_garrison and
                    # backs out (B,B) instead of trying to send a general the bot
                    # has not read. ch3 14-x: the source probe reached the list but
                    # the plain source_probe path kept going to a sortie it could
                    # not resolve, and the garrison stayed at four castles.
                    'roster_probe': True,
                    'note': f'暫定: 膠着のため{source}の将軍一覧を読んでから{target}へ出撃する'}
    return out


STALE_ROSTER_PROBE_INTERVAL = 300


def _unblocked_boss_source_ready(mem, chapter, owned, boss_targets):
    """True when an open boss target already has a reachable assault source."""
    garrison = _world(mem).get('garrison') or {}
    if chapter == 4:
        for order in chart.orders(chapter):
            if order.get('target') not in boss_targets:
                continue
            src = _boss_companion_source(order, mem)
            if src in owned and not _march_blocked(chapter, src, order['target'], owned):
                return True
        return False
    busy, _ = _en_route(mem)
    unavailable = busy | set(_world(mem).get('general_location_unknown') or ()) | {NAME}
    for castle in owned:
        names = [g for g in (garrison.get(castle) or ()) if g not in unavailable]
        if len(set(names)) >= 1 and len(set(garrison.get(castle) or ())) >= 2:
            if any(not _march_blocked(chapter, castle, target, owned) for target in boss_targets):
                return True
    return False


def _stale_boss_roster_probe(mem, chapter, castles, owned, busy_sources):
    """Return a read-only roster probe for the stalest reachable owned castle
    when normal conquests are finished, no boss sortie source is ready, and
    country_general_count exceeds located garrison members.
    """
    totals = mem.get('country_general_count')
    total = totals.get('count') if isinstance(totals, dict) else None
    garrison_map = _world(mem).get('garrison') or {}
    located = sum(len(names) for names in garrison_map.values()
                  if isinstance(names, (list, tuple)))
    if type(total) is not int or total <= located:
        return None
    if chapter == 4:
        boss_targets = [c for c in chart.boss_sites(4)
                        if c in castles and not _site_king_down(mem, c)]
    else:
        boss_targets = [c for c in castles
                        if chart.is_boss_castle(c) and c not in owned
                        and not (mem.get('boss_victories') or {})]
    if not boss_targets or _unblocked_boss_source_ready(mem, chapter, owned, boss_targets):
        return None
    garrison_tick = _world(mem).get('garrison_tick') or {}
    now = int(mem.get('tick') or 0)
    eligible = []
    for castle in owned:
        if castle in busy_sources:
            continue
        last_tick = garrison_tick.get(castle)
        if type(last_tick) is int and 0 <= now - last_tick < STALE_ROSTER_PROBE_INTERVAL:
            continue
        reachable = [t for t in boss_targets if not _march_blocked(chapter, castle, t, owned)]
        if not reachable:
            continue
        eligible.append((last_tick if type(last_tick) is int else -1, castle, reachable[0]))
    if not eligible:
        return None
    eligible.sort()
    _, source, target = eligible[0]
    return {
        'general': MOVE_ANY_GENERAL, 'source': source, 'target': target,
        'cards': [], 'after': None, 'purpose': 'attack',
        'source_probe': True, 'roster_probe': True,
        'note': f'暫定: 膠着のため{source}の将軍一覧を読んでから{target}へ出撃する',
    }


def _boss_source_staffing_needed(mem, chapter, owned):
    if chapter == 4:
        boss_targets = [c for c in chart.boss_sites(4)
                        if c in chart.castles(chapter) and not _site_king_down(mem, c)]
        return bool(boss_targets) and not _unblocked_boss_source_ready(mem, chapter, owned, boss_targets)
    return True


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

    A read-only roster probe is not a candidate for this decision: it opens a
    castle list and never sends anybody, so it must neither release the wait
    nor stop the probe from running.
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
    candidates = interim_candidates(mem)
    # docich#5324: a probe-only candidate used to count as "a second general
    # became available" and cleared the wait. The wait then never aged: on
    # 2026-11-24 ch3 it was released six times in 35 minutes, each release
    # followed by the same read-only order and the same wait again, while
    # every castle kept one general and no sortie left for over an hour.
    probe_only = bool(candidates) and all(
        isinstance(order, dict) and order.get('roster_probe') is True
        for order in candidates.values())
    if candidates and not probe_only:
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
    if probe_only:
        # The probe still runs (the caller plans it), but it adds no general:
        # keep the wait so the monthly count keeps ageing towards the break.
        return None
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
    status = _plan_region(mem).get('orders') or {}
    if (interim and _plan_region(mem).get('active') != interim['step']
            and status.get(interim['step']) is None
            and interim['step'] in (mem.get('order_start_tick') or {})):
        # A terminal status written outside ``_finish_order`` is pruned the
        # same way: a step that started and whose orders entry vanished has
        # run its course, so the interim slot is free for the next candidate.
        state['interim_order'] = None
        interim = None
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
    # docich#4728: the step number used to grow with every rebuild
    # (I:5ae4d9:57 → :61 for the same ユージア→セレーネ retake), so the failed
    # status never carried over and the plan re-issued the same doomed sortie
    # forever. Name the interim step after the order itself: an identical
    # (purpose, source, target, general) keeps its step, and a failed one is
    # skipped instead of re-tried.
    steps = state.setdefault('interim_steps', {})

    def stable_step(order):
        identity = '|'.join(str(order.get(k) or '')
                            for k in ('purpose', 'source', 'target', 'general'))
        if order.get('roster_probe') is True:
            # A finished read settles the step 'done'; the same stamp rule as
            # ``_mechanical_step`` keeps a later request for the same castle —
            # a retained due after a partial read, or the next dispatch into
            # it — from inheriting that finished status (docich#5127).
            ticks = [s.get('tick') for _, s in routes.unread_dispatches(mem, order.get('source'))
                     if isinstance(s, dict)]
            stamp = max((t for t in ticks if type(t) is int), default=None)
            if stamp is None:
                read_tick = (_world(mem).get('garrison_tick') or {}).get(order.get('source'))
                stamp = read_tick if type(read_tick) is int else 0
            identity += '|probe:' + str(stamp)
        stable = steps.get(identity)
        if not stable:
            state['interim_count'] = state.get('interim_count', 0) + 1
            digest = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:8]
            # Namespace the identity by the request id: within one state the
            # same order keeps its step (so a failure is not re-issued every
            # rebuild), but a changed state starts a fresh namespace, so a
            # retry is still possible after the board actually moved
            # (docich#4729: the bare hash made ユージア→セレーネ permanently
            # failed and next_order returned None while four candidates were
            # ready).
            stable = f"{chart_adjust.INTERIM_PREFIX}{rid[:6]}:{digest}"
            steps[identity] = stable
        return stable

    # A settled interim step must not be re-issued: the top candidate whose
    # step already read 'failed' was re-picked on every rebuild, so
    # ``chart_interim_order`` looped and the slot never advanced to the next
    # candidate (g1190 ch1v4: ゼウス イソ→イデ！ step I:381454:0115cf0a stayed
    # 'failed' while 16 identical orders were recorded and the month saw no
    # departure). Walk the candidates and take the first whose step is still
    # live; a settled step can only run again once its status is re-armed.
    picked = None
    for label, order in candidates.items():
        step = stable_step(order)
        if status.get(step) in RETIRED_ORDER_STATES:
            continue
        picked = (label, order, step)
        break
    if picked is None:
        # Every candidate's step already settled. Hold like the no-candidate
        # case (once per request revision); the stale-failure windows re-arm
        # the statuses before this slot can retry.
        if state.get('interim_hold_digest') != digest:
            state['interim_hold_digest'] = digest
            _record(mem, 'chart_interim_hold', chart_step=None,
                    strategy_variant='chart_adjust_pending',
                    request_id=rid, choice=None, confidence=None,
                    jev_status='all_settled',
                    deviation_reason='interim_all_settled',
                    reason='暫定候補の全注文が失敗・完了済みのため再発行せず調整チャートを待つ')
        return
    label, order, step = picked
    if interim and interim.get('step') == step:
        # The slot already holds this order: re-setting it would record the
        # same chart_interim_order again without changing anything.
        state['interim_order'] = {**order, 'step': step}
        return
    state['interim_order'] = {**order, 'step': step}
    if order.get('hero_hub_move') is True:
        _note_hero_hub_move(mem)
    if order.get('hub_first') is True:
        _note_hub_reinforce(mem)
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
    status = _plan_region(mem).setdefault('orders', {})
    for order in orders:
        at = recent.get(order.get('step'))
        if type(at) is not int or now - at >= PLAN_STALE_TICKS:
            continue
        status[order['step']] = 'failed'
        _tally(mem, 'retry_suppressed')
    # Suppression settles the step: drop its plan context with it (#4054).
    drop_retired_step_context(mem)


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
    status = _plan_region(mem).setdefault('orders', {})
    book = _plan_region(mem).setdefault('plan_step_rearm', {})
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
        for key in ('general_override', 'source_override', 'card_override'):
            (_plan_region(mem).get(key) or {}).pop(step, None)
        _plan_region(mem).setdefault('order_context', {}).pop(step, None)
        _plan_region(mem).setdefault('sortie_general', {}).pop(step, None)
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


def _plan_has_foreign_source(mem, stored):
    """The stored plan still names a castle we no longer own.

    g1190 2026-11-17: the hub タタール fell while the plan's orders still sent
    every sortie from it (retake タタール→ネペンテ, move アルマムーン→タタール). The
    plan stayed adopted for 8,400 ticks while no order could launch.
    """
    chapter = mem.get('chapter') or 0
    owned = {c for c in _owned(mem) if c in (chart.castles(chapter) or {})}
    for order in stored.get('orders') or ():
        if not isinstance(order, dict):
            continue
        purpose = order.get('purpose')
        source = order.get('source')
        target = order.get('target')
        if purpose in ('retake', 'attack', 'move') and isinstance(source, str) and source not in owned:
            return True
        if purpose == 'move' and isinstance(target, str) and target not in owned:
            return True
    return False


def _carry_hero_permission(stored, orders):
    """Give a kept plan the hero-hub permission the rebuild now carries.

    The stored plan's orders are reused while their local steps match, so a
    flag added after they were adopted never reached the deploy flow (g1190
    2026-11-18 15:19: the kept hero order still lacked ``hero_hub_move`` and
    ``_move_general_pick`` refused the hero, sending ロックフォール instead —
    the hub never got its recruiting base, #4546).
    """
    granted = {o.get('local_step') or o.get('step') for o in orders
               if o.get('hero_hub_move') is True}
    changed = False
    for row in stored.get('orders') or ():
        if (isinstance(row, dict) and row.get('local_step') in granted
                and row.get('hero_hub_move') is not True):
            row['hero_hub_move'] = True
            changed = True
    return changed


def _grant_stored_hero_permission(mem, stored):
    """Restore the hero-hub permission on a plan adopted before it existed.

    A pending plan keeps ``_mechanical_chart`` on its early return all month,
    so a stored order can reach the deploy flow without ``hero_hub_move``;
    ``_move_general_pick`` then refuses the hero (g1190 2026-11-18 15:19-15:25:
    step A:b38abb1c:Ma4237a kept sending ロックフォール/チキータ/タピオカ to
    タタール while どうし stayed home — #4546). The only plan that names the
    hero for a move to the gateway hub is that owner's move.
    """
    gate = chart.gateway(mem.get('chapter') or 0)
    if not gate or not isinstance(stored, dict) or gate['hub'] not in _owned(mem):
        return False
    changed = False
    for row in stored.get('orders') or ():
        if (isinstance(row, dict) and row.get('general') == NAME
                and row.get('purpose') == 'move' and row.get('target') == gate['hub']
                and row.get('hero_hub_move') is not True):
            row['hero_hub_move'] = True
            changed = True
    return changed


def _mechanical_boss_plan(mem, rid):
    """A deterministic boss plan once every non-boss castle is owned.

    The base chart's boss orders are gated on the hero standing at their
    source castles (non-site chapters) or on an unblocked companion source
    (Chapter 4 四季王 sites); when prerequisite moves failed or stale overrides
    pinned an empty home castle, next_order skipped them and interim
    candidates were empty. Build the open base boss orders (plus hero moves only
    on non-site chapters), clear stale conquest/override state, and let
    next_order/launch run them.
    """
    chapter = mem.get('chapter') or 0
    castles = set(chart.castles(chapter))
    if not castles:
        return False
    normal = {c for c in castles if not chart.is_boss_castle(c)}
    owned = _owned(mem)
    if not normal or not normal <= owned:
        return False
    base = list(chart.orders(chapter))
    boss_orders = [o for o in base if chart.is_boss_castle(o.get('target'))
                   and not _site_king_down(mem, o.get('target'))]
    if not boss_orders:
        return False
    plan_region = _plan_region(mem)
    status = plan_region.setdefault('orders', {})
    live = {step for step, sortie in ((mem.get('world') or {}).get('sorties') or {}).items()
            if isinstance(sortie, dict)
            and sortie.get('status') in ('en_route', 'launched_unconfirmed')}
    for order in base:
        step = order.get('step')
        if step in live:
            continue
        if (order.get('purpose') != 'move'
                and not chart.is_boss_castle(order.get('target'))
                and order.get('target') in owned
                and status.get(step) in (None, 'pending', 'launched')):
            # Already-conquered non-move conquest steps (e.g. 4-A1 once
            # ミヒラギアン is owned) must be settled done so neither _order nor
            # next_order re-attacks an owned castle.
            status[step] = 'done'
            continue
        if status.get(step) != 'launched':
            continue
        general = order.get('general')
        garrison = _world(mem).get('garrison') or {}
        at_target = (isinstance(general, str)
                     and general in (garrison.get(order.get('target')) or ()))
        if order.get('target') in owned and at_target:
            status[step] = 'done'
            _record(mem, 'mechanical_boss_step_settled', chart_step=step,
                    observed_metric={'target': order.get('target'), 'general': general},
                    reason='ボス前提移動は行先の在城名簿に本人を確認できたため到着済みとして閉じる')
        else:
            status[step] = 'pending'
    boss_actors = {o.get('general') for o in boss_orders if o.get('general')}
    boss_sources = {o.get('source') for o in boss_orders}
    moves = [o for o in base
             if o.get('purpose') == 'move'
             and o.get('target') in boss_sources
             and o.get('general') in boss_actors]
    site_bosses = (chapter == 4 and all(o.get('target') in chart.boss_sites(4)
                                        for o in boss_orders))
    chosen = []
    seen = set()
    hero = next((o.get('general') for o in boss_orders if o.get('general')), None)
    garrison = _world(mem).get('garrison') or {}
    current = next((castle for castle, names in garrison.items()
                    if hero and hero in (names or ())), None)
    if not site_bosses and isinstance(hero, str) and isinstance(current, str):
        for boss in boss_orders:
            source = boss.get('source')
            if not isinstance(source, str):
                continue
            if current and current != source:
                move_step = f"M:{rid}:{current}>{source}"
                if move_step not in seen:
                    seen.add(move_step)
                    chosen.append({'step': move_step, 'source': current,
                                   'general': hero, 'target': source,
                                   'purpose': 'move', 'cards': [], 'after': None,
                                   'note': f'機械ボス移動 {current}→{source}'})
            step = boss.get('step')
            if isinstance(step, str) and step not in seen:
                seen.add(step)
                chosen.append(boss)
            current = source
    else:
        for order in (*(() if site_bosses else moves), *boss_orders):
            step = order.get('step')
            if not isinstance(step, str) or step in seen:
                continue
            seen.add(step)
            chosen.append(order)
    if not any(chart.is_boss_castle(o.get('target')) for o in chosen):
        return False
    plan_region = _plan_region(mem)
    plan_region['chart_plan'] = {
        'request_id': rid, 'source': 'mechanical',
        'orders': [{**o, 'cards': list(o.get('cards') or ()),
                    'after': o.get('after')} for o in chosen],
        'purchases': None, 'recruitment': None,
    }
    status = plan_region.setdefault('orders', {})
    override = plan_region.get('source_override')
    live = {step for step, sortie in ((mem.get('world') or {}).get('sorties') or {}).items()
            if isinstance(sortie, dict)
            and sortie.get('status') in ('en_route', 'launched_unconfirmed')}
    for order in chosen:
        step = order['step']
        if status.get(step) in (None, 'failed'):
            status[step] = 'pending'
        elif status.get(step) == 'launched' and step not in live:
            general = order.get('general')
            garrison = _world(mem).get('garrison') or {}
            at_target = (isinstance(general, str)
                         and general in (garrison.get(order.get('target')) or ()))
            if order.get('target') in owned and at_target:
                status[step] = 'done'
                _record(mem, 'mechanical_boss_step_settled', chart_step=step,
                        observed_metric={'target': order.get('target'), 'general': general},
                        reason='ボス前提移動は行先の在城名簿に本人を確認できたため到着済みとして閉じる')
            else:
                status[step] = 'pending'
        if isinstance(override, dict) and chart.is_boss_castle(order.get('target')):
            over = override.get(step)
            if over and (over not in owned or garrison.get(over) == []
                         or _march_blocked(chapter, over, order['target'], owned)):
                override.pop(step, None)
    # Keep a map-activated chosen step; a rebuild must not clear the active
    # step between the activation hook and the launch.
    if plan_region.get('active') not in {o['step'] for o in chosen}:
        plan_region['active'] = None
    plan_region['attack'] = None
    mem['chart_plan_tick'] = int(mem.get('tick') or 0)
    _record(mem, 'mechanical_boss_plan', strategy_variant='mechanical_boss',
            chart_step=None,
            observed_metric={'chapter': chapter,
                             'steps': [o['step'] for o in chosen]},
            reason='通常城を全て確保済みのため、基本チャートのボス注文と将軍移動を機械的に計画する')
    return True


def _mechanical_chart(mem, state, rid, digest):
    """Construct and adopt a full chart mechanically (no LLM answer)."""
    if _grant_stored_hero_permission(mem, _plan_region(mem).get('chart_plan') or {}):
        _note_hero_hub_move(mem)    # the month's hero move already lives in the stored plan
    if _plan_pending(mem):
        return
    allow_sole = False   # no castle is ever emptied (owner 2026-11-06)
    stored = _plan_region(mem).get('chart_plan') or {}
    if stored and _plan_has_foreign_source(mem, stored):
        _record(mem, 'chart_plan_invalidated',
                observed_metric={'orders': [o.get('step') for o in stored.get('orders') or ()
                                            if isinstance(o, dict)][:6]},
                reason='計画が自軍でない城を出撃元/移動先にしているため破棄して組み直す')
        _plan_region(mem)['chart_plan'] = None
        stored = {}
    if stored.get('source') == 'mechanical' and stored.get('request_id') != rid:
        # docich#4714: a mechanical plan whose request_id no longer matches the
        # state is stale, and keeping it pins orders the state has outgrown —
        # ch6 20-x held オケアナ→ボス(エシャロット) for many minutes while the
        # castle's list read ビーツ/ポセイドン/ゼウス, so the boss never started.
        # Drop it so the rebuilt plan re-picks a measured actor (v1211).
        _record(mem, 'chart_plan_stale_request',
                observed_metric={'stored': stored.get('request_id'),
                                 'current': rid},
                reason='計画のrequest_idが現在の状態と一致しないため破棄して組み直す')
        _plan_region(mem)['chart_plan'] = None
        stored = {}
    if stored.get('request_id') == rid and stored.get('source') == 'mechanical':
        # Even within the same month, rebuild an exhausted mechanical chart
        # against the current garrison and in-flight sorties (keep defenders).
        _plan_region(mem)['chart_plan'] = None
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
        if _mechanical_boss_plan(mem, rid):
            return
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
            # The plan's order dict only copies known fields; the hero-hub
            # move permission has to survive this rebuild or startable()
            # rejects the move as hero_not_dispatchable (g1190 2026-11-18:
            # the hero stayed at home while the plan named only attacks).
            **({'hero_hub_move': True} if order.get('hero_hub_move') is True else {}),
            # The promoted hub reinforcement keeps its flag so a later replan
            # does not mint a second promoted move while this one is queued.
            **({'hub_first': True} if order.get('hub_first') is True else {}),
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
    from .orders import _clear_stale_pending_overrides
    if (stored.get('source') == 'mechanical' and previous_local
            and None not in previous_local
            and [o['step'] for o in orders] == previous_local):
        _carry_hero_permission(stored, orders)
        _plan_region(mem)['chart_plan'] = stored
        _clear_stale_pending_overrides(mem)
        if any(o.get('hero_hub_move') is True for o in stored.get('orders') or ()):
            _note_hero_hub_move(mem)
        if any(o.get('hub_first') is True for o in stored.get('orders') or ()):
            _note_hub_reinforce(mem)
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
    # Validation deliberately drops internal-only permissions. Restore the
    # trusted mechanical permission by local step, never from external plans.
    hero_steps = {o['step'] for o in orders if o.get('hero_hub_move') is True}
    hub_first_steps = {o['step'] for o in orders if o.get('hub_first') is True}
    validated = dict(validated, request_digest=digest, orders=[
        {**o,
         **({'hero_hub_move': True} if o['step'] in hero_steps else {}),
         **({'hub_first': True} if o['step'] in hub_first_steps else {})}
        for o in validated['orders']])
    _adopt_plan(mem, validated, rid)
    if hero_steps:
        _note_hero_hub_move(mem)
    if hub_first_steps:
        _note_hub_reinforce(mem)


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
            stamp = max((t for t in ticks if type(t) is int), default=None)
            if stamp is None:
                read_tick = (_world(mem).get('garrison_tick') or {}).get(order.get('source'))
                stamp = read_tick if type(read_tick) is int else 0
            key += ':' + str(stamp)
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
                'strong_card_kit', 'dropper_card_kit',
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
    status = _plan_region(mem).setdefault('orders', {})
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
        for key in ('general_override', 'source_override', 'card_override'):
            (_plan_region(mem).get(key) or {}).pop(step, None)
        _plan_region(mem).setdefault('order_context', {}).pop(step, None)
        _plan_region(mem).setdefault('sortie_general', {}).pop(step, None)
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
    status = _plan_region(mem).setdefault('orders', {})
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
    # A castle read only as a full page contributes its confirmed lower
    # bound, so ``startable`` can confirm the page's own generals at their
    # castle without the page ever claiming the roster (docich#3997).
    recorded = _world(mem).get('garrison') or {}
    partial = _world(mem).get('garrison_partial') or {}
    situation_garrison = {}
    for castle in sorted(set(recorded) | set(partial)):
        names = _known_residents(mem, recorded, castle)
        # An unread castle still serialises as [] (see the comment above);
        # only its confirmed lower bound is ever added, never a roster claim.
        situation_garrison[castle] = sorted(names or ())
    return {'garrison': situation_garrison,
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
    previous = (_plan_region(mem).get('chart_plan') or {}).get('orders') or []
    statuses = _plan_region(mem).setdefault('orders', {})
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
    _plan_region(mem)['chart_plan'] = {
        'request_id': rid, 'source': doc.get('source'), 'orders': orders,
        'recruitment': doc.get('recruitment'),
        'purchases': ({**purchases, 'month': list(purchases['month']),
                       'cards': [list(c) for c in purchases['cards']]} if purchases else None)}
    from .orders import _clear_stale_pending_overrides
    _clear_stale_pending_overrides(mem)
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
    plan = _plan_region(mem).get('chart_plan') or {}
    adopted = mem.get('chart_plan_tick')
    if not plan.get('orders') or type(adopted) is not int:
        return
    now = int(mem.get('tick') or 0)
    if now - adopted < PLAN_STALE_TICKS:
        return
    status = _plan_region(mem).get('orders') or {}
    # docich#4642: an all-failed (or completed) plan must rebuild the same
    # way an all-pending one does — only a march still in flight keeps it.
    live = {s.get('step') for s in (_world(mem).get('sorties') or {}).values()
            if isinstance(s, dict) and s.get('status') in ('en_route', 'launched_unconfirmed')}
    if any(o.get('step') in live or status.get(o.get('step')) == 'launched_unconfirmed'
           for o in plan['orders']):
        return
    _plan_region(mem)['chart_plan'] = None
    mem.pop('chart_plan_tick', None)
    mem.pop('plan_stale_signature', None)
    _tally(mem, 'plan_stale_rebuild')


def _off_chart_mechanical(mem, state, rid, gate):
    """Speedrun chart abolished: take castles mechanically, ask no model.

    The same stand-in the adjusted-chart path uses while an answer is pending,
    but nothing is requested and no model answer is ever adopted, so the next
    sortie is always the first deterministic candidate.
    """
    status = _plan_region(mem).get('orders') or {}
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
        if (_plan_region(mem).get('chart_plan') or {}).get('source') == 'mechanical':
            _plan_region(mem)['chart_plan'] = None     # built for an older situation
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
                    # The depleted-egg gate must not judge the wait itself, so
                    # blank both egg mirrors in the readiness probe (#4439).
                    and _ready(o, {**mem, 'egg_uses': {}, 'egg_memory': {}})
                    and (_plan_region(mem).get('orders') or {}).get(o['step']) in (None, 'pending')), None)
    if waiting:
        key = (waiting['step'], mem.get('month'), egg_memory.current_uses(mem, NAME))
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
    plan = _plan_region(mem).get('chart_plan') or {}
    # A mechanical chart is only a stand-in while waiting for an adjusted chart,
    # so its orders must not replace the base chart when computing blocked/reason
    # and the request digest (just as INTERIM_PREFIX orders are excluded).
    source_orders = (chart.orders(mem.get('chapter') or 0)
                     if plan.get('source') == 'mechanical' else _orders(mem))
    orders = [o for o in source_orders if not o['step'].startswith(chart_adjust.INTERIM_PREFIX)]
    status = _plan_region(mem).get('orders') or {}
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
        if (_plan_region(mem).get('chart_plan') or {}).get('source') == 'mechanical':
            _plan_region(mem)['chart_plan'] = None
        _record(mem, 'chart_adjust_request', chart_step=None, strategy_variant='chart_adjust_pending',
                **fields,
                reason='チャート外: 状況が更新されたため同じ要求を最新の在庫・配置で再要求')
    doc = mem.get('_adjusted')
    plan = _plan_region(mem).get('chart_plan') or {}
    doc_digest = doc.get('request_digest') if doc else None
    if (doc and doc.get('request_id') == rid and doc.get('chapter') == mem.get('chapter')
            and (plan.get('request_id') != rid or (_plan_region(mem).get('chart_plan') or {}).get('source') == 'mechanical')
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
    if (_plan_region(mem).get('chart_plan') or {}).get('source') not in (None, 'mechanical'):
        # Fall back to the single interim sortie for exhausted adopted plans.
        _mechanical_interim(mem, state, rid, digest, allow_sole=(gate == 'break'))
        return
    if gate == 'break':
        _mechanical_interim(mem, state, rid, digest, allow_sole=False)
    else:
        _mechanical_chart(mem, state, rid, digest)
