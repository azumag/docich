"""Bounded, screen-driven broken-egg repair for every friendly general.

No ROM, emulator I/O, clocks or provider calls. The caller supplies the same
parsed native frame as the ordinary policy and persists ``mem['house']``.
Coordinates and menu paths are measured in the isolated libretro probe.
"""
from __future__ import annotations

import re

from ..os_layer.font import UNKNOWN
from ...hanjuku_egg_stock import observed_uses
from ..os_layer import roster as receipts
# World facts shared with the economy strategy live in the OS layer.
from ..os_layer.world import HOUSE_VIEW, _castle_status_after_sortie, _repair_hp_ready

# The heart's centre and the selectable cell differ by half a view pixel
# (four field pixels). Chapter 2's successful isolated trip used (128, 94).
HOUSE_TARGET = {chapter: (int(x), int(y)) for chapter, (x, y) in HOUSE_VIEW.items()}
GIFTS = (('スカーフ', 200), ('みずぎ', 100), ('ピアス', 50))
ROSTER_LIMIT = receipts.MAX_GENERALS
STEP_LIMIT = 24
TRAVEL_LIMIT = 240
SESSION_LIMIT = 1200
SCAN_INTERVAL = 200
INTERRUPTION_RETRIES = 2
# Conservative route envelope, in whole-map view pixels (8 = 64 field pixels).
# This covers a castle footprint/localisation error and either axis-first route;
# it is not a measured terrain path or permission to invent detour waypoints.
HOUSE_ROUTE_CLEARANCE = 8
PRE_DISPATCH_PHASES = frozenset({
    'find_castle', 'castle_view', 'castle_open', 'castle_verify',
    'castle_sortie', 'castle_pick', 'castle_kit', 'castle_confirm'})


def general_status(screen):
    """Read the named general and egg row, never equate zero uses with broken.

    Main-menu panels use x=24/88, y=39 and egg y=119. Field/sortie panels
    use x=16/80, y=31 and egg y=111. Unknown tiles invalidate the egg row.
    """
    for x, y in ((24, 39), (16, 31)):
        row = next((r for r in screen.lines if r.y == y), None)
        egg = next((r for r in screen.lines if r.y == y + 80), None)
        if row is None or egg is None:
            continue
        name = row.span(x, x + 64).strip()
        if (not name or UNKNOWN in name or re.search(r'[\d\s]', name)
                or row.span(x + 64, x + 112) != 'しょうぐん'):
            continue
        text = egg.span(x, x + 112).replace(' ', '')
        if UNKNOWN in text or not text.startswith('たまご'):
            continue
        body = text[len('たまご'):]
        broken = body == 'こわれている'
        # Angelin can leave five uses. Never fold extra digits into the egg name.
        uses = re.fullmatch(r'([^\d]+)([0-9])', body)
        if uses and observed_uses(int(uses[2])) is None:
            uses = None
        if not broken and body != 'なし' and not uses:
            continue
        hp_row = next((r for r in screen.lines if r.y == y + 16), None)
        hp_text = hp_row.span(x + 40, x + 112) if hp_row else ''
        hp_read = re.fullmatch(r'(\d+)\s*/\s*(\d+)\s*P?', hp_text)
        hp = int(hp_read[1]) if hp_read else None
        max_hp = int(hp_read[2]) if hp_read else None
        if hp is not None and not 0 <= hp <= max_hp:
            hp, max_hp = None, None
        wage_value = receipts.fixed_wage(name)  # stable salary; no extra individual screen
        camp = (x == 24 and y == 39 and any(r.y == 151
                and r.span(24, 232).replace(' ', '') == 'キャンプしています' for r in screen.lines))
        return {'general': name, 'broken': broken, 'hp': hp, 'max_hp': max_hp, 'wage': wage_value,
                'egg': uses[1] if uses else None, 'uses': int(uses[2]) if uses else None,
                **({'camp_observed': True} if camp else {}),
                'location': 'castle' if 'しろのなかにいます' in screen.text else 'field',
                'location_observed': camp or any(phrase in screen.text for phrase in
                                         ('しろのなかにいます', 'いどうしています'))}
    return None


def roster(screen):
    if 'Aボタンでステータスひょうじ' not in screen.text or not screen.hand:
        return None
    names = _names_at(screen, 168, 39, 151)
    if not names or any(UNKNOWN in n or re.search(r'[\d\s]', n) for n in names):
        return None
    # An unread unselected name must not block a positively identified row.
    # page() still rejects the whole page for membership/payroll accumulation;
    # the selected person's actual status must confirm its name before reuse.
    if (any(receipts.fixed_wage(n) is None for n in names)
            and (screen.selected not in names or receipts.fixed_wage(screen.selected) is None)):
        return None
    return names


def _singleton_page(screen, frame):
    """A fully drawn first row and physically empty remaining name slots.

    OCR's one readable name is insufficient: unknown or undrawn pixels in
    any other slot must not masquerade as a one-member global list.
    """
    from ..os_layer.phase import green
    return (frame is not None and roster(screen) == ['どうし']
            and screen.selected == 'どうし' and screen.hand[1] == 33
            and all(green(*frame.pixel(x, y))
                    for y in range(47, 159) for x in range(168, 232)))


def _roster_scroll_end(screen, frame):
    """Native final eight-row page: up arrow present, down slot empty.

    A stopped cursor or OCR alone is insufficient. Both the visible arrow
    and the green empty down-arrow slot must be drawn on this same frame.
    """
    from ..os_layer.phase import green
    names = roster(screen)
    return (frame is not None and frame.width == 256 and frame.height == 224
            and names is not None and len(names) == 8
            and screen.selected == names[-1] and screen.hand[1] in (145, 147)
            and all(min(frame.pixel(x, y)) >= 240
                    for y in range(30, 34) for x in range(195 - (y - 30), 196 + (y - 30)))
            and all(green(*frame.pixel(x, y))
                    for y in range(159, 162) for x in range(192, 199))
            and all(max(frame.pixel(x, 162)) <= 8 for x in range(192, 199))
            and all(170 <= frame.pixel(x, 163)[0] <= 220
                    and 110 <= frame.pixel(x, 163)[1] <= 170
                    and 50 <= frame.pixel(x, 163)[2] <= 100
                    for x in range(192, 199)))


def _names_at(screen, x, y0, y1):
    from ..os_layer.readers import _name_read_cleanly
    names = []
    for row in screen.lines:
        if not y0 <= row.y <= y1 or (row.y - y0) % 16:
            continue
        name = dict(row.spans()).get(x)
        if dict(row.cells).get(x) == UNKNOWN:
            return []
        if name:
            if not _name_read_cleanly(row, name):
                return []
            names.append(name)
    return names


def gift_prices(screen):
    listed = {}
    for row in screen.lines:
        if row.y not in (167, 183, 199):
            continue
        text = row.span(168, 232).replace(' ', '')
        match = re.fullmatch(r'(ピアス|みずぎ|スカーフ)(\d+)G', text)
        if match:
            listed[match[1]] = int(match[2])
    return listed if listed == dict(GIFTS) else None


def affordable_gift(gold, reserve):
    if type(gold) is not int:
        return None
    return next(((name, cost) for name, cost in GIFTS if gold >= cost + reserve), None)


def _record(mem, event, **fields):
    from ..decision_log import _record
    state = mem.get('house') or {}
    _record(mem, 'house_' + event, chart_step=None, general=state.get('general'),
            strategy_variant='broken_egg_repair', **fields)


def _phase(state, phase, **fields):
    state.update(phase=phase, age=0, **fields)


def _choose(screen, label, *, mem=None):
    from ..os_layer.commands import guarded_menu_to, menu_to, pad
    # g604 (2026-10-06): the monthly survey pressed the same direction at a
    # cursor box that never moved for hours. The monthly entry uses a bounded
    # guard; other house phases retain their own existing step limits.
    move = (guarded_menu_to(screen, mem, label, key='house_nav')
            if mem is not None else menu_to(screen, label))
    if isinstance(move, list):
        return move
    return [pad('a')] if move == 'here' else [move] if move else []


