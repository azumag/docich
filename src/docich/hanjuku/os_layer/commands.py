"""OS層: command primitives that turn an intent into bounded pad input.

Pad construction and hand-cursor menu movement (``menu_to`` and the guarded
variant) live here. Functions in this module know *how* to press toward a
visible label; they never decide *which* label the strategy or judgment
layers want.
"""
from __future__ import annotations

from .screen import Screen, month_confirmation_positions
from . import menu_feedback
from ..decision_log import _record
from .readers import _current, _options


FPS = 60
VISIBLE_LIST_BATCH_MAX = 4
VISIBLE_LIST_RELEASE_MS = 50


def pad(button, frames=6):
    return {'type': 'pad', 'buttons': [button], 'hold_ms': max(16, round(frames * 1000 / FPS))}


def menu_to(screen: Screen, label: str, *, exact=True):
    """One press toward ``label`` (menu with a hand cursor); 'here' when on it."""
    cur = _current(screen)
    target = [(x, y, w) for x, y, w in _options(screen)
              if (w == label if exact else w.startswith(label))]
    if not cur or not target:
        return None
    if cur[2] == label or (not exact and cur[2].startswith(label)):
        return 'here'
    tx, ty, _ = min(target, key=lambda t: abs(t[1] - cur[1]) + abs(t[0] - cur[0]))
    if abs(ty - cur[1]) > 2:
        return pad('down' if ty > cur[1] else 'up')
    return pad('right' if tx > cur[0] else 'left')


# Count failed movement within one menu/target episode, independently of cursor
# jitter. The legacy counter survived shop round trips and hotloads forever.
# Re-evaluate that old state once; never migrate an unobserved target arrival.
MENU_NAV_SCHEMA = 2
MENU_NAV_PRESS_LIMIT = 4
MENU_NAV_RETRY_HOLD = 6          # held observations (~9 s) after the cancel B before retrying
# A real cursor step moves a whole menu row (16 px in the measured layouts), so
# anything smaller is detection jitter, not progress toward the target.
MENU_NAV_PROGRESS_PX = 4

_FEEDBACK_MENU_KINDS = frozenset({'month_menu', 'main_menu', 'castle_menu', 'general_list',
                                'yes_no', 'shop_list', 'card_select', 'sortie_confirm',
                                'battle_menu', 'monster_menu', 'egg_battle_menu',
                                'okunote_menu', 'egg_choice_menu', 'name_entry'})


def menu_navigation_reading(screen):
    """Current visible row and readable destinations, for feedback timing only.

    This never creates an input, a target selection, or an arrival receipt.
    Unknown cursors/rows and clipped/fading menus keep normal cadence.
    """
    semantic = menu_feedback.reading(screen)
    if semantic is not None or (screen.kind == 'month_menu' and screen.has('じゅうじキー')):
        return semantic
    if screen.kind not in _FEEDBACK_MENU_KINDS:
        return None
    options = _options(screen)
    cur = _current(screen)
    if screen.hand:
        positions = month_confirmation_positions(screen) if screen.kind == 'month_menu' else []
        if positions:
            options = [(x, y, w) for x, y, w in options if (x, y) in positions]
        if cur is None or not cur[2] or cur not in options:
            return None
        x, y, _ = cur
    else:
        if type(screen.menu_cursor) is not int:
            return None
        rows = [(x, y, w) for x, y, w in options if y == screen.menu_cursor and w]
        if len(rows) != 1:
            return None
        x, y, _ = rows[0]
    directions = []
    for button, sign in (('up', -1), ('down', 1)):
        if any(abs(tx - x) <= 8 and (ty - y) * sign > 2 for tx, ty, word in options if word):
            directions.append(button)
    # menu_to/name_step also move horizontally, one observed press at a
    # time. Reading the next frame sooner never authorizes a confirm.
    for button, sign in (('left', -1), ('right', 1)):
        if any(abs(ty - y) <= 2 and (tx - x) * sign > 2 for tx, ty, word in options if word):
            directions.append(button)
    return {'kind': screen.kind, 'cursor': [x, y], 'directions': directions} if directions else None


