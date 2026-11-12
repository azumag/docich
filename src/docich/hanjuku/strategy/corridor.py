"""Chapter 9 before its boss phase: send every general to the next castle.

Owner 2026-11-11: ９話においては単純に機械的に次の城にすべての将軍を送っていき、
それ以外の操作はdisableにしてほしい. Earlier the road ran through the normal
planner (garrison-named orders, in-flight caps, retake reservations, adjusted
plans whose steps run once) and kept stalling on one gate after another.

Mechanical mode replaces all of that with castle-scoped orders:

* the castle beside the next road castle attacks it (a retake when it was ours);
* every other owned castle moves one hop along the road toward it;
* each order names nobody: the deploy step sends whoever the list cursor is on
  (the hero last, never in an attack) and takes cards from the top;
* an order's step id carries a per-castle sequence, so the next order from the
  same castle is a fresh step as soon as the previous one left;
* a castle whose live list showed nobody usable is skipped until something is
  sent into it or the month changes (``drained``).

Squads (owner 2026-11-11, v671): ３２人全員移動させると大変だから、先発隊６人を
敵城にいかせ、しろがとれたら将軍とプラス６人をいかせる（どちらも６人待つという
意味ではなく、６人がMAX）. Both limits are maxima; nothing waits for six.

* at most ``SQUAD`` attackers march at the next castle at once;
* each capture opens a wave: every road hop may move at most ``SQUAD``
  generals;
* the hero is posted one road castle behind the front (owner 2026-11-12:
  主人公は前線の自城一つ前の自城に常駐 — 新しく雇った将軍は主人公のいる後方から
  送ることになるため). He walks forward only up to that post and steps back to it
  when he is already at the front;
* a wave whose front castle showed an empty list with no attacker marching
  opens a fresh budget, so a wiped vanguard never deadlocks the road;
* an attack never takes the hero's last companion from his castle.

The boss phase (every road castle ours) keeps the normal flows.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust
from ..os_layer.world import _owned, _world
from ..os_layer.world import NAME, SORTIE_BUSY_TICKS, _hero_castle
from ..state.regions import flow as _flow_region, plan as _plan_region, sorties as _sorties
from . import routes

PREFIX = 'I:c9:'
DRAINED = 'corridor_drained'
WAVE = 'corridor_wave'
PROBE = 'corridor_probe'
SQUAD = 6
DRAINED_MIN_HOLD = 300      # ticks a fresh empty reading is trusted before a re-probe
# Probe orders a month may spend looking for unaccounted generals. A castle
# list cannot show a general who is camped on the field, so the probe tour
# never finds them and must not run forever (see ``_probe_spend``).
PROBE_BUDGET = 6
# Ticks after which one more look is allowed even when nothing was found.
PROBE_REARM_TICKS = 3600
# Idle probes (nothing else to plan) are minted at most this often. Zero
# keeps one minted per observation, which is what stops the empty plan from
# standing still; the in-flight suppression is what removes the churn.
IDLE_PROBE_SPACING = 0
# A sortie attempt older than this is stale evidence, not a launch in flight.
SORTIE_ATTEMPT_TTL = 120
# Ticks a march may take before a dispatched general is treated as unknown
# rather than still on the road (a march is observed at ~1 tick/second).
ARRIVAL_RECHECK_TICKS = 120
MARCHING = ('en_route', 'launched_unconfirmed', 'launched', 'pending')


def mechanical(mem) -> bool:
    chapter = mem.get('chapter')
    if not chart.guard_free(chapter) or not chart.corridor(chapter):
        return False
    owned = _owned(mem) & set(chart.castles(chapter))
    return chart.corridor_next(chapter, owned) is not None


def _drained(mem, castle, purpose) -> bool:
    entry = (mem.get(DRAINED) or {}).get(castle)
    if not isinstance(entry, dict):
        return False
    if entry.get('month') != mem.get('month'):
        # A new month alone is not news about this castle. Re-probing every
        # drained castle at each month change cost about a fifth of the run's
        # navigation (g1163 2026-11-12 07:0x-07:3x: twelve タオ>サヌト orders in
        # an hour, each ending in 「出撃元に出せる将軍がいない」) while nobody had
        # been sent there. Only a castle somebody was dispatched into can have
        # gained a general, so only that one is re-read in the new month.
        since = entry.get('tick')
        if type(since) is not int:
            return False
        dispatched = any(isinstance(sortie, dict) and sortie.get('target') == castle
                         and type(sortie.get('tick')) is int and sortie['tick'] > since
                         for sortie in _sorties(mem).values())
        if not dispatched:
            return True
    if entry.get('kind') == 'hero' and purpose == 'move':
        return False                       # the hero alone still walks forward
    since = entry.get('tick')
    if type(since) is not int:
        return False
    now = int(mem.get('tick') or 0)
    for sortie in _sorties(mem).values():
        if not isinstance(sortie, dict) or sortie.get('target') != castle:
            continue
        tick = sortie.get('tick')
        if type(tick) is not int or tick <= since:
            continue
        # Only a confirmed arrival puts somebody back in this castle. A march
        # that was merely *sent* clears the mark while the general is still on
        # the road, so the plan opens this castle's list immediately, finds
        # nobody and spends a whole sortie cycle doing it: g1163 2026-11-11
        # 20:3x-21:4x ended 21 order starts as
        # 「出撃元に出せる将軍がいないため、この城を一時的に対象から外す」.
        # A march that already ended without arriving (overdue, closed) never
        # puts anybody in this castle, so re-checking it just repeats the same
        # empty list: g1163 2026-11-12 00:2x-00:3x re-ordered サヌト>オシロガ
        # every couple of minutes and closed it with
        # 「出撃元に出せる将軍がいない」 each time. Only a march that may still be
        # on the road gets the one recheck.
        status = sortie.get('status')
        if status == 'arrived':
            return False
        if status in ('en_route', 'launched_unconfirmed') and now - tick >= ARRIVAL_RECHECK_TICKS:
            # A castle just read empty is not re-opened a moment later unless
            # the mark itself is old. g1163 2026-11-12 11:0x-11:1x: 47 order
            # starts an hour against 8 launches, mostly アルマムーン>ハヤク /
            # ハヤク>タオ / タオ>サヌト into castles that had just been recorded
            # empty — each a jump, a status panel and a close.
            if now - since < DRAINED_MIN_HOLD:
                continue
            return False
    return True


def mark_drained(mem, castle, kind):
    """``kind``: 'empty' (no general) or 'hero' (only the hero; no attack)."""
    mem.setdefault(DRAINED, {})[castle] = {'kind': kind, 'tick': int(mem.get('tick') or 0),
                                          'month': mem.get('month')}


def _step(mem, source, target, tag=''):
    """A fresh id past every id this castle pair has used.

    A count of settled ids collided with the castle's own history once a
    settled id sat above a pending hole (g1163 2026-11-11 15:41-17:34:
    タオ>サヌト had 0-3,6 done, 4,7 pending and 5 launched, so the count gave
    :6, already done; no order from the front ran for almost two hours).
    """
    prefix = f'{PREFIX}{source}>{target}:{tag}'
    used = [int(step[len(prefix):]) for step in (_plan_region(mem).get('orders') or {})
            if step.startswith(prefix) and step[len(prefix):].isdigit()]
    return f'{prefix}{max(used) + 1 if used else 0}'


def _pair(step):
    """``(source, target)`` of a march step id, else ``None``."""
    if not isinstance(step, str) or not step.startswith(PREFIX):
        return None
    body = step[len(PREFIX):].rsplit(':', 1)[0]
    source, _, target = body.partition('>')
    return (source, target) if source and target else None


def _march(mem):
    """Every chapter 9 march sortie: ``(step, sortie, source, target)``."""
    for step, sortie in _sorties(mem).items():
        pair = _pair(step)
        if pair and isinstance(sortie, dict) and type(sortie.get('tick')) is int:
            yield step, sortie, *pair


def _attackers_marching(mem, target) -> int:
    """In-flight attackers at ``target``: the single budget both sides see.

    #3944: the planner counted corridor step ids while the deploy gate counted
    reserved targets, with caps 6 vs 3, so in-flight 3-5 kept minting retakes
    the gate then held every observation. Both now count the same sorties
    once: a corridor step whose hop is the target, or any sortie whose
    reserved target is the target (a ``launched_unconfirmed`` sortie reserves
    its intended target through ``launched_orders``). Non-corridor retakes
    (e.g. ``I:old``) count too; ``purpose == 'move'`` and settled/expired
    sorties do not.
    """
    from .orders import _arrival_limit, _sortie_reserved_target
    now = int(mem.get('tick') or 0)
    count = 0
    for step, sortie in _sorties(mem).items():
        if (not isinstance(sortie, dict) or type(sortie.get('tick')) is not int
                or sortie.get('purpose') == 'move' or sortie.get('status') not in MARCHING
                or not 0 <= now - sortie['tick'] < max(SORTIE_BUSY_TICKS, _arrival_limit(mem, sortie))):
            continue
        pair = _pair(step)
        if (pair and pair[1] == target) or _sortie_reserved_target(mem, step, sortie) == target:
            count += 1
    return count


def _wave(mem, target, front):
    """The current capture's wave; a new target (a capture) opens a new one."""
    now = int(mem.get('tick') or 0)
    wave = mem.get(WAVE)
    if not isinstance(wave, dict) or wave.get('target') != target or type(wave.get('tick')) is not int:
        wave = mem[WAVE] = {'target': target, 'tick': now}
        return wave
    # A wiped vanguard: the front's live list was empty, nobody is marching at
    # the target and this wave's movers into the front have all been sent.
    drained = (mem.get(DRAINED) or {}).get(front) or {}
    if (front and drained.get('kind') == 'empty' and type(drained.get('tick')) is int
            and drained['tick'] >= wave['tick'] and _moved(mem, wave, front) >= SQUAD
            and not _attackers_marching(mem, target)):
        wave = mem[WAVE] = {'target': target, 'tick': now, 'refill': int(wave.get('refill') or 0) + 1}
    return wave


