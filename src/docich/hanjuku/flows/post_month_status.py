"""Post-month roster-first status / recovery sequence.

The monthly command finishes before this owner opens the global X menu.  A
complete individual reading precedes any recall, house dispatch or map order.
All commands are bounded; an unverified location is never treated as a castle.
"""
from __future__ import annotations

from .. import decision_log
from ..os_layer import roster as receipts
from ..os_layer import status_return
from ..os_layer.commands import menu_to, pad
from ..os_layer.readers import world_markers
from ..os_layer.screen import castle_roofs, own_camps
from ..os_layer.world import NAME
from ..state.regions import flow as _flow, world as _world
from . import house, recall as recall_flow, map_registration
from copy import deepcopy

MAX_STEPS = 900
MAX_SCAN = receipts.MAX_GENERALS
MENU_RETRIES = 4
STATUS_RETRIES = 12
MARKER_RETRIES = 4  # bounded roster frames before status A; avoids transient sprite misses
HOUSE_READ_FRESH_TICKS = 300  # a same-month house status read newer than this needs no re-open
LOCATE_RETRIES = 2  # retry the named SELECT -> actual castle status, never infer a castle
# g1147 2026-11-07 1-8: an 8-name roster fills the visible page, so the
# short-page end proof never applies. The pixel end proof needs a 256x224
# frame, but live frames are 299x224, and this list does not wrap: down on
# the last row is a no-op (13 identical frames then roster_end_unconfirmed).
# After this many consecutive already-visited observations stalled on the
# visible last row of a full walk, a passing _complete_scan is the end proof.
END_STALL_READS = 3
# Menus this owner never opens while it waits for the X menu or the map. A
# castle menu left over from a swallowed A used to answer [] forever (live
# 2026-11-06 g1139 48-10: しゅつげき/ステータス open, overlay 「task_menu」, no
# input), and the generic no-input fallback never fired because the animated
# map changes the frame digest on every observation.
STRAY_MENU_KINDS = frozenset({'castle_menu', 'castle_info', 'general_list'})
STRAY_MENU_BACKOUTS = 12
# docich#3659: the #3505 boss-adjacent camp exemption was indefinite, so a
# general camped on the boss castle could neither be recalled nor re-ordered
# toward it (no camp->boss route exists). Bound the exemption: after this many
# consecutive months the monthly pass queues the recall again, returning him
# to the planner's garrison pool so a later boss wave can send him.
BOSS_CAMP_EXEMPT_MONTHS = 3
MARCH = frozenset({'いまこちらにむかっています', 'しんぐんちゅうです',
                   'いどうしています'})
# Chapter carry-over queue: after a chapter change only three generals per
# month may follow from the previous chapter, so the rest stay in a known
# waiting state (live g1153 2026-11-08: 24 generals).  This is not a field
# march; it is a queue whose arrivals are confirmed from the hero's castle
# resident list, not by opening every general every month.
CARRY = frozenset({'いまこちらにむかっております'})
CAMP = frozenset({'キャンプしています', 'キャンプのなかにいます'})
CASTLE = frozenset({'しろのなかにいます'})
# Without a manifest the queue is sampled three names per month (the game's
# measured carry rate); the manifest normally replaces this entirely.
CARRY_RECHECK_BUDGET = 3
CARRY_OVERDUE_MONTHS = 2
# A battle/event can take the foreground while the recall's destination
# picker is open.  ``camp_recheck._defer`` then drops the recall without
# sending the return A, and the task used to be marked finished, so the
# general waited for the next month's scan (live g1153 2026-11-08: ポワソン
# and リースリング were interrupted at 17:00/17:05 and stayed camped).  Retry
# the same task a bounded number of times within this month; a dispatched
# recall is never repeated.
RECALL_TASK_RETRIES = 2


def _log(mem, event, **details):
    decision_log._record(mem, event, **details)


def _camp_stays(mem):
    from .recall import camp_stays
    return camp_stays(mem)


def _boss_camp_holds_siege(info):
    """Whether a boss-adjacent camp still justifies keeping the general there.

    #3505/#3512: a general camped just outside the boss castle is part of an
    in-progress boss attack; a monthly recall would cancel it. A broken egg is
    the signal for the repair task below, but that task only starts from a
    verified castle, so a boss-adjacent camp can never satisfy it. Leaving him
    camped pins him indefinitely (live g1153 2026-11-07: ヴィーナス HP3/82,
    broken, camped at けっかい and never recalled). Recall him so the next
    monthly pass can repair him. An unread broken flag keeps the old exemption
    (never recall on unknown evidence).
    """
    from ... import hanjuku_chart as chart
    marker = info.get('marker')
    candidate = marker.get('castle_candidate') if isinstance(marker, dict) else None
    return (isinstance(candidate, str) and chart.is_boss_castle(candidate)
            and info.get('broken') is not True)


def _road_skips_checks(mem):
    """Chapter 9 before its boss phase: no monthly locate or camp recall.

    Owner 2026-11-11: 月次の位置チェックも必要がないので省略できる。キャンプに
    なった将軍は仕方ないのでこの章は放置。ただし、ボスフェーズになったときは、
    規定の動き、ボスを追いかけるためにチェックが必要. g1163 spent ~9 minutes
    a month on ~11 locates (13:04-13:13) while no sortie was planned; v665
    sends whoever the list cursor is on, so a general's castle is not needed.
    """
    from ... import hanjuku_chart as chart
    from .recall import boss_phase
    chapter = mem.get('chapter')
    return chart.guard_free(chapter) and bool(chart.corridor(chapter)) and not boss_phase(mem)


def _tasks(statuses, names, boss_camp_months=None, camp_stays=False, road=False):
    """``camp_stays``: a moving-boss chapter never recalls a camp home (owner
    2026-11-09); the camp is re-sent at the boss by the camp flow instead.
    ``road``: chapter 9 before the boss phase keeps only repairs."""
    months = boss_camp_months or {}
    queue = []
    for name in names:
        info = statuses[name]
        if road:
            if info.get('location') == 'castle' and info.get('broken'):
                queue.append({'kind': 'repair', 'name': name})
            continue
        if info.get('location') == 'camp' and camp_stays:
            continue
        if info.get('location') == 'camp':
            # #3505: a camp whose roster triangle maps onto the boss castle is a
            # siege in progress, not a straggler. ``marker_castle`` already binds
            # castle_candidate only within MAP_CASTLE_TOLERANCE, so "boss-adjacent"
            # reuses that measured tolerance instead of a new arbitrary threshold
            # (g1133: a general camped at field [824,176] against the boss at
            # (818,184) and the monthly recall dragged him back, cancelling the
            # boss fight). The exemption expires after BOSS_CAMP_EXEMPT_MONTHS
            # consecutive observed months (docich#3659): a siege that never
            # resolves must not pin him to the camp forever. A broken general
            # never holds the siege (docich#3659 follow-up: ヴィーナス).
            if (_boss_camp_holds_siege(info)
                    and int(months.get(name) or 0) <= BOSS_CAMP_EXEMPT_MONTHS):
                continue
            queue.append({'kind': 'recall', 'name': name})
        elif info.get('location') == 'castle':
            # A castle name measured from the roster triangle or the native
            # status panel is already known; only its ownership is pending.
            # Re-running the expensive SELECT->ステータス locate for it would
            # repeat the same failure every month (2026-11-07 g1139 ポワソン).
            if not info.get('castle') and not info.get('castle_pending'):
                queue.append({'kind': 'locate', 'name': name})
            if info.get('broken'):
                queue.append({'kind': 'repair', 'name': name})
    # Owner 2026-11-11 (間違った座標にむかったしょうぐんがずっと崖でキャンプしてる):
    # a camped general is an army out of play, and a camp recall is short. In
    # name order g1163 queued ジェラート's repair ahead of シュゼット's recall;
    # the repair ran ~3 minutes, the month rolled over and the recall never
    # started (12:32, 12:38, 12:44 — three months). Recalls go first.
    return ([job for job in queue if job['kind'] == 'recall']
            + [job for job in queue if job['kind'] != 'recall'])


def _house_freshly_read(mem):
    """Generals whose individual status a same-month house scan already read.

    A house pass reads the same status panel this walk would open. Reusing a
    read that is seconds old saves a panel visit per general (g1163
    2026-11-09: the house pass read at 09:55 and this walk at 09:58).
    """
    now = mem.get('tick')
    dirty = ((mem.get('general_statuses') or {}).get('dirty') or {})
    dirty = dirty if isinstance(dirty, dict) else {}
    fresh = set()
    for name, info in (mem.get('house_eggs') or {}).items():
        if not isinstance(info, dict) or info.get('month') != mem.get('month'):
            continue
        if (info.get('location') not in ('castle', 'carry', 'march', 'camp', 'field', 'unknown')
                or info.get('location_observed') is False):
            continue
        tick = info.get('observed_tick')
        if type(tick) is int and type(now) is int and 0 <= now - tick <= HOUSE_READ_FRESH_TICKS:
            fresh.add(name)
            continue
        # A general read inside a castle this month keeps that reading until a
        # battle marks him dirty (``battle_end`` does): the monthly walk
        # re-read all 27 generals even though only a handful had taken damage
        # (g1163 2026-11-09 18:21-18:26: five minutes of panel visits a month).
        # A marching/field reading stays on the tick window alone, because an
        # arrival can change it with no battle to mark.
        if (info.get('location') == 'castle' and name not in dirty
                and type(tick) is int):
            fresh.add(name)
    return fresh


