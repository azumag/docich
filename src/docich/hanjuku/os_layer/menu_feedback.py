"""Fresh cursor feedback for measured list and digit-editor layouts.

Only cadence changes here. No target selection, wrap, batched input or input
receipt is inferred from the layout. Every next move still reads a new frame.
"""
from __future__ import annotations

from .readers import (
    ALL_CARD_NAMES, _measured_card_select, _name_read_cleanly, _options, is_camp_menu, is_fort_menu,
)
from .screen import _card_picker_rows
from ...hanjuku_egg_reference import GENERAL_HP
from ...hanjuku_map_sources import SOURCE_CASTLES

TEXT_MENUS = frozenset({'global_roster', 'castle_list', 'egg_recovery', 'fort_roster',
                        'fort_commands', 'camp_commands', 'battle_cards'})
# Match the source-jump spelling rule (long-vowel marks may move in OCR).
# These names identify the panel only, never ownership or a destination.
_CASTLE_NAMES = frozenset(name.replace('ー', '')
                          for chapter in SOURCE_CASTLES.values() for name in chapter)


def valid_context(kind, menu):
    return ((kind == 'text' and isinstance(menu, str) and menu in TEXT_MENUS)
            or (kind == 'month_menu' and menu == 'soldier_quantity')
            or (kind == 'starter_list' and menu == 'starter_upper'))


def _soldier_quantity_reading(screen):
    """The measured g741 digit editor, excluding payment/yes-no cursors.

    The ones-digit hand hides the tens digit. Only the selected digit must
    be readable here; this supplies cadence, never a quantity or a receipt.
    """
    if not screen.hand:
        return None
    prompts = [line for line in screen.lines if line.y == 183]
    numbers = [line for line in screen.lines if line.y == 199]
    if len(prompts) != 1 or len(numbers) != 1:
        return None
    prompt, number = prompts[0], numbers[0]
    if (prompt.span(24, 152).replace(' ', '') != 'じゅうじキーのさゆうでケタが'
            or number.span(24, 168).replace(' ', '') != 'じょうげですうちがかわりますぞ!'
            or len(dict(number.cells)) != len(number.cells)):
        return None
    left, top, right, bottom = screen.hand
    if (abs(top - 197) > 2 or abs(bottom - 210) > 2
            or not 16 <= right - left <= 24 or not 10 <= bottom - top <= 16):
        return None
    x = 216 if left < 200 else 224
    if not 0 < x - right <= 4:
        return None
    cells = dict(number.cells)
    if cells.get(x) not in tuple('0123456789') or cells.get(224) not in tuple('0123456789'):
        return None
    return {'kind': 'month_menu', 'menu': 'soldier_quantity', 'cursor': [x, 199],
            'directions': ['up', 'down', 'right' if x == 216 else 'left']}


def _named_rows(screen, x, ys):
    rows = []
    for line in screen.lines:
        if line.y not in ys:
            continue
        # Only a readable current row authorizes feedback. Unknown pixels
        # elsewhere can be map art or a clipped option, never proof that a
        # roster is complete (this reader creates no membership receipt).
        word = dict(line.spans()).get(x)
        if word:
            if ((word not in GENERAL_HP and word != 'どうし')
                    or not _name_read_cleanly(line, word)):
                return None
            rows.append((x, line.y, word))
    if not rows or len({y for _, y, _ in rows}) != len(rows):
        return None
    return rows


def _castle_rows(screen):
    rows = []
    for line in screen.lines:
        if line.y not in range(39, 152, 16):
            continue
        word = dict(line.spans()).get(168)
        if word:
            if (word.replace('ー', '') not in _CASTLE_NAMES
                    or not _name_read_cleanly(line, word)):
                return None
            rows.append((168, line.y, word))
    if not rows or len({y for _, y, _ in rows}) != len(rows):
        return None
    return rows


def _hand_cursor(screen, rows):
    if not screen.hand:
        return None
    right, y = screen.hand[2], screen.hand[1] + 6
    current = [(x, row) for x, row, _ in rows
               if 0 <= x - right <= 8 and abs(row - y) <= 2]
    return current[0] if len(current) == 1 else None


def _reading(screen, menu, rows, cursor, *, scrolling=False):
    if not rows or cursor is None:
        return None
    x, y = cursor
    # Scrolling past a visible end is already a supported one-row command.
    # A fresh current row proves the menu, not the existence of another row.
    # The next observation must still prove movement/end/selected identity.
    directions = ['up', 'down'] if scrolling else [
        button for button, sign in (('up', -1), ('down', 1))
        if any(abs(tx - x) <= 8 and (ty - y) * sign > 2 for tx, ty, _ in rows)]
    return {'kind': screen.kind, 'menu': menu, 'cursor': [x, y],
            'directions': directions} if directions else None