def _exit(mem, reason, *, limit=None):
    state = mem['house']
    if state['phase'].startswith('income_') or state['phase'] in {
            'month_open', 'open_roster', 'roster', 'status', 'roster_next', 'roster_advance', 'leave_roster'}:
        mem['recruit_roster_recheck'] = True
        mem['recruit_hold'] = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
                              'status': 'observation_failed', 'reason': reason}
    _record(mem, 'deferred', reason=reason, observed_metric={
        'phase': state['phase'], 'age': state.get('age'), 'total': state.get('total'),
        'limit': limit})
    _phase(state, 'close')
    return []


def _finish(mem):
    # A trip that ends without a seen arrival leaves its bookkeeping sortie
    # looking like a marching army. target=None then matches every castle in
    # _bind_sortie, which claimed キカンドン as this sortie's arrival at
    # 2026-10-01 07:08 and could turn later arrivals ambiguous. Close them
    # here; a successful trip already recorded them as 'recalled'.
    stale = [key for key, sortie in (mem.get('sorties') or {}).items()
             if sortie.get('purpose') == 'house'
             and sortie.get('status') in ('en_route', 'launched_unconfirmed')]
    for key in stale:
        mem['sorties'][key]['status'] = 'closed'
    if stale:
        _record(mem, 'sortie_closed', observed_metric={'sorties': stale},
                reason='到着を確認できないまま卵修理を終了したため仮の出撃を閉じて入城表示との誤結合を防ぐ')
    state = mem.pop('house', None) or {}
    # The free monthly roster check never completes or delays field repair.
    # Only a field transaction in its original chapter/month owns this marker.
    scope = [mem.get('chapter'), mem.get('month')]
    if (not state.get('month_scan') and state.get('chapter', scope[0]) == scope[0]
            and state.get('roster_month', scope[1]) == scope[1]):
        tick = int(mem.get('tick') or 0)
        mem['house_scan_tick'] = tick
        mem['house_scan_month'] = scope[1]
        mem['house_field_scan'] = {'scope': scope, 'tick': tick}
    mem['uncertain'] = True
    for key in ('near_goal', 'nav_last', 'anchor', 'expect_menu'):
        mem.pop(key, None)


def _field_budget(mem, scope):
    """Separate field budget, including a bounded migration of v121 state.

    v121's shared counter includes every monthly attempt. Both counters carry
    chapter/month scope, so subtract only matching, nonnegative integer counts.
    Freeze before any new monthly increment; the new counter thereafter remains
    independent of monthly checks, including the legacy marker provenance.
    """
    current = mem.get('recruit_field_scan_attempts') or {}
    if current.get('scope') == scope:
        return current.get('count', 0), current.get('legacy_monthly', False)
    def count(key):
        row = mem.get(key) or {}
        value = row.get('count')
        return value if row.get('scope') == scope and type(value) is int and value >= 0 else 0
    monthly = count('recruit_month_scan_attempts')
    field_count = min(2, max(0, count('recruit_roster_attempts') - monthly))
    current = {'scope': scope, 'count': field_count}
    if monthly:
        current['legacy_monthly'] = True
    mem['recruit_field_scan_attempts'] = current
    return field_count, bool(monthly)


def _survey_work(mem):
    """Repairs and payroll income are what another roster walk would add.

    The free monthly read records broken eggs and full wages but never leaves
    the monthly menu, so it cannot dispatch a repair or refresh a payroll
    receipt. Those two jobs, and only those, justify a second walk.
    """
    from ..os_layer.readers import STATUS_NAMES
    from ..os_layer.world import _owned
    from ..strategy.economy import _recruit_shortage
    month = mem.get('month')
    if receipts.recheck_targets(mem):
        return True
    if any(info.get('broken') and info.get('month') == month
           for info in (mem.get('house_eggs') or {}).values()):
        return True
    if _recruit_shortage(mem) is None:
        return False
    owned = sorted(_owned(mem))
    return any(receipts.economics(mem, [STATUS_NAMES.get(c, c)]) is None
               for c in owned)


def _observe_status(screen, mem):
    info = general_status(screen)
    if info:
        name = info['general']
        mem.setdefault('house_eggs', {})[name] = {
            **info, 'month': mem.get('month'), 'observed_chapter': mem.get('chapter'),
            'observed_tick': mem.get('tick'),
            'observed_run_identity': receipts.run_identity(mem.get('_run_identity'))}
        if info['uses'] is not None:
            mem.setdefault('egg_uses', {})[name] = info['uses']
            mem.setdefault('egg_types', {})[name] = info['egg']
        receipts.status_observed(mem, mem['house_eggs'][name])
        from ..strategy.economy import _hero_repair_defer_reason
        _hero_repair_defer_reason(mem)
        _record(mem, 'egg_seen', observed_metric=info, reason='将軍のステータスで卵の状態を確認')
    return info


def _view_move(screen, mem, frame, target, next_phase, tolerance=0.6):
    from ..os_layer.commands import pad
    from ..os_layer.readers import world_cursor, world_flags
    from ..os_layer.world import _apply_world_flags
    state = mem['house']
    if screen.kind != 'world_map':
        return []
    cursor = world_cursor(frame)
    if cursor is None:
        return []
    flags = world_flags(frame, mem.get('chapter'), cursor)
    if flags:
        _apply_world_flags(mem, flags)
    dx, dy = target[0] - cursor[0], target[1] - cursor[1]
    if abs(dx) <= tolerance and abs(dy) <= tolerance:
        if next_phase == 'house_confirm' and _defer_unsafe_house_route(mem):
            return []
        _phase(state, next_phase)
        return [pad('a')]
    return [pad('right' if dx > 0 else 'left', min(56, max(1, round(abs(dx) * 2))))] if abs(dx) > tolerance else [
        pad('down' if dy > 0 else 'up', min(56, max(1, round(abs(dy) * 2))))]


def _castle_view(mem, name):
    from ... import hanjuku_chart as chart
    from ..os_layer.navigation import Y_JUMP_OFFSET
    x, y = chart.castles(mem['chapter'])[name]
    ox, oy = Y_JUMP_OFFSET[mem['chapter']]
    return x / 8 + ox, y / 8 + oy


def _house_route_problem(mem):
    """Reject unsafe/unknown repair routes; never use the destination cursor as origin.

    The game chooses the actual marching path. A line-only intersection test
    misses axis-first movement, so use the expanded source/destination box.
    We have no verified waypoint execution for repairs: a castle in that
    envelope must have a recent own flag, otherwise defer rather than attack
    it or guess a bypass. Field generals without a named source are unknown.
    """
    from ... import hanjuku_chart as chart
    from ..os_layer.navigation import Y_JUMP_OFFSET
    from ..os_layer.world import WORLD_FLAG_TRUST, _owned
    state = mem['house']
    chapter = mem.get('chapter')
    source = state.get('source')
    castles = chart.castles(chapter)
    expected = chart.CASTLE_NAMES.get(chapter)
    if (chapter not in HOUSE_TARGET or chapter not in Y_JUMP_OFFSET
            or not expected or not set(expected) <= castles.keys()):
        return '修理ルートの城配置が未確認のため派遣を保留', []
    if source not in castles:
        return '修理対象の出発地点が未確認のため派遣を保留', []
    owned = _owned(mem)
    flags = mem.get('world_flags') or {}
    source_flag = flags.get(source) or {}
    if (source not in owned or (source_flag.get('chapter') == chapter
                               and source_flag.get('kind') == 'enemy')):
        return '修理の出発城が自軍と確認できないため派遣を保留', [source]
    start, target = _castle_view(mem, source), HOUSE_TARGET[chapter]
    margin = HOUSE_ROUTE_CLEARANCE
    bounds = [(min(a, b) - margin, max(a, b) + margin) for a, b in zip(start, target)]
    now = mem.get('tick')
    blocked = []
    for castle in castles:
        if castle == source:
            continue
        position = _castle_view(mem, castle)
        if not all(lo <= value <= hi for value, (lo, hi) in zip(position, bounds)):
            continue
        seen = flags.get(castle) or {}
        observed = seen.get('tick')
        own = (castle in owned and seen.get('kind') == 'own'
               and seen.get('chapter') == chapter
               and type(now) is int and type(observed) is int
               and 0 <= now - observed <= WORLD_FLAG_TRUST)
        if not own:
            blocked.append(castle)
    if blocked:
        return '修理ルートに敵城または所属未確認の城があり、確実な迂回を確認できないため派遣を保留', sorted(blocked)
    return None