def _moved(mem, wave, hop, source=None):
    """Generals other than the hero this wave sent into ``hop``."""
    return sum(1 for _, sortie, src, dst in _march(mem)
               if dst == hop and (source is None or src == source)
               and sortie['tick'] >= wave['tick'] and sortie.get('purpose') == 'move'
               and sortie.get('general') != NAME)


def _hero_moved(mem, wave) -> bool:
    return wave.get('hero_absent') is True or any(
        sortie['tick'] >= wave['tick'] and sortie.get('general') == NAME for _, sortie, _, _ in _march(mem))


def orders(mem) -> list[dict]:
    if not mechanical(mem):
        return []
    # A stale location mark must not keep the whole army out of the road for
    # good; the sortie itself re-reads the castle's live list.
    from ..os_layer.world import expire_unknown_locations
    expire_unknown_locations(mem)
    chapter = mem.get('chapter')
    owned = _owned(mem) & set(chart.castles(chapter))
    target = chart.corridor_next(chapter, owned)
    lost = set(mem.get('lost') or ())
    routes_by = {source: routes.path(mem, source, target, owned) for source in sorted(owned)}
    front = next((s for s, r in routes_by.items() if r and len(r) == 2), None)
    wave = _wave(mem, target, front)
    hero_at = _hero_castle(mem)
    attacks, moves, hero = [], [], []
    for source, route in routes_by.items():
        if not route or len(route) < 2:
            continue
        if len(route) == 2:
            purpose = 'retake' if target in lost else 'attack'
            hop = target
            if _attackers_marching(mem, target) >= SQUAD:
                continue                   # the vanguard is full; nobody waits for six
            # Owner 2026-11-12: 将軍のステータスによらず、どんどん上から順に将軍を
            # 送る（波状攻撃）。HPや戦闘力で出撃を見送ると、守備将軍に1人ずつ
            # 各個撃破されるだけで城は落ちない。待ち合わせ・冷却・強さ判定は
            # 攻撃側では行わない。
        else:
            purpose, hop = 'move', route[1]
        if purpose == 'move' and _moved(mem, wave, hop, source) >= SQUAD:
            continue                       # this capture's six already left
        if purpose == 'move' and _capture_guard_short(mem, source):
            # A castle we just took is retaken the moment we empty it: g1163
            # 2026-11-12 19:0x took ドンドン twice (wave wins) and lost it twice
            # inside the same window because the next wave left it empty.
            # Attacks still leave (the wave the owner asked for); only the
            # move out of a freshly taken castle waits for a second general.
            continue
        if _drained(mem, source, purpose):
            continue
        order = _order(mem, source, hop, target, purpose)
        (moves if purpose == 'move' else attacks).append((len(route), order))
    hero_order = _hero_post_order(mem, routes_by, hero_at, wave)
    if hero_order is not None:
        hero.append((2, hero_order))
    attacks.sort(key=lambda item: item[0])
    moves.sort(key=lambda item: item[0])      # nearest the front first
    out = [order for _, order in (*attacks, *moves, *hero)]
    # The budget bounds only the preemption: a probe may jump the queue while
    # real orders wait, not forever. With nothing else to do the tour is the
    # only thing that can find anybody, and an empty plan leaves the bot
    # standing on the field map (g1163 2026-11-12 13:1x: 169 consecutive
    # no-input map plans, five minutes without a single press).
    if out and not _probe_budget_left(mem):
        return out
    if not out and _attackers_marching(mem, target) > 0:
        # The wave is the work: while attackers are still marching at the next
        # castle an idle castle-list probe only spends a visit (g1163
        # 2026-11-12 19:1x-19:3x: 12 idle probes in half an hour during the
        # ドンドン wave). Let the wave run first.
        return out
    if not out and _sortie_in_flight(mem):
        # The active order is being launched right now (a selection or a
        # departure is open). Minting an idle probe here replaced that order on
        # the next observation: g1163 2026-11-12 14:2x minted 19 idle probes in
        # ten minutes while the march started only 12 orders, and the probes
        # were never executed.
        return out
    if not out and _idle_probe_pending(mem):
        # An idle probe that was already minted and not started yet is this
        # observation's work. Minting another one every frame was the churn
        # v713 measured (19 probes in ten minutes, none executed).
        return out
    if not out and not _idle_probe_due(mem):
        # Spacing the idle tour left the bot standing on the field map again
        # (g1163 2026-11-12 15:0x: 79 of 162 plans in five minutes were
        # no-input map frames). The tour is what keeps it moving; only the
        # launch-time churn needed the tighter rule, and that is handled by
        # ``_sortie_in_flight``.
        return out
    probe = _roster_probe(mem, routes_by, target, lost)
    decision = 'roster_probe'
    if probe is None and not out:
        probe = _drained_probe(mem, routes_by, target, lost)
        decision = 'drained_probe'
    if probe is None:
        if not out:
            _probe_exhausted_once(mem)
        return out
    idle = not out
    _probe_spend(mem, f'{decision}_idle' if idle else decision, idle=idle)
    if idle:
        mem[PROBE]['idle_step'] = probe['step']
    out.insert(0, probe)                # the re-read goes first: the tour is priority
    return out


