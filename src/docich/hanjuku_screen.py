"""Structured, deterministic features of one Hanjuku frame (stdlib only).

The parser reports what is visible: text read from exact 8x8 glyph tiles, the
menu hand cursor, the map cursor, castle roofs and the battle panel. It never
infers a value that is not on screen; missing features are ``None``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import collections
import re

from .hanjuku_font import TextLine, dark, joined, light, read_lines, row_masks
from .hanjuku_pixels import Frame

HEADER = re.compile(r'(?:(\d+)わ)?(\d+)ねん(\d+)のつき(\d+)G')
PRICE = re.compile(r'^(\S+?)(\d+)G$')
HAND_COLORS = ((255, 174, 82), (205, 105, 24), (205, 149, 32))
# Summoned-monster turn menu: a knight sprite sits left of the selected option
# row, and the stacked HP panel carries an ally (red) / enemy (blue) drop mark.
# Both measurements come from live frames (decision-003/024/029).
KNIGHT_COLORS = ((230, 105, 74), (230, 149, 74), (148, 80, 230), (123, 56, 222),
                 (255, 165, 139), (255, 189, 180), (172, 113, 230), (230, 198, 74))
ALLY_MARK = (222, 72, 65)
ENEMY_MARK = (57, 121, 189)
MENU_ROW_Y = 148          # option rows live in the bottom command box band
# Increasing effect order; choose the strongest of the three offered moves.
OKUNOTE_CHOICES = ('ヤケクソ', 'おどす', 'よたる', 'あやまる', 'うそなき',
                   'しんだフリ', 'てぶくろ', 'ハダカでぶつかる', 'せっとく',
                   'くすぐってみる', 'せいしゅん', 'いあつする', 'ブンシーンもどき',
                   'リューキシもどき', 'ウェイブもどき', 'ファバードもどき', 'だいじんアタック')


def _near(pixel, color, tolerance=10):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, color))


def find_hand(frame: Frame, *, option_positions=None):
    """Unique orange hand, optionally aligned with known menu option positions.

    Filter whole components rather than cropping: cutting a large orange sprite
    at a menu boundary must not make it look like a hand.
    """
    def at_option(box):
        return option_positions is None or any(
            0 <= x - box[2] <= 48 and abs(y - (box[1] + 6)) <= 2
            for x, y in option_positions)

    points = set()
    rgb, width = frame.rgb, frame.width
    for y in range(frame.height):
        base = y * width * 3
        for x in range(width):
            i = base + 3 * x
            pixel = (rgb[i], rgb[i + 1], rgb[i + 2])
            if pixel[0] >= 195 and any(_near(pixel, c) for c in HAND_COLORS):
                points.add((x, y))
    if len(points) < 40:
        return None
    xs, ys = zip(*points)
    # The hand is 18-20 px wide; a wider spread means two orange objects.
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1 - x0 <= 26 and y1 - y0 <= 18:
        box = x0, y0, x1, y1
        return box if at_option(box) else None
    # g478: the merchant's orange sprite shares the cursor palette. A
    # global box merges both objects and loses the hand. Keep only a unique
    # connected component of the measured hand size; ambiguity still holds.
    candidates = []
    while points:
        seed = points.pop()
        component, pending = [seed], [seed]
        while pending:
            x, y = pending.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbor = (x + dx, y + dy)
                    if neighbor in points:
                        points.remove(neighbor)
                        component.append(neighbor)
                        pending.append(neighbor)
        if len(component) < 40:
            continue
        xs, ys = zip(*component)
        box = min(xs), min(ys), max(xs), max(ys)
        if (12 <= box[2] - box[0] <= 26 and 8 <= box[3] - box[1] <= 18
                and at_option(box)):
            candidates.append(box)
    return candidates[0] if len(candidates) == 1 else None


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
            if not all(_bits(masks_white[y + d], width, x + 1, 1) for d in (1, 2, 3)):
                continue
            # Each corner bar is exactly 3 px with a dark pixel on both sides.
            # Snow (winter maps) is solid white and matched everywhere, so the
            # single real cursor was never unique (g419 09:26: 220 unread frames).
            if any(_bits(line, width, x + k, 1) for line in (row, bottom) for k in (1, 5, 10, 14)):
                continue
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


# Our camping tent (野営): a yellow/orange body with a light-blue base band
# and a pure-red flag above it (isolated probe 2026-09-28, confirmed on a
# live frame). The flag can be clipped by the top edge in live frames
# (g421 17:06: body at y0-8, no red anywhere in the frame), so the band
# carries the body signature when the flag is out of view.
# いどう/ステータス/キャンプ/きかん opens on A; enemy camps fly another flag.
CAMP_FLAG = (255, 0, 0)
CAMP_YELLOW = (238, 198, 65)
CAMP_ORANGE = (238, 113, 57)
CAMP_BAND = (131, 198, 222)


def _camp_flagish(p) -> bool:
    return p[0] >= 190 and p[1] <= 110 and p[2] <= 110


def own_camps(frame: Frame) -> list[dict]:
    """Our camping tents (野営) with the cursor cell that selects them.

    A fully visible tent must show its red flag above the body. A tent
    clipped by the top edge has no flag in frame: the body (yellow+orange
    with the light-blue band) still counts, and the target may sit above
    the screen so the servo scrolls the camera until the flag appears.
    """
    body = {(x, y) for y in range(frame.height) for x in range(frame.width)
            if frame.pixel(x, y) in (CAMP_YELLOW, CAMP_ORANGE, CAMP_BAND)}
    found, seen = [], set()
    for start in sorted(body):
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy)
                    if n in body and n not in seen:
                        seen.add(n)
                        stack.append(n)
        if not 12 <= len(comp) <= 200:
            continue
        xs = [c[0] for c in comp]
        ys = [c[1] for c in comp]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if x1 - x0 > 16 or y1 - y0 > 16:
            continue
        yellow = sum(1 for c in comp if frame.pixel(*c) == CAMP_YELLOW)
        orange = sum(1 for c in comp if frame.pixel(*c) == CAMP_ORANGE)
        band = sum(1 for c in comp if frame.pixel(*c) == CAMP_BAND)
        if yellow < 6 or orange < 6 or band < 3:
            continue
        flag = None
        for yy in range(max(0, y0 - 4), y0):
            hits = [(xx, yy) for xx in range(max(0, x0 - 6), min(frame.width, x1 + 7))
                    if _camp_flagish(frame.pixel(xx, yy))]
            if len(hits) >= 2:
                flag = hits[0]
                break
        clipped = y0 - 6 < 0
        if flag is None and not clipped:
            continue
        target = (flag[0] - 8, flag[1] - 2) if flag else (x0 - 6, y0 - 6)
        if any(abs(t['target'][0] - target[0]) <= 8 and abs(t['target'][1] - target[1]) <= 8
               for t in found):
            continue
        found.append({'target': target, 'clipped': flag is None})
    return found


@dataclass
class Battle:
    enemy: str | None
    enemy_hp: int | None
    ally: str | None
    ally_hp: int | None


@dataclass
class EggRow:
    name: str
    hp: int
    side: str | None
    y: int


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
    menu_rows: list[TextLine] = field(default_factory=list)
    menu_cursor: int | None = None
    egg_rows: list[EggRow] = field(default_factory=list)
    # (味方, 敵) の兵士数。読めない側は None、フィールド計測なしは全体で None。
    field_soldiers: tuple[int | None, int | None] | None = None
    hidden_battle_commands: bool = False
    # The two-row item box with every row disabled (measured g534 07:51).
    disabled_item_box: bool = False

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


def _human_hp(frame: Frame, line: TextLine, x0: int) -> int | None:
    """Read the entire right-aligned three-cell field, including blank cells.

    Measured 256x224 human panels use x=88..111 / 216..239, y=176.
    A flying soldier can cover the tens digit of 32 while leaving a valid 2.
    words() intentionally skips unknown glyphs, so it is unsafe for this field.
    Both the normal background and HP ink are achromatic; sprite colours or
    unknown cells invalidate the observation instead of inventing lower HP.
    """
    if line.y != 176:
        return None
    for y in range(176, 184):
        for x in range(x0, x0 + 24):
            r, g, b = frame.pixel(x, y)
            if r != g or g != b:
                return None
    cells = dict(line.cells)
    text = ''.join(cells.get(x, ' ') for x in range(x0, x0 + 24, 8))
    return int(text) if re.fullmatch(r' *[0-9]{1,3}', text) else None


def _battle(frame: Frame) -> Battle | None:
    lines = read_lines(frame, predicate=dark, rect=(0, 160, 256, 200))
    for line in lines:
        left, right = line.words(0, 128), line.words(128, 256)
        if len(left) >= 2 and len(right) >= 2 and left[-1].isdigit() and right[-1].isdigit():
            enemy_hp, ally_hp = _human_hp(frame, line, 88), _human_hp(frame, line, 216)
            if (enemy_hp is not None and ally_hp is not None
                    and str(enemy_hp) == left[-1] and str(ally_hp) == right[-1]):
                return Battle(left[0], enemy_hp, right[0], ally_hp)
    return None


# 戦闘中の兵士を数える帯域。HPパネル (rect (0,160,256,200)) より上の
# フィールドだけで、下端はボックス上辺 (y152〜) に切りられる兵士も数える。
SOLDIER_BAND = (0, 40, 256, 150)
# 1体ぶんの連結成分。gcgx battle.html「兵士は1人ずつHP10を持っている」に
# 対応する実測サイズ (面積55..260、bbox 26x26以内)。
SOLDIER_AREA = (55, 260)
SOLDIER_BOX = (26, 26)
# 2体以上が重なったとみなす成分。片側でも1つあればその側は計測をやめる。
SOLDIER_MERGED_AREA = (261, 900)
SOLDIER_MERGED_BOX = (60, 60)


def _field_soldiers(frame: Frame) -> tuple[int | None, int | None] | None:
    """フィールドに並ぶ兵士スプライトを数える。返り値は (味方, 敵)。

    左右の割り当ては味方が左・敵が右で、HPパネル (敵が左) とは逆。実測:
    どうし90 vs ロックフォール57 の新規戦で左6 (出撃確認のへいし6人と一致)、
    どうし0 の敗戦フレームで左が空、ビシソワーズ0 の勝利フレームで右が空。
    背景色 (上位4色) を完全一致で除いた連結成分のうち1体ぶんの寸法・面積に
    収まるものだけを兵士と数え、側ごとに2体以上の重なりを見つけたらその側は
    None (読めない=推測しない) にする。フィールド計測が要る戦闘フレームでの
    み呼ばれる。
    """
    if frame.width < SOLDIER_BAND[2] or frame.height < SOLDIER_BAND[3]:
        return None
    x0, y0, x1, y1 = SOLDIER_BAND
    rgb, stride = frame.rgb, frame.width * 3
    span = (x1 - x0) * 3
    tones = collections.Counter()
    for y in range(y0, y1):
        row = rgb[y * stride + x0 * 3: y * stride + x0 * 3 + span]
        for i in range(0, span, 3):
            tones[row[i:i + 3]] += 1
    background = {tone for tone, _ in tones.most_common(4)}
    mask = set()
    for y in range(y0, y1):
        row = rgb[y * stride + x0 * 3: y * stride + x0 * 3 + span]
        for i in range(0, span, 3):
            if row[i:i + 3] not in background:
                mask.add((x0 + i // 3, y))
    seen: set[tuple[int, int]] = set()
    sides, merged = [0, 0], [0, 0]
    for seed in mask:
        if seed in seen:
            continue
        stack, cells = [seed], []
        seen.add(seed)
        while stack:
            cx, cy = stack.pop()
            cells.append((cx, cy))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nxt = (cx + dx, cy + dy)
                    if nxt in mask and nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
        if len(cells) < SOLDIER_AREA[0]:
            continue
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        # 上端・左右端に接する成分は壁や枠なので数えない。下端だけは
        # ボックスに沿って切られる兵士を残すため除外しない。
        if min(ys) <= y0 or min(xs) <= x0 or max(xs) >= x1 - 1:
            continue
        side = 0 if sum(xs) // len(cells) < (x0 + x1) // 2 else 1
        width, height = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        if (len(cells) > SOLDIER_AREA[1] or width > SOLDIER_BOX[0]
                or height > SOLDIER_BOX[1]):
            if (SOLDIER_AREA[1] < len(cells) <= SOLDIER_MERGED_AREA[1]
                    and width <= SOLDIER_MERGED_BOX[0] and height <= SOLDIER_MERGED_BOX[1]
                    and max(ys) < y1 - 1):
                merged[side] += 1
            continue
        sides[side] += 1
    return (None if merged[0] else sides[0], None if merged[1] else sides[1])


def _menu_rows(lines: list[TextLine]) -> list[TextLine]:
    """Light option rows of the bottom command box (first word x=40 or x=176)."""
    rows = []
    for line in lines:
        if line.y < MENU_ROW_Y:
            continue
        spans = line.spans()
        if spans and (32 <= spans[0][0] <= 56 or 168 <= spans[0][0] <= 192):
            rows.append(line)
    return rows


def _knight_count(frame: Frame, line: TextLine) -> int:
    spans = line.spans()
    if not spans:
        return 0
    x = spans[0][0]
    n = 0
    for y in range(max(0, line.y - 8), min(frame.height, line.y + 9)):
        for px in range(max(0, x - 34), max(0, x - 1)):
            pixel = frame.pixel(px, y)
            if any(_near(pixel, c, 25) for c in KNIGHT_COLORS):
                n += 1
    return n


def _menu_cursor(frame: Frame, rows: list[TextLine]) -> int | None:
    """Row y whose knight sprite sits just left of that row's first word."""
    best_y, best_n = None, 0
    for line in rows:
        n = _knight_count(frame, line)
        if n > best_n:
            best_y, best_n = line.y, n
    return best_y if best_n >= 8 else None