def _defer_unsafe_house_route(mem):
    problem = _house_route_problem(mem)
    if problem is None:
        return False
    reason, blocked = problem
    state = mem['house']
    # A serialized/partially prepared movement intent is not an issued trip.
    # Close through the existing bounded cancel path and retain the broken egg
    # observation; _finish applies the normal field-scan cooldown.
    state.pop('departure_pending', None)
    _record(mem, 'route_deferred', source=state.get('source'),
            observed_metric={'blocked_castles': blocked,
                             'target': HOUSE_TARGET.get(mem.get('chapter')),
                             'clearance_view_pixels': HOUSE_ROUTE_CLEARANCE},
            reason=reason)
    _exit(mem, reason)
    return True


def _next_general(mem):
    from ... import hanjuku_chart as chart
    from ..os_layer.world import _owned
    from ..strategy.economy import WAGE_SPEND_HOLD
    state = mem['house']
    pending = state.get('pending') or []
    if not pending:
        _phase(state, 'close')
        return
    if not affordable_gift(mem.get('gold'), WAGE_SPEND_HOLD):
        # Without this record an unaffordable trip closes silently and the
        # owner sees "the general never entered the house" with no evidence
        # (g530 2026-10-01 09:20:30: ゼウス broken, gold≈18-31 < 80).
        _record(mem, 'general_deferred',
                observed_metric={'gold': mem.get('gold'), 'pending': list(pending),
                                 'need': min(cost for _, cost in GIFTS) + WAGE_SPEND_HOLD},
                reason='所持金では破損卵の修理費（家の贈り物）を賄えないため派遣せず保留（次スキャンで再判定）')
        _phase(state, 'close')
        return
    name = pending.pop(0)
    info = (mem.get('house_eggs') or {}).get(name) or {}
    state.update(general=name, pending=pending, source=None, purchased=False, purchase=None,
                 returning=False)
    state.pop('return_goal', None)
    state.pop('sole_defender', None)
    # Re-routing a marching army was measured not to reach the house: the
    # 2026-10-01 06:48 dispatch re-aimed ヴィーナス while en_route, and she
    # instead charged キカンドン at 07:08 instead of the house. Wait for her
    # arrival instead of spending the session on a destination the game
    # ignores; the next scan retries her.
    marching = next((s for s in (mem.get('sorties') or {}).values()
                     if s.get('general') == name
                     and s.get('status') in ('en_route', 'launched_unconfirmed')
                     and not _castle_status_after_sortie(info, s, mem)), None)
    if marching:
        _record(mem, 'general_deferred',
                observed_metric={'status': marching.get('status'),
                                 'target': marching.get('target')},
                reason='行軍中の将軍はいどう先を到着まで確定できないため派遣を保留')
        _next_general(mem)
        return
    if info.get('location') == 'castle':
        owned = _owned(mem) & chart.castles(mem['chapter']).keys()
        known = [c for c in sorted(owned) if name in (mem.get('garrison') or {}).get(c, [])]
        state['castles'] = known + [c for c in sorted(owned) if c not in known]
        _phase(state, 'find_castle')
    else:
        _phase(state, 'find_field')


def _month_count(screen, mem, state, frame):
    """Monthly roster count: names from the list and scroll only (Issue #2367).

    The monthly check needs the number of resident generals, not their HP,
    egg type, remaining uses or location. Every visible name on each fresh
    page is merged into this scan; no status screen is opened. A page shorter
    than the native eight rows ends the list on screen, a wrapped cursor proves
    a full walk and the native scroll-end frame proves the last page. An
    unproven end leaves ``complete`` false and the existing retry cap applies.
    """
    from ..os_layer.commands import pad
    names = roster(screen)
    if names is None or screen.selected not in names:
        return []
    selected = screen.selected
    if not state.get('seen'):
        state['roster_first_general'] = selected if selected == 'どうし' and screen.hand[1] == 33 else None
        state['singleton_first_page'] = _singleton_page(screen, frame)
    for name in names:
        if name not in state.get('seen', []):
            state.setdefault('seen', []).append(name)
    if _roster_scroll_end(screen, frame):
        state['roster_end_observed'] = True
        _phase(state, 'leave_roster')
        return [pad('b')]
    if (len(state['seen']) >= 2 and state.get('month_moved')
            and selected == state['seen'][0]):
        state['roster_wrapped'] = True
        _phase(state, 'leave_roster')
        return [pad('b')]
    if len(names) < receipts.VISIBLE_NAME_ROWS:
        state['roster_end_observed'] = True
        _phase(state, 'leave_roster')
        return [pad('b')]
    state['selected'] = selected
    state['month_moved'] = True
    _phase(state, 'roster_advance')
    return [pad('down')]


def _month_count_advance(screen, mem, state, frame):
    """Follow a list scroll without opening any general's status screen."""
    from ..os_layer.commands import pad
    names = roster(screen)
    if names is None or screen.selected not in names:
        state['month_advance_observations'] = 0
        return []
    if screen.selected != state.get('selected'):
        state.pop('month_advance_observations', None)
        _phase(state, 'roster')
        return []
    observations = int(state.get('month_advance_observations') or 0) + 1
    state['month_advance_observations'] = observations
    if observations >= 2:
        # A stalled cursor is not proof of the list end: the native
        # continuation page g749-11428 shows a full eight-row page whose
        # Down has not moved yet. Only a wrap, a short page or the native
        # scroll-end frame proves the end. Leave incomplete otherwise.
        _record(mem, 'month_roster_end_unproven',
                observed_metric={'generals': list(state.get('seen') or []),
                                 'observations': observations,
                                 'selected': screen.selected,
                                 'last_visible': names[-1]},
                reason='月次の一覧でスクロール端の証拠を得られないため、読めた名前だけを残して未完了で終了')
        _phase(state, 'leave_roster')
        return [pad('b')]
    return [pad('down')]


def _resume_completed_survey(screen, mem, scope, field_count, field_tick):
    from ..os_layer.commands import pad
    from ..os_layer.readers import STATUS_NAMES
    from ..os_layer.world import _owned
    from ..strategy.economy import _recruit_shortage
    survey = receipts.reusable_survey(mem)
    if survey is None:
        return False, None
    if (screen.kind != 'map' or mem.get('chapter') not in HOUSE_VIEW
            or any(mem.get(k) for k in ('active', 'recall', 'y_jump', 'sortie_attempt', 'month_sub', 'battle'))):
        return True, None
    dirty = receipts.recheck_targets(mem)
    pending = [name for name, info in survey['statuses'].items()
               if info.get('broken') is True and 'egg' not in survey.get('dirty', {}).get(name, [])]
    missing = ([c for c in sorted(_owned(mem))
                if receipts.economics(mem, [STATUS_NAMES.get(c, c)]) is None]
               if _recruit_shortage(mem) and not mem.get('recruit_payroll_pending') else [])
    if not dirty and not pending and not missing:
        return True, None  # returning to the field is not a status change
    if field_count >= 2 or int(mem.get('tick') or 0) - int(field_tick) < SCAN_INTERVAL:
        return True, None
    state = mem['house'] = {'phase': 'open_roster' if dirty else 'income_next' if missing else 'close',
        'chapter': mem['chapter'], 'roster_month': scope[1], 'month_scan': False,
        'age': 0, 'total': 0, 'seen': [], 'pending': pending, 'from_survey': True,
        'status_targets': dirty, 'income_castles': missing, 'income_failures': []}
    mem['recruit_field_scan_attempts']['count'] = field_count + 1
    _record(mem, 'survey_reused', observed_metric={'status_targets': dirty,
            'broken': pending, 'missing_incomes': missing},
            reason='同月・同ランで読めた本人の情報を引き継ぎ、変化した本人や未読の城収入だけ確認する')
    if dirty:
        return True, [pad('x')]
    if not missing:
        _next_general(mem)
    return True, []