def _sortie_in_flight(mem) -> bool:
    """A selection or a requested departure is open for the active order.

    Only a fresh attempt counts. A ``sortie_attempt`` that was never cleared
    (g1163 2026-11-12 14:4x: confirmation_tick 95774 still present at tick
    96376, ten minutes later) would otherwise suppress every idle probe for
    good, and with every road castle drained the plan went empty.
    """
    flow = _flow_region(mem)
    if flow.get('departure_request'):
        return True
    attempt = flow.get('sortie_attempt')
    if not isinstance(attempt, dict):
        return False
    since = attempt.get('confirmation_tick')
    if type(since) is not int:
        return False
    now = int(mem.get('tick') or 0)
    return 0 <= now - since <= SORTIE_ATTEMPT_TTL


def _idle_probe_pending(mem) -> bool:
    """An earlier idle probe is still waiting to be executed."""
    state = mem.get(PROBE)
    if not isinstance(state, dict):
        return False
    step = state.get('idle_step')
    if not isinstance(step, str):
        return False
    if step not in ((_plan_region(mem).get('orders') or {}) if isinstance(
            _plan_region(mem).get('orders'), dict) else {}):
        return False
    if _plan_region(mem).get('active') == step:
        return False                     # it is running: a fresh one may wait
    return True


