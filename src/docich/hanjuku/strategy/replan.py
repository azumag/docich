"""戦略層: off-chart replanning (interim orders and adjusted charts).

Builds interim candidates when the chart has no ready order, starts the first
of them without waiting for a model, adopts a validated adjusted chart when
one answers, and records every deviation.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust
from ..decision_log import _record
from ..os_layer.world import NAME, SORTIE_BUSY_TICKS, _en_route, _owned
from . import routes
from .orders import (_arrival_castle, _boss_companion_source, _boss_egg_depleted,
                               _distance, _hero_egg_broken, _holds_egg,
                               _hero_source_alternative, _orders, _plan_pending, _ready, _reserved_targets, _source,
                               _defense_move_pick, _defense_priority_castles, _defense_score,
                               _post_conquest_wait,
                               _source_list_open, _source_location_recheck, _source_location_recheck_due,
                               _unplaced_castle_generals)


def _march_blocked(chapter, source, target, owned):
    # Chapter 3 marches follow the road table (``routes``), not the straight
    # line — an own castle off the road is no blocker even when it sits on
    # the segment.
    return chapter != 3 and chart.march_blocked(chapter, source, target, owned)


def _march_blocker(chapter, source, target, owned):
    return chart.march_blocker(chapter, source, target, owned) if chapter != 3 else None


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
    garrison = mem.get('garrison') or {}
    busy = busy | set(mem.get('general_location_unknown') or ())
    unavailable_for_attack = {NAME} if _hero_egg_broken(mem) else set()
    sole = len(owned) == 1          # owner: a single castle may leave the hero alone
    free = {c: [g for g in garrison.get(c) or () if g not in busy] for c in owned}
    if chapter == 3:
        owned = {c for c in owned if routes.allowed(mem, c, target)}
    # Emptying a castle is how the undefended ジョンリギ fell (g401), so a
    # castle that keeps somebody behind is required. Owner 2026-11-02: an
    # own castle lying on the march stops the army inside (アルマムーン →
    # クロチェット enters オトラント; every measured アルマムーン-launch
    # stalled while the only arrival was オトラント-launched 5-Z2), so a
    # source whose path crosses another own castle ranks behind a clear one.
    need = 1 if allow_sole else 2
    excluded = (set(mem.get('defense_break_blocked') or ()) | set(mem.get('lost') or ())
                if allow_sole else set())
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
                spares = _spares([g for g in free.get(blocker) or ()
                                  if g not in unavailable_for_attack],
                                 unavailable_for_attack, sole)
                if spares:
                    return blocker, spares[0]
                if garrison.get(blocker) is None:
                    return blocker, MOVE_ANY_GENERAL
        return best, _spares(free[best], unavailable_for_attack, sole)[0]
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
        # Joiners stand in the hero's castle; the sortie list is the reading
        # and still cancels if fewer than two are really there.
        return arrival, unplaced[0]
    general = chart_order['general'] if chart_order else NAME
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
            return source, general
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


MOVE_ANY_GENERAL = 'しょうぐん'    # move from an unread list: the spare is picked on the list


def _spares(free, unavailable=(), sole=False):
    """Generals a castle may send, the hero last and only when alone.

    The hero's castle keeps ``HERO_CASTLE_GUARD`` other generals (owner
    2026-10-29) unless it is our only castle (``sole``); when they would leave
    it thin, the hero is not sent instead.
    """
    others = [g for g in free if g != NAME and g not in unavailable
              and not chart_adjust.hero_guard_short(free, g, sole_castle=sole)]
    if others:
        return others
    return [NAME] if set(free) == {NAME} and NAME not in unavailable else []


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
    garrison = mem.get('garrison') or {}
    home = chart.home_castle(chapter)
    priority = _defense_priority_castles(mem)
    unlocated = set(mem.get('general_location_unknown') or ())
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
                    'note': f'暫定: ボス出撃待ちの防衛優先として{general}を{source}から{target}へ移動'})
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
    all_orders = chart.all_orders(chapter)
    boss_path = {o.get('target') for o in all_orders
                 if chart.is_boss_castle(o.get('target'))}
    while True:
        grow = {o.get('source') for o in all_orders
                if o.get('target') in boss_path and o.get('source') not in boss_path}
        if not grow:
            break
        boss_path |= grow
    boss_gate = {after[1] for order in all_orders
                 if order.get('target') in boss_path
                 for after in (order.get('after'),)
                 if isinstance(after, (list, tuple)) and len(after) == 2
                 and after[0] == 'captured' and after[1] in lost}
    lost.sort(key=lambda c: (c != home, c not in boss_gate))  # home first, then the boss gate
    targets = [c for c in (*lost, *chart_orders, *castles)
               if c in castles and c not in owned and not chart.is_boss_castle(c)]
    targets = list(dict.fromkeys(targets))
    # An interim sortie that has already gone out (launched/en route) must not be
    # re-issued while it is still in flight: otherwise the same target would be
    # attacked twice and the candidate list would never advance. Only recent
    # sortie receipts count; a launched order or a lost sortie never observed
    # to arrive must not hold its target forever (launched/launched_orders are
    # never pruned).
    now = int(mem.get('tick') or 0)
    inflight = {sortie['target'] for step, sortie in (mem.get('sorties') or {}).items()
                if isinstance(step, str) and step.startswith(chart_adjust.INTERIM_PREFIX)
                and isinstance(sortie, dict)
                and sortie.get('status') in ('en_route', 'launched_unconfirmed', 'launched', 'pending')
                and sortie.get('target') is not None and sortie.get('tick') is not None
                and 0 <= now - int(sortie['tick']) < SORTIE_BUSY_TICKS}
    targets = [c for c in targets if c not in inflight]
    # One recent expedition per target. Unobserved old arrivals stop reserving
    # after SORTIE_BUSY_TICKS; they must not empty every castle in the meantime.
    targets = [c for c in targets if c not in heading]
    sorties = {'retake': [], 'attack': []}
    for target in targets:
        picked = _interim_source(mem, target, chart_orders.get(target), owned, busy, allow_sole)
        if picked is None:
            continue
        source, general = picked
        purpose = 'retake' if target in lost else 'attack'
        note = (f"暫定: {general}で奪われた{target}を奪還" if purpose == 'retake'
                else f"暫定: {general}で{target}を攻撃")
        # Owner (2026-09-28): 切り札は持たせたほうが良い. The chart's cards for
        # this castle, else the retry opener (two イッテツーン). Cards out of
        # stock are dropped at the card list (card_drop), never waited for.
        base = chart_orders.get(target)
        cards = list(base['cards']) if base and base.get('cards') else list(INTERIM_CARDS)
        sorties[purpose].append({
            'general': general, 'target': target, 'cards': cards, 'source': source,
            'after': None, 'purpose': purpose, 'note': note,
            **({'source_probe': True} if general == MOVE_ANY_GENERAL else {})})
    garrison = mem.get('garrison') or {}
    moves = []
    if chapter == 3:
        # Stage spare generals along chart legs rather than aiming a distant
        # enemy straight across a cliff. Every stage ends at an owned castle.
        seen_legs = set()
        for target in targets:
            options = []
            for donor in sorted(owned):
                free = [g for g in garrison.get(donor) or () if g not in busy
                        and g not in (mem.get('general_location_unknown') or ())]
                probe = routes.probe_due(mem, donor)
                if len(free) < 2 and not probe:
                    continue
                if len(free) >= 2 and not [g for g in _spares(free) if g != NAME]:
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
            general = (MOVE_ANY_GENERAL if len(free) < 2
                       else (_defense_move_pick(mem, donor, route[1],
                                                _spares(free)) or _spares(free)[0]))
            moves.append({'general': general, 'source': donor, 'target': route[1],
                          'cards': [], 'after': None, 'purpose': 'move',
                          'note': f'暫定: {target}への中継として{route[1]}へ移動',
                          **({'source_probe': True} if general == MOVE_ANY_GENERAL else {})})
    # A castle that was sent a move after its list was last read is not known
    # to be empty: past the busy window it was picked again and again (#3037).
    empty = [c for c in sorted(owned, key=lambda c: _distance(chapter, home, c))
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
            'note': f"暫定: {general}を{donors[0]}から空の{target}へ移動"})
    # A pending 四季王 site order with no companion needs an egg pair at one
    # castle: move a spare general to join a lone egg-holder (or the hero's
    # castle) so one can sortie while an egg-holder stays. g1075 held every
    # site order pending for an hour while a lone egg-holder guarded each
    # castle (owner 2026-10-30: 城に戻って出撃できる人員を探す).
    pending_sites = [o for o in chart.orders(chapter)
                     if o['target'] in chart.boss_sites(4)
                     and (mem.get('orders') or {}).get(o['step']) in (None, 'pending')]
    if pending_sites and all(_boss_companion_source(o, mem) is None for o in pending_sites):
        unavailable = busy | set(mem.get('general_location_unknown') or ())
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
    # Every candidate must be startable from the recorded garrisons, so a
    # chart general named in another castle never reaches the castle menu.
    situation = _adjust_situation(mem)
    out = {}
    known_retakes = [o for o in sorties['retake'] if not o.get('source_probe')]
    unread_retakes = [o for o in sorties['retake'] if o.get('source_probe')]
    for order in (*known_retakes, *moves, *unread_retakes, *sorties['attack']):
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
    busy = busy | set(mem.get('general_location_unknown') or ())
    garrison = mem.get('garrison') or {}
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
    garrison = mem.get('garrison') or {}
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
    if allow_sole and len(set(((mem.get('garrison') or {}).get(order['source']) or ()))) < 2:
        # Only the monthly defence-break fallback sends a sole general out.
        mem.setdefault('defense_break_sources', {})[order['source']] = order['target']
        _record(mem, 'sortie_defense_break', chart_step=None, strategy_variant='chart_defense_break',
                general=order['general'], source=order['source'], target=order['target'],
                observed_metric={'source_garrison': list(
                    (mem.get('garrison') or {}).get(order['source']) or ()),
                    'month': mem.get('month')},
                reason='守備制約下の月次フォールバック: 1将軍出撃（出撃元の喪失を監視）')
    state['interim_count'] = state.get('interim_count', 0) + 1
    step = f"{chart_adjust.INTERIM_PREFIX}{rid[:6]}:{state['interim_count']}"
    state['interim_order'] = {**order, 'step': step}
    _record(mem, 'chart_interim_order', chart_step=step, strategy_variant='chart_interim_mechanical',
            request_id=rid, choice=label, confidence=None, general=order['general'],
            source=order['source'], target=order['target'], purpose=order.get('purpose'),
            deviation_reason='mechanical_chart',
            reason='LLM応答を待たず決定的候補の先頭から暫定出撃')


def _mechanical_chart(mem, state, rid, digest):
    """Construct and adopt a full chart mechanically (no LLM answer)."""
    if (mem.get('chart_plan') or {}).get('request_id') == rid:
        return
    if _plan_pending(mem):
        return
    allow_sole = _defense_gate(mem) == 'break'
    candidates = interim_candidates(mem, allow_sole=allow_sole)
    if not candidates:
        _mechanical_interim(mem, state, rid, digest, allow_sole=allow_sole)
        return
    chapter = mem.get('chapter') or 0
    orders = []
    for i, (label, order) in enumerate(candidates.items(), 1):
        orders.append({
            'step': 'M%d' % i,
            'general': order['general'],
            'source': order['source'],
            'target': order['target'],
            'cards': list(order.get('cards') or ()),
            'after': order.get('after'),
            'note': order.get('note') or '',
        })
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
    marching = sorted({(s['general'], s.get('target')) for s in (mem.get('sorties') or {}).values()
                       if s.get('status') in ('en_route', 'launched_unconfirmed') and s.get('general')
                       and s.get('tick') is not None and now - int(s['tick']) < SORTIE_BUSY_TICKS},
                      key=str)
    return {'garrison': {castle: sorted(names) for castle, names in sorted((mem.get('garrison') or {}).items())},
            'owned': sorted(_owned(mem)),
            'lost': sorted(mem.get('lost') or []), 'home_lost': bool(mem.get('home_lost')),
            'en_route': [{'general': g, 'target': t} for g, t in marching],
            'card_stock': dict(sorted((mem.get('card_stock') or {}).items()))}


def _adopt_plan(mem, doc, rid):
    """Adopt a validated adjusted chart as the plan, with per-generation step ids."""
    orders = [{**o, 'step': chart_adjust.execution_step(rid, o['step']), 'local_step': o['step'],
               'cards': list(o['cards']), 'after': list(o['after']) if o['after'] else None}
              for o in doc['orders']]
    purchases = doc.get('purchases')
    mem['chart_plan'] = {
        'request_id': rid, 'source': doc.get('source'), 'orders': orders,
        'recruitment': doc.get('recruitment'),
        'purchases': ({**purchases, 'month': list(purchases['month']),
                       'cards': [list(c) for c in purchases['cards']]} if purchases else None)}
    state = mem.setdefault('chart_adjust', {})
    state.pop('interim_order', None)
    mem['variant'] = 'chart_adjusted'
    # Digest for commentary: grounded order content, not the full plan blob.
    order_digest = [{'general': o['general'], 'source': o['source'],
                     'target': o['target'], 'cards': list(o['cards'])} for o in orders]
    _record(mem, 'chart_adjust_applied', chart_step=None, strategy_variant='chart_adjusted',
            request_id=rid, source=doc.get('source'), steps=[o['step'] for o in orders],
            local_steps=[o['local_step'] for o in orders], order_digest=order_digest,
            reason='調整チャートを受信したため独自の指示列で出撃を再開')


def _off_chart(mem):
    """No order is ready: request an adjusted chart, or adopt one that answered.

    The base chart is never mutated. A request is recorded once per distinct
    situation (chapter, captures, order states); an adjusted chart is adopted
    only when it answers exactly that request, as a complete order list that
    replaces the previous plan. Until then the previous plan keeps waiting, or,
    with no plan waiting, the first deterministic interim candidate runs. No
    model answer is required for progress.
    """
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
        _mechanical_interim(mem, state, rid, digest, allow_sole=True)
    else:
        _mechanical_chart(mem, state, rid, digest)