def castle_list_batch(screen: Screen, label: str):
    """Tap along a fully visible castle-list path, or keep the caller's one step.

    This never scrolls beyond a visible row, wraps, or confirms a destination.
    Each tap has an explicit released interval so it is a distinct input.
    """
    return _visible_list_batch(screen, label, kind='text', menu='castle_list', x=168)


def general_list_batch(screen: Screen, label: str):
    """Move to a visible sortie general; never scan, scroll or confirm."""
    return _visible_list_batch(screen, label, kind='general_list', menu='castle_roster', x=144)


def roster_list_batch(screen: Screen, label: str):
    """Move to a visible partial-survey target, without skipping survey reads."""
    return _visible_list_batch(screen, label, kind='text', menu='global_roster', x=168)


def fort_list_batch(screen: Screen, label: str):
    return _visible_list_batch(screen, label, kind='text', menu='fort_roster', x=144)


def egg_recovery_list_batch(screen: Screen, label: str):
    return _visible_list_batch(screen, label, kind='text', menu='egg_recovery', x=32,
                               ys=(47, *range(71, 168, 16)))


def starter_list_batch(screen: Screen, label: str, *, limit=VISIBLE_LIST_BATCH_MAX):
    from .starter_view import NAME_LEFT, UPPER_ROW_Y
    return _visible_list_batch(screen, label, kind='starter_list', menu='starter_upper',
                               x=NAME_LEFT, ys=UPPER_ROW_Y, limit=limit)


def _visible_list_batch(screen, label, *, kind, menu, x, ys=tuple(range(39, 152, 16)),
                        limit=VISIBLE_LIST_BATCH_MAX):
    reading = menu_feedback.reading(screen)
    if not reading or reading.get('kind') != kind or reading.get('menu') != menu:
        return None
    if (reading['cursor'][0] != x or reading['cursor'][1] not in ys
            or type(limit) is not int or not 2 <= limit <= VISIBLE_LIST_BATCH_MAX):
        return None
    rows = [(y, word) for rx, y, word in _options(screen)
            if rx == x and y in ys]
    target = [y for y, word in rows if word == label]
    if len(target) != 1:
        return None
    current = ys.index(reading['cursor'][1])
    target_index = ys.index(target[0])
    delta = target_index - current
    distance = abs(delta)
    if distance < 2:
        return None
    sign = 1 if delta > 0 else -1
    # The full path to the requested row must be present, even when the
    # four-tap cap means this observation only traverses its first part.
    if any(sum(y == ys[current + sign * step] for y, _ in rows) != 1
           for step in range(distance + 1)):
        return None
    button = 'down' if sign > 0 else 'up'
    actions = [pad(button)]
    for _ in range(min(distance, limit) - 1):
        actions.extend([{'type': 'wait', 'ms': VISIBLE_LIST_RELEASE_MS}, pad(button)])
    return actions