def _idle_probe_due(mem) -> bool:
    """One idle probe per ``IDLE_PROBE_SPACING`` ticks, not one per observation."""
    state = mem.get(PROBE)
    if not isinstance(state, dict) or state.get('month') != mem.get('month'):
        return True
    last = state.get('idle_tick')
    if type(last) is not int:
        return True
    return int(mem.get('tick') or 0) - last >= IDLE_PROBE_SPACING


def _garrison_seen(mem) -> int:
    """Generals the castle lists actually showed (the probe's yield)."""
    world = _world(mem)
    return sum(len(members) for members in (world.get('garrison') or {}).values()
               if isinstance(members, list))


def _probe_budget_left(mem) -> bool:
    """Whether one more castle-list probe may be spent this month.

    A probe exists to find generals the accounting cannot place. Generals who
    are camped on the field are in no castle list at all, so the tour that
    used to run forever never found them and took every cycle: g1163
    2026-11-12 12:4x spent a quarter of an hour opening drained castles, read
    「しょうぐんはおりません」 each time and never reached a sortie. The tour is
    bounded per month, re-armed as soon as any castle list shows a general
    again (a recall or an arrival) or after ``PROBE_REARM_TICKS``.
    """
    state = mem.get(PROBE)
    if not isinstance(state, dict) or state.get('month') != mem.get('month'):
        return True
    if _garrison_seen(mem) > int(state.get('seen') or 0):
        return True
    if int(mem.get('tick') or 0) - int(state.get('tick') or 0) >= PROBE_REARM_TICKS:
        return True
    return int(state.get('count') or 0) < PROBE_BUDGET