def arm(mem):
    """The month alone never invalidates a verified per-general reading."""
    scope = [mem.get('chapter'), mem.get('month')]
    if not scope[0] or not scope[1]:
        return
    from ..strategy import corridor
    if corridor.mechanical(mem):
        # Owner 2026-11-11: chapter 9 before its boss phase does nothing but
        # march; no monthly status reads, repairs, recalls or locates.
        mem.pop('post_month_status', None)
        return
    old = mem.get('post_month_status') or {}
    if old.get('scope') == scope:
        return
    mem.pop('post_month_status', None)  # discard an obsolete previous-month owner
    identity = receipts.run_identity(mem.get('_run_identity'))
    cache = receipts._persistent_status(mem)
    statuses = deepcopy(cache['statuses']) if cache is not None else {}
    stale = set((cache or {}).get('dirty') or {})
    # A moving general can reach a castle, camp or battle without an explicit
    # status callback. Stationary castle residents need no periodic re-open.
    # Chapter carry-over waiters are a known queue, not unknown movement: their
    # arrivals are read from the hero's castle resident manifest, never by
    # opening every general every month.
    from ..os_layer.world import _owned
    owned = _owned(mem)
    # A house scan of this same month has already read the individual status
    # panel for these generals (egg, HP, location). Re-opening every one of
    # them here spent minutes per month re-reading data that was seconds old
    # (g1163 2026-11-09: the house pass ran at 09:55 and this walk at 09:58).
    freshly_read = _house_freshly_read(mem)
    for name, row in statuses.items():
        if name in freshly_read:
            continue
        if (row.get('location') in ('march', 'unknown')
                or row.get('location') == 'castle'
                   and (not row.get('castle') or row['castle'] not in owned)):
            stale.add(name)
    carry = [name for name, row in statuses.items()
             if row.get('location') == 'carry']
    pending = list((cache or {}).get('pending_joins') or [])
    pending.extend(name for name in mem.get(receipts.PENDING_JOINS_KEY) or []
                   if name not in statuses and name not in pending)
    count = mem.get('country_general_count') or {}
    count_mismatch = (isinstance(count, dict)
                      and count.get('scope') == scope and count.get('identity') == identity
                      and type(count.get('count')) is int
                      and count['count'] != len(statuses))
    # ``general_location_unknown`` is itself the request for the next
    # individual status confirmation (an expired march, or a general who lost
    # a battle). Schedule it from that list instead of trusting only the
    # cached field flags: a request recorded while a monthly pass was running
    # is dropped when that pass commits its snapshot, and a cached row that
    # still names an owned castle never turns dirty on its own — the general
    # was then never re-read (2026-11-07: a lost march stayed unavailable
    # while every month reused the cache).
    world = _world(mem)
    unknown = [name for name in (world.get('general_location_unknown') or ())
               if isinstance(name, str) and name]
    members = set(statuses) | set(pending)
    if cache is not None and unknown:
        # A verified roster is the membership proof: a name it does not hold
        # cannot be an employed general whose whereabouts are unknown.
        dropped = [name for name in unknown if name not in members]
        if dropped:
            world['general_location_unknown'] = [
                name for name in unknown if name in members]
            unknown = [name for name in unknown if name in members]
            _log(mem, 'post_month_unknown_location_pruned',
                 observed_metric={'generals': dropped, 'members': len(members)},
                 reason='確定済みの名簿にいない将軍は雇用中の所在不明ではないため再確認対象から外す')
    for name in unknown:
        if name in statuses:
            stale.add(name)
    # Carry-over queue policy.  The queue drains at three generals per month;
    # the destination is the hero's castle, and its resident list names the
    # arrivals, so the queue order never has to be known or displayed.
    tracking = mem.get('carry_tracking')
    if (carry and isinstance(tracking, dict)
            and tracking.get('chapter') == mem.get('chapter')):
        if tracking.get('last_scope') != scope:
            tracking['months'] = int(tracking.get('months') or 0) + 1
            tracking['last_scope'] = list(scope)
        expected = max(1, (int(tracking.get('initial') or len(carry)) + 2) // 3)
        if (int(tracking.get('months') or 0) > expected + CARRY_OVERDUE_MONTHS
                and not tracking.get('overdue_done')):
            stale.update(carry)
            tracking['overdue_done'] = True
            _log(mem, 'carry_overdue_recheck',
                 observed_metric={'carry': carry,
                                  'months': tracking['months'],
                                  'expected': expected},
                 reason='章またぎ待ちが想定月数を超えたため一度だけ本人状態を再確認する')
    hero_castle = _carry_destination(mem, statuses) if carry else None
    manifest_due = (bool(carry) and isinstance(hero_castle, str)
                    and mem.get('carry_manifest_scope') != scope
                    and mem.get('carry_manifest_failed_scope') != scope)
    if (carry and not manifest_due
            and mem.get('carry_manifest_failed_scope') == scope
            and mem.get('carry_fallback_scope') != scope):
        # The resident list could not be read this month.  Sample the queue
        # once instead of reopening all of it; the next month retries the
        # manifest.
        _fallback_carry(stale, mem, carry)
        mem['carry_fallback_scope'] = list(scope)
    scan = cache is None or bool(stale or pending or count_mismatch)
    # A castle recorded while its ownership was stale is now usable the moment
    # the Y map shows our flag; do not wait for another roster SELECT.
    promoted = _promote_pending(mem, statuses)
    # Count each general observed camping on the boss castle once per month.
    # ``arm`` can also be re-entered inside the same month after ``_finish``
    # dropped the owner state, so gate the increment on the counted scope —
    # never on the presence of a live owner.
    if mem.get('boss_camp_exempt_scope') != scope:
        mem['boss_camp_exempt_scope'] = list(scope)
        camping = {
            name for name, row in statuses.items()
            if row.get('location') == 'camp' and _boss_camp_holds_siege(row)}
        previous = mem.get('boss_camp_exempt') or {}
        counted = {name: int(previous.get(name) or 0) + 1 for name in camping}
        if counted:
            mem['boss_camp_exempt'] = counted
        else:
            mem.pop('boss_camp_exempt', None)
    tasks = _tasks(statuses, list(statuses), mem.get('boss_camp_exempt'),
                   camp_stays=_camp_stays(mem), road=_road_skips_checks(mem))
    if not scan and not manifest_due and not tasks:
        # Common case: zero input and zero roster navigation this month.
        mem['post_month_status_last'] = {
            'scope': scope, 'statuses': statuses, 'complete': True,
            'result': 'unchanged_cached', 'carry': carry,
            'tick': int(mem.get('tick') or 0)}
        _log(mem, 'post_month_status_reused',
             observed_metric={'generals': len(statuses), 'reopened': 0,
                              'carry': carry},
             reason='月替わりだけでは将軍の確定済み状態を無効化しない')
        return
    mem['post_month_status'] = {
        'scope': scope, 'identity': identity,
        'stage': ('manifest' if manifest_due
                  else 'await_map' if scan else 'process'),
        'statuses': statuses,
        'dirty': sorted(stale), 'names': [] if (scan or manifest_due) else list(statuses),
        'tasks': [] if scan else tasks,
        'manifest': ({'castle': hero_castle, 'name': NAME,
                      'scope': list(scope), 'waiting': carry,
                      'then_scan': bool(scan), 'then_process': bool(tasks)}
                     if manifest_due else None),
        'steps': 0, 'phase_reads': 0,
        'complete': not scan and not manifest_due,
        'started_tick': int(mem.get('tick') or 0)}
    _log(mem, 'post_month_status_armed',
         observed_metric={'scope': scope, 'cached': len(statuses),
                          'reopen': sorted(stale), 'joins': pending,
                          'unknown_location': list(unknown),
                          'promoted': promoted,
                          'carry': carry, 'manifest': bool(manifest_due),
                          'count_mismatch': count_mismatch},
         reason=('章またぎ待ちは主人公の城の在籍名簿で確認し、変更があり得る将軍だけ個別ステータスを再取得する'
                 if scan else
                 '章またぎ待ちの在籍名簿を確認する'
                 if manifest_due else
                 '既知の帰還・修理タスクのみ実行する'))


def _stage(state, name, **fields):
    if name != 'roster_next':
        state.pop('close_probe_pending', None)
    if name not in {'status', 'roster_next'}:
        state.pop('status_return', None)
    state.update(stage=name, phase_reads=0, **fields)


def _open_status(screen, state, frame):
    state['selected'] = screen.selected
    proof = status_return.capture(screen, frame)
    if proof:
        state['status_return'] = proof
    else:
        state.pop('status_return', None)
    _stage(state, 'status')
    return [pad('a')]


def _location(screen):
    text = house.location_row(screen)
    if text in CAMP:
        return 'camp'
    if text in CASTLE:
        return 'castle'
    if text in CARRY:
        return 'carry'
    if text in MARCH:
        return 'march'
    return 'unknown'


def _carry_destination(mem, statuses):
    """The hero's castle, preferring his own verified status row."""
    row = statuses.get(NAME) if isinstance(statuses, dict) else None
    if isinstance(row, dict) and row.get('location') == 'castle':
        castle = row.get('castle')
        if isinstance(castle, str) and castle:
            return castle
    from ..os_layer.world import _hero_castle
    castle = _hero_castle(mem)
    if isinstance(castle, str) and castle:
        return castle
    return None


def _fallback_carry(stale, mem, carry):
    """Sample the queue three names per month when no manifest is possible."""
    ordered = [name for name in carry if isinstance(name, str) and name]
    if not ordered:
        return
    window = mem.get('carry_fallback_window')
    window = window if type(window) is int and window >= 0 else 0
    picks = [ordered[(window + index) % len(ordered)]
             for index in range(min(CARRY_RECHECK_BUDGET, len(ordered)))]
    stale.update(picks)
    mem['carry_fallback_window'] = (window + len(picks)) % max(1, len(ordered))


def _clear_dirty_field(mem, state, name, field):
    """A manifest proves location; never re-open only for that field."""
    cache = receipts._persistent_status(mem)
    fields = set((cache or {}).get('dirty', {}).get(name) or ())
    fields.discard(field)
    if cache is not None:
        if fields:
            cache['dirty'][name] = sorted(fields)
        else:
            cache['dirty'].pop(name, None)
    if not fields and isinstance(state.get('dirty'), list) and name in state['dirty']:
        state['dirty'].remove(name)


def _apply_manifest(mem, state, names, *, complete, evidence):
    """Bind the hero-castle resident list to every resident at once.

    A listed general is positively in the hero's castle; one who is absent
    while the list is complete is still waiting in the carry queue.  Only
    presence/absence of the *residents* is evidence; a name outside the list
    is never marked as departed here.
    """
    manifest = state.get('manifest') or {}
    castle = manifest.get('castle')
    scope = list(state.get('scope') or [mem.get('chapter'), mem.get('month')])
    now = int(mem.get('tick') or 0)
    statuses = state['statuses']
    raw = [name for name in dict.fromkeys(names)
           if isinstance(name, str) and name]
    # Only names already in the verified roster are used.  A partial OCR name
    # or a brand-new recruit is not a carry arrival and must not create a row
    # from the list alone; the normal roster proof handles new membership.
    listed = [name for name in raw if isinstance(statuses.get(name), dict)]
    ignored = [name for name in raw if name not in listed]
    cache = receipts._persistent_status(mem)
    world = _world(mem)
    garrison = world.setdefault('garrison', {})
    if isinstance(castle, str) and listed:
        # A complete sortie list is the resident manifest, but keep the hero
        # entry if the list omitted him (a non-sortie display is not proof he
        # left).  An incomplete walk only adds positive evidence.
        if complete and NAME in listed:
            # Keep known residents the sortie list omitted (non-sortie display
            # is not evidence they left).  A later full scan still corrects it.
            keep = [name for name, row in statuses.items()
                    if name not in listed
                    and row.get('location') == 'castle'
                    and row.get('castle') == castle]
            garrison[castle] = list(dict.fromkeys([*listed, *keep]))
        else:
            members = garrison.setdefault(castle, [])
            for name in listed:
                if name not in members:
                    members.append(name)
    for other, members in list(garrison.items()):
        if other == castle or not isinstance(members, list):
            continue
        for name in listed:
            if name in members:
                members.remove(name)
    eggs = mem.setdefault('house_eggs', {})
    arrived, satisfied = [], []
    for name in listed:
        row = statuses[name]
        if row.get('location') == 'carry':
            arrived.append(name)
        row.update(location='castle', castle=castle,
                   location_observed=True,
                   castle_evidence='hero_castle_manifest',
                   manifest_tick=now)
        _clear_dirty_field(mem, state, name, 'location')
        satisfied.append(name)
        egg = eggs.get(name)
        if isinstance(egg, dict):
            egg.update(location='castle', castle=castle,
                       location_observed=True, month=mem.get('month'),
                       observed_chapter=mem.get('chapter'),
                       observed_tick=now, carry_waiting=False,
                       manifest_tick=now)
    waiting = [name for name, row in statuses.items()
               if row.get('location') == 'carry' and name not in listed]
    if complete:
        for name in waiting:
            _clear_dirty_field(mem, state, name, 'location')
            egg = eggs.get(name)
            if isinstance(egg, dict):
                egg.update(location='field', location_observed=True,
                           month=mem.get('month'),
                           observed_chapter=mem.get('chapter'),
                           observed_tick=now, carry_waiting=True,
                           carry_manifest_scope=scope, manifest_tick=now)
    manifest.update(done=True, residents=list(listed),
                    complete=bool(complete), arrived=list(arrived),
                    waiting=list(waiting), evidence=evidence)
    state['manifest_done'] = True
    # A list, not a set: this flow state is part of the bot's policy memory and
    # `brains/hanjuku/bot.py` persists that memory as JSON. A `set` here raised
    # TypeError on every write, which the bot reported as an invalid
    # observation (exit 2) and the agent therefore dropped the decided input
    # (g1153 2026-11-08 19:26: the carry manifest stalled to input_stalled).
    state['manifest_satisfied'] = list(listed)
    mem['carry_manifest_scope'] = list(scope)
    mem.pop('carry_manifest_failed_scope', None)
    tracking = mem.get('carry_tracking')
    if (isinstance(tracking, dict)
            and tracking.get('chapter') == mem.get('chapter')):
        tracking['remaining'] = len(waiting)
        tracking['last_manifest_scope'] = list(scope)
        if not waiting:
            tracking['overdue_done'] = False
    _log(mem, 'carry_manifest_read',
         observed_metric={'castle': castle, 'residents': len(listed),
                          'ignored': ignored,
                          'arrived': list(arrived), 'waiting': list(waiting),
                          'complete': bool(complete), 'evidence': evidence},
         reason='主人公の城の在籍名簿で章またぎ待ち将軍の到着を一括確認')
    for name in arrived:
        _log(mem, 'carry_arrived', general=name, castle=castle,
             reason='章またぎ待ちだった将軍が主人公の城の在籍名簿に現れたため到着とした')
    # A manifest-only month does not run a full roster walk; persist the
    # location/survey update here so the next flow does not repeat it.
    mem['general_statuses'] = {
        'scope': list(scope), 'identity': state.get('identity'),
        'statuses': statuses,
        'dirty': deepcopy((cache or {}).get('dirty') or {}),
        'pending_joins': list((cache or {}).get('pending_joins') or []),
        'suspected_departures': list((cache or {}).get('suspected_departures') or []),
        'complete': True, 'tick': now}


def _reset_for_scan(state):
    """Start the normal roster walk after the manifest closed the menu."""
    state['names'] = []
    state['complete'] = False
    for key in ('top_confirmed', 'top_seeks', 'list_moves', 'highlight',
                'selected', 'marker_reads', 'markers', 'end_stall',
                'end_stall_selected', 'stray_backouts', 'manifest_scan'):
        state.pop(key, None)
    state['steps'] = 0
    state['phase_reads'] = 0


def _manifest_fail(mem, state, reason):
    state['manifest_failed'] = reason
    mem['carry_manifest_failed_scope'] = list(state.get('scope') or [])
    _log(mem, 'carry_manifest_unverified',
         observed_metric={'reason': reason, 'stage': state.get('stage'),
                          'castle': (state.get('manifest') or {}).get('castle')},
         reason='主人公の城の在籍名簿を読めないため、待ち将軍の一括確認を翌月へ延期する')
    state.pop('job', None)
    state.pop('manifest_scan', None)
    _stage(state, 'manifest_close')
    return []


def _manifest_step(screen, mem, state):
    stage = state.get('stage')
    if stage == 'manifest':
        if screen.kind == 'map':
            manifest = state.get('manifest') or {}
            state['job'] = {'kind': 'manifest',
                            'name': manifest.get('name') or NAME}
            state['seeks'] = 0
            _stage(state, 'task_menu')
            return [pad('x')]
        if screen.kind in STRAY_MENU_KINDS or house.roster(screen):
            return [pad('b')]
        # Waiting for the field map, like 'await_map': any other foreground
        # (a merchant visit, an event, a target picker) belongs to its own
        # handler. g1163 2026-11-11 09:20: a 行商人 quantity prompt was held
        # here with no input for 150 s until the frozen-screen fallback B,
        # then its quantity screen the same way (manifest at 275 steps).
        return None
    if stage == 'manifest_list':
        return _manifest_list_step(screen, mem, state)
    if stage == 'manifest_close':
        return _manifest_close_step(screen, mem, state)
    return []


def _manifest_list_step(screen, mem, state):
    """Walk the castle's しゅつげき general list and read every resident.

    The castle status panel only exposes five rows in the current reader and
    has no scroll handling (live hero castle 2026-11-08 already held six), so
    the sortie list is the reliable resident manifest.  Only Down and B are
    sent; no general is ever selected.
    """
    from ..os_layer.readers import _current, _present_generals
    if screen.kind == 'castle_menu':
        if state.get('phase_reads') in (3, 6):
            toward = menu_to(screen, 'しゅつげき')
            if toward == 'here':
                return [pad('a')]
            if toward and toward != 'here':
                return [toward]
        if state.get('phase_reads') >= STATUS_RETRIES:
            return _manifest_fail(mem, state, 'general_list_not_opened')
        return []
    if screen.kind != 'general_list':
        if screen.kind == 'map':
            return _manifest_fail(mem, state, 'general_list_closed')
        if screen.kind in ('castle_info', 'main_menu') or house.roster(screen):
            return [pad('b')]
        return []
    present = _present_generals(screen)
    if present is None:
        if state.get('phase_reads') >= STATUS_RETRIES:
            return _manifest_fail(mem, state, 'general_list_unreadable')
        return []
    if not present:
        return _manifest_fail(mem, state, 'general_list_empty')
    cur = _current(screen)
    if not cur or cur[2] not in present:
        if state.get('phase_reads') >= STATUS_RETRIES:
            return _manifest_fail(mem, state, 'general_list_cursor_unreadable')
        return []
    scan = state.get('manifest_scan')
    if not isinstance(scan, dict):
        scan = state['manifest_scan'] = {
            'anchor': cur[2], 'last': cur[2], 'names': [], 'presses': 0,
            'stalls': 0, 'end': None}
    scan['names'] += [name for name in present if name not in scan['names']]
    if cur[2] != scan['last']:
        scan['last'] = cur[2]
        scan['stalls'] = 0
        if cur[2] == scan['anchor']:
            scan['end'] = 'wrapped'
    elif scan['presses']:
        scan['stalls'] += 1
        if scan['stalls'] >= 3:
            scan['end'] = 'stalled'
    if not scan['end'] and scan['presses'] >= 16:
        scan['end'] = 'limit'
    if scan['end']:
        _apply_manifest(mem, state, scan['names'],
                        complete=scan['end'] in ('wrapped', 'stalled'),
                        evidence=scan['end'])
        _stage(state, 'manifest_close')
        return [pad('b')]
    scan['presses'] += 1
    return [pad('down')]


def _manifest_close_step(screen, mem, state):
    if screen.kind in ('general_list', 'castle_menu', 'castle_info', 'main_menu'):
        return [pad('b')]
    if screen.kind != 'map':
        if house.roster(screen):
            return [pad('b')]
        return []
    manifest = state.get('manifest') or {}
    if state.get('manifest_failed'):
        mem['carry_manifest_failed_scope'] = list(state['scope'])
    if manifest.get('then_scan'):
        _reset_for_scan(state)
        _stage(state, 'await_map')
        return []
    if manifest.get('then_process') or state.get('tasks'):
        _stage(state, 'process')
        return []
    _finish(mem, state)
    return []


def _static_marker(state, marker):
    """True when this marker box already failed to resolve for another general.

    A box that never produced a castle candidate is a static decoration, not a
    transient sprite miss: re-reading it costs one observation per read and
    cannot give any general a castle (g1163 2026-11-09 09:58-10:02: box
    [59,120,64,125] repeated for twenty generals, four reads each).
    """
    box = tuple(marker.get('box') or ()) if isinstance(marker, dict) else ()
    if not box:
        return False
    if marker.get('castle_candidate'):
        state.pop('static_marker', None)   # this box can resolve; remember nothing
        return False
    static = box == tuple(state.get('static_marker') or ())
    if not static:
        state['static_marker'] = list(box)
    return static


def _marker(mem, frame, general=None):
    """Record the roster selection triangle and bind its castle from the map.

    The roster draws a white/gray inverted triangle over the selected general.
    The red army marker of the Y view is a different sprite and sits on the
    hero, so it cannot answer "which castle is this general in". The measured
    roster marker is therefore read first; the older red-triangle/camera
    heuristic stays only as a fallback for frames where that sprite is absent.
    """
    if frame is None:
        return None
    from ..os_layer.readers import marker_castle, selection_marker
    marker = selection_marker(frame) if hasattr(frame, 'pixel') else None
    if marker is not None:
        result = {'box': list(marker['box']), 'source': 'selection_marker'}
        _why = {}
        mapped = marker_castle(frame, mem.get('chapter') or 0,
                               hint=_recorded_castle(mem, general), evidence=_why)
        result['marker_reason'] = _why.get('reason')
        # A triangle without any castle flag on the same map is not enough to
        # name a castle; leave it to the SELECT/status confirmation task.
        if isinstance(mapped, dict) and int(mapped.get('icons') or 0) >= 1:
            result['castle_candidate'] = mapped['castle']
            result['mapping'] = mapped
            result['icons_seen'] = mapped['icons']
        else:
            # Distinguish a missing flag sprite from a visible flag with an
            # uncalibrated or ambiguous chart match in live diagnostics.
            from ..os_layer.readers import _map_icon_centres
            result['icons_seen'] = len(_map_icon_centres(frame))
        return result
    boxes = [b for b in world_markers(frame)
             if b[2] < 164 and b[3] < 150]
    if len(boxes) != 1:
        # No white selection triangle and no single army marker either: the
        # monthly walk pays a status panel for this, so name the cause
        # (g1163 2026-11-09 23:35-23:37: 20 of 25 markers were unbound with no
        # recorded reason).
        return {'box': None, 'source': 'none',
                'marker_reason': 'no_selection_marker' if not boxes else 'many_markers',
                'candidate_boxes': [list(b) for b in boxes[:4]]}
    box = boxes[0]
    result = {'box': list(box), 'source': 'roster_triangle_candidate',
              'marker_reason': 'army_marker_fallback'}
    last = mem.get('nav_last') or {}
    cell = mem.get('cursor')
    if (mem.get('uncertain') or last.get('kind') != 'map'
            or not isinstance(cell, (list, tuple)) or len(cell) != 2
            or not isinstance(last.get('screen'), list)
            or len(last['screen']) != 2
            or type(mem.get('tick')) is not int
            or type(last.get('tick')) is not int
            or not 0 <= mem['tick'] - last['tick'] <= 30):
        return result
    # Main-menu roster draws on top of the last field camera. The camera did
    # not move while the roster is open; retain only a unique near-castle hit.
    from ... import hanjuku_chart as chart
    mx = (box[0] + box[2]) / 2
    my = (box[1] + box[3]) / 2
    camera = [cell[a] - last['screen'][a] for a in (0, 1)]
    near = [name for name, xy in chart.castles(mem.get('chapter') or 0).items()
            if abs(xy[0] - mx - camera[0]) <= 8
            and abs(xy[1] - my - camera[1]) <= 8
            and not chart.is_boss_castle(name)]
    if len(near) == 1:
        result['castle_candidate'] = near[0]
    return result


def _assign(mem, name, castle, *, evidence):
    """Make an observed residence immediately available to the planner."""
    from ..os_layer.world import _owned
    if castle not in _owned(mem):
        return False
    garrison = _world(mem).setdefault('garrison', {})
    for other, names in garrison.items():
        if other != castle and isinstance(names, list) and name in names:
            names.remove(name)
    occupants = garrison.setdefault(castle, [])
    if name not in occupants:
        occupants.append(name)
    row = (mem.get('post_month_status') or {}).get('statuses', {}).get(name)
    if isinstance(row, dict):
        row.update(castle=castle, castle_evidence=evidence)
    unknown = _world(mem).get('general_location_unknown')
    if isinstance(unknown, list) and name in unknown:
        unknown.remove(name)
    _log(mem, 'post_month_location_confirmed', general=name, castle=castle,
         reason='将軍本人の現在位置と城の実表示を対応させて在城リストを更新')
    return True


def _forget_field_residents(mem, name):
    for occupants in (_world(mem).get('garrison') or {}).values():
        if isinstance(occupants, list) and name in occupants:
            occupants.remove(name)


def _forget_pending(mem, name):
    pending = _world(mem).get('castle_location_pending')
    if isinstance(pending, dict):
        pending.pop(name, None)


def _note_pending_castle(mem, name, castle, *, evidence):
    """Keep a measured castle name until the ownership cache catches up.

    A general's own status can say しろのなかにいます while the roster triangle
    (or the castle status panel opened at his SELECT) names a castle the Y map
    still shows as enemy: the castle was taken moments ago and ``captured`` has
    not been refreshed yet (g1139 2026-11-07: ポワソン took ジュメルズ at 00:03,
    the roster bound him to it at 00:04, and the locate task spent three
    12-read waits because ``_assign`` kept rejecting the name; the Y map only
    recorded the capture at 00:09). The castle name is observed; only
    ownership is missing, so keep it here and never add him to a garrison of a
    castle we do not own yet. ``_promote_pending`` binds it once the Y map
    confirms the flag, without another SELECT round trip.
    """
    if not isinstance(name, str) or not isinstance(castle, str):
        return
    world = _world(mem)
    pending = world.get('castle_location_pending')
    if not isinstance(pending, dict):
        pending = world['castle_location_pending'] = {}
    previous = pending.get(name)
    pending[name] = {'castle': castle, 'chapter': mem.get('chapter'),
                     'tick': int(mem.get('tick') or 0), 'evidence': evidence}
    # He is verifiably in *a* castle, just not one we may place him in yet. An
    # older garrison entry at another castle is no longer his observed station.
    _forget_field_residents(mem, name)
    row = ((mem.get('post_month_status') or {}).get('statuses') or {}).get(name)
    if isinstance(row, dict):
        row.update(castle=castle, castle_evidence=evidence, castle_pending=True)
    cached = ((receipts._persistent_status(mem) or {}).get('statuses') or {}).get(name)
    if isinstance(cached, dict):
        # The full-scan snapshot was written before this locate task ran.
        cached.update(castle=castle, castle_evidence=evidence, castle_pending=True)
    if not isinstance(previous, dict) or previous.get('castle') != castle:
        _log(mem, 'post_month_castle_pending_ownership', general=name, castle=castle,
             observed_metric={'evidence': evidence, 'tick': int(mem.get('tick') or 0)},
             reason='城名は実画面で確認できたが所有一覧が未更新のため、所有確認まで在城一覧へ入れない')


def _promote_pending(mem, statuses):
    """Bind a remembered castle once the Y map proves it is ours."""
    pending = _world(mem).get('castle_location_pending')
    if not isinstance(pending, dict) or not pending:
        return 0
    from ..os_layer.world import _owned
    owned = _owned(mem)
    cache = receipts._persistent_status(mem)
    promoted = 0
    for name in list(pending):
        record = pending.get(name)
        if not isinstance(record, dict) or record.get('chapter') != mem.get('chapter'):
            pending.pop(name, None)
            continue
        castle = record.get('castle')
        if not isinstance(castle, str) or castle not in owned:
            continue
        if not _assign(mem, name, castle, evidence='pending_ownership_confirmed'):
            continue
        pending.pop(name, None)
        promoted += 1
        row = statuses.get(name) if isinstance(statuses, dict) else None
        if isinstance(row, dict):
            row.update(castle=castle, castle_evidence='pending_ownership_confirmed')
            row.pop('castle_pending', None)
        cached = ((cache or {}).get('statuses') or {}).get(name)
        if isinstance(cached, dict):
            cached.update(castle=castle, castle_evidence='pending_ownership_confirmed')
            cached.pop('castle_pending', None)
    return promoted


def _roster_end_assumable(state):
    """The walk listed the game's whole roster, only its end pixels failed.

    The roster caps at 32 (``MAX_SCAN``): once a walk that started at the hero
    has collected that many names, every resident is enumerated even when the
    live 299x224 frame shows no scroll-end pixels and ``down`` on the last row
    is a no-op (g1163 2026-11-09 19:00/19:17: ``roster_end_unconfirmed`` with
    ``names: 32`` and 13 stalled reads). The month then continues into the
    per-general tasks instead of dropping them, and nothing is evicted because
    ``complete`` stays False.
    """
    return (bool(state.get('top_confirmed'))
            and len(list(state.get('names') or ())) >= MAX_SCAN)


def _recorded_castle(mem, name):
    """The castle this general was last recorded in, when it is known."""
    if not isinstance(name, str) or not name:
        return None
    row = ((mem.get('post_month_status') or {}).get('statuses') or {}).get(name)
    if isinstance(row, dict) and isinstance(row.get('castle'), str):
        return row['castle']
    cached = ((mem.get('general_statuses') or {}).get('statuses') or {}).get(name)
    if isinstance(cached, dict) and isinstance(cached.get('castle'), str):
        return cached['castle']
    house = (mem.get('house_eggs') or {}).get(name)
    if isinstance(house, dict) and isinstance(house.get('castle'), str):
        return house['castle']
    return None


def _complete_scan(mem, state):
    if (not state['names'] or len(set(state['names'])) != len(state['names'])
            or not state.get('top_confirmed')
            or any(name not in state['statuses'] for name in state['names'])
            or any(name in state.get('dirty', ()) for name in state['names'])
            or any(state['statuses'][name].get('location') == 'unknown'
                   for name in state['names'])):
        return False
    country = mem.get('country_general_count') or {}
    if (isinstance(country, dict) and country.get('scope') == state['scope']
            and country.get('identity') == state['identity']
            and type(country.get('count')) is int
            and country['count'] != len(state['names'])):
        # A mismatched national headcount means the roster was not complete
        # (or changed mid-visit). Do not delete a general by extrapolation.
        return False
    state['complete'] = True
    state['steps'] = 0
    # A *complete* roster walk proves that missing names have left. A battle
    # defeat by itself never proves death. Exclude absent names and all their
    # pending/field references only after this membership proof.
    missing = set(state['statuses']) - set(state['names'])
    for name in missing:
        receipts.record_departure(mem, name)
        _forget_field_residents(mem, name)
        _forget_pending(mem, name)
        for key in ('house_eggs', 'egg_types', 'egg_uses'):
            if isinstance(mem.get(key), dict):
                mem[key].pop(name, None)
        for key in ('egg_recheck', 'roster_pending_joins'):
            if isinstance(mem.get(key), list):
                mem[key] = [actor for actor in mem[key] if actor != name]
        # ``general_location_unknown`` lives in the world region; the flat
        # read here never matched it, so a departed general stayed queued for
        # a status confirmation he could never receive.
        unknown = _world(mem).get('general_location_unknown')
        if isinstance(unknown, list) and name in unknown:
            unknown.remove(name)
        _log(mem, 'post_month_general_departed', general=name,
             reason='将軍名簿を末尾まで実測したが本人がいないため旧状態を除籍')
    # A name that was never in the cached statuses cannot be purged by the
    # ``missing`` loop above, yet the completed walk is the same membership
    # proof: only names on this list are employed generals.
    world = _world(mem)
    unknown = world.get('general_location_unknown')
    if isinstance(unknown, list):
        known = set(state['names'])
        kept = [name for name in unknown if name in known]
        if len(kept) != len(unknown):
            world['general_location_unknown'] = kept
    state['statuses'] = {name: state['statuses'][name] for name in state['names']}
    carry = [name for name, row in state['statuses'].items()
             if row.get('location') == 'carry']
    tracking = mem.get('carry_tracking')
    if carry:
        if (not isinstance(tracking, dict)
                or tracking.get('chapter') != mem.get('chapter')):
            mem['carry_tracking'] = {
                'chapter': mem.get('chapter'), 'initial': len(carry),
                'remaining': len(carry), 'started': list(state['scope']),
                'last_scope': list(state['scope']), 'months': 0,
                'overdue_done': False}
        else:
            tracking['initial'] = max(int(tracking.get('initial') or 0),
                                      len(carry))
            tracking['remaining'] = len(carry)
    else:
        mem.pop('carry_tracking', None)
    state['tasks'] = _tasks(state['statuses'], state['names'],
                            mem.get('boss_camp_exempt'), camp_stays=_camp_stays(mem),
                            road=_road_skips_checks(mem))
    mem['general_statuses'] = {
        'scope': list(state['scope']), 'identity': state['identity'],
        'statuses': state['statuses'], 'dirty': {}, 'pending_joins': [],
        'suspected_departures': [], 'complete': True,
        'tick': int(mem.get('tick') or 0)}
    queue = state['tasks']
    # Commit the same full-survey contract that the existing planner, payroll
    # and egg-repair logic already understand. This is a verified per-general
    # scan, unlike the earlier month-menu names-only count.
    mem['recruit_roster'] = {
        'chapter': state['scope'][0], 'month': state['scope'][1],
        'tick': state['started_tick'], 'identity': state['identity'],
        'names': list(state['names']),
        'wages': {name: receipts.fixed_wage(name) for name in state['names']},
        'complete': False}
    receipts.complete(mem, {
        'roster_started': state['started_tick'], 'seen': list(state['names']),
        'roster_end_observed': True})
    _stage(state, 'process')
    _log(mem, 'post_month_status_complete',
         observed_metric={'generals': list(state['names']),
                          'carry': carry, 'tasks': list(queue)},
         reason=('全将軍の個別卵・所在地を確認したため帰還・修理・所在確定を開始'
                 if not carry else
                 '全将軍の個別卵・所在地を確認し、章またぎ待ちは在籍名簿の確認へ委ねる'))
    return True


def _finish(mem, state):
    failure = state.get('failure')
    carry = [name for name, row in (state.get('statuses') or {}).items()
             if row.get('location') == 'carry']
    mem['post_month_status_last'] = {
        'scope': list(state['scope']), 'complete': bool(state['complete']),
        'statuses': state['statuses'], 'failed': failure,
        'manifest': state.get('manifest'),
        'manifest_done': bool(state.get('manifest_done')),
        'manifest_failed': state.get('manifest_failed'),
        'carry': carry,
        'tick': int(mem.get('tick') or 0)}
    mem.pop('post_month_status', None)
    _log(mem, 'post_month_sequence_finished',
         observed_metric={'complete': bool(state['complete']), 'failed': failure,
                          # How long the monthly walk took, in ticks: the only
                          # way to prove a reuse change helped (a run without
                          # this field cannot be compared with the next one).
                          'elapsed_ticks': max(0, int(mem.get('tick') or 0)
                                               - int(state.get('started_tick') or 0)),
                          'manifest': bool(state.get('manifest_done')),
                          'manifest_failed': state.get('manifest_failed'),
                          'carry': carry,
                          'remaining': list(state.get('tasks') or [])},
         reason=('確認できなかった月次情報を城の所在地として扱わず、通常の出撃計画へ制御を返す'
                 if failure else
                 '章またぎ待ちの在籍名簿を確認し、他の将軍の変更がなければ通常の計画へ制御を返す'
                 if state.get('manifest_done') else
                 '月次の個別確認と状態別の処理を終えて通常攻略の計画へ制御を返す'))


def _task_complete(mem, state, *, result):
    job = state.get('job') or {}
    if job.get('kind') == 'locate' and result != 'castle_verified':
        name = job.get('name')
        if name and name not in _world(mem).setdefault('general_location_unknown', []):
            _world(mem)['general_location_unknown'].append(name)
        _forget_field_residents(mem, name)
    _log(mem, 'post_month_task_result',
         general=job.get('name'), resulting_event=result,
         observed_metric={'task': job.get('kind')},
         reason='将軍別の処理を終了し次の将軍の処理へ進む')
    state.pop('job', None)
    state['steps'] = 0
    _stage(state, 'process')


def _recall_was_dispatched(mem, name, *, since):
    """Whether this month's recall A was actually sent for ``name``.

    ``camp_recheck._defer`` pops a recall that lost the foreground before the
    destination A.  ``recall_verification`` only exists once the dispatch
    step observed the same-run input receipt, so a missing or unsent receipt
    means nothing was sent.  ``since`` rejects a verification left over from
    an earlier month's recall of the same general.
    """
    verification = _flow(mem).get('recall_verification') or {}
    if (not isinstance(verification, dict)
            or verification.get('input_sent') is not True
            or verification.get('general') != name):
        return False
    requested = verification.get('requested_tick')
    return type(requested) is int and requested >= since


def _retry_location(mem, state, screen, reason):
    """Revisit the same general before discarding a location without proof."""
    general = (state.get('job') or {}).get('name')
    count = int(state.get('locate_retries') or 0)
    _log(mem, 'post_month_castle_location_unverified', general=general,
         resulting_event=reason,
         observed_metric={'attempt': count + 1, 'screen': screen.kind,
                          'text': (screen.text or '')[:120]},
         reason='SELECTの後に城名が確認できなかったため同じ将軍を再確認する')
    if count < LOCATE_RETRIES:
        state['locate_retries'] = count + 1
        _stage(state, 'locate_reset')
    else:
        _task_complete(mem, state, result=reason)
    return [pad('b')] if screen.kind in {
        'castle_menu', 'castle_info', 'main_menu', 'general_list', 'text'} else []


def reenter_after_reanchor(mem, resume):
    """Reopen this month's workflow from the recovered map, without blind A.

    SELECT reanchor always lands on アルマムーン, not the general whose status
    or castle was being inspected.  Preserve verified statuses, tasks and the
    pending job, but discard only the menu/cursor phase whose screen vanished.
    """
    state = mem.get('post_month_status')
    if (not isinstance(state, dict)
            or state.get('scope') != resume.get('scope')
            or state.get('scope') != [mem.get('chapter'), mem.get('month')]
            or state.get('identity') != receipts.run_identity(mem.get('_run_identity'))):
        return False
    if resume.get('owner_reentered'):
        return True
    previous = state.get('stage')
    if previous in {'roster', 'status', 'roster_next'}:
        # The prior roster highlight disappeared when SELECT moved the camera.
        # A fresh top-to-bottom membership proof is required; cached clean
        # individual statuses remain and dirty entries are reread.
        state['names'] = []
        state['list_moves'] = 0
        state['top_confirmed'] = False
        for key in ('highlight', 'selected', 'marker_reads', 'markers', 'top_seeks',
                    'end_stall', 'end_stall_selected'):
            state.pop(key, None)
        _stage(state, 'open_menu')
        state['phase_reads'] = 2  # next map observation opens X immediately
    elif previous in {'task_menu', 'task_select', 'task_jump',
                      'locate_open', 'locate_info', 'locate_reset'}:
        # A task SELECT originally jumped to its general, but the recovery
        # SELECT moved to home.  Re-select the *named* general before A.
        _stage(state, 'task_menu' if state.get('job') else 'process', seeks=0)
        if state['stage'] == 'task_menu':
            state['phase_reads'] = 2
    elif previous in {'manifest', 'manifest_list', 'manifest_close'}:
        state.pop('manifest_scan', None)
        state.pop('job', None)
        _stage(state, 'manifest_close' if state.get('manifest_done') else 'manifest')
        state['phase_reads'] = 2
    elif previous == 'open_menu':
        state['phase_reads'] = 2
    elif previous not in {'await_map', 'process', 'blocked', 'wait_repair', 'wait_recall'}:
        return False
    resume['owner_reentered'] = True
    _log(mem, 'post_month_reanchor_reenter',
         observed_metric={'from': previous, 'to': state.get('stage'),
                          'cached': len(state.get('statuses') or {}),
                          'pending': len(state.get('tasks') or [])},
         reason='カーソル復旧後、月次処理を元の段階から安全に開き直す')
    return True


# A battle can replace the general list/status panel with a map while this
# monthly owner still expects that now-closed panel. Re-enter the same owner
# rather than letting an exhausted [] handler or the sortie planner take over.
_BATTLE_SCAN_STAGES = frozenset({'roster', 'status', 'roster_next'})
_BATTLE_TASK_STAGES = frozenset({
    'task_menu', 'task_select', 'task_jump', 'locate_open', 'locate_info',
    'locate_reset',
})


def resume_after_battle(mem, battle):
    """Resume an interrupted monthly status operation on the next field map.

    Keep previously observed statuses and queued recovery tasks. A roster
    traversal must start at its verified top again: a partial list after
    combat cannot prove that an absent general died. A SELECT-based task must
    reopen the named general's menu, not act on the combat camera position.
    """
    state = mem.get('post_month_status')
    if not isinstance(state, dict) or (
            state.get('scope') != [mem.get('chapter'), mem.get('month')]):
        return
    stage = state.get('stage')
    actor = battle.get('ally') if isinstance(battle, dict) else None
    statuses = state.get('statuses') or {}
    if actor in statuses:
        # battle_end also dirties the persistent roster, but an already armed
        # monthly scan has its own snapshot of the dirty list.
        dirty = set(state.get('dirty') or ())
        dirty.add(actor)
        state['dirty'] = sorted(dirty)
    if stage in {'manifest', 'manifest_list', 'manifest_close'}:
        state.pop('manifest_scan', None)
        state.pop('job', None)
        state['steps'] = 0
        _stage(state, 'manifest_close' if state.get('manifest_done') else 'manifest')
        resumed = state['stage']
    elif stage in _BATTLE_SCAN_STAGES or (stage == 'blocked' and not state.get('job')):
        state['names'] = []
        state['complete'] = False
        for key in ('top_confirmed', 'top_seeks', 'list_moves', 'highlight',
                    'marker_reads', 'selected', 'markers', 'stray_backouts',
                    'end_stall', 'end_stall_selected',
                    'failure'):
            state.pop(key, None)
        state['steps'] = 0
        _stage(state, 'await_map')
        resumed = 'await_map'
    elif stage in _BATTLE_TASK_STAGES or (stage == 'blocked' and state.get('job')):
        # Keep the exact job and remaining queue. The battle invalidated the
        # old SELECT position, so opening the list again is mandatory.
        state['steps'] = 0
        state.pop('failure', None)
        _stage(state, 'task_menu', seeks=0)
        state['phase_reads'] = 2  # first map frame opens X immediately
        resumed = 'task_menu'
    elif stage == 'open_menu':
        # First map frame after the battle may open X without three idle reads.
        state['phase_reads'] = 2
        resumed = 'open_menu'
    else:
        # await_map/process/wait_repair/wait_recall own their existing
        # continuations; never steal a live repair/recall transaction.
        resumed = stage
    _log(mem, 'post_month_battle_resume',
         general=actor,
         observed_metric={'previous_stage': stage, 'resume_stage': resumed},
         reason='戦闘で中断した月次ステータス処理を優先し、出撃計画ではなく元の確認手順へ戻る')


def step(screen, mem, frame):
    """Return actions/[] while owned; None when unrelated foreground wins."""
    state = mem.get('post_month_status')
    if not isinstance(state, dict):
        return None
    if (state.get('scope') != [mem.get('chapter'), mem.get('month')]
            or state.get('identity') != receipts.run_identity(mem.get('_run_identity'))):
        mem.pop('post_month_status', None)
        return None
    if (mem.get('battle') or mem.get('month_sub')
            or screen.kind in {'battle', 'battle_menu', 'egg_battle_menu',
                               'egg_choice_menu', 'monster_menu', 'month_menu',
                               'attack_started', 'defense_started',
                               'boss_attack_started', 'field_encounter_started'}):
        return None
    if state.get('stage') == 'blocked':
        # A failed verification must not hold the field forever. The old
        # state answered [] until a battle or a reanchor happened, so a scan
        # that gave up (observation_budget / status_unreadable / roster end)
        # left the map idle with no input for the rest of the month. Nothing
        # unverified was written to general_statuses, so releasing the screen
        # is safe: record the failure and return control to the field.
        _finish(mem, state)
        if screen.kind in STRAY_MENU_KINDS and not house.roster(screen):
            # Close the leftover menu once before the ordinary handlers run;
            # a map/text frame is released immediately instead.
            return [pad('b')]
        return None
    close_probe = (state.get('stage') == 'roster_next'
                   and state.get('close_probe_pending') is True)
    # A scripted dialogue/event is not this owner's screen. The menu stages
    # below would answer [] and starve the ordinary A handler: g1133 stalled on
    # the new-partner ceremony at 2026-11-06 02:56 right after the status scan,
    # with every later observation still reading 'task_menu'. Only 'status'
    # reads a text panel (the individual general status) and the wait_* stages
    # hand the frame to recall/house; everything else releases it.
    if (screen.kind == 'text'
            and state['stage'] not in {'status', 'wait_repair', 'wait_recall'}
            and house.roster(screen) is None and not close_probe):
        return None
    # The sortie target picker belongs to the sortie that opened it: no stage
    # here reads it, and every stage answered [] on it. g1163 2026-11-11
    # 08:50:57: a battle ended while シャルドネ's target picker was open, the
    # resumed 'manifest' stage held map_target with no input, and the month
    # rolled over before the target was ever decided — four times in twenty
    # minutes. Only the wait_* stages hand the frame to a repair/recall that
    # may own the picker.
    if (screen.kind == 'map_target'
            and state['stage'] not in {'wait_repair', 'wait_recall'}):
        return None
    # The monthly workflow owns this map.  A lost cursor is a temporary
    # navigation interrupt, not a reason to let the sortie planner take over.
    if (screen.kind == 'map' and screen.cursor and not screen.marker
            and mem.get('nav_search')
            and state.get('stage') not in {'blocked', 'wait_repair', 'wait_recall'}
            and not close_probe):
        from . import map as map_flow
        recovery = map_flow._start_reanchor_jump(mem, owner='post_month_status')
        if recovery is not None:
            return recovery
    if state['stage'] in {'await_map', 'open_menu', 'task_menu'}:
        if screen.kind in STRAY_MENU_KINDS and not house.roster(screen):
            backouts = state.get('stray_backouts')
            backouts = backouts if type(backouts) is int and backouts >= 0 else 0
            if backouts < STRAY_MENU_BACKOUTS:
                state['stray_backouts'] = backouts + 1
                if backouts == 0:
                    _log(mem, 'post_month_stray_menu_backout', screen=screen.kind,
                         observed_metric={'stage': state['stage']},
                         reason='月越し後の確認が待つX・マップ画面ではなく城メニュー等が残っているため、Bで戻る')
                return [pad('b')]
        else:
            state.pop('stray_backouts', None)
    state['steps'] += 1
    stage = state['stage']
    if state['steps'] > MAX_STEPS and stage not in {'wait_repair', 'wait_recall'}:
        state['failure'] = 'observation_budget'
        _log(mem, 'post_month_status_unverified',
             observed_metric={'stage': stage, 'names': list(state['statuses'])},
             reason='確認上限に達したため未確認情報を城の所在地として扱わない')
        _stage(state, 'blocked')
        return []
    state['phase_reads'] += 1
    if close_probe:
        state.pop('close_probe_pending', None)
        proof = state.pop('status_return', None)
        matched = status_return.matches(screen, frame, proof)
        tick = mem.get('tick')
        _log(mem, 'post_month_status_close_probe', general=state.get('selected'),
             observed_metric={'map_matched': matched, 'screen_kind': screen.kind,
                              'observation_tick': tick if type(tick) is int and tick >= 0 else 0},
             reason=('本人行と開く前の左地図が一致したため一覧への復帰を観測'
                     if matched else
                     '早い観測では一覧への復帰を確認できず入力せず通常周期へ戻る'))
        if not matched:
            # Exactly one early look: do not act on the surviving background
            # list or resend B. The next observation uses normal cadence and
            # the existing owner. Battles above retain immediate priority.
            return []
    if stage in {'manifest', 'manifest_list', 'manifest_close'}:
        return _manifest_step(screen, mem, state)
    if stage == 'await_map':
        if screen.kind == 'map':
            _stage(state, 'open_menu')
            return [pad('x')]
        if screen.kind == 'main_menu' or house.roster(screen):
            # A battle can leave the global X/roster overlay open while the
            # monthly scan is being restarted. Close it as this owner's
            # recovery, rather than allowing deploy_step to claim the list.
            return [pad('b')]
        return None
    # These hops only relabel an already visible roster. Continue on the
    # same observation, with one budget increment and at most one pad. The
    # stable-highlight/marker gate in 'roster' still needs distinct frames.
    if stage == 'open_menu' and house.roster(screen):
        _stage(state, 'roster')
        stage = 'roster'
    if stage == 'task_menu' and house.roster(screen):
        _stage(state, 'task_select', seeks=0)
        stage = 'task_select'
    if stage == 'open_menu':
        if screen.kind == 'main_menu' and screen.hand:
            direction = menu_to(screen, 'しょうぐん')
            if direction == 'here':
                _stage(state, 'roster')
                return [pad('a')]
            return [direction] if direction else []
        if screen.kind == 'map' and state['phase_reads'] in (3, 6):
            return [pad('x')]
        return []
    if stage == 'roster':
        names = house.roster(screen)
        if not names or screen.selected not in names:
            return []
        # A SELECT or a previous roster visit can leave the list scrolled to
        # the middle. Only a walk beginning at the hero/top can prove a
        # defeated general absent; a partial tail must never evict residents.
        if not state.get('top_confirmed'):
            if names[0] == 'どうし' and screen.selected == 'どうし':
                state['top_confirmed'] = True
            else:
                state['top_seeks'] = state.get('top_seeks', 0) + 1
                if state['top_seeks'] > MAX_SCAN:
                    state['failure'] = 'roster_top_unconfirmed'
                    _stage(state, 'blocked')
                    return [pad('b')]
                return [pad('up')]
        selected = screen.selected
        if selected in state['names']:
            first = state['names'][0] if state['names'] else None
            at_end = (house._roster_scroll_end(screen, frame)
                      or (len(names) < receipts.VISIBLE_NAME_ROWS
                          and selected == names[-1]))
            wrapped = selected == first and state.get('list_moves', 0) > 0
            if (at_end or wrapped) and _complete_scan(mem, state):
                state.pop('end_stall', None)
                state.pop('end_stall_selected', None)
                return [pad('b')]
            if (len(state['names']) >= receipts.VISIBLE_NAME_ROWS
                    and selected == names[-1]):
                # Full-page non-wrapping list (live 299x224): down on the last
                # row never moves and never shows scroll-end pixels. Count the
                # stall and let a passing membership proof finish the walk.
                if state.get('end_stall_selected') == selected:
                    stall = int(state.get('end_stall') or 0) + 1
                else:
                    stall = 1
                    state['end_stall_selected'] = selected
                state['end_stall'] = stall
                if stall >= END_STALL_READS and _complete_scan(mem, state):
                    state.pop('end_stall', None)
                    state.pop('end_stall_selected', None)
                    return [pad('b')]
            else:
                state.pop('end_stall', None)
                state.pop('end_stall_selected', None)
            if state['phase_reads'] > STATUS_RETRIES:
                if _roster_end_assumable(state):
                    # The whole roster is enumerated; only the end proof is
                    # missing. Continue into the per-general tasks (no eviction)
                    # instead of failing the month and repeating it next month.
                    state['roster_end_assumed'] = True
                    state.pop('end_stall', None)
                    state.pop('end_stall_selected', None)
                    _log(mem, 'post_month_roster_end_assumed',
                         observed_metric={'names': len(state['names']),
                                          'dirty': list(state.get('dirty') or ()),
                                          'selected': selected},
                         reason='名簿上限まで列挙できたため末尾の未確認は置き、将軍別の確認へ進む')
                    _stage(state, 'process')
                    return [pad('b')]
                state['failure'] = 'roster_end_unconfirmed'
                _log(mem, 'post_month_status_unverified',
                     observed_metric={'names': len(state['names']),
                                      'selected': selected,
                                      'top_confirmed': bool(state.get('top_confirmed')),
                                      'dirty': list(state.get('dirty') or ()),
                                      'phase_reads': state['phase_reads'],
                                      'end_stall': int(state.get('end_stall') or 0)},
                     reason='将軍一覧の末尾が確認できず、通常の出撃計画を保留')
                _stage(state, 'blocked')
                return [pad('b')]
            state['list_moves'] = state.get('list_moves', 0) + 1
            return [pad('down')]
        if selected in state['statuses'] and selected not in state.get('dirty', ()):
            # The roster page establishes current membership, but does not
            # require pressing A on a cached, unchanged general.
            state['names'].append(selected)
            state['list_moves'] = state.get('list_moves', 0) + 1
            state.pop('end_stall', None)
            state.pop('end_stall_selected', None)
            return [pad('down')]
        # Wait for a stable highlight so that the triangle belongs to this
        # general, not to the preceding row's last rendered frame.
        if state.get('highlight') != selected:
            state['highlight'] = selected
            state['marker_reads'] = 0
            return []
        # The manifest already proved this resident's castle. If HP/egg are
        # still dirty, open the status once for those fields without another
        # triangle read or SELECT locate.
        if selected in state.get('manifest_satisfied', ()):
            state.setdefault('markers', {})[selected] = {
                'castle_candidate': (state.get('manifest') or {}).get('castle'),
                'source': 'hero_castle_manifest'}
            return _open_status(screen, state, frame)
        # The white selection triangle can be absent in one rendered frame.
        # The old path sampled it once then unconditionally entered the status
        # panel, so every failed sample became an expensive SELECT visit.
        marker = _marker(mem, frame, general=selected)
        reads = int(state.get('marker_reads') or 0) + 1
        state['marker_reads'] = reads
        candidate = marker.get('castle_candidate') if isinstance(marker, dict) else None
        static = _static_marker(state, marker)
        if frame is not None and not candidate and reads < MARKER_RETRIES and not static:
            return []  # stay on the same highlighted general for another frame
        state.setdefault('markers', {})[selected] = marker
        if not candidate:
            _log(mem, 'post_month_roster_marker_unverified', general=selected,
                 observed_metric={'reads': reads, 'marker_seen': isinstance(marker, dict),
                                  'source': marker.get('source') if isinstance(marker, dict) else None,
                                  'marker_reason': (marker.get('marker_reason')
                                                    if isinstance(marker, dict) else None),
                                  'icons_seen': (marker.get('icons_seen')
                                                 if isinstance(marker, dict) else None),
                                  'marker_box': (marker.get('box')
                                                 if isinstance(marker, dict) else None)},
                 reason='一覧の三角マークから城を確定できず個別状態確認後にSELECTへ委ねる')
        else:
            _log(mem, 'post_month_roster_marker_mapped', general=selected, castle=candidate,
                 observed_metric={'reads': reads, 'source': marker.get('source'),
                                  'marker_reason': marker.get('marker_reason')},
                 reason='一覧の選択三角マークから在城候補を観測した')
        return _open_status(screen, state, frame)
    if stage == 'status':
        info = house._observe_status(screen, mem)
        if not info or info.get('general') != state.get('selected'):
            if state['phase_reads'] >= STATUS_RETRIES:
                state['failure'] = 'status_unreadable'
                _log(mem, 'post_month_status_unverified',
                     general=state.get('selected'),
                     reason='将軍個別の状態を読めないため通常の出撃計画を保留')
                _stage(state, 'blocked')
                return [pad('b')]
            return []
        name = info['general']
        loc = _location(screen)
        marker = (state.get('markers') or {}).get(name)
        row = {**info, 'location': loc, 'status_text': house.location_row(screen),
               'marker': marker, 'tick': int(mem.get('tick') or 0)}
        state['names'].append(name)
        state['statuses'][name] = row
        state.pop('end_stall', None)
        state.pop('end_stall_selected', None)
        if name in state.get('dirty', []):
            state['dirty'].remove(name)
        if loc in ('camp', 'march', 'carry'):
            _forget_field_residents(mem, name)
            _forget_pending(mem, name)
        elif loc == 'castle':
            # Existing named castle rosters are still valid when the observed
            # status says resident. Do not repeat a costly SELECT for them.
            old = [castle for castle, residents in (_world(mem).get('garrison') or {}).items()
                   if isinstance(residents, list) and name in residents]
            candidate = marker.get('castle_candidate') if isinstance(marker, dict) else None
            if candidate:
                if _assign(mem, name, candidate, evidence='roster_triangle'):
                    _forget_pending(mem, name)
                else:
                    # The triangle and the status agree on the castle; only the
                    # Y map still lists it as enemy. Keep the name, skip the
                    # repeated SELECT, and promote it when ownership lands.
                    _note_pending_castle(mem, name, candidate, evidence='roster_triangle')
            elif len(old) == 1:
                from ..os_layer.world import _owned
                if old[0] in _owned(mem):
                    row.update(castle=old[0], castle_evidence='previous_named_roster')
                    _forget_pending(mem, name)
        proof = state.get('status_return')
        if isinstance(proof, dict) and proof.get('general') == name:
            state['close_probe_pending'] = True
        else:
            state.pop('status_return', None)
        _stage(state, 'roster_next')
        return [pad('b')]
    if stage == 'roster_next':
        if house.roster(screen):
            _stage(state, 'roster')
            state['list_moves'] = state.get('list_moves', 0) + 1
            return [pad('down')]
        return []
    if stage == 'process':
        if screen.kind != 'map':
            if screen.kind in {'main_menu', 'castle_menu', 'castle_info', 'general_list'} or house.roster(screen):
                return [pad('b')]
            return None
        if not state['tasks']:
            _finish(mem, state)
            return []
        job = state['tasks'].pop(0)
        state['job'] = job
        if job['kind'] == 'repair' and not state['statuses'][job['name']].get('castle'):
            _task_complete(mem, state, result='location_unverified')
            return []
        _stage(state, 'task_menu', seeks=0, locate_retries=0)
        return [pad('x')]
    if stage == 'task_menu':
        if screen.kind == 'main_menu' and screen.hand:
            direction = menu_to(screen, 'しょうぐん')
            if direction == 'here':
                _stage(state, 'task_select', seeks=0)
                return [pad('a')]
            return [direction] if direction else []
        if screen.kind == 'map' and state['phase_reads'] in (3, 6):
            return [pad('x')]
        return []
    if stage == 'task_select':
        names = house.roster(screen)
        if not names:
            return []
        job = state['job']
        if screen.selected == job['name']:
            _stage(state, 'task_jump')
            return [pad('select')]
        if state.get('seeks', 0) >= MAX_SCAN:
            if job.get('kind') == 'manifest':
                return _manifest_fail(mem, state, 'general_not_found')
            _task_complete(mem, state, result='general_not_found')
            return [pad('b')]
        state['seeks'] += 1
        if job.get('kind') == 'manifest' and job['name'] not in names:
            # The hero is the first roster row.  A list opened at a later
            # scroll position must walk up to it; Down would move away.
            return [pad('up')]
        direction = menu_to(screen, job['name'])
        return [pad('down')] if direction is None else [pad('a')] if direction == 'here' else [direction]
    if stage == 'task_jump':
        job = state['job']
        name = job['name']
        if screen.kind == 'world_map':
            return [pad('y')]
        if screen.kind != 'map':
            if (job['kind'] in ('locate', 'manifest', 'repair') and house.roster(screen)
                    and screen.selected == name
                    and state['phase_reads'] in (4, 8)):
                # A SELECT can be lost during the roster's closing animation.
                # Resend it even for repair: an A on the still-open roster
                # would open the individual status, not the castle menu.
                return [pad('select')]
            if state['phase_reads'] >= STATUS_RETRIES:
                if job['kind'] == 'locate':
                    return _retry_location(mem, state, screen, 'select_jump_unconfirmed')
                if job['kind'] == 'manifest':
                    return _manifest_fail(mem, state, 'select_jump_unconfirmed')
                _task_complete(mem, state, result='jump_unconfirmed')
                return [pad('b')]
            return []
        if job['kind'] == 'manifest':
            _stage(state, 'locate_open')
            return [pad('a')]
        if job['kind'] == 'locate':
            # SELECT is the observed anchor for *this named general*. The
            # cursor itself can completely cover the castle roof, so the
            # roof detector is not a prerequisite for opening its menu.
            _stage(state, 'locate_open')
            return [pad('a')]
        if job['kind'] == 'recall':
            camps = own_camps(frame) if frame is not None else []
            cursor = screen.cursor
            if not camps and cursor is None:
                _task_complete(mem, state, result='camp_cursor_unobserved')
                return []
            # SELECT in the named roster centres the field cursor on that
            # general. A tent hidden under its cursor can still be opened by
            # recall.py's one-shot occlusion path; never reuse an older map
            # coordinate without this observed SELECT->map transition.
            camp = (min(camps, key=lambda c: sum(abs(c['target'][a] - cursor[a])
                                                 for a in (0, 1)) if cursor else 0)
                    if camps else {'target': list(cursor), 'kind': None})
            if (cursor and sum(abs(camp['target'][a] - cursor[a])
                               for a in (0, 1)) > 12):
                _task_complete(mem, state, result='selected_general_camp_unconfirmed')
                return []
            _flow(mem)['recall'] = {
                'stage': 'to_camp', 'target': list(camp['target']),
                'last_seen': list(camp['target']), 'steps': 0,
                'general': name, 'camp_kind': camp.get('kind')}
            _stage(state, 'wait_recall', recall_started=True)
            recall = recall_flow.camp_recall_step(screen, mem, frame)
            return recall if recall is not None else []
        if job['kind'] == 'repair':
            if mem.get('house'):
                return []
            source = state['statuses'][name].get('castle')
            if not source:
                _task_complete(mem, state, result='repair_source_unconfirmed')
                return []
            # SELECT is the observed anchor for *this named general*. The
            # cursor brackets can completely cover the castle roof, so the
            # roof image is not a prerequisite for opening its menu — the
            # same occlusion that ended the locate task before #3487. The
            # house castle_verify FSM reads the actual castle name and its
            # resident list (and backs out of a camp/menu mismatch) before
            # any castle_sortie / card / destination / receipt transaction.
            mem['house'] = {
                'phase': 'castle_verify', 'chapter': mem['chapter'],
                'roster_month': mem.get('month'), 'age': 0, 'total': 0,
                'seen': [], 'pending': [], 'month_scan': False,
                'general': name, 'source': source, 'castles': []}
            _stage(state, 'wait_repair')
            return [pad('a')]
        _task_complete(mem, state, result='unknown_task')
        return []
    if stage == 'locate_open':
        job = state.get('job') or {}
        if screen.kind in ('castle_menu', 'castle_info'):
            _stage(state, 'locate_info')
            return []
        if screen.kind == 'map' and state['phase_reads'] in (3, 6):
            # The first A may have been swallowed while SELECT closed the
            # roster; press again only on a confirmed map, boundedly.
            return [pad('a')]
        if state['phase_reads'] >= STATUS_RETRIES:
            if job.get('kind') == 'manifest':
                return _manifest_fail(mem, state, 'castle_menu_not_opened')
            return _retry_location(mem, state, screen, 'castle_menu_not_opened')
        return []
    if stage == 'locate_info':
        job = state.get('job') or {}
        if job.get('kind') == 'manifest':
            if screen.kind == 'general_list':
                _stage(state, 'manifest_list')
                return []
            manifest = state.get('manifest') or {}
            castle = map_registration._label(screen, mem.get('chapter'))
            if (castle is not None and manifest.get('castle')
                    and castle != manifest.get('castle')):
                return _manifest_fail(mem, state, 'hero_castle_changed')
            if screen.kind == 'castle_menu' and screen.hand:
                toward = menu_to(screen, 'しゅつげき')
                if toward == 'here' and state['phase_reads'] in (1, 4, 8):
                    _stage(state, 'manifest_list')
                    return [pad('a')]
                if toward and toward != 'here':
                    return [toward]
            if state['phase_reads'] >= STATUS_RETRIES:
                return _manifest_fail(mem, state, 'general_list_not_opened')
            return []
        name = state['job']['name']
        # The native status panel keeps the castle menu visible behind it, so
        # the frame stays classified 'castle_menu' (the しゅつげき/ステータス box
        # is still drawn) even after ステータス is opened. Never claim a location
        # from the menu header alone: _label answers only when the panel text
        # carries the CASTLE_STATUS rows (name + しゅうにゅう/レベル/しょうぐん).
        castle = map_registration._label(screen, mem.get('chapter'))
        if castle is not None:
            if _assign(mem, name, castle, evidence='native_castle_name'):
                _task_complete(mem, state, result='castle_verified')
                return [pad('b')]
            # The panel named the castle. Waiting out STATUS_RETRIES here only
            # repeated the same three attempts every month (g1139 ポワソン
            # 2026-11-07): the missing fact was ownership, not the name, and the
            # Y map records it minutes later. Remember the name and finish.
            _note_pending_castle(mem, name, castle, evidence='native_castle_name')
            _task_complete(mem, state, result='castle_named_unowned')
            return [pad('b')]
        if screen.kind == 'castle_menu' and screen.hand:
            toward = menu_to(screen, 'ステータス')
            if toward == 'here' and state['phase_reads'] in (1, 4, 8):
                return [pad('a')]
            if toward and toward != 'here':
                return [toward]
        if state['phase_reads'] >= STATUS_RETRIES:
            return _retry_location(mem, state, screen, 'castle_status_not_read')
        return []
    if stage == 'locate_reset':
        if screen.kind == 'map':
            _stage(state, 'task_menu', seeks=0)
            return [pad('x')]
        if screen.kind in ('castle_menu', 'castle_info', 'main_menu', 'general_list', 'text'):
            return [pad('b')]
        return []
    if stage == 'wait_recall':
        if _flow(mem).get('recall'):
            recall = recall_flow.camp_recall_step(screen, mem, frame)
            return recall if recall is not None else []
        job = state.get('job') or {}
        name = job.get('name') if isinstance(job, dict) else None
        if job.get('kind') == 'recall' and isinstance(name, str) and name:
            retries = state.get('recall_task_retries')
            retries = retries if isinstance(retries, dict) else {}
            used = retries.get(name)
            used = used if type(used) is int and used >= 0 else 0
            if (used < RECALL_TASK_RETRIES
                    and not _recall_was_dispatched(
                        mem, name, since=state.get('started_tick') or 0)):
                retries[name] = used + 1
                state['recall_task_retries'] = retries
                state.setdefault('tasks', []).append(dict(job))
                _log(mem, 'post_month_recall_requeued', general=name,
                     observed_metric={'attempt': used + 1,
                                      'limit': RECALL_TASK_RETRIES},
                     reason='帰還Aの送信前に戦闘・イベントが割り込み、本人の帰還が'
                            '未送信のため同月内に再試行する')
                _task_complete(mem, state, result='recall_interrupted_requeued')
                return []
        _task_complete(mem, state, result='recall_finished_or_deferred')
        return []
    if stage == 'wait_repair':
        if mem.get('house'):
            return house.step(screen, mem, frame) or []
        _task_complete(mem, state, result='house_flow_finished')
        return []
    return []
