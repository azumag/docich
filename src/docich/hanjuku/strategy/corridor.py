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
  generals (the 「増援として数人」 of docich#3957) toward the front;
* on each capture the hero is sent to the castle that just fell
  (docich#3957, owner 2026-11-12: 城が取れたタイミングで主人公をその城に送る).
  While he sits anywhere behind the front his order is the next leg toward
  it — a march already on the road is the only pause, so he converges on
  the front instead of advancing a single hop per wave;
* a wave whose front castle showed an empty list with no attacker marching
  opens a fresh budget, so a wiped vanguard never deadlocks the road;
* an attack never takes the hero's last companion from his castle.

The boss phase (every road castle ours) keeps the normal flows.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust
from ..os_layer.world import _owned
from ..os_layer.world import NAME, SORTIE_BUSY_TICKS, _hero_castle
from ..state.regions import plan as _plan_region, sorties as _sorties
from . import routes

PREFIX = 'I:c9:'
DRAINED = 'corridor_drained'
WAVE = 'corridor_wave'
SQUAD = 6
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
    if not isinstance(entry, dict) or entry.get('month') != mem.get('month'):
        return False
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
        # Past the arrival window the general's whereabouts are unknown, so let
        # the plan read the live list once more and mark the castle again.
        if sortie.get('status') == 'arrived' or now - tick >= ARRIVAL_RECHECK_TICKS:
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
    from .orders import _arrival_limit
    now = int(mem.get('tick') or 0)
    return sum(1 for _, sortie, _, hop in _march(mem)
               if hop == target and sortie.get('purpose') != 'move'
               and sortie.get('status') in MARCHING
               and 0 <= now - sortie['tick'] < max(SORTIE_BUSY_TICKS, _arrival_limit(mem, sortie)))


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


def _hero_marching(mem) -> bool:
    """Whether a corridor march carrying the hero is still on the road.

    docich#3957 (owner 2026-11-12): on each capture the hero is sent to the
    castle that just fell, so he keeps walking toward the front instead of
    moving a single hop per capture. A march already in flight is the only
    reason to hold his next leg; a sortie past its arrival window is treated
    as unknown, exactly like ``_attackers_marching`` treats stale marches.
    """
    from .orders import _arrival_limit
    now = int(mem.get('tick') or 0)
    return any(sortie.get('general') == NAME and sortie.get('status') in MARCHING
               and 0 <= now - sortie['tick'] < max(SORTIE_BUSY_TICKS, _arrival_limit(mem, sortie))
               for _, sortie, _, _ in _march(mem))


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
        else:
            purpose, hop = 'move', route[1]
        if (source == hero_at and purpose == 'move' and not wave.get('hero_absent')
                and not _hero_marching(mem)):
            hero.append((len(route), _order(mem, source, hop, target, 'move', hero=True)))
        if purpose == 'move' and _moved(mem, wave, hop, source) >= SQUAD:
            continue                       # this capture's six already left
        if _drained(mem, source, purpose):
            continue
        order = _order(mem, source, hop, target, purpose)
        (moves if purpose == 'move' else attacks).append((len(route), order))
    attacks.sort(key=lambda item: item[0])
    moves.sort(key=lambda item: item[0])      # nearest the front first
    # docich#3957: the hero goes to the fallen castle before its reinforcements.
    return [order for _, order in (*attacks, *hero, *moves)]


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