def menu_navigation_feedback(state, actions):
    """Reobserve a short move or a bounded card/visible-list batch sooner."""
    reading = state.get('menu_navigation_reading')
    if (not isinstance(reading, dict) or type(state.get('step')) is not int
            or type(reading.get('step')) is not int or reading.get('step') != state['step']
            or (reading.get('kind') not in _FEEDBACK_MENU_KINDS
                and not menu_feedback.valid_context(reading.get('kind'), reading.get('menu')))
            or reading.get('kind') != state.get('screen_kind')
            or not isinstance(actions, list) or not 1 <= len(actions) <= 7):
        return False
    directions = reading.get('directions')
    cursor = reading.get('cursor')
    if (not isinstance(directions, list) or not 1 <= len(directions) <= 4
            or any(d not in ('up', 'down', 'left', 'right') for d in directions)
            or not isinstance(cursor, list) or len(cursor) != 2
            or any(type(n) is not int or not 0 <= n < bound for n, bound in zip(cursor, (256, 224)))):
        return False
    quantity = (reading.get('kind'), reading.get('menu')) in (
        ('month_menu', 'soldier_quantity'), ('shop_quantity', 'merchant_quantity'))
    if quantity and (len(actions) != 1 or cursor not in ([216, 199], [224, 199])):
        return False
    released_batch = (len(actions) > 1 and
                      (reading.get('kind'), reading.get('menu')) in
                      (('text', 'castle_list'), ('general_list', 'castle_roster'),
                       ('text', 'global_roster'), ('text', 'fort_roster'),
                       ('text', 'egg_recovery'), ('starter_list', 'starter_upper')))
    if released_batch:
        if len(actions) not in (3, 5, 7):
            return False
        for release in actions[1::2]:
            if (not isinstance(release, dict) or set(release) != {'type', 'ms'}
                    or release.get('type') != 'wait' or type(release.get('ms')) is not int
                    or release['ms'] != VISIBLE_LIST_RELEASE_MS):
                return False
        actions = actions[::2]  # local view only; preserve the complete plan
    if len(actions) > 4:
        return False
    batch = len(actions) > 1
    # Card batches retain their existing adjacent taps. Visible-list batches have
    # explicit releases. Neither path authorizes a confirmation or a receipt.
    if batch and not (released_batch or (reading.get('kind') == 'card_select'
                                      and reading.get('menu') == 'sortie_cards')):
        return False
    direction = None
    for action in actions:
        if not isinstance(action, dict):
            return False
        buttons, hold = action.get('buttons'), action.get('hold_ms')
        if not (action.get('type') == 'pad' and isinstance(buttons, list) and len(buttons) == 1
                and buttons[0] in ('up', 'down', 'left', 'right') and buttons[0] in directions
                and type(hold) is int and 16 <= hold <= 100):
            return False
        if quantity and hold != 100:
            return False
        if batch and (buttons[0] not in ('up', 'down') or hold != 100
                      or (direction is not None and buttons[0] != direction)):
            return False
        direction = buttons[0]
    return True


def status_panel_feedback(state, actions):
    """One early look after the monthly owner opens/closes a read-only status.

    The orchestrator replaces this annotation on every observation. It says
    nothing about delivery, the panel opening, or the general's actual state.
    """
    reading = state.get('status_panel_feedback')
    if (not isinstance(reading, dict) or set(reading) != {'step', 'kind', 'transition'}
            or type(state.get('step')) is not int or type(reading.get('step')) is not int
            or reading['step'] != state['step']
            or reading.get('kind') != 'text' or state.get('screen_kind') != 'text'
            or reading.get('transition') not in ('general_status_open', 'general_status_close')
            or not isinstance(actions, list) or len(actions) != 1
            or not isinstance(actions[0], dict)):
        return False
    action = actions[0]
    button = 'a' if reading['transition'] == 'general_status_open' else 'b'
    return (action.get('type') == 'pad' and action.get('buttons') == [button]
            and type(action.get('hold_ms')) is int and action['hold_ms'] == 100)


def _menu_distance(screen: Screen, label: str):
    """Manhattan distance from the hand to the row :func:`menu_to` would pick."""
    cur = _current(screen)
    target = [(x, y, w) for x, y, w in _options(screen) if w == label]
    if not cur or not target:
        return None
    tx, ty, _ = min(target, key=lambda t: abs(t[1] - cur[1]) + abs(t[0] - cur[0]))
    return abs(ty - cur[1]) + abs(tx - cur[0])


