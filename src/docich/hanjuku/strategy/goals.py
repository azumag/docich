"""戦略層: durable deferred-goal queue.

Bounded deferrals and interruptions already record decision events, but the
intent itself was re-derived at each site (or dropped: ``month_sub_lost`` left
an 'opened' purchase unreachable for the rest of the month). ``mem['goals']``
materializes a ledger over the observed facts those sites already persist --
lost castles, broken eggs, depleted egg counts, roster shortage, empty
garrisons -- so an interrupted or deferred goal stays listed, ages, and is
re-offered to its existing owner whenever the surface is idle.

The queue never plans input and never authorizes a payment or a sortie: it
only records intent and attempt history. Every screen-evidence gate in the
owning flow still decides whether the goal may act.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart
from ..decision_log import _record
from ..os_layer.world import SORTIE_BUSY_TICKS, _owned


GOAL_LIMIT = 24            # bounded ledger; the fact stores stay the fallback
STARVE_ATTEMPTS = 6        # deferral outcomes per goal before a starved record
MAP_IDLE_WINDOW = 2        # observations a "nothing to sortie" reading stays current

# The fact store that keeps each kind's intent alive, and the flow that owns it:
#   repair      house_eggs[name].broken            -> flows/house.py session
#   egg_recover economy._egg_recovery_targets      -> flows/month.py sub
#   recruit     economy._recruit_shortage          -> flows/month.py sub
#   retake      mem['lost']                        -> replan interim candidates
#   move        garrison[castle] == []             -> replan interim candidates


def _goals(mem):
    goals = mem.get('goals')
    if not isinstance(goals, list):
        goals = mem['goals'] = []
    return goals


def _key(goal):
    return (goal.get('kind'), goal.get('actor'), goal.get('target'))


def _summary(goal):
    return {k: goal.get(k) for k in ('kind', 'actor', 'target', 'targets',
                                     'queued', 'attempts', 'outcome')}


def _departed(mem, chapter):
    """Generals a complete roster stopped listing, until a roster names them.

    A broken-egg fact outlives its general. g1097 2026-11-02: ゼウス left the
    army at 37-2 and his goal kept the repair dispatch open for hours. A scan
    clears ``complete`` while it runs, so the proof is remembered here rather
    than read off the receipt, or each scan would requeue the goal.
    """
    departed = set(mem.get('goal_departed') or ())
    roster = mem.get('recruit_roster') or {}
    if roster.get('chapter') == chapter:
        listed = set(roster.get('names') or ())
        departed -= listed
        if roster.get('complete') is True:
            departed |= {name for name, info in (mem.get('house_eggs') or {}).items()
                         if isinstance(info, dict) and info.get('broken') is True
                         and name not in listed}
    if departed:
        mem['goal_departed'] = sorted(departed)
    else:
        mem.pop('goal_departed', None)
    return departed


def _wanted(mem):
    """Intent keys the observed facts still support: ``{key: spec}``."""
    wanted = {}
    chapter = mem.get('chapter') or 0
    # Same chapter-map bound as ``interim_candidates``: a name outside the
    # measured map cannot be navigated to, so it is not a serveable want.
    try:
        castles = set(chart.castles(chapter))
    except AttributeError:
        castles = set()
    for castle in mem.get('lost') or ():
        if type(castle) is str and castle in castles:
            wanted[('retake', None, castle)] = {'kind': 'retake', 'target': castle}
    departed = _departed(mem, chapter)
    for name, info in (mem.get('house_eggs') or {}).items():
        if isinstance(info, dict) and info.get('broken') is True and name not in departed:
            wanted[('repair', name, None)] = {'kind': 'repair', 'actor': name}
    from .economy import _egg_recovery_targets, _recruit_shortage
    targets = _egg_recovery_targets(mem)
    if targets:
        wanted[('egg_recover', None, None)] = {'kind': 'egg_recover', 'targets': targets}
    try:
        shortage = _recruit_shortage(mem)
    except AttributeError:
        # A rebound chart without castle tables (the layer contract allows a
        # minimal namespace) cannot evaluate roster shortage: no recruit want.
        shortage = None
    if shortage:
        wanted[('recruit', None, None)] = {'kind': 'recruit'}
    owned = _owned(mem) & castles
    lost = set(mem.get('lost') or ())
    now = int(mem.get('tick') or 0)
    # A march already on the way to an empty castle is the move being served;
    # it stops counting as a want the way ``interim_candidates`` sees it.
    from .orders import _sortie_reserved_target
    heading = {_sortie_reserved_target(mem, step, s)
               for step, s in (mem.get('sorties') or {}).items()
               if s.get('status') in ('en_route', 'launched_unconfirmed')
               and s.get('tick') is not None
               and 0 <= now - int(s['tick']) < SORTIE_BUSY_TICKS} - {None}
    garrison = mem.get('garrison') or {}
    for castle in sorted(owned - lost - heading):
        if garrison.get(castle) == []:
            wanted[('move', None, castle)] = {'kind': 'move', 'target': castle}
    return wanted


def refresh(mem):
    """Reconcile the ledger with the observed facts. Idempotent per observation.

    A goal the facts still support keeps its queue position and age; one whose
    intent is satisfied (by any means, including ordinary play) leaves with a
    resolution record when it has a history worth closing. Newly observed
    wants append behind the existing queue.
    """
    wanted = _wanted(mem)
    goals = _goals(mem)
    tick = int(mem.get('tick') or 0)
    kept, resolved = [], []
    for goal in goals:
        spec = wanted.pop(_key(goal), None)
        if spec is not None:
            if spec.get('targets') is not None:
                goal['targets'] = spec['targets']
            kept.append(goal)
        else:
            resolved.append(goal)
    for goal in resolved:
        if goal.get('attempts') or goal.get('outcome'):
            _record(mem, 'goal_resolved', chart_step=None,
                    observed_metric=_summary(goal),
                    reason='延期・中断されていた目標が別の手段で達成されたか不要になったため台帳から外す')
    new_items = list(wanted.items())
    room = max(0, GOAL_LIMIT - len(kept))
    for key, spec in new_items[:room]:
        goal = {'kind': spec['kind'], 'actor': spec.get('actor'),
                'target': spec.get('target'), 'targets': spec.get('targets'),
                'queued': tick, 'attempts': 0}
        kept.append(goal)
        _record(mem, 'goal_queued', chart_step=None, observed_metric=_summary(goal),
                reason='観測済みの未達目標が延期・中断で失われないよう台帳へ登録する')
    dropped = new_items[room:]
    if dropped:
        if mem.get('goals_overflow') != len(dropped):
            mem['goals_overflow'] = len(dropped)
            _record(mem, 'goal_queue_full', chart_step=None,
                    observed_metric={'queued': len(kept), 'overflow': len(dropped),
                                     'dropped': [str(key) for key, _ in dropped[:4]]},
                    reason='目標台帳が上限のため新しい未達目標を登録できない。事実は各元データに残る')
    else:
        mem.pop('goals_overflow', None)
    if resolved or new_items:
        mem['goals'] = kept


def _find(mem, kind, *, actor=None, target=None):
    return next((g for g in _goals(mem)
                 if g.get('kind') == kind and g.get('actor') == actor
                 and g.get('target') == target), None)


def pending(mem, kinds=None):
    """Queued goals, oldest first; ``kinds`` filters by kind name(s)."""
    wanted_kinds = None if kinds is None else (
        {kinds} if isinstance(kinds, str) else set(kinds))
    return [g for g in _goals(mem)
            if wanted_kinds is None or g.get('kind') in wanted_kinds]


REPAIR_SLEEP_ATTEMPTS = 6   # failed repair dispatches before the goal sleeps


def _repair_signature(mem, actor):
    """Location facts that decide whether a repair session can reach the actor."""
    info = (mem.get('house_eggs') or {}).get(actor) or {}
    in_garrison = any(actor in (roster or [])
                    for roster in (mem.get('garrison') or {}).values())
    gold = mem.get('gold')
    bucket = (min(gold, 400) // 100) if type(gold) is int and gold >= 0 else None
    return [info.get('location'), info.get('month'), in_garrison, bucket]


def _repair_goal_due(mem, goal):
    """A failed repair sleeps until the actor's location facts change.

    Generals the session can never reach burn the whole idle map window:
    ソーピニヨン and ブカティーニ sit in castles we no longer own and ゼウス
    is stranded on the field after the failed boss sortie — 39/19/18
    dispatch cycles each ended at the observation limit, and every idle
    frame re-dispatched them, starving next_order of map time entirely.
    After REPAIR_SLEEP_ATTEMPTS the goal sleeps until the actor's location
    signature changes (returns to an owned garrison, a fresh status read
    moves it, or the purse crosses a gift bracket), then wakes with a
    fresh attempt budget.
    """
    if int(goal.get('attempts') or 0) < REPAIR_SLEEP_ATTEMPTS:
        return True
    sig = _repair_signature(mem, goal.get('actor'))
    previous = goal.get('sleep_signature')
    if previous == sig:
        return False
    if previous is None:
        goal['sleep_signature'] = sig
        _record(mem, 'repair_goal_sleeping', chart_step=None,
                observed_metric={**_summary(goal), 'signature': sig},
                reason='修理の試行が上限に達し、将軍の所在・駐留・所持金が変わるまで再ディスパッチを休眠')
        return False
    # Location facts changed: release the goal with a fresh attempt budget.
    goal.pop('sleep_signature', None)
    goal['attempts'] = 0
    return True


def repair_due(mem):
    """A deferred broken-egg repair is waiting for idle map time."""
    return any(g.get('kind') == 'repair' and _repair_goal_due(mem, g)
               for g in _goals(mem))


def note_map_idle(mem):
    """``map_step`` looked for an order, off-chart ones included, and found none."""
    mem['map_idle_tick'] = int(mem.get('tick') or 0)


def map_idle(mem):
    idle, tick = mem.get('map_idle_tick'), mem.get('tick')
    return type(idle) is int and type(tick) is int and 0 <= tick - idle <= MAP_IDLE_WINDOW


def repair_dispatch_due(mem):
    """A deferred repair may exceed the field scan budget only on idle map time.

    The house flow runs before ``map_step``, so a quiet frame is not idle time:
    g1097 2026-11-02 sent no sortie for about 4.6 hours while the bypass took
    every map frame. Only ``map_step`` can say that nothing is ready to sortie.
    """
    return map_idle(mem) and repair_due(mem)


def note_attempt(mem, kind, outcome, *, actor=None, target=None, reason=None, metric=None):
    """Count one deferral/interruption outcome against a live goal.

    Called beside the existing deferral records; a goal the facts no longer
    support is silently absent and the call is a no-op. Every
    ``STARVE_ATTEMPTS``-th unresolved outcome records ``goal_starved`` so a
    goal that keeps losing its turn is visible in the decision log.
    """
    goal = _find(mem, kind, actor=actor, target=target)
    if goal is None:
        return None
    goal['attempts'] = int(goal.get('attempts') or 0) + 1
    goal['outcome'] = outcome
    if reason:
        goal['reason'] = reason
    if goal['attempts'] % STARVE_ATTEMPTS == 0:
        _record(mem, 'goal_starved', chart_step=None,
                observed_metric={**_summary(goal), **(metric or {})},
                reason=f'{kind}の目標が{goal["attempts"]}回の延期・中断でも達成できていないため飢餓として記録')
    return goal