def _retry_target_current(mem):
    state = mem['house']
    name = state.get('general')
    info = (mem.get('house_eggs') or {}).get(name) or {}
    observed, now = info.get('observed_tick'), mem.get('tick')
    return (isinstance(name, str) and name and UNKNOWN not in name
            and isinstance(mem.get('month'), str) and bool(mem['month'])
            and mem.get('chapter') in HOUSE_VIEW
            and info.get('general') == name and info.get('broken') is True
            and info.get('location') == 'castle' and info.get('location_observed') is True
            and _repair_hp_ready(info) and not state.get('month_scan')
            and state.get('chapter') == info.get('observed_chapter') == mem.get('chapter')
            and state.get('roster_month') == info.get('month') == mem.get('month')
            and type(observed) is int and type(now) is int and 0 <= observed <= now
            and not any(state.get(k) for k in ('purchase', 'purchased', 'returning', 'departure_pending')))


def _retry_interrupted_repair(mem):
    state = mem['house']
    retries = state.get('interruption_retries', 0)
    if (state['phase'] not in PRE_DISPATCH_PHASES or not _retry_target_current(mem)
            or type(retries) is not int or not 0 <= retries < INTERRUPTION_RETRIES
            or state.get('total', 0) >= SESSION_LIMIT):
        return False
    prior = state['phase']
    _phase(state, 'repair_recheck', interruption_retries=retries + 1)
    state.pop('sole_defender', None)
    _record(mem, 'interruption_retry', observed_metric={
        'phase': prior, 'retry': retries + 1, 'limit': INTERRUPTION_RETRIES,
        'total': state.get('total')},
        reason='出撃前の修理が割り込まれたため、マップ復帰後に城・本人・卵・HPを再確認する')
    return True


