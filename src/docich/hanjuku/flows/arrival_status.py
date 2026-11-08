"""Menu-based general-state check after an unverified sortie target.

A map target is a cursor cell, not evidence about the general's whereabouts.
Do not press X inside the target picker. Only after its bounded B cancellation
has been requested may we open X -> しょうぐん -> the named general -> A.
The actual status sentence establishes castle/march/camp, not a guessed roof.
"""
from __future__ import annotations

from ..decision_log import _record
from ..os_layer.commands import menu_to, pad
from ..state.regions import flow as _flow, world as _world
from . import house

PROBE_READ_LIMIT = 70
PROBE_RECENT_TICKS = 240
PROBE_LIST_LIMIT = 48
STATUS_READ_LIMIT = 10
PICKER_KINDS = frozenset({
    'map_target', 'castle_menu', 'general_list', 'card_select',
    'sortie_confirm', 'castle_info',
})


def queue(mem, order, general):
    """Schedule one bounded read after an unsafe target picker is cancelled."""
    if not isinstance(general, str) or not general or general == 'しょうぐん':
        return False
    if mem.get('arrival_status_probe'):
        return False
    step = order.get('step')
    if not isinstance(step, str) or not step:
        return False
    now = int(mem.get('tick') or 0)
    history = mem.setdefault('arrival_status_probe_history', {})
    last = history.get(step)
    if type(last) is int and 0 <= now - last < PROBE_RECENT_TICKS:
        return False
    history[step] = now
    mem['arrival_status_probe'] = {
        'step': step, 'general': general, 'phase': 'backout',
        'reads': 0, 'seeks': 0, 'status_reads': 0, 'x_retries': 0,
    }
    _record(mem, 'arrival_status_probe_queued', chart_step=step, general=general,
            reason='出撃先を確定できなかったため、取消後にX→しょうぐん→個別状態で本人の所在地を確かめる')
    return True


def _closing(mem, probe, reason):
    probe['phase'] = 'close'
    probe['outcome'] = reason
    _record(mem, 'arrival_status_probe_unverified', chart_step=probe['step'],
            general=probe['general'], resulting_event=reason,
            reason='本人と所在地の表示を確認できないため位置を捏造せずメニューを閉じる')


def _location(screen):
    from .post_month_status import CASTLE, MARCH, CAMP
    text = house.location_row(screen)
    if text in CASTLE:
        return 'castle'
    if text in MARCH:
        return 'march'
    if text in CAMP:
        return 'camp'
    return None


def _record_observation(mem, probe, screen, location):
    name = probe['general']
    now = int(mem.get('tick') or 0)
    mem.setdefault('arrival_general_observations', {})[name] = {
        'location': location, 'status_text': house.location_row(screen),
        'step': probe['step'], 'tick': now,
    }
    # A marching/camping general cannot simultaneously be a verified
    # resident. Do not infer its destination from this status sentence.
    if location in ('march', 'camp'):
        for residents in (_world(mem).get('garrison') or {}).values():
            if isinstance(residents, list) and name in residents:
                residents.remove(name)
        unknown = _world(mem).setdefault('general_location_unknown', [])
        if isinstance(unknown, list) and name not in unknown:
            unknown.append(name)
    _record(mem, 'arrival_status_probe_verified', chart_step=probe['step'],
            general=name, observed_metric={'location': location,
                                           'status_text': house.location_row(screen)},
            reason='Xメニューの本人名と個別所在地の文字表示を照合し、屋根画像によらず状態を確認')