def _egg_rows(frame: Frame) -> list[EggRow]:
    """Stacked ally/enemy HP rows of the summoned-monster turn menu.

    The panel is always on the opposite side of the command box, so the mark
    window follows the row's first word: x>=128 means the panel sits right,
    otherwise it sits left.
    """
    rows = []
    # g508: the lower HP row starts at y212; its glyphs need the
    # final eight-pixel band too (216 cropped the enemy monster away).
    for line in read_lines(frame, predicate=dark, rect=(0, 140, 256, 224)):
        left, right = line.words(0, 128), line.words(128, 256)
        if len(left) >= 2 and len(right) >= 2:
            continue          # the standard side-by-side battle panel
        spans = line.spans()
        if len(spans) < 2 or not spans[-1][1].isdigit() or spans[0][1].isdigit():
            continue
        window = range(120, 248) if spans[0][0] >= 128 else range(0, 120)
        red = blue = 0
        for y in range(max(0, line.y - 8), min(frame.height, line.y + 9)):
            for x in window:
                pixel = frame.pixel(x, y)
                if _near(pixel, ALLY_MARK, 25):
                    red += 1
                elif _near(pixel, ENEMY_MARK, 25):
                    blue += 1
        side = 'ally' if red >= 12 and red > blue else ('enemy' if blue >= 12 else None)
        rows.append(EggRow(''.join(w for _, w in spans[:-1]), int(spans[-1][1]), side, line.y))
    return rows


