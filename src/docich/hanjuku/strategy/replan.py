"""戦略層: off-chart replanning (interim orders and adjusted charts).

Builds interim candidates when the chart has no ready order, starts the first
of them without waiting for a model, adopts a validated adjusted chart when
one answers, and records every deviation.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart, hanjuku_chart_adjust as chart_adjust
from ..decision_log import _record
from ..os_layer.world import NAME, SORTIE_BUSY_TICKS, _en_route, _owned
from .orders import (_arrival_castle, _boss_egg_depleted, _distance, _hero_egg_broken,
                               _hero_source_alternative, _orders, _plan_pending, _ready, _reserved_targets, _source,
                               _source_location_recheck, _source_location_recheck_due, _unplaced_castle_generals)


def _interim_source(mem, target, chart_order, owned, busy):
    """(source, general) for an interim sortie to ``target``, or None.

    A castle whose general list we last read is used as measured: an empty one
    is never a source (g401: the chart general had already left キカンドン and
    the bot pressed A there for 14 minutes). Without a reading, fall back to
    the chart's source, then the home castle, as before.
    """
    chapter = mem.get('chapter') or 0
    home = chart.home_castle(chapter)
    garrison = mem.get('garrison') or {}
    busy = busy | set(mem.get('general_location_unknown') or ())
    unavailable_for_attack = {NAME} if _hero_egg_broken(mem) else set()
    free = {c: [g for g in garrison.get(c) or () if g not in busy] for c in owned}
    # Emptying a castle is how the undefended ジョンリギ fell (g401), so a
    # castle that keeps somebody behind is required, then the nearest goes.
    staffed = sorted((c for c in owned if len(set(free[c])) >= 2
                      and set(free[c]) - unavailable_for_attack),
                     key=lambda c: _distance(chapter, c, target))
    if staffed:
        present = [g for g in free[staffed[0]] if g not in unavailable_for_attack]
        present.sort(key=lambda g: g == NAME)          # risk the hero last
        return staffed[0], present[0]
    recheck = sorted((c for c in owned if _source_location_recheck_due(mem, c)),
                     key=lambda c: (_distance(chapter, c, target), c))
    if recheck:
        return recheck[0], MOVE_ANY_GENERAL
    unplaced = [g for g in _unplaced_castle_generals(mem)
                if g not in busy and g not in unavailable_for_attack]
    arrival = _arrival_castle(mem, owned)
    if unplaced and arrival in owned and _source_location_recheck(mem, arrival) is None:
        # Joiners stand in the hero's castle; the sortie list is the reading
        # and still cancels if fewer than two are really there.
        return arrival, unplaced[0]
    general = chart_order['general'] if chart_order else NAME
    if general in busy or general in unavailable_for_attack:
        return None
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


INTERIM_CARDS = ('イッテツーン', 'イッテツーン')   # same opener as a lost melee's retry


def interim_candidates(mem) -> dict:
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
    lost.sort(key=lambda c: c != home)          # a lost home castle is retaken first
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
        picked = _interim_source(mem, target, chart_orders.get(target), owned, busy)
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
    empty = [c for c in sorted(owned, key=lambda c: _distance(chapter, home, c))
             if garrison.get(c) == [] and c not in heading]
    for target in empty:
        # A castle whose list was never read may hold several generals: the
        # bot reads lists only when it sorties, so g407 knew one castle while
        # the stream showed crowded ones. Opening its list for the move is
        # the reading; with fewer than two there the move is cancelled
        # (deploy_step), which records the list and drops the donor.
        known = [c for c in owned if c != target
                 and len([g for g in garrison.get(c) or () if g not in busy]) >= 2]
        unread = [c for c in owned if c != target and garrison.get(c) is None]
        donors = (sorted(known, key=lambda c: _distance(chapter, c, target))
                  + sorted(unread, key=lambda c: _distance(chapter, c, target)))
        if not donors:
            continue
        spare = [g for g in garrison.get(donors[0]) or () if g not in busy]
        spare.sort(key=lambda g: g == NAME)
        general = spare[0] if spare else MOVE_ANY_GENERAL
        moves.append({
            'general': general, 'target': target, 'cards': [], 'source': donors[0],
            'after': None, 'purpose': 'move',
            'note': f"暫定: {general}を{donors[0]}から空の{target}へ移動"})
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


def _mechanical_interim(mem, state, rid, digest):
    """Start the next deterministic sortie while no adjusted plan is pending.

    No model answers this: the first candidate from ``interim_candidates``
    goes out once the previous interim order is no longer waiting to start.
    An adjusted chart from the worker still replaces it when it is adopted.
    """
    interim = state.get('interim_order')
    status = mem.get('orders') or {}
    busy = (interim and status.get(interim['step']) in (None, 'pending')
            and _source(interim, mem) in _owned(mem))
    if busy:
        return
    candidates = interim_candidates(mem)
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
        _record(mem, 'chart_adjust_request', chart_step=None, strategy_variant='chart_adjust_pending',
                **fields,
                reason='チャート外: 状況が更新されたため同じ要求を最新の在庫・配置で再要求')
    doc = mem.get('_adjusted')
    plan = mem.get('chart_plan') or {}
    doc_digest = doc.get('request_digest') if doc else None
    if (doc and doc.get('request_id') == rid and doc.get('chapter') == mem.get('chapter')
            and plan.get('request_id') != rid
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
    _mechanical_interim(mem, state, rid, digest)