def step(screen, mem, frame):
    """Actions, or None to let battle/month/ordinary map policy run.

    A repair owns only its bounded menu transaction. Battles and monthly
    events retain their existing handlers; a changed chapter drops its route.
    """
    from ..os_layer.commands import menu_to, pad
    from ..os_layer.readers import STATUS_NAMES, month_menu_ready
    from ..os_layer.world import _owned
    from ..strategy.economy import _egg_recovery_targets, _recruit_shortage, _recruit_sufficient
    state = mem.get('house')
    if state and screen.kind == 'name_entry':
        _finish(mem)
        return None  # let the caller initialize the new game immediately
    if state and mem.get('recall'):
        _record(mem, 'aborted', reason='既存の緊急帰還を優先し、卵修理の移動を中断')
        _finish(mem)
        return None
    if state is None:
        tick = int(mem.get('tick') or 0)
        scope = [mem.get('chapter'), mem.get('month')]
        field_count, legacy_monthly = _field_budget(mem, scope)
        monthly = month_menu_ready(screen) and not mem.get('month_sub')
        if monthly:
            if any(mem.get(k) for k in ('recall', 'battle')):
                return None
            from .month import army_count_owned
            shop = mem.get('shop') or {}
            if (army_count_owned(mem)
                    or (shop.get('key') == mem.get('month')
                        and (shop.get('soldier_receipt') or {}).get('status') == 'pending')):
                # Issue #2366 (g809 5-7/6-2): the survey took the menu to
                # メインメニュー mid-inspection; the inspection read that as
                # another owner and dropped the month's refill. The count
                # check and an unsettled refill finish first (both bounded).
                return None
            if ((mem.get('shop') or {}).get('key') == mem.get('month')
                    and (mem.get('shop') or {}).get('recruit') in ('opened', 'done', 'unverified')):
                return None
            if (_egg_recovery_targets(mem)
                    and (mem.get('shop') or {}).get('egg') not in ('done', 'skipped', 'not_needed',
                                                                    'unverified')):
                # Existing recovery/reserve ordering precedes the free survey.
                # g684 1-12/2-1: a paid recovery whose debit could not be
                # verified stayed 'unverified', this gate never opened, the
                # roster was never re-read in the month and no general was
                # recruited (owner 2026-10-11: 将軍たりてないのに募集かけてない).
                # The attempt is over either way; only the receipt is unknown.
                return None
            if (_recruit_sufficient(mem) or receipts.surveyed(mem)
                    or (receipts.fresh(mem)
                        and receipts.fresh(mem).get('complete'))):
                return None
            attempts = mem.get('recruit_month_scan_attempts') or {}
            scope = [mem.get('chapter'), mem.get('month')]
            if attempts.get('scope') == scope and attempts.get('count', 0) >= 2:
                return None
            move = menu_to(screen, 'メインメニュー')
            if not move:
                return None
            mem['recruit_month_scan_attempts'] = {'scope': scope, 'count':
                (attempts.get('count', 0) if attempts.get('scope') == scope else 0) + 1}
        field = mem.get('house_field_scan') or {}
        field_done = field.get('scope') == scope
        # Legacy house_scan_* may be the last monthly check. Its source is
        # ambiguous when monthly attempts exist, so it cannot block the
        # remaining bounded field observation. A new field receipt can.
        field_tick = (field.get('tick', 0) if field_done else
                      0 if legacy_monthly else mem.get('house_scan_tick', 0))
        field_month = (scope[1] if field_done else
                       None if legacy_monthly else mem.get('house_scan_month'))
        if not monthly:
            reused, actions = _resume_completed_survey(screen, mem, scope, field_count, field_tick)
            if reused:
                return actions
        if ((screen.kind != 'map' and not monthly) or mem.get('chapter') not in HOUSE_VIEW
                or (not monthly and any(mem.get(k) for k in ('active', 'recall', 'y_jump', 'sortie_attempt', 'month_sub', 'battle')))
                or (not monthly and tick - int(field_tick) < SCAN_INTERVAL)
                or (not monthly and field_count >= 2)
                or (not monthly and field_month == mem.get('month') and not mem.get('recruit_roster_recheck'))
                or (not monthly and receipts.surveyed(mem) and not _survey_work(mem)
                    and not mem.get('recruit_roster_recheck'))):
            return None
        state = mem['house'] = {'phase': ('month_open' if monthly and move != 'here' else 'open_roster'),
                                'age': 0, 'total': 0,
                                'chapter': mem['chapter'], 'seen': [], 'pending': [], 'month_scan': monthly}
        if not monthly:
            mem['recruit_field_scan_attempts']['count'] = field_count + 1
        receipts.begin(mem, state)
        _record(mem, 'scan_started', reason='月ごとに全将軍の卵を確認する')
        return ([pad('a')] if move == 'here' else [move]) if monthly else [pad('x')]
    if state.get('chapter') != mem.get('chapter'):
        _finish(mem)
        return None
    if state.get('from_survey') and receipts.reusable_survey(mem) is None:
        if state.get('phase') == 'close':
            # g684 2-11 15:49:04-15:50+: already closed, this check re-ran
            # _exit every observation, held [] and swallowed a duel yes/no
            # (「ごあいてねがえぬか?」). Release the screen to its owner.
            _finish(mem)
            return None
        return _exit(mem, '完了確認の月・ラン・名簿が無効になったため部分再確認を中断')
    phase = state['phase']
    if state.get('month_scan') and phase == 'month_open' and screen.kind == 'main_menu':
        # The information menu was already open on this frame (no navigation
        # was needed from the month menu); continue with its しょうぐん row.
        _phase(state, 'open_roster')
        phase = 'open_roster'
    # The monthly main-menu survey returns to the same monthly menu, never
    # starts a repair trip, and gives the ordinary spending policy the next
    # observation. Delayed pre-open monthly frames do not start another flow.
    if state.get('month_scan') and screen.kind == 'month_menu':
        if not month_menu_ready(screen):
            # A foreground quantity/confirmation/recruitment panel belongs to
            # the monthly transaction policy, not the background roster route.
            # Only a tracked transaction may pause the scan budget. Losing a
            # hand without such an owner is a failed observation, not an
            # unbounded suspension of this month's roster survey.
            if not mem.get('month_sub'):
                if phase == 'close':
                    _finish(mem)
                    return None
                state['age'] += 1
                state['total'] += 1
                if state['age'] >= STEP_LIMIT or state['total'] >= SESSION_LIMIT:
                    _exit(mem, '月初の前面を読み取れないため募集確認を有限に終了',
                          limit='step' if state['age'] >= STEP_LIMIT else 'session')
            return None
        if phase == 'open_roster' and isinstance(menu_to(screen, 'メインメニュー'), dict):
            # v134 could label its recovery A as a successful menu selection.
            # Resume from the observed monthly row, preserving the scan budget.
            state['phase'] = phase = 'month_open'
            _record(mem, 'month_menu_reobserved',
                    reason='情報画面への遷移を確認できないため現在の選択行から移動を再開')
        if phase in ('leave_roster', 'close'):
            if phase == 'leave_roster':
                complete = receipts.complete(mem, state)
                _record(mem, 'month_scan_complete', observed_metric={'roster_complete': complete,
                        'generals': state['seen']}, reason='当月の実一覧を確認して月初メニューへ戻る')
            _finish(mem)
            return []
        if phase == 'month_open':
            state['age'] += 1
            if state['age'] >= STEP_LIMIT:
                return _exit(mem, '月初の情報メニューへ移動できないため募集確認を有限に保留', limit='step')
            reached = menu_to(screen, 'メインメニュー') == 'here'
            actions = _choose(screen, 'メインメニュー', mem=mem)
            if reached and actions == [pad('a')]:
                _phase(state, 'open_roster')
            return actions
        if phase == 'open_roster':
            state['age'] += 1
            if state['age'] >= STEP_LIMIT:
                return _exit(mem, '月初メニューから情報画面を開けないため募集確認を有限に保留', limit='step')
            return []
    if ('roster_month' in state and state['roster_month'] != mem.get('month')
            and (phase in ('roster', 'status', 'roster_next', 'roster_advance')
                 or phase.startswith('income_'))):
        state['roster_invalidated'] = True
        return _exit(mem, '月が変わったため一覧の同一scanを破棄して次の再観測へ戻る')
    receipts.page(mem, roster(screen))
    owns_observation = (bool(mem.get('battle')) or bool(mem.get('month_sub')) or screen.kind in {
        'battle', 'battle_menu', 'egg_battle_menu', 'egg_choice_menu', 'monster_menu',
        'okunote_menu', 'attack_started', 'defense_started', 'boss_attack_started',
        'field_encounter_started'})
    if not owns_observation:
        state['total'] += 1
    if mem.get('battle') and screen.kind == 'map':
        return []  # battle_end needs two map observations; do not start an unrelated sortie between them
    # Repair must never consume unrelated battle commands or monthly menus.
    if (mem.get('battle') or screen.kind in {'battle', 'battle_menu', 'egg_battle_menu', 'egg_choice_menu',
            'monster_menu', 'okunote_menu', 'attack_started', 'defense_started', 'boss_attack_started',
            'field_encounter_started',
            'month_menu', 'shop_quantity', 'shop_quantity_prompt', 'shop_exit_confirm', 'discharge_menu'}
            or mem.get('month_sub')):
        if phase not in ('travel', 'close', 'repair_recheck'):
            state['interrupted'] = True
        return None
    if state.pop('interrupted', False):
        if _retry_interrupted_repair(mem):
            return []
        return _exit(mem, '割り込みでメニューの同一性を失ったため修理操作を中断')
    state['age'] += 1
    if state['total'] > SESSION_LIMIT:
        if phase == 'close':
            _record(mem, 'aborted', reason='閉じる操作も上限に達したため入力を解放')
            _finish(mem)
            return None
        return _exit(mem, '卵修理の共通セッション観測上限に達したため入力を中止',
                     limit='session')
    phase_limit = TRAVEL_LIMIT if phase == 'travel' else STEP_LIMIT
    if state['age'] > phase_limit:
        if phase == 'close':
            _record(mem, 'aborted', reason='閉じる操作も上限に達したため入力を解放')
            _finish(mem)
            return None
        limit = 'travel' if phase == 'travel' else 'step'
        return _exit(mem, '卵修理の観測回数上限に達したため入力を中止', limit=limit)
    if phase == 'repair_recheck':
        if not _retry_target_current(mem):
            return _exit(mem, '修理対象の月・本人・卵状態が変わったため再試行しない')
        if screen.kind == 'map':
            name = state['general']
            state['pending'] = [name, *(n for n in state.get('pending') or [] if n != name)]
            _next_general(mem)  # rebuild owned castles; verify the resident before committing
            return []
        if screen.kind in {'world_map', 'castle_menu', 'general_list', 'card_select', 'sortie_confirm'}:
            return [pad('y' if screen.kind == 'world_map' else 'b')]
        return None  # unrelated dialogue keeps its ordinary handler
    if phase == 'close':
        if screen.kind == 'map':
            _finish(mem)
            return []
        return [pad('y' if screen.kind == 'world_map' else 'b')]
    if phase == 'open_roster':
        if screen.kind == 'map' and state['age'] in (3, 6):
            _record(mem, 'roster_open_retry', observed_metric={'age': state['age']},
                    reason='既知のマップへ戻ったままのため、将軍一覧を開くXを有限回再試行')
            return [pad('x')]
        if screen.kind == 'main_menu' and screen.hand:
            # Status reading is free. Repair dispatch still checks funds in
            # _next_general; never keep a stale egg state just because we are poor.
            actions = _choose(screen, 'しょうぐん')
            if actions == [pad('a')]:
                state['roster_opened'] = True
                _phase(state, 'roster')
            return actions
        return []
    if phase == 'roster':
        if state.get('month_scan'):
            return _month_count(screen, mem, state, frame)
        names = roster(screen)
        if names is None:
            return []
        selected = screen.selected
        if selected not in names:
            return []
        if state.get('from_survey'):
            targets = state.get('status_targets') or []
            if not targets:
                _phase(state, 'leave_roster')
                return [pad('b')]
            target = targets[0]
            if target not in names:
                return [pad('down')]  # the existing step budget bounds this search
            actions = _choose(screen, target)
            if actions == [pad('a')]:
                state['selected'] = target
                _phase(state, 'status')
            return actions
        if not state['seen'] and state.get('roster_opened'):
            state['singleton_first_page'] = _singleton_page(screen, frame)
            state['roster_first_general'] = selected if selected == 'どうし' and screen.hand[1] == 33 else None
        if selected in state['seen'] or len(state['seen']) >= ROSTER_LIMIT:
            state['roster_wrapped'] = (len(state['seen']) >= 2 and selected == state['seen'][0]
                                       and len(state['seen']) <= ROSTER_LIMIT)
            _phase(state, 'leave_roster')
            return [pad('b')]
        state['selected'] = selected
        _phase(state, 'status')
        return [pad('a')]
    if phase == 'status':
        info = _observe_status(screen, mem)
        if not info or info['general'] != state.get('selected'):
            return []
        name = info['general']
        receipts.wage(mem, info)
        state['seen'].append(name)
        if info['broken'] and name not in state['pending']:
            state['pending'].append(name)
        if state.get('from_survey') and name in state['status_targets']:
            state['status_targets'].remove(name)
        _phase(state, 'roster_next')
        return [pad('b')]
    if phase == 'roster_next':
        if roster(screen) is None:
            return []
        if state.get('from_survey'):
            _phase(state, 'roster')
            return []
        _phase(state, 'roster_advance')
        return [pad('down')]
    if phase == 'roster_advance':
        if state.get('month_scan'):
            return _month_count_advance(screen, mem, state, frame)
        names = roster(screen)
        if names is None:
            state['roster_end_observations'] = 0
            return []
        if screen.selected != state['selected']:
            state['roster_end_observations'] = 0
            state.pop('singleton_first_page', None)
            _phase(state, 'roster')
            return []
        end = (state.get('roster_first_general') == 'どうし'
               and state.get('seen', [])[:1] == ['どうし']
               and set(names) <= set(state['seen'])
               and _roster_scroll_end(screen, frame))
        state['roster_end_observations'] = state.get('roster_end_observations', 0) + 1 if end else 0
        if state['age'] >= 6 and state['roster_end_observations'] >= 3:
            state['roster_end_observed'] = True
            _phase(state, 'leave_roster')
            return [pad('b')]
        single = (state.get('singleton_first_page') and state['seen'] == ['どうし']
                  and _singleton_page(screen, frame))
        state['singleton_observations'] = state.get('singleton_observations', 0) + 1 if single else 0
        if state['age'] >= 6 and state['singleton_observations'] >= 3:
            state['roster_wrapped'] = True
            state['singleton_complete'] = True
            _phase(state, 'leave_roster')
            return [pad('b')]
        if state['age'] in (3, 6):
            return [pad('down')]
        if state['age'] >= 9:
            # The unchanged cursor alone proves nothing; complete() accepts it
            # only when every listed name was read on a page ending on screen.
            state['roster_wrapped'] = False
            _phase(state, 'leave_roster')
            return [pad('b')]
        return []
    if phase == 'leave_roster':
        if screen.kind == 'map':
            complete = (receipts.completed_survey(mem) is not None if state.get('from_survey')
                        else receipts.complete(mem, state))
            _record(mem, 'scan_complete', observed_metric={'generals': list(state['seen']), 'broken': list(state['pending']),
                                                         'roster_complete': complete},
                    reason='一覧で確認した将軍の卵状態から修理対象を決定')
            # Reuse measured castle navigation/status paths to acquire only
            # missing incomes. No sortie or paid action is part of this survey.
            if complete and _recruit_shortage(mem):
                owned = sorted(_owned(mem))
                missing = [c for c in owned if receipts.economics(mem, [STATUS_NAMES.get(c, c)]) is None]
                if missing and not mem.get('recruit_payroll_pending'):
                    _phase(state, 'income_next', income_castles=missing, income_failures=[])
                    return []
            _next_general(mem)
            return []
        return [pad('b')]
    if phase.startswith('income_'):
        return _income_step(screen, mem, frame)
    return _repair_step(screen, mem, frame)