def step(screen, mem):
    """Prioritize the safe menu workflow, never confirm a map target."""
    probe = mem.get('arrival_status_probe')
    if not isinstance(probe, dict):
        return None
    # Foreground monthly/repair/recall owners may already be reading this menu.
    # Do not steal their cursor or assume their status panel belongs to us.
    if (mem.get('post_month_status') or mem.get('house')
            or _flow(mem).get('recall')):
        return None
    kind = screen.kind
    if kind not in PICKER_KINDS | {'map', 'main_menu', 'text'}:
        return None
    probe['reads'] += 1
    if probe['reads'] > PROBE_READ_LIMIT + 12:
        mem.pop('arrival_status_probe', None)
        _record(mem, 'arrival_status_probe_timeout', chart_step=probe['step'],
                general=probe['general'], screen=kind,
                reason='将軍の状態確認が上限を超えたため、現在画面を推測で決定しない')
        return [pad('b')] if kind in PICKER_KINDS | {'main_menu'} else None
    if probe['reads'] > PROBE_READ_LIMIT and probe['phase'] != 'close':
        _closing(mem, probe, 'read_limit')

    phase = probe['phase']
    if phase == 'backout':
        if kind == 'map':
            probe['phase'] = 'menu'
            probe['menu_reads'] = 0
            return [pad('x')]
        if kind == 'main_menu':
            # Already on the required screen: no input or new observation
            # is needed merely to change the owner phase.
            probe['phase'] = phase = 'menu'
        elif kind in PICKER_KINDS:
            return [pad('b')]
        else:
            return None

    if phase == 'menu':
        if kind == 'map':
            probe['menu_reads'] = int(probe.get('menu_reads') or 0) + 1
            if probe['menu_reads'] >= 3:
                probe['menu_reads'] = 0
                probe['x_retries'] += 1
                if probe['x_retries'] > 2:
                    _closing(mem, probe, 'x_not_open')
                    return None
                return [pad('x')]
            return []
        if kind == 'main_menu':
            move = menu_to(screen, 'しょうぐん')
            if move == 'here':
                probe['phase'] = 'list'
                return [pad('a')]
            if move:
                return [move]
            _closing(mem, probe, 'general_menu_missing')
            return [pad('b')]
        if house.roster(screen):
            probe['phase'] = 'list'
        else:
            return []

    if probe['phase'] == 'list':
        names = house.roster(screen)
        if not names or screen.selected not in names:
            probe['status_reads'] += 1
            if probe['status_reads'] >= STATUS_READ_LIMIT:
                _closing(mem, probe, 'roster_unreadable')
                return [pad('b')] if kind in PICKER_KINDS else []
            return []
        if screen.selected == probe['general']:
            probe['phase'] = 'status'
            probe['status_reads'] = 0
            return [pad('a')]
        probe['seeks'] += 1
        if probe['seeks'] > PROBE_LIST_LIMIT:
            _closing(mem, probe, 'general_not_found')
            return [pad('b')]
        move = menu_to(screen, probe['general'])
        return [move] if move and move != 'here' else [pad('down')]

    if probe['phase'] == 'status':
        row = next((r for r in screen.lines if r.y == 39), None)
        name = row.span(24, 88).strip() if row else None
        rank = row.span(88, 136).replace(' ', '') if row else None
        location = _location(screen)
        if name == probe['general'] and rank == 'しょうぐん' and location:
            _record_observation(mem, probe, screen, location)
            probe['phase'] = 'close'
            probe['outcome'] = location
            return [pad('b')]
        probe['status_reads'] += 1
        if probe['status_reads'] >= STATUS_READ_LIMIT:
            _closing(mem, probe, 'status_unreadable')
            return [pad('b')] if kind != 'map' else None
        return []

    if probe['phase'] == 'close':
        if kind == 'map':
            mem.pop('arrival_status_probe', None)
            # X/status round trips invalidate the old camera estimate. The
            # existing X -> しろ -> SELECT recovery re-establishes a coordinate
            # before the normal planner resumes.
            mem['uncertain'] = True
            mem['nav_search'] = True
            for key in ('anchor', 'nav_last', 'nav_search_leg'):
                mem.pop(key, None)
            _record(mem, 'arrival_status_probe_done', chart_step=probe['step'],
                    general=probe['general'],
                    observed_metric={'outcome': probe.get('outcome')},
                    reason='将軍の文字状態を確認し、座標は既存のしろ一覧SELECTで再アンカーする')
            return None
        if kind == 'main_menu' or kind in PICKER_KINDS or house.roster(screen):
            return [pad('b')]
        if kind == 'text':
            row = next((r for r in screen.lines if r.y == 39), None)
            if (row and row.span(24, 88).strip() == probe['general']
                    and row.span(88, 136).replace(' ', '') == 'しょうぐん'):
                # A lost B may leave the same named status on screen. Retry B
                # rather than waiting on a panel we already read successfully.
                return [pad('b')]
        return []
    return None