# Measured human battle command box. Disabled rows are dark and absent from
# OCR; in castle defense with no cards, only the first row may remain readable.
_BATTLE_COMMANDS = ((176, 'たまごをつかう'), (192, 'きりふだ'), (208, 'たいきゃく'))


def _box_rows(rows):
    # Measured two-row box without たいきゃく: g508 y192/208, g510 Zeus
    # y196/212, and the g534 07:51 castle-defense frame y192/208 with both
    # rows disabled grey. Require both exact labels: a lone egg row is also
    # an egg-opponent command.
    by_y = {line.y: line.spans() for line in rows}
    for y in (192, 196):
        if (by_y.get(y) == [(176, 'たまごをつかう')]
                and by_y.get(y + 16) == [(176, 'きりふだ')]):
            return [line for line in rows if line.y in (y, y + 16)]
    return []


def _boss_command_rows(screen):
    return _box_rows(screen.menu_rows)


def _human_commands(screen):
    return bool(_boss_command_rows(screen)) or any((line.y == y and line.spans() == [(176, label)])
               or (line.y in (176, 192, 208) and line.spans() == [(176, 'おくのて')])
               for line in screen.menu_rows for y, label in _BATTLE_COMMANDS)


def _partial_human_commands(screen):
    # The opening box is clipped at the bottom before the other rows arrive.
    # It must wait, never fall through to legacy A on the egg command.
    return (screen.battle is None and not screen.egg_rows
            and len(screen.menu_rows) == 1
            and screen.menu_rows[0].y in (192, 196, 208, 212, 216)
            and screen.menu_rows[0].spans() == [(176, 'たまごをつかう')])


