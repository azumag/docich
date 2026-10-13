"""OS層: readers for the chapter-advance party (先発隊) screen.

Reads the upper/lower name panes and recognises the party list and its final
confirmation. Used by ``os_layer.screen.parse`` and the ``flows.starter``
procedure; nothing here plans input.
"""
from __future__ import annotations

from ...hanjuku_egg_reference import GENERAL_HP
from .font import UNKNOWN


# Seven-glyph names fit here. x=112 is the panel's scroll arrow/border,
# measured in g684-starter-empty.png, never part of a general's name.
NAME_LEFT = 48
NAME_RIGHT = 112
# Selected names dim out of text recognition but retain their upper slots
# and the hand (g692: first selection, cursor on y=47, lower Zeus at y=119).
UPPER_ROW_Y = (47, 63, 79, 95)


def rows(screen, *, selected=False):
    """Full names in the left pane; unknown/duplicate/extra rows are unread."""
    band = (112, 176) if selected else (32, 112)
    result = []
    by_y = {line.y: dict(line.cells) for line in screen.lines}
    for line in screen.lines:
        if not band[0] <= line.y < band[1]:
            continue
        cells = tuple((x, c) for x, c in line.cells if NAME_LEFT <= x < NAME_RIGHT)
        if not cells:
            continue
        # Voicing marks are UNKNOWN on the row above their combined glyph.
        # Only ignore those exact columns; an unread name/body row is not a
        # blank slot and must not be hidden by known-only text extraction.
        voiced = frozenset('がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポヴ')
        if all(c == UNKNOWN and by_y.get(line.y + 8, {}).get(x) in voiced for x, c in cells):
            continue
        # Never use a readable substring
        # adjoining an unknown tile as a complete general's identity.
        word = line.span(NAME_LEFT, NAME_RIGHT)
        if UNKNOWN in word or word not in GENERAL_HP or word == 'しゅじんこう':
            return None
        result.append((cells[0][0], line.y, word))
    if (len(result) > (3 if selected else 4)
            or len({w for _, _, w in result}) != len(result)
            or len({x for x, _, _ in result}) > 1
            or any((b[1] - a[1] != 16 if selected else (b[1] - a[1]) % 16 != 0)
                   for a, b in zip(result, result[1:]))):
        return None
    return result


def kind(screen):
    flat = screen.text.replace(' ', '')
    if ('せんぱつたいに' in flat and 'くわえます' in flat
            or 'つぎのたたかいに' in flat):
        return 'starter_list'
    # Hotload may first see the final confirmation, after the prompt vanished.
    # Its two left name panes and right HP/egg status distinguish it from a
    # normal paid monthly confirmation or a general trade.
    left_present = any(40 <= x < 128 for line in screen.lines if 32 <= line.y < 176
                       for x, _ in line.cells)
    right = ''.join(line.span(128, 256).replace(' ', '') for line in screen.lines if 32 <= line.y < 176)
    if ('よろしいですか' in flat and 'HP' in right and 'たまご' in right and left_present):
        return 'starter_confirm'
    return None