def _income_step(screen, mem, frame):
    from ... import hanjuku_chart as chart
    from ..os_layer.commands import pad
    from ..os_layer.navigation import Y_JUMP_TOL, _align_on_roof
    from ..os_layer.readers import CASTLE_STATUS, STATUS_NAMES, is_camp_menu
    from ..os_layer.world import _owned
    state = mem['house']
    phase = state['phase']
    if phase == 'income_next':
        if screen.kind != 'map':
            return [pad('b')]
        queue = state.get('income_castles') or []
        if not queue:
            _record(mem, 'income_survey_finished', observed_metric={
                'failures': state.get('income_failures') or []},
                reason='募集収支の実城収入確認を有限回で終了し通常進行へ戻る')
            _next_general(mem)
            return []
        source = queue.pop(0)
        if source not in _owned(mem) or source not in chart.castles(mem['chapter']):
            state['income_failures'].append(source)
            return []
        state['source'] = source
        _phase(state, 'income_view')
        return [pad('y')]
    if phase == 'income_view':
        return _view_move(screen, mem, frame, _castle_view(mem, state['source']),
                          'income_open', Y_JUMP_TOL)
    if phase == 'income_open':
        if screen.kind == 'map':
            aligned = _align_on_roof(screen, mem, frame, {'own'}, 'HOUSE:income')
            if aligned == 'on':
                receipts.owner(mem, STATUS_NAMES.get(state['source'], state['source']), 'own')
                _phase(state, 'income_verify')
                return [pad('a')]
            return aligned or []
        return []
    if phase == 'income_verify':
        match = CASTLE_STATUS.search(screen.text)
        if match:
            source = state['source']
            if match[1] not in (source, STATUS_NAMES.get(source)):
                state['income_failures'].append(source)
            _phase(state, 'income_close')
            return [pad('b')]
        if screen.kind == 'castle_menu':
            return _choose(screen, 'ステータス')
        if screen.kind == 'text' and is_camp_menu(screen):
            state['income_failures'].append(state['source'])
            _phase(state, 'income_close')
            return [pad('b')]
        return []
    if phase == 'income_close':
        if screen.kind == 'map':
            _phase(state, 'income_next')
            return []
        return [pad('b')]
    return []


def _sole_defender_repair_due(mem, name, source):
    """Repair immediately unless this is the hero or our last castle.

    Owner 2026-10-11: prioritize a broken-egg Venus even when she is the
    only defender. A previous-month deferral is not permission to leave
    the hero or the last castle unprotected, including after a hotload.
    """
    from ..os_layer.world import NAME, _owned
    owned = _owned(mem)
    return name != NAME and source in owned and len(owned) > 1


