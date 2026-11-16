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

from ... import hanjuku_chart as chart
from ..decision_log import _record
from ..os_layer import roster as _roster
from ..os_layer.commands import fort_list_batch, menu_to, pad
from ..os_layer.readers import (
    _current, _options, fort_list_open, is_camp_menu, is_fort_menu)
from ..state.regions import plan as _plan_region, world as _world
from . import house

PURSUIT_READ_LIMIT = 90
PURSUIT_LIST_LIMIT = 48
PURSUIT_COOLDOWN = 240        # ticks before the same general is pursued again
PURSUIT_MARCH_MIN = 400       # a just-launched march is left alone this long
LIST_KEY = 'boss_wave_generals'
ABSENT_END_READS = 3          # identical roster reads at the list end = full pass
PURSUIT_STEP = 'PURSUIT'      # step id of the pursuit's own Y jump (y_jump['step'])
CLOSE_LIMIT = 12              # observations a closing probe waits for the map
UNIT_MENU_WAIT = 14           # map reads waiting for the unit's menu to draw


def _drop_probe(mem):
    """End the probe and leave no half-finished PURSUIT Y jump behind.

    A remembered y_jump keeps the map flow steering a view nobody owns; a
    remembered y_jump_return / y_jumped record reads as a finished jump.
    """
    mem.pop('boss_pursuit', None)
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
    if home & lost:
        # A name seen back in a castle garrison was a misread, not a loss.
        mem['generals_lost'] = [g for g in (mem.get('generals_lost') or ()) if g not in home]
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


def _candidate(mem):
    """The oldest boss-bound general that has marched long enough to re-aim."""
    now = int(mem.get('tick') or 0)
    wave = mem.get(LIST_KEY) or {}
    best = None
    for general, e in wave.items():
        launched = e.get('launched')
        if type(launched) is int and now - launched < PURSUIT_MARCH_MIN:
            continue
        last = e.get('pursuit_tick')
        if type(last) is int and now - last < PURSUIT_COOLDOWN:
            continue
        if best is None or (launched or 0) < (best[1].get('launched') or 0):
            best = (general, e)
    return best


def _close(mem, probe, reason):
    probe['phase'] = 'close'
    probe['outcome'] = reason
    entry = (mem.get(LIST_KEY) or {}).get(probe.get('general'))
    if entry is not None:
        entry['pursuit_tick'] = int(mem.get('tick') or 0)
    _record(mem, 'boss_pursuit_unverified', chart_step=probe.get('step'),
            general=probe.get('general'), resulting_event=reason,
            reason='将軍へのSELECTジャンプが成立しないため位置を推測せずメニューを閉じる')


ACTIONABLE = frozenset({'map', 'main_menu', 'general_list', 'castle_menu',
                        'castle_info', 'map_target', 'card_select', 'text',
                        'world_map', 'unknown'})


def step(screen, mem, frame):
    """Owner the screen only while a pursuit is queued or may be started."""
    if (mem.get('chapter') or 0) != 9:
        return None
    track(mem)
    kind = screen.kind
    busy = (mem.get('house') or mem.get('post_month_status')
            or mem.get('month_sub') or mem.get('month_nav')
            or mem.get('month_blind_nav'))
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
        if (kind != 'map' or _plan_region(mem).get('active')
                or not chart.moving_boss_cell(9) or busy):
            return None
        pick = _candidate(mem)
        if pick is None:
            return None
        general, entry = pick
        # A fresh pursuit gets a fresh jump budget; three spent jumps would
        # otherwise end every later pursuit in boss_jump_failed.
        (mem.get('y_jumps') or {}).pop(f'{PURSUIT_STEP}:target', None)
        probe = mem['boss_pursuit'] = {
            'general': general, 'step': entry.get('step'), 'phase': 'open',
            'reads': 0, 'seeks': 0, 'x_retries': 0}
        _record(mem, 'boss_pursuit_queued', chart_step=entry.get('step'), general=general,
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
            return [pad('b')]
        if house.roster(screen):
            probe['phase'] = 'pick'
        else:
            return []

    if probe['phase'] == 'pick':
        names = house.roster(screen)
        if names is None:
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
        pseudo = {'step': 'PURSUIT', 'target': goal, 'source': None}
        if _map._target_roof_under_marker(screen, mem, frame, pseudo):
            entry = (mem.get(LIST_KEY) or {}).get(probe['general'])
            if entry is not None:
                entry['pursuit_tick'] = int(mem.get('tick') or 0)
            _record(mem, 'boss_pursuit_redirected', chart_step=probe.get('step'),
                    general=probe['general'],
                    observed_metric={'target_cell': list(marker)},
                    reason='将軍ジャンプした部隊のいどう先を、実測ボスセルへ合わせて確定する')
            probe['phase'] = 'close'
            probe['outcome'] = 'redirected'
            return [pad('a')]
        from ..os_layer.navigation import _start_y_jump
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
            _close(mem, probe, 'general_not_in_fort')
            return [pad('b')]
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