def egg_choice_names(screen):
    """The measured Elabel summon picker, not monster skills or HP panels."""
    from .hanjuku_reference import MONSTER_SKILLS
    digit_fold = str.maketrans('０１２３４５６７８９', '0123456789')
    known = {name.translate(digit_fold) for name in MONSTER_SKILLS}
    rows = {line.y: line.spans() for line in screen.menu_rows}
    choices = []
    for y in (176, 192, 208):
        spans = rows.get(y, [])
        if len(spans) != 1 or spans[0][0] != 176 or spans[0][1].translate(digit_fold) not in known:
            return []
        choices.append(spans[0][1])
    return choices if screen.battle is None and not screen.egg_rows else []


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
    if screen.battle is not None:
        # 召喚メニューのフレームでは _battle() は None (敵側は兵士ではなく
        # 召喚獣なので上書きしない)。読み取りは直近の通常戦闘フレームのもの。
        screen.field_soldiers = _field_soldiers(frame)
    screen.menu_rows = _menu_rows(lines)
    box_rows = _box_rows(screen.menu_rows)
    if not screen.menu_rows:
        # Measured disabled text is (106,105,106), not white. Keep it out
        # of selectable labels: only use all three exact rows to detect the
        # scrollable human menu, then require its knight cursor as well.
        grey = read_lines(frame, predicate=lambda r,g,b: 95 <= min(r,g,b)
                          and max(r,g,b) <= 120 and max(r,g,b)-min(r,g,b) <= 5,
                          rect=(176,176,256,216))
        screen.hidden_battle_commands = all(
            any(line.y == y and line.spans() == [(176,label)] for line in grey)
            for y,label in _BATTLE_COMMANDS)
        # A castle defense has no たいきゃく row, so its item box is the
        # two-row box one row lower than the three-row human command box
        # (g534 07:51 death frame: y192 たまごをつかう / y208 きりふだ, both
        # disabled grey, knight cursor on y192). The three-row rule above
        # cannot see it and no light row survives, so an open rescue menu
        # read 'unknown' and the bot answered it with a blind A instead of
        # closing it: that menu episode ran 3.8 s while どうし fell
        # 40 -> 22 and the enemy only 57 -> 49.
        box_rows = _box_rows(grey)
        screen.disabled_item_box = bool(box_rows)
    if screen.menu_rows or screen.hidden_battle_commands or screen.disabled_item_box:
        cursor_rows = (box_rows or
                       ([TextLine(y, ((176, label),)) for y, label in _BATTLE_COMMANDS]
                        if _human_commands(screen) or screen.hidden_battle_commands else screen.menu_rows))
        screen.menu_cursor = _menu_cursor(frame, cursor_rows)
        screen.hidden_battle_commands &= screen.menu_cursor is not None
        screen.disabled_item_box &= screen.menu_cursor is not None
        screen.egg_rows = _egg_rows(frame)
    if phase in (None, 'field', 'field_menu', 'battle_intro', 'event'):
        screen.marker = _target_marker(frame)
        white = row_masks(frame, lambda r, g, b: min(r, g, b) > 200)
        screen.cursor = _free_cursor(white, frame)
    screen.kind = classify_text(screen)
    if screen.kind == 'summer_bonus' and screen.hand is None:
        # An orange event sprite can make the global hand ambiguous. Only a
        # unique whole hand aligned with an actual choice may recover it.
        positions = [(x, line.y) for line in lines for x, word in line.spans()
                     if any(word.startswith(v) for variants in SUMMER_BONUS_CHOICES.values()
                            for v in variants)]
        screen.hand = find_hand(frame, option_positions=positions)
        screen.selected = _selected(lines, screen.hand)
    if screen.kind == 'unknown' and is_world_map(frame):
        screen.kind = 'world_map'
    return screen