def _repair_step(screen, mem, frame):
    from ..os_layer.commands import menu_to, pad
    from ..os_layer.navigation import Y_JUMP_TOL, _align_on_roof
    from ..os_layer.readers import CASTLE_STATUS, STATUS_NAMES, _empty_sortie_inventory, _present_generals, is_camp_menu
    from ..os_layer.world import _owned
    from ..strategy.economy import WAGE_SPEND_HOLD
    from ..strategy.orders import _garrison_move
    state = mem['house']
    phase, name = state['phase'], state['general']
    if (phase in ('castle_kit', 'castle_confirm', 'house_confirm')
            and state.get('sole_defender')
            and not _sole_defender_repair_due(mem, name, state.get('source'))):
        return _exit(mem, '主人公または最後の自軍城の唯一の守将を修理へ派遣しない')
    if phase == 'find_castle':
        if screen.kind != 'map':
            if screen.kind == 'text' and roster(screen) is None:
                return None  # event/dialogue text: the ordinary handler advances it
            return [pad('y' if screen.kind == 'world_map' else 'b')]
        castles = state.get('castles') or []
        if not castles:
            _record(mem, 'general_deferred', reason='修理対象の在城を確認できないため次の将軍へ進む')
            _next_general(mem)
            return []
        state['source'] = castles.pop(0)
        if state['source'] not in _owned(mem):
            return []
        _phase(state, 'castle_view')
        return [pad('y')]
    if phase == 'castle_view':
        # The ring jitters over a flag. Use the existing castle-jump tolerance;
        # roof alignment and the castle's written name still verify the source.
        return _view_move(screen, mem, frame, _castle_view(mem, state['source']), 'castle_open', Y_JUMP_TOL)
    if phase == 'castle_open':
        if screen.kind == 'map':
            aligned = _align_on_roof(screen, mem, frame, {'own'}, 'HOUSE:source')
            if aligned == 'on':
                _phase(state, 'castle_verify')
                return [pad('a')]
            return aligned or []
        return []
    if phase == 'castle_verify':
        match = CASTLE_STATUS.search(screen.text)
        if match:
            source = state['source']
            if match[1] not in (source, STATUS_NAMES.get(source)):
                _phase(state, 'find_castle')
                return [pad('b')]
            count = re.search(r'しょうぐん(\d+)めい', screen.text)
            names = _names_at(screen, 144, 95, 159)
            if not count or len(names) != int(count[1]) or any(UNKNOWN in g for g in names):
                return _exit(mem, '出撃元の在城人数を完全に読めないため派遣しない')
            mem.setdefault('garrison', {})[source] = names
            if name not in names:
                _phase(state, 'find_castle')
            elif len(names) < 2 and not _sole_defender_repair_due(mem, name, source):
                _record(mem, 'general_deferred', reason='主人公または最後の自軍城の唯一の守将を残す')
                _next_general(mem)
            else:
                if len(names) < 2:
                    state['sole_defender'] = True
                    _record(mem, 'repair_sole_defender', castle=source,
                            observed_metric={'month': mem.get('month'), 'owned_castles': len(_owned(mem))},
                            reason='主人公でも最後の自軍城でもないため、城を空けても卵修復を優先する')
                else:
                    state.pop('sole_defender', None)
                _phase(state, 'castle_sortie')
            return [pad('b')]
        if screen.kind == 'castle_menu':
            return _choose(screen, 'ステータス')
        if screen.kind == 'text' and is_camp_menu(screen):
            _phase(state, 'find_castle')
            return [pad('b')]
        return []
    if phase == 'castle_sortie':
        if screen.kind == 'castle_menu':
            actions = _choose(screen, 'しゅつげき')
            if actions == [pad('a')]:
                _phase(state, 'castle_pick')
            return actions
        return []
    if phase == 'castle_pick':
        if screen.kind != 'general_list':
            return []
        present = _present_generals(screen)
        if present is None:
            return []
        if ((len(present) < 2 and not _sole_defender_repair_due(mem, name, state['source'])) or name not in present
                or state['source'] not in _owned(mem)):
            return _exit(mem, '出撃直前の在城・守備条件を満たさないため派遣しない')
        if len(present) < 2:
            state['sole_defender'] = True
        else:
            state.pop('sole_defender', None)
        actions = _choose(screen, name)
        if actions == [pad('a')]:
            _phase(state, 'castle_kit')
        return actions
    if phase == 'castle_kit':
        if screen.kind != 'card_select':
            return []
        info = general_status(screen)
        if info is None:
            return []
        if info['general'] != name or not info['broken']:
            return _exit(mem, '出撃画面で対象本人の卵割れを確認できないため派遣しない')
        if not _repair_hp_ready(info):
            return _exit(mem, '負傷またはHP未確認の将軍を無装備の卵修理へ派遣せず在城で回復を待つ')
        _phase(state, 'castle_confirm')
        return [pad('b')]  # no battle cards are spent on a repair trip
    if phase == 'castle_confirm':
        if screen.kind != 'sortie_confirm':
            return []
        info = general_status(screen)
        if (not info or info['general'] != name or not info['broken']
                or not _empty_sortie_inventory(screen)):
            return []
        if not _repair_hp_ready(info):
            return _exit(mem, '派遣確定前に全快HPを確認できないため出撃を取り消す')
        if not affordable_gift(mem.get('gold'), WAGE_SPEND_HOLD):
            return _exit(mem, '派遣前に修理資金が不足したため出撃を取り消す')
        if _defer_unsafe_house_route(mem):
            return []
        actions = _choose(screen, 'うむッ!')
        if actions == [pad('a')]:
            _phase(state, 'out_target')
        return actions
    if phase == 'out_target':
        if screen.kind == 'map_target':
            state.pop('target_observation', None)
            _phase(state, 'house_view', alignment_unread=0, alignment_moves=0)
            return [pad('y')]
        return []
    if phase == 'house_view':
        return _view_move(screen, mem, frame, HOUSE_TARGET[mem['chapter']], 'house_confirm')
    if phase == 'house_confirm':
        if screen.kind == 'map_target':
            if _defer_unsafe_house_route(mem):
                return []
            from ..os_layer.world import observed_cursor_cell
            from ..os_layer.navigation import Y_JUMP_OFFSET, MAX_HOLD_FRAMES
            # A on the Y view only proposes a landing. Measure the current
            # field marker before committing this unit's movement; the old
            # integrated cursor / house_view phase cannot authorize that A.
            position = observed_cursor_cell(screen, mem, frame, max_error=2, ambiguity=4)
            offset = Y_JUMP_OFFSET.get(mem.get('chapter'))
            if position is None or offset is None or not screen.marker:
                reads = state.get('alignment_unread', 0)
                if type(reads) is not int or not 0 <= reads < 2:
                    return _exit(mem, '家への実マーカー位置を現在の屋根配置で確認できないため派遣を保留')
                state['alignment_unread'] = reads + 1
                return []
            target = [(HOUSE_TARGET[mem['chapter']][a] - offset[a]) * 8 for a in (0, 1)]
            delta = [target[a] - position[a] for a in (0, 1)]
            if any(abs(d) > 2 for d in delta):
                moves = state.get('alignment_moves', 0)
                if type(moves) is not int or not 0 <= moves < 8:
                    return _exit(mem, '家への実マーカー位置合わせが有限上限に達したため派遣を保留')
                state['alignment_moves'] = moves + 1
                axis = 0 if abs(delta[0]) >= abs(delta[1]) else 1
                button = ('right' if delta[axis] > 0 else 'left') if axis == 0 else (
                          'down' if delta[axis] > 0 else 'up')
                return [pad(button, min(MAX_HOLD_FRAMES, abs(delta[axis])))]
            state['target_observation'] = {'position': position, 'target': target, 'tick': mem.get('tick')}
            _phase(state, 'travel', departure_pending=True)
            _record(mem, 'dispatch_requested', source=state.get('source'),
                    observed_metric={'view': HOUSE_TARGET[mem['chapter']], 'field_position': position,
                                     'field_target': target, 'basis': 'current_two_roof_constellation'},
                    reason='卵が壊れた本人をあたしの家へ派遣する入力を確定')
            return [pad('a')]
        return []
    if phase in ('find_field', 'field_roster_open', 'field_pick', 'field_open', 'field_status', 'field_read', 'unit_move'):
        return _field_step(screen, mem, frame)
    if phase == 'travel':
        # Checked before the map early-return: an arrival toast can be drawn
        # over the field map (parse keeps kind 'map' while the free cursor is
        # visible), where the old `return []` left it unanswered forever and
        # the trip starved to its travel limit instead of entering the house.
        discovery = re.search(r'([^\ufffd\s]+)しょうぐんがあたし.*いえを', screen.text, re.S)
        if discovery and discovery[1] != name:
            return _exit(mem, '家を訪れた将軍が派遣対象と異なるため購入しない')
        if screen.kind == 'map':
            if state.pop('departure_pending', False):
                _garrison_move(mem, name, source=state.get('source'))
                for sortie in (mem.get('sorties') or {}).values():
                    if sortie.get('general') == name and sortie.get('status') in ('en_route', 'launched_unconfirmed'):
                        sortie['status'] = 'redirected_house'
                tick = int(mem.get('tick') or 0)
                mem.setdefault('sorties', {})[f'HOUSE_OUT:{tick}'] = {
                    'general': name, 'target': None, 'status': 'en_route', 'purpose': 'house', 'tick': tick}
                _record(mem, 'departed', reason='移動先選択からフィールドへ戻ったことを確認')
                return []
            if discovery:
                return [pad('a')]  # dismiss the house-visit toast; prices arrive next
            return []
        prices = gift_prices(screen)
        if prices:
            _record(mem, 'arrival_seen', screen=screen.kind,
                    observed_metric={'prices': prices, 'gold': (screen.header or {}).get('gold')},
                    reason='家の贈り物一覧と価格を実画面で確認')
            gold = (screen.header or {}).get('gold')
            gift = affordable_gift(gold, WAGE_SPEND_HOLD)
            if not gift:
                _record(mem, 'purchase_skipped', reason='到着時の所持金では破損卵の修理費（実費）を支払えないため購入しない')
                _phase(state, 'decline')
                return []
            state['purchase'] = {'item': gift[0], 'cost': gift[1], 'gold_before': gold,
                                 'month': mem.get('month')}
            _phase(state, 'buy')
            return []
        if screen.kind == 'text' and any(s in screen.text for s in ('あたし', 'たまご', 'かって')):
            return [pad('a')]
        return None
    if phase == 'buy':
        if not gift_prices(screen):
            return []
        buy = state['purchase']
        gold = (screen.header or {}).get('gold')
        if type(gold) is not int or gold < buy['cost'] + WAGE_SPEND_HOLD:
            return _exit(mem, '購入直前の所持金を確認できないため購入しない')
        move = menu_to(screen, buy['item'], exact=False)
        if move == 'here':
            buy['gold_before'] = gold
            _phase(state, 'receipt')
            _record(mem, 'purchase_requested', observed_metric=dict(buy),
                    reason='実表示の価格と所持金を確認して贈り物を一度だけ選択')
            return [pad('a')]
        return [move] if move else []
    if phase == 'receipt':
        buy = state['purchase']
        gold = (screen.header or {}).get('gold')
        if type(gold) is int and gold == buy['gold_before'] - buy['cost'] and mem.get('month') == buy['month']:
            buy['gold_after'] = gold
        if 'おかねがたりません' in screen.text:
            return _exit(mem, '所持金不足の実表示を確認したため購入失敗として扱う')
        if screen.kind == 'map':
            _phase(state, 'verify_open')
            return [pad('x')]
        if gift_prices(screen):
            return []  # the old list may persist during a fade; never buy twice
        if screen.kind == 'text':
            return [pad('a')]
        return None
    if phase == 'decline':
        gold = (screen.header or {}).get('gold')
        if gift_prices(screen) and type(gold) is int and gold < 200:
            # This forced gift list ignores B. An unaffordable choice closes
            # it with the measured おかねがたりません message, without spending.
            move = menu_to(screen, 'スカーフ', exact=False)
            if move == 'here':
                _phase(state, 'decline_done')
                return [pad('a')]
            return [move] if move else []
        return []
    if phase == 'decline_done':
        if screen.kind == 'map':
            _phase(state, 'find_field', returning=True)
            return []
        if screen.kind == 'text':
            return [pad('a')]
        return []
    if phase == 'verify_open':
        if screen.kind == 'main_menu' and screen.hand:
            actions = _choose(screen, 'しょうぐん')
            if actions == [pad('a')]:
                _phase(state, 'verify_pick', scrolls=0)
            return actions
        return []
    if phase == 'verify_pick':
        names = roster(screen)
        if names is None:
            return []
        if name in names:
            actions = _choose(screen, name)
            if actions == [pad('a')]:
                _phase(state, 'verify_status')
            return actions
        if state['scrolls'] >= ROSTER_LIMIT:
            return _exit(mem, '購入後の本人を一覧で確認できないため成功扱いしない')
        state['scrolls'] += 1
        state['age'] = 0
        return [pad('down')]
    if phase == 'verify_status':
        info = general_status(screen)
        if not info or info['general'] != name:
            return []
        buy = state['purchase']
        gold = (screen.header or {}).get('gold')
        if type(gold) is int and gold == buy['gold_before'] - buy['cost'] and mem.get('month') == buy['month']:
            buy['gold_after'] = gold
        repaired = not info['broken'] and info['uses'] is not None and 'gold_after' in buy
        state['purchased'] = repaired
        _observe_status(screen, mem)
        _record(mem, 'repair_verified' if repaired else 'repair_unconfirmed',
                observed_metric={**buy, 'status': info},
                reason='購入後の本人の卵表示と支払額を照合した結果')
        _phase(state, 'return_close', returning=True)
        return [pad('b')]
    if phase == 'return_close':
        if screen.kind == 'map':
            _phase(state, 'find_field')
            return []
        return [pad('b')]
    if phase in ('return_view', 'return_confirm'):
        return _return_step(screen, mem, frame)
    if phase == 'return_done':
        if screen.kind == 'map':
            receipts.dirty_status(mem, [name], fields=('hp', 'location'))
            tick = int(mem.get('tick') or 0)
            for sortie in (mem.get('sorties') or {}).values():
                if sortie.get('general') == name and sortie.get('purpose') == 'house':
                    sortie['status'] = 'recalled'
            mem.setdefault('sorties', {})[f'HOUSE:{tick}'] = {
                'general': name, 'target': state['return_goal'], 'status': 'en_route',
                'purpose': 'move', 'tick': tick}
            _next_general(mem)
        return []
    return []


