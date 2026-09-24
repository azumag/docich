"""Structured, deterministic features of one Hanjuku frame (stdlib only).

The parser reports what is visible: text read from exact 8x8 glyph tiles, the
menu hand cursor, the map cursor, castle roofs and the battle panel. It never
infers a value that is not on screen; missing features are ``None``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .hanjuku_font import TextLine, dark, joined, light, read_lines, row_masks
from .hanjuku_pixels import Frame

HEADER = re.compile(r'(?:(\d+)わ)?(\d+)ねん(\d+)のつき(\d+)G')
PRICE = re.compile(r'^(\S+?)(\d+)G$')
HAND_COLORS = ((255, 174, 82), (205, 105, 24), (205, 149, 32))


def _near(pixel, color, tolerance=10):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, color))


def find_hand(frame: Frame):
    """Bounding box of the orange pointing-hand menu cursor, or None."""
    xs, ys = [], []
    rgb, width = frame.rgb, frame.width
    for y in range(frame.height):
        base = y * width * 3
        for x in range(width):
            i = base + 3 * x
            pixel = (rgb[i], rgb[i + 1], rgb[i + 2])
            if pixel[0] >= 195 and any(_near(pixel, c) for c in HAND_COLORS):
                xs.append(x)
                ys.append(y)
    if len(xs) < 40:
        return None
    # The hand is 18-20 px wide; a wider spread means two orange objects.
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1 - x0 > 26 or y1 - y0 > 18:
        return None
    return x0, y0, x1, y1


def _target_marker(frame: Frame):
    """The deployment target marker: a 12x12 blue square with a white G."""
    pts = [(x, y) for y in range(frame.height) for x in range(frame.width)
           if _near(frame.pixel(x, y), (0, 64, 189), 14)]
    if not 60 <= len(pts) <= 140:
        return None
    x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
    x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
    if x1 - x0 > 13 or y1 - y0 > 13:
        return None
    # Box begins two pixels inside the 16x16 cursor cell.
    return x0 - 2, y0 - 2


def _bits(row: int, width: int, x: int, n: int) -> bool:
    """True when pixels x..x+n-1 of a row mask are all lit."""
    return (row >> (width - x - n)) & ((1 << n) - 1) == (1 << n) - 1


def _free_cursor(masks_white, frame: Frame):
    """Top-left of the white bracket map cursor (16x16 cell), or None."""
    width = frame.width
    found = []
    # Row y is the cell's second row: white bars at x+2..4 and x+11..13,
    # repeated on row y+13, with the left vertical bar at x+1 below it.
    for y in range(1, frame.height - 14):
        row = masks_white[y]
        if not row:
            continue
        for x in range(0, width - 16):
            if not (_bits(row, width, x + 2, 3) and _bits(row, width, x + 11, 3)):
                continue
            bottom = masks_white[y + 13]
            if not (_bits(bottom, width, x + 2, 3) and _bits(bottom, width, x + 11, 3)):
                continue
            if all(_bits(masks_white[y + d], width, x + 1, 1) for d in (1, 2, 3)):
                found.append((x, y - 1))
    return found[0] if len(found) == 1 else None


def _roof_kind(r, g, b):
    if r <= 50 and 80 <= g <= 120 and 120 <= b <= 160:
        return 'enemy'
    if r >= 190 and g <= 70 and 30 <= b <= 85:
        return 'own'
    return None


def castle_roofs(frame: Frame, exclude=None) -> list[dict]:
    """Castle roof components with a bottom-centre anchor, in screen pixels."""
    labels = {}
    for y in range(0, frame.height, 1):
        for x in range(0, frame.width, 1):
            kind = _roof_kind(*frame.pixel(x, y))
            if kind and not (exclude and exclude[0] <= x <= exclude[2] and exclude[1] <= y <= exclude[3]):
                labels[(x, y)] = kind
    seen, out = set(), []
    for start, kind in labels.items():
        if start in seen:
            continue
        stack, pts = [start], []
        seen.add(start)
        while stack:
            q = stack.pop()
            pts.append(q)
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    n = (q[0] + dx, q[1] + dy)
                    if labels.get(n) == kind and n not in seen:
                        seen.add(n)
                        stack.append(n)
        if len(pts) < 25:
            continue
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        if x1 - x0 > 40 or y1 - y0 > 30:
            continue
        clipped = x0 <= 1 or y0 <= 1 or x1 >= frame.width - 2 or y1 >= frame.height - 2
        out.append({'kind': kind, 'box': (x0, y0, x1, y1), 'clipped': clipped,
                    # Cursor top-left that selects this castle.
                    'target': ((x0 + x1) // 2, y1 - 8)})
    return out


@dataclass
class Battle:
    enemy: str | None
    enemy_hp: int | None
    ally: str | None
    ally_hp: int | None


@dataclass
class Screen:
    lines: list[TextLine]
    hand: tuple | None
    text: str
    header: dict | None = None
    selected: str | None = None
    battle: Battle | None = None
    marker: tuple | None = None
    cursor: tuple | None = None
    kind: str = 'unknown'
    options: list = field(default_factory=list)

    def has(self, needle: str) -> bool:
        return needle.replace(' ', '') in self.text


def _selected(lines: list[TextLine], hand) -> str | None:
    if not hand:
        return None
    x0, y0, x1, y1 = hand
    for line in lines:
        if y0 - 2 <= line.y <= y1:
            words = line.words(x1 - 4, 256)
            if words:
                return words[0]
    return None


def _battle(frame: Frame) -> Battle | None:
    lines = read_lines(frame, predicate=dark, rect=(0, 160, 256, 200))
    for line in lines:
        left, right = line.words(0, 128), line.words(128, 256)
        if len(left) >= 2 and len(right) >= 2 and left[-1].isdigit() and right[-1].isdigit():
            return Battle(left[0], int(left[-1]), right[0], int(right[-1]))
    return None


def parse(frame: Frame, *, phase: str | None = None) -> Screen:
    masks = row_masks(frame, light)
    lines = read_lines(frame, masks=masks)
    text = joined(lines)
    hand = find_hand(frame)
    screen = Screen(lines=lines, hand=hand, text=text)
    match = HEADER.search(text)
    if match:
        chapter, year, month, gold = match.groups()
        screen.header = {'chapter': int(chapter) if chapter else None,
                         'year': int(year), 'month': int(month), 'gold': int(gold)}
    screen.selected = _selected(lines, hand)
    screen.battle = _battle(frame)
    if phase in (None, 'field', 'field_menu', 'battle_intro', 'event'):
        screen.marker = _target_marker(frame)
        white = row_masks(frame, lambda r, g, b: min(r, g, b) > 200)
        screen.cursor = _free_cursor(white, frame)
    screen.kind = classify_text(screen)
    return screen


def classify_text(s: Screen) -> str:
    t = s.text
    if t == 'いばらのとうをとりまいていたすべてのいばらがしょうめつしました!':
        return 'barrier_removed'
    if 'なまえのかきとり' in t:
        return 'name_entry'
    if 'きりふだセレクト' in t:
        return 'card_select'
    if 'しゅつげきしますか' in t:
        return 'sortie_confirm'
    if 'みせじまい' in t:
        return 'shop_exit_confirm'
    if 'おいくつ' in t:
        return 'shop_quantity_prompt'
    if 'よろしいでっか' in t or 'なりまんな' in t:
        return 'shop_quantity'
    if 'かいあたえ' in t or 'ほしいな' in t:
        return 'gift_request'
    if sum(1 for line in s.lines if PRICE.match(''.join(line.words(120, 256)))) >= 3:
        return 'shop_list'
    if 'しょうにん' in t and 'おしまい' in t:
        return 'month_menu'
    if 'たまごをつかう' in t and 'たいきゃく' in t:
        return 'battle_menu'
    if 'こうげき' in t and 'もうこうげき' in t and 'たまごをつかう' in t:
        return 'egg_battle_menu'
    if 'しゅつげき' in t and 'ステータス' in t:
        # The general list opens beside the castle menu; its hand is right of it.
        if s.hand and s.hand[0] > 100:
            return 'general_list'
        # An empty list draws「しょうぐんはおりません……」and no hand cursor.
        # Without this the screen is misread as castle_menu and menu_to() never
        # moves (no hand), so the bot plans zero actions forever (g340 stall).
        if 'おりません' in t:
            return 'general_list'
        return 'castle_menu'
    if re.fullmatch(r'[^\ufffd\s]+しょうぐんがボスじょうにせめこんだ!!', t):
        return 'boss_attack_started'
    if 'のりこんだ' in t:
        return 'attack_started'
    if 'せめこまれ' in t:
        return 'defense_started'
    if 'けっかいで' in t:
        return 'sealed_castle'
    if 'じょう' in t and 'しゅうにゅう' in t and 'しょうぐん' in t:
        return 'castle_info'
    if 'ステータス' in t and 'システム' in t:
        return 'main_menu'
    if s.battle:
        return 'battle'
    if s.marker:
        return 'map_target'
    if s.cursor:
        return 'map'
    if 'うむッ' in t and 'いかんッ' in t:
        return 'yes_no'
    if t:
        return 'text'
    return 'unknown'