WORLD_SEA = (106, 165, 205)
WORLD_BORDER = (57, 40, 16)


def is_world_map(frame: Frame) -> bool:
    """The Y whole-island view: open sea inside the gold frame (measured 2026-09-28)."""
    if frame.pixel(128, 10) != WORLD_BORDER:
        return False
    sea = sum(1 for x in range(60, 200, 2) for y in range(60, 180, 2) if frame.pixel(x, y) == WORLD_SEA)
    return sea >= 1500


# 月イチイベント「8月バカンス」(odoru7094 のイベントデータと gcgx event.html
# が一致)。バカンス→兵士が半減、ボーナス→お金が半減、まとめて解雇→何も
# ならないか兵士が全員いなくなる。画面実測は保存されていないので、正典の
# 表記とそのひらがな読みを候補にし、2つとも読めた時だけこの画面と断定する。
SUMMER_BONUS_CHOICES = {
    'vacation': ('バカンス', 'ばかんす'),
    'bonus': ('ボーナス', 'ぼーなす', 'ぼなす', 'ぼーナス'),
    'discharge': ('まとめて解雇', 'まとめてかいこ', 'まとめてかいほう'),
}


def summer_bonus_seen(text: str) -> bool:
    """The 8月バカンス prompt: both outcome options legible, no more."""
    flat = text.replace(' ', '')
    return any(v in flat for v in SUMMER_BONUS_CHOICES['vacation']) and \
        any(v in flat for v in SUMMER_BONUS_CHOICES['bonus'])