def guarded_menu_to(screen: Screen, mem, label: str, *, key: str, limit=MENU_NAV_PRESS_LIMIT):
    """Move toward an observed label; cancel once after bounded non-progress.

    A may confirm a purchase or an unwanted answer, so recovery never emits
    it. Only the caller's explicit ``'here'`` branch may confirm the target.
    """
    move = menu_to(screen, label)
    if not isinstance(move, dict):
        mem.pop(key, None)
        return move
    buttons = move.get('buttons')
    direction = buttons[0] if isinstance(buttons, list) and buttons else None
    state = mem.get(key)
    distance = _menu_distance(screen, label)
    context = [screen.kind, label, mem.get('chapter'), mem.get('month'),
               bool(month_confirmation_positions(screen))]
    same = (isinstance(state, dict) and state.get('schema') == MENU_NAV_SCHEMA
            and state.get('context') == context and state.get('direction') == direction
            and direction is not None)
    cancel_sent = False
    if not same:
        presses = 0
    else:
        before = state.get('distance')
        progressed = (isinstance(distance, int) and isinstance(before, int)
                      and distance + MENU_NAV_PROGRESS_PX <= before)
        previous = state.get('presses')
        previous = previous if type(previous) is int and previous >= 0 else 0
        presses = 0 if progressed else min(previous + 1, limit + 1)
        cancel_sent = not progressed and state.get('cancel_sent') is True
    held = int(state.get('held') or 0) if same and cancel_sent else 0
    state = mem[key] = {'schema': MENU_NAV_SCHEMA, 'context': context,
                        'direction': direction, 'distance': distance,
                        'presses': presses, 'cancel_sent': cancel_sent, 'held': held}
    if presses < limit:
        return move
    if not cancel_sent:
        state['cancel_sent'] = True
        _record(mem, 'menu_nav_stuck', screen=screen.kind, choice=label,
                observed_metric={'presses': presses, 'direction': direction,
                                 'route': key},
                reason='目標行へ近づかないため確定せずBで戻り、前面を再観測する')
        return [pad('b')]
    # 2026-10-11 1-11 and 2-1: right after the month menu appeared, the moves
    # and the cancel B were ignored and this hold lasted until the bot's 150 s
    # no-input fallback, whose fresh navigation then worked. Retry the same
    # bounded move/cancel episode after a short wait instead (never A).
    state['held'] = held + 1
    if state['held'] < MENU_NAV_RETRY_HOLD:
        return []
    mem[key] = {'schema': MENU_NAV_SCHEMA, 'context': context, 'direction': direction,
                'distance': distance, 'presses': 0, 'cancel_sent': False, 'held': 0}
    _record(mem, 'menu_nav_retry', screen=screen.kind, choice=label,
            observed_metric={'held': state['held'], 'direction': direction, 'route': key},
            reason='Bで戻った後も目標行へ近づかないため、短い待機の後に移動をやり直す')
    return move


# ---------------------------------------------------------------- name entry
# ひらがな page of the name grid (measured). The hand can hide the glyph it
# passes over, so the fixed layout supplies target cells OCR cannot see.
KANA_GRID = ('あいうえおらりるれろ', 'かきくけこわをん  ', 'さしすせそっゃゅょ ',
             'たちつてとがぎぐげご', 'なにぬねのざじずぜぞ', 'はひふへほだぢづでど',
             'まみむめもばびぶべぼ', 'やゆよ  ぱぴぷぺぽ')


def kana_cell(ch):
    for r, row in enumerate(KANA_GRID):
        c = row.find(ch)
        if c >= 0:
            return (80 + 16 * c if c < 5 else 168 + 16 * (c - 5)), 87 + 16 * r
    return None


def _human_menu_open(cur, owner, mem=None):
    if (not cur.get('egg_battle')
            and all(type(cur.get(k)) is int and cur[k] > 0 for k in ('ally_hp', 'enemy_hp'))):
        cur['command_open_pending'] = {'owner': owner, 'waits': 0}
        if mem is not None:
            _record(mem, 'battle_command_open_requested',
                    ally=cur.get('ally'), enemy=cur.get('enemy'),
                    observed_metric={'owner': owner},
                    reason='通常戦闘の救済メニューを開くBを計画。送信・表示成功は未確認')
    return [pad('b')]


def _battle_menu_to(screen, label):
    if screen.hand:
        return menu_to(screen, label)
    target = [y for x, y, word in _options(screen) if word == label]
    if screen.menu_cursor is None or not target:
        return None
    y = min(target, key=lambda y: abs(y - screen.menu_cursor))
    if y == screen.menu_cursor:
        return 'here'
    return pad('down' if y > screen.menu_cursor else 'up')