def _probe_spend(mem, decision, idle=False):
    """Count one probe. An idle probe (nothing else to plan) is not budgeted."""
    state = mem.setdefault(PROBE, {})
    if state.get('month') != mem.get('month'):
        state.clear()
        state.update(month=mem.get('month'), count=0, seen=_garrison_seen(mem))
    if idle:
        state['idle_tick'] = int(mem.get('tick') or 0)
    else:
        state['count'] = int(state.get('count') or 0) + 1
    state['tick'] = int(mem.get('tick') or 0)
    state['seen'] = _garrison_seen(mem)
    state.pop('exhausted', None)
    _record_only(mem, decision,
                 observed_metric={'probe_count': state['count'],
                                  'probe_budget': PROBE_BUDGET,
                                  'idle': idle,
                                  'unaccounted': _unaccounted(mem)},
                 reason=('ほかに出す指示がないため、所在不明の将軍を城一覧で確認する'
                         if idle else
                         '所在不明の将軍を城一覧で確認する（月ごとの上限つき）'))


def _probe_exhausted_once(mem):
    state = mem.setdefault(PROBE, {})
    if state.get('exhausted'):
        return
    state['exhausted'] = True
    state.setdefault('month', mem.get('month'))
    _record_only(mem, 'corridor_probe_exhausted',
                 observed_metric={'probe_count': int(state.get('count') or 0),
                                  'probe_budget': PROBE_BUDGET,
                                  'unaccounted': _unaccounted(mem),
                                  'garrison_seen': _garrison_seen(mem)},
                 reason=('城一覧の確認を使い切ったため打ち切る。所在不明の将軍は'
                         '野営中の可能性が高く、城一覧では見つからない。帰還や入城で'
                         '城一覧に将軍が現れるか、月が変われば再開する'))


def _unaccounted(mem):
    """Employed generals whose location nobody can name.

    docich#3963 (g1163 2026-11-12 01:2x): the country held 32 generals while
    ``world.garrison`` listed 0 and ``general_location_unknown`` held 5, so
    27 were nowhere the plan could see. Accounting covers the three places a
    location can be recorded: the live list of a castle, the unknown list,
    and an in-flight march. A stale or unscoped count proves nothing.
    """
    count = mem.get('country_general_count')
    if not isinstance(count, dict) or type(count.get('count')) is not int:
        return 0
    if count.get('scope') != [mem.get('chapter'), mem.get('month')]:
        return 0
    world = _world(mem)
    known = {name for members in (world.get('garrison') or {}).values()
             if isinstance(members, list) for name in members}
    known.update(world.get('general_location_unknown') or ())
    for _, sortie, _, _ in _march(mem):
        if sortie.get('status') in MARCHING and sortie.get('general'):
            known.add(sortie['general'])
    return count['count'] - len(known)