def reading(screen):
    if screen.hand is not None and (
            not isinstance(screen.hand, (tuple, list)) or len(screen.hand) != 4
            or any(type(value) is not int for value in screen.hand)):
        return None
    if screen.kind == 'month_menu' and screen.has('じゅうじキー'):
        return _soldier_quantity_reading(screen)
    if screen.kind == 'general_list' and screen.has('しゅつげき') and screen.has('ステータス'):
        rows = _named_rows(screen, 144, range(39, 152, 16))
        return _reading(screen, 'castle_roster', rows, _hand_cursor(screen, rows or []), scrolling=True)
    if screen.kind == 'card_select':
        inventory = _measured_card_select(screen)
        if inventory:
            rows = [(160, row['y'], row['card']) for row in inventory['rows']]
            return _reading(screen, 'sortie_cards', rows, (160, inventory['selected_y']), scrolling=True)
        return None
    if screen.kind == 'starter_list':
        from .starter_view import NAME_LEFT, UPPER_ROW_Y, rows as party_rows
        upper, lower = party_rows(screen), party_rows(screen, selected=True)
        if not upper or lower is None or not screen.hand:
            return None
        y = screen.hand[1] + 6
        row = next((row for row in UPPER_ROW_Y if abs(row - y) <= 2), None)
        if row is None or not 0 <= NAME_LEFT - screen.hand[2] <= 8:
            return None
        # Selecting a general dims that upper slot. The same frame must
        # positively show a selected lower member before that blank slot can
        # be used as a cursor origin; never borrow the right status panel.
        if not any(ty == row for _, ty, _ in upper) and not lower:
            return None
        return _reading(screen, 'starter_upper', upper, (NAME_LEFT, row))
    if screen.kind != 'text':
        return None
    if (not screen.hand and _card_picker_rows(screen)
            and type(screen.menu_cursor) is int):
        rows = [(x, line.y, word) for line in screen.menu_rows
                for x, word in line.spans()
                if x == 176 and line.y in (176, 192, 208) and word in ALL_CARD_NAMES]
        current = [(x, y) for x, y, _ in rows if y == screen.menu_cursor]
        cursor = current[0] if len(current) == 1 else None
        return _reading(screen, 'battle_cards', rows, cursor, scrolling=True)
    if not screen.hand:
        return None
    if screen.has('Aボタンでステータスひょうじ'):
        rows = _named_rows(screen, 168, range(39, 152, 16))
        if rows:
            return _reading(screen, 'global_roster', rows, _hand_cursor(screen, rows), scrolling=True)
        # The しろ list shares the roster geometry/help text, but contains
        # castle names. Keep blind/fading/partial rows at the normal cadence.
        if screen.has('コマンドにもどります'):
            rows = _castle_rows(screen)
            return _reading(screen, 'castle_list', rows, _hand_cursor(screen, rows or []), scrolling=True)
        return None
    if (screen.has('どのしょうぐんのたまごを')
            and screen.has('おはらいしますか?')):
        rows = _named_rows(screen, 32, range(71, 168, 16))
        full = [(x, y, word) for x, y, word in _options(screen)
                if (x, y, word) == (32, 47, 'ぜんかいふく')]
        if rows and len(full) == 1:
            rows = full + rows
            return _reading(screen, 'egg_recovery', rows, _hand_cursor(screen, rows), scrolling=True)
        return None
    if is_fort_menu(screen):
        if screen.hand[0] > 100:
            rows = _named_rows(screen, 144, range(39, 152, 16))
            return _reading(screen, 'fort_roster', rows, _hand_cursor(screen, rows or []), scrolling=True)
        rows = [(x, y, word) for x, y, word in _options(screen)
                if (x, y, word) in ((64, 31, 'しゅつげき'), (64, 47, 'こうかん'))]
        return _reading(screen, 'fort_commands', rows, _hand_cursor(screen, rows))
    if is_camp_menu(screen):
        rows = [(x, y, word) for x, y, word in _options(screen)
                if word in {'いどう', 'ステータス', 'キャンプ', 'きかん'}]
        if len(rows) != 4 or len({x for x, _, _ in rows}) != 1:
            return None
        return _reading(screen, 'camp_commands', rows, _hand_cursor(screen, rows))
    return None