def classify_text(s: Screen) -> str:
    t = s.text
    if t in ('いばらのとうをとりまいていたすべてのいばらがしょうめつしました!',
             'いばらとともにけっかいもしょうめつしたようです!'):
        return 'barrier_removed'
    if 'なまえのかきとり' in t:
        return 'name_entry'
    if 'きりふだセレクト' in t:
        return 'card_select'
    if 'しょうぐん' in t and 'かいこに' in t:
        # Debt forces「どのしょうぐんをかいこに?」on a green list panel
        # that classify() calls shop; B there is ignored (g358: 2700 B).
        return 'discharge_menu'
    if 'しゅつげきしますか' in t:
        return 'sortie_confirm'
    if 'みせじまい' in t:
        return 'shop_exit_confirm'
    if 'おいくつ' in t:
        return 'shop_quantity_prompt'
    if 'よろしいでっか' in t or 'なりまんな' in t:
        return 'shop_quantity'
    if 'かいあたえ' in t or 'ほしいな' in t or 'だからなんかかって' in t:
        return 'gift_request'
    if summer_bonus_seen(t):
        # 8月の月イチイベント (odoru7094 / gcgx event.html)。3択だが片方が
        # 常に非選択なので yes_no とは別。free map cursor より先に見る。
        return 'summer_bonus'
    if sum(1 for line in s.lines if PRICE.match(''.join(line.words(120, 256)))) >= 3:
        return 'shop_list'
    if 'しょうにん' in t and 'おしまい' in t:
        return 'month_menu'
    # おどす is also a summoned monster's skill: a たまごに もどれ row makes it
    # our monster's turn (g401 21:58: read as okunote, held 300 s, stalled).
    if s.menu_cursor is not None and any(
            word in OKUNOTE_CHOICES for line in s.menu_rows for _,word in line.spans()) and not any(
            'もどれ' in r.known.replace(' ', '') for r in s.menu_rows):
        return 'okunote_menu'
    if egg_choice_names(s):
        return 'egg_choice_menu'
    if _human_commands(s) or s.hidden_battle_commands or s.disabled_item_box:
        return 'battle_menu'
    if _partial_human_commands(s):
        return 'battle_menu_pending'
    if 'たまごをつかう' in t and 'たいきゃく' in t:
        return 'battle_menu'
    if 'きりふだ' in t and 'たいきゃく' in t and any('たいきゃく' in r.known.replace(' ', '') for r in s.menu_rows):
        # A general whose egg is spent draws たまごをつかう greyed out, so it
        # is not read; the cursor still starts on it. As text, legacy A hit
        # the dead row forever (g389 16:48, フットバース due at ガルバンゾー 18).
        return 'battle_menu'
    if 'こうげき' in t and 'もうこうげき' in t and 'たまごをつかう' in t:
        return 'egg_battle_menu'
    if 'こうげき' in t and 'もうこうげき' in t and any(
            'もうこうげき' in r.known.replace(' ', '') for r in s.menu_rows):
        # The same spent-egg greying as the きりふだ/たいきゃく fallback above:
        # たまごをつかう drops out of OCR and this panel otherwise falls to
        # kind 'text' with no cursor, holding the bot forever (viewer report
        # 2026-09-28: 持ってないタマゴを使おうとして止まっている).
        return 'egg_battle_menu'
    if 'たまごに' in t and 'もどれ' in t and any('もどれ' in r.known.replace(' ', '') for r in s.menu_rows):
        # Our summoned monster's own turn: the option box with たまごに もどれ.
        return 'monster_menu'
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