def _roster_probe(mem, routes_by, target, lost):
    """Re-read a drained castle list while generals are unaccounted.

    When the country's general count exceeds ``in garrison + unknown +
    marching``, somebody is standing in a castle the plan stopped visiting.
    A drained mark is what stops the visit, so the oldest mark gets one
    re-read order ahead of the regular sorties; the visit either launches
    whoever is found or re-marks the castle, and the next cycle picks the
    next-oldest mark, rotating the tour through the castles (docich#3963).
    ``_drained`` already yields to a march that may still be on the road,
    so a castle expecting an arrival is not probed.
    """
    if _unaccounted(mem) <= 0:
        return None
    drained = {source: (mem.get(DRAINED) or {}).get(source)
               for source, route in routes_by.items()
               if route and len(route) >= 2
               and _drained(mem, source, 'move')}
    if not drained:
        return None
    source = min(drained, key=lambda c: drained[c].get('tick') or 0)
    route = routes_by[source]
    purpose = ('retake' if target in lost else 'attack') if len(route) == 2 else 'move'
    return _order(mem, source, route[1], target, purpose)


def _drained_probe(mem, routes_by, target, lost):
    """Visit the front castle when every owned castle is marked empty.

    A drained mark is cleared only by observing a general inside that castle,
    and the plan never visits a drained castle, so nothing can ever clear it:
    g1163 2026-11-12 01:2x had all three road castles drained while the country
    held 32 generals, and the plan produced no order at all. One visit per cycle
    reads the live list and either launches the mover or marks the castle empty
    again, so the position recovers by itself.
    """
    if not routes_by:
        return None
    routable = {source: route for source, route in routes_by.items()
                if route and len(route) >= 2}
    if not routable:
        return None
    if not all(_drained(mem, source, 'move') or _drained(mem, source, 'attack')
               for source in routable):
        return None                       # only the all-drained deadlock
    # Rotate: always probing the front castle left the rear ones unread forever
    # (g1163 2026-11-12 08:0x: アルマムーン held どうし+3 and the plan never looked,
    # because タオ is the nearest routable castle every time). The oldest mark is
    # read next, so every drained castle comes round again.
    marks = mem.get(DRAINED) or {}
    _, source = min((int((marks.get(source) or {}).get('tick') or 0), source)
                    for source in routable)
    route = routable[source]
    if len(route) == 2:
        purpose = 'retake' if target in lost else 'attack'
    else:
        purpose = 'move'
    return _order(mem, source, route[1], target, purpose)


# A lone attacker that lost to a nearly untouched defender cannot win alone:
# g1163 2026-11-12 sent single generals at オオキク and watched the defender read
# 18, 34 and 44 HP in a row (each fresh attacker died without denting it). Two
# marching attackers are the least that changes that outcome.
REPELLED_FLOOR_HP = 30







def _residents(mem, castle):
    world = _world(mem)
    garrison = world.get('garrison') or {}
    names = garrison.get(castle) if isinstance(garrison, dict) else None
    if isinstance(names, list) and names:
        return list(names)
    partial = world.get('garrison_partial') or {}
    names = partial.get(castle) if isinstance(partial, dict) else None
    return list(names) if isinstance(names, list) else []




CAPTURE_GUARD = 1               # generals kept in a castle captured moments ago


def _capture_guard_short(mem, castle) -> bool:
    """This castle was captured just now and holds only the guard.

    Emptying it lets the enemy walk back in (the flip the road kept paying
    for). A measured list is the only evidence; an unread castle is not empty.
    """
    now = int(mem.get('tick') or 0)
    captured_at = None
    for row in mem.get('sortie_history') or ():
        if not isinstance(row, dict) or row.get('target') != castle:
            continue
        tick = row.get('tick')
        if type(tick) is not int:
            continue
        if row.get('outcome') == 'won':
            captured_at = tick
        elif captured_at is not None and tick > captured_at:
            captured_at = None             # lost again: not a fresh capture
    if captured_at is None or now - captured_at > FLIP_WINDOW_TICKS:
        return False
    world = _world(mem)
    garrison = world.get('garrison') or {}
    names = garrison.get(castle) if isinstance(garrison, dict) else None
    if not isinstance(names, list):
        partial = world.get('garrison_partial') or {}
        names = partial.get(castle) if isinstance(partial, dict) else None
    if not isinstance(names, list) or len(names) > CAPTURE_GUARD:
        return False
    _record_only(mem, 'capture_guard_held', target=castle,
                 observed_metric={'residents': list(names)[:8], 'keep': CAPTURE_GUARD},
                 reason='取ったばかりの城を空にすると奪い返されるため、この城からの移動は控える')
    return True


