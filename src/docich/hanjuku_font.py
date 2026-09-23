"""Deterministic 8x8 tile text reader for Hanjuku screens (stdlib only).

Text is drawn on the SNES background tile grid. Each glyph is one 8x8 tile and
its dakuten/handakuten mark is the tile above it. Tiles are matched exactly
against measured glyph bitmaps; an unknown tile is reported as ``UNKNOWN`` rather
than guessed. No model, OCR engine, network or image upload is involved.
"""
from __future__ import annotations

from dataclasses import dataclass

from .hanjuku_glyphs import GLYPHS, MARKS
from .hanjuku_pixels import Frame

_DAKUTEN = str.maketrans(
    'かきくけこさしすせそたちつてとはひふへほカキクケコサシスセソタチツテトハヒフヘホウ',
    'がぎぐげござじずぜぞだぢづでどばびぶべぼガギグゲゴザジズゼゾダヂヅデドバビブベボヴ')
_HANDAKUTEN = str.maketrans('はひふへほハヒフヘホ', 'ぱぴぷぺぽパピプペポ')
_COMBINE = {'゛': _DAKUTEN, '゜': _HANDAKUTEN}
UNKNOWN = '\ufffd'


def light(r, g, b):
    return min(r, g, b) > 180


def dark(r, g, b):
    return max(r, g, b) < 90


@dataclass(frozen=True)
class TextLine:
    """One tile row. ``cells`` holds (x, character) for every non-empty tile."""
    y: int
    cells: tuple[tuple[int, str], ...]

    @property
    def text(self) -> str:
        out, last = [], None
        for x, ch in self.cells:
            if last is not None and x - last > 8:
                out.append(' ' * ((x - last) // 8 - 1))
            out.append(ch)
            last = x
        return ''.join(out)

    def span(self, x0: int, x1: int) -> str:
        return TextLine(self.y, tuple(c for c in self.cells if x0 <= c[0] < x1)).text.strip()

    def spans(self) -> list[tuple[int, str]]:
        """(x, word) for runs of adjacent known glyphs; unknown tiles split runs."""
        out, start, word, last = [], None, '', None
        for x, ch in self.cells:
            if ch == UNKNOWN or (last is not None and x - last > 8):
                if word:
                    out.append((start, word))
                start, word = None, ''
            if ch != UNKNOWN:
                if not word:
                    start = x
                word += ch
            last = x
        if word:
            out.append((start, word))
        return out

    def words(self, x0: int = 0, x1: int = 256) -> list[str]:
        """Readable words in a pixel column range, without unknown tiles."""
        return self.span(x0, x1).replace(UNKNOWN, ' ').split()

    @property
    def known(self) -> str:
        """Readable content without unknown/border tiles."""
        return ' '.join(part for part in self.text.replace(UNKNOWN, ' ').split())


def row_masks(frame: Frame, predicate) -> list[int]:
    width, rgb = frame.width, frame.rgb
    masks = []
    for y in range(frame.height):
        base = y * width * 3
        value = 0
        for x in range(width):
            i = base + 3 * x
            value = (value << 1) | bool(predicate(rgb[i], rgb[i + 1], rgb[i + 2]))
        masks.append(value)
    return masks


def _tile(masks: list[int], width: int, x: int, y: int) -> int:
    shift = width - 8 - x
    value = 0
    for row in range(y, y + 8):
        value = (value << 8) | ((masks[row] >> shift) & 0xFF)
    return value


def _char(masks, width, x, y):
    code = _tile(masks, width, x, y)
    if not code:
        return None
    ch = GLYPHS.get(code, UNKNOWN)
    if y >= 8 and ch != UNKNOWN:
        mark = MARKS.get(_tile(masks, width, x, y - 8))
        if mark:
            ch = ch.translate(_COMBINE[mark])
    return ch


def read_lines(frame: Frame, *, predicate=light, rect=(0, 0, 256, 224),
               offsets=range(8), masks=None) -> list[TextLine]:
    """Read tile rows inside ``rect`` at the vertical grid offset with most glyphs.

    Menus/dialogue use a different background scroll from battle panels, so
    the grid offset is measured instead of assumed. The x grid is fixed at 0.
    """
    x0, y0, x1, y1 = rect
    width = frame.width
    masks = row_masks(frame, predicate) if masks is None else masks
    best = (-1, 0, [])
    for dy in offsets:
        lines, score = [], 0
        start = y0 + ((dy - y0) % 8)
        for y in range(start, min(y1, frame.height) - 7, 8):
            cells = []
            for x in range(x0 - x0 % 8, min(x1, width) - 7, 8):
                ch = _char(masks, width, x, y)
                if ch is not None:
                    cells.append((x, ch))
                    score += ch != UNKNOWN
            if cells:
                lines.append(TextLine(y, tuple(cells)))
        if score > best[0]:
            best = (score, dy, lines)
    return best[2]


def find(lines: list[TextLine], needle: str) -> TextLine | None:
    compact = needle.replace(' ', '')
    for line in lines:
        if compact in line.known.replace(' ', ''):
            return line
    return None


def joined(lines: list[TextLine]) -> str:
    return ''.join(line.known.replace(' ', '') for line in lines)
