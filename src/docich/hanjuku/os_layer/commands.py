"""OS層: command primitives that turn an intent into bounded pad input.

Pad construction and hand-cursor menu movement (``menu_to`` and the guarded
variant) live here. Functions in this module know *how* to press toward a
visible label; they never decide *which* label the strategy or judgment
layers want.
"""
from __future__ import annotations

from .screen import Screen, month_confirmation_positions
from ..decision_log import _record
from .readers import _current, _options


FPS = 60


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
                                'okunote_menu', 'egg_choice_menu'})


def menu_navigation_reading(screen):
    """Current visible row and readable destinations, for feedback timing only.

    This never creates an input, a target selection, or an arrival receipt.
    Unknown cursors/rows and clipped/fading menus keep normal cadence.
    """
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
    return {'kind': screen.kind, 'cursor': [x, y], 'directions': directions} if directions else None


def menu_navigation_feedback(state, actions):
    """One short directional press on this observation can reobserve sooner."""
    reading = state.get('menu_navigation_reading')
    if (not isinstance(reading, dict) or type(state.get('step')) is not int
            or type(reading.get('step')) is not int or reading.get('step') != state['step']
            or reading.get('kind') not in _FEEDBACK_MENU_KINDS
            or reading.get('kind') != state.get('screen_kind')
            or not isinstance(actions, list) or len(actions) != 1):
        return False
    directions = reading.get('directions')
    cursor = reading.get('cursor')
    if (not isinstance(directions, list) or not 1 <= len(directions) <= 2
            or any(d not in ('up', 'down') for d in directions)
            or not isinstance(cursor, list) or len(cursor) != 2
            or any(type(n) is not int or not 0 <= n < bound for n, bound in zip(cursor, (256, 224)))):
        return False
    action = actions[0]
    if not isinstance(action, dict):
        return False
    buttons, hold = action.get('buttons'), action.get('hold_ms')
    return (action.get('type') == 'pad' and isinstance(buttons, list) and len(buttons) == 1
            and buttons[0] in ('up', 'down') and buttons[0] in directions
            and type(hold) is int and 16 <= hold <= 100)


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


def _human_menu_open(cur, owner):
    if (not cur.get('egg_battle')
            and all(type(cur.get(k)) is int and cur[k] > 0 for k in ('ally_hp', 'enemy_hp'))):
        cur['command_open_pending'] = {'owner': owner, 'waits': 0}
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