def _repelled_alone(mem, target) -> bool:
    """The last attack on this castle lost against a barely-damaged defender."""
    history = [h for h in (mem.get('sortie_history') or [])
               if isinstance(h, dict) and h.get('target') == target]
    if not history:
        return False
    last = history[-1]
    enemy_hp = last.get('enemy_hp')
    return (last.get('outcome') == 'lost' and type(enemy_hp) is int
            and enemy_hp >= REPELLED_FLOOR_HP)


FLIP_WINDOW_TICKS = 900    # a capture lost again inside this window is a flip


def _flip_prone(mem, target) -> bool:
    """This castle was captured and lost again almost at once.

    g1163 2026-11-12 09:54/09:56: ドンドン was taken and lost again two minutes
    later, twice in the same afternoon. A lone attacker captures, moves on (or
    falls), and the enemy walks straight back in — the castle changes hands
    without the road advancing. Waiting for a second marching attacker costs a
    little time; losing the general and the castle costs both.
    """
    history = [h for h in (mem.get('sortie_history') or [])
               if isinstance(h, dict) and h.get('target') == target
               and type(h.get('tick')) is int]
    if len(history) < 2:
        return False
    last, before = history[-1], history[-2]
    if last.get('outcome') != 'lost' or before.get('outcome') != 'won':
        return False
    return 0 <= last['tick'] - before['tick'] <= FLIP_WINDOW_TICKS


HERO_POST_HOPS = 3          # the hero stands this many castles from the target


def _hero_post_order(mem, routes_by, hero_at, wave):
    """Move the hero to one road castle behind the front, never past it.

    Owner 2026-11-12: 主人公は前線の自城一つ前の自城に常駐したほうが良い。
    新しく将軍をやとったときに常に主人公のいる後方から送ることになる。
    The hero therefore walks forward only while he is behind that post, stays
    when he is on it, and steps back one hop when a previous wave left him in
    the front castle itself (where a recruit would arrive under the enemy).
    """
    if not hero_at or hero_at not in routes_by or _hero_moved(mem, wave):
        return None
    dists = {source: len(route) for source, route in routes_by.items() if route}
    here = dists.get(hero_at)
    if here is None:
        return None
    post = next((d for d in sorted(set(dists.values())) if d >= HERO_POST_HOPS), None)
    if post is None:
        return None                       # only the front and the target: stay
    if here > post:
        return _order(mem, hero_at, routes_by[hero_at][1], routes_by[hero_at][-1],
                      'move', hero=True)
    if here < post:
        behind = next((source for source, d in dists.items() if d == here + 1), None)
        if behind is None:
            return None
        return _order(mem, hero_at, behind, routes_by[hero_at][-1], 'move', hero=True)
    return None                           # already posted


def _record_only(mem, decision, **fields):
    """Record a decision without touching the plan (corridor holds)."""
    from ..decision_log import _record
    _record(mem, decision, **fields)


def _order(mem, source, hop, target, purpose, hero=False):
    order = {'step': _step(mem, source, hop, 'h' if hero else ''), 'general': chart_adjust.PROBE_GENERAL,
             'source': source, 'target': hop, 'cards': [], 'after': None,
             'purpose': purpose, 'source_probe': True, 'corridor': True,
             'note': (f'第9話: {source}から{target}へ最大{SQUAD}人' if hop == target
                      else f'第9話: {target}へ向けて{source}から{hop}へ最大{SQUAD}人移動')}
    if hero:
        order.update(corridor_hero=True, note=f'第9話: 城を取ったため主人公を{source}から{hop}へ進める')
    return order


def hero_absent(mem):
    """The hero order's live list did not show him: drop it for this wave."""
    wave = mem.get(WAVE)
    if isinstance(wave, dict):
        wave['hero_absent'] = True