def _field_step(screen, mem, frame):
    from ..os_layer.commands import menu_to, pad
    from ..os_layer.readers import is_camp_menu
    from ..os_layer.world import NAME
    state = mem['house']
    phase, name = state['phase'], state['general']
    if phase == 'find_field':
        if screen.kind != 'map':
            if screen.kind == 'text' and roster(screen) is None:
                return None  # event/dialogue text: the ordinary handler advances it
            return [pad('y' if screen.kind == 'world_map' else 'b')]
        if name == NAME:
            _phase(state, 'field_open')
            return [pad('select')]
        _phase(state, 'field_roster_open')
        return [pad('x')]
    if phase == 'field_roster_open':
        if screen.kind == 'main_menu' and screen.hand:
            actions = _choose(screen, 'しょうぐん')
            if actions == [pad('a')]:
                _phase(state, 'field_pick', scrolls=0)
            return actions
        return []
    if phase == 'field_pick':
        names = roster(screen)
        if names is None:
            return []
        if name not in names:
            if state['scrolls'] >= ROSTER_LIMIT:
                return _exit(mem, '対象本人を一覧で確認できないため位置選択を中止')
            state['scrolls'] += 1
            state['age'] = 0
            return [pad('down')]
        move = menu_to(screen, name)
        if move == 'here':
            # In the main roster SELECT jumps directly to the selected
            # general. Field SELECT alone would always focus the hero.
            _phase(state, 'field_open')
            return [pad('select')]
        return [move] if move else []
    if phase == 'field_open':
        if screen.kind == 'map':
            _phase(state, 'field_status')
            return [pad('a')]
        if screen.kind == 'text':
            # A menu the SELECT jump left behind is closed; any other text is
            # an event the ordinary handler advances (holding [] here starved
            # the dispatch until STEP_LIMIT when a concert cut in at 07:01).
            return [pad('b')] if roster(screen) is not None else None
        if screen.kind == 'world_map':
            return [pad('y')]
        if screen.kind in ('main_menu', 'castle_menu', 'castle_info', 'general_list'):
            return [pad('b')]
        return []  # transition/unknown frames still wait for the map
    if phase == 'field_status':
        if is_camp_menu(screen):
            actions = _choose(screen, 'ステータス')
            if actions == [pad('a')]:
                _phase(state, 'field_read')
            return actions
        if screen.kind in ('castle_menu', 'castle_info') or (screen.kind == 'map' and state['age'] > 4):
            return _exit(mem, '本人の部隊メニューを確認できないため派遣を保留')
        if screen.kind == 'map':
            return []
        if screen.kind == 'text':
            # Not the camp menu: a lingering roster closes, an event text is
            # yielded so the ordinary handler can advance it (07:44/07:52).
            return [pad('b')] if roster(screen) is not None else None
        if screen.kind == 'world_map':
            return [pad('y')]
        if screen.kind in ('main_menu', 'general_list'):
            return [pad('b')]
        return []
    if phase == 'field_read':
        info = general_status(screen)
        if info is None:
            return []
        if info['general'] != name or (not state.get('returning') and not info['broken']):
            return _exit(mem, '本人の卵割れまたは帰還対象を確認できないため操作を中止')
        if not state.get('returning') and not _repair_hp_ready(info):
            state['returning'] = True
            _record(mem, 'recall_needed', observed_metric={'hp': info.get('hp'), 'max_hp': info.get('max_hp')},
                    reason='負傷またはHP未確認の卵割れ将軍は修理へ向かわせず、自軍旗を確認した城へ帰還する')
        _phase(state, 'unit_move')
        return [pad('b')]
    if phase == 'unit_move':
        if not is_camp_menu(screen):
            return []
        label = 'きかん' if state.get('returning') else 'いどう'
        actions = _choose(screen, label)
        if actions == [pad('a')]:
            _phase(state, 'return_view' if state.get('returning') else 'out_target')
        return actions
    return []


def _return_step(screen, mem, frame):
    from ... import hanjuku_chart as chart
    from ..os_layer.commands import pad
    from ..os_layer.navigation import _align_on_roof
    from ..os_layer.readers import WORLD_MAP_OFFSET, world_cursor, world_flags
    state = mem['house']
    if screen.kind == 'world_map':
        cursor = world_cursor(frame)
        if cursor is None:
            return []
        # きかん uses a castle picker with an R ring, not the movable Y/G
        # cursor. Its blue/white ring cannot create an own red flag, which
        # remains readable underneath it (chapter 2 isolated measurement).
        flags = world_flags(frame, mem['chapter'])
        ox, oy = WORLD_MAP_OFFSET[mem['chapter']]
        nearby = [c for c, (x, y) in chart.castles(mem['chapter']).items()
                  if abs(x / 8 + ox - cursor[0]) <= 6 and abs(y / 8 + oy - cursor[1]) <= 6]
        if len(nearby) == 1 and flags.get(nearby[0]) == 'own':
            state['return_goal'] = nearby[0]
            if state['phase'] == 'return_view':
                _phase(state, 'return_confirm')
            else:
                _return_requested(mem)
            return [pad('a')]
        if state['phase'] == 'return_confirm':
            return _exit(mem, '帰還確定前に自軍旗と選択城の一致を確認できないため中止')
        return [pad('right')]  # bounded by STEP_LIMIT; never approve an enemy flag
    if state['phase'] != 'return_confirm':
        return []
    if screen.kind == 'map_target':
        goal = state['return_goal']
        aligned = _align_on_roof(screen, mem, frame, {'own'}, 'HOUSE:return')
        if aligned == 'on':
            _return_requested(mem)
            return [pad('a')]
        return aligned or []
    if screen.kind == 'map':
        _return_requested(mem)
    return []


def _return_requested(mem):
    state = mem['house']
    _record(mem, 'return_requested', target=state['return_goal'],
            reason='本人の部隊メニューから自軍城への帰還を指示（到着は未確認）')
    _phase(state, 'return_done')
