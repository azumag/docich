"""OS層: screen understanding helpers over a parsed ``Screen`` or ``Frame``.

Pure readers of the visible game: menu options and the hand row, world-map
flags and cursor, sortie inventories, monthly dialogue bodies, prompts and
fixed UI vocabularies. Nothing here reads or writes policy memory.
"""
from __future__ import annotations

import re

from .screen import (SUMMER_BONUS_CHOICES, Screen, castle_roofs, field_card_list_rows,
                     month_confirmation_positions)
# The policy's map readers. Flows import them from here (not from .screen) so a
# rebinding through the hanjuku_policy facade reaches every policy caller while
# screen.parse keeps its own binding, as in the single-module policy.
from .screen import own_camps  # noqa: F401
from .font import TextLine, UNKNOWN
from .pixels import bit_points, near_bits
from ... import hanjuku_chart as chart, hanjuku_reference as reference
from ...hanjuku_egg_reference import GENERAL_HP
from ...hanjuku_egg_stock import observed_uses


BATTLE_MENU = ('たまごをつかう', 'きりふだ', 'たいきゃく')
AFTER_BATTLE_KINDS = frozenset({'map', 'map_target', 'castle_menu', 'general_list', 'month_menu',
                                'attack_started', 'defense_started', 'field_encounter_started', 'castle_info', 'main_menu',
                                'yes_no', 'shop_list', 'boss_attack_started'})
CARD_NAMES = frozenset({
    'イッテツーン', 'ダイチスイム', 'ブラッキー', 'フットバース', 'グリンボー', 'ピッグローラー',
    'カンケリン', 'ノリウツール', 'クースカン', 'ゼンマイン', 'ミックミー', 'デッドガン',
    'ブレイコウ', 'ブンシーン', 'ファイアーボイス', 'ファバード', 'エンジェリン', 'マグネガキン',
    'ハリケーン', 'キャトルミュー'})
# Every real card name (the 32-card gcgx table). The panel reader accepts
# these as rows; only CARD_NAMES can be planned or selected.
# Cards the reference ID table does not price yet. They still occupy rows in
# the real きりふだ panel, and rejecting the panel for them made every sortie
# unreadable while one was visible (g1163 2026-11-10 04:52: ゲンゴナイト and
# ライチスイム on screen, five holds then a cancel). Read them, never select
# them by name: selection comes from CARD_IDS, which stays the priced subset.
EXTRA_CARD_NAMES = frozenset({'ゲンゴナイト', 'ライチスイム'})
ALL_CARD_NAMES = frozenset(reference.ALL_CARD_IDS) | EXTRA_CARD_NAMES


# ---------------------------------------------------------------- menus
def _options(screen: Screen) -> list[tuple[int, int, str]]:
    return [(x, line.y, _canonical_general(word))
            for line in screen.lines for x, word in line.spans()]


def _current(screen: Screen):
    if not screen.hand:
        return None
    x0, y0, x1, y1 = screen.hand
    row = y0 + 6
    cands = [(x, y, w) for x, y, w in _options(screen) if abs(y - row) <= 2 and x >= x1 - 4]
    if cands:
        return min(cands, default=None)
    # docich#4686: the historical match above assumes the box starts one row
    # above its text and that the text begins at/after the box's right edge.
    # The main menu's box instead starts on its text row and its text begins at
    # the box's left edge, so nothing matched there and both the monthly scan
    # and the house roster could not open しょうぐん (live: main_menu 90 times,
    # stage stayed open_menu; roster opened 2 of 18 tries). Fall back to the
    # box's own row and span only when the historical match found nothing, so
    # every menu that works today keeps its exact behaviour.
    if screen.kind != 'main_menu':
        return (((x1 - 4 + 7) // 8) * 8, row, '')
    alt = [(x, y, w) for x, y, w in _options(screen)
           if abs(y - y0) <= 2 and x0 - 8 <= x <= x1 + 8]
    if alt:
        alt.sort(key=lambda c: (c[0], c[1]))
        return alt[0]
    # A blank cell (e.g. right of ょ in the kana grid) still has a position.
    return (((x1 - 4 + 7) // 8) * 8, row, '')


def _month_dialog_unread(screen: Screen) -> bool:
    """An unread tile in the dialogue window, other than a voicing mark.

    Dakuten/handakuten sit on the tile row 8 px above their kana and read as
    unknown tiles there; anything else unknown may be a choice.
    """
    rows = {line.y: line for line in screen.lines if line.y >= 167}
    # The window's vertical frame reads as unknown tiles in its two outermost
    # columns on every row (g1105 43-5: the month-end resume notice could never
    # be advanced because x=0/x=248 looked like unread glyphs). An x used by an
    # unknown cell on at least two rows at the observed extremes is the frame,
    # not a glyph.
    xs = [x for line in rows.values() for x, _ in line.cells]
    edges = {min(xs), max(xs)} if xs else set()
    border = {x for x in edges
              if sum(1 for line in rows.values()
                     if any(cx == x and ch == UNKNOWN for cx, ch in line.cells)) >= 2}
    for y, line in rows.items():
        unknown = [x for x, ch in line.cells if ch == UNKNOWN and x not in border]
        if not unknown:
            continue
        below = rows.get(y + 8)
        known_below = {x for x, ch in (below.cells if below else ()) if ch != UNKNOWN}
        if len(unknown) < len(line.cells) or not set(unknown) <= known_below:
            return True
    return False


# ---------------------------------------------------------------- map cursor
def _cursor(screen: Screen):
    return screen.marker or screen.cursor


def _general_strict_spans(screen, general):
    """Visible rows whose exact name has no adjacent unknown glyph."""
    rows = []
    for line in screen.lines:
        for x, word in line.spans():
            if x <= 100 or _canonical_general(word) != general:
                continue
            neighbors = (x - 8, x + 8 * len(word))
            if not any(ch == UNKNOWN and cx in neighbors
                       and not (screen.hand and screen.hand[0] <= cx <= screen.hand[2])
                       for cx, ch in line.cells):
                rows.append((x, line.y))
    return rows


def _general_visible(screen, general):
    """Exact name with no adjacent unknown glyph; ignore hand/border pixels."""
    return bool(_general_strict_spans(screen, general))


# Y shows the whole island at 1/8 scale; each castle's flag sits at the
# castle cell / 8 + offset (chapter 1 measured in an isolated emulator on
# 2026-09-28, all seven flags within 1 px). Blue fill = enemy, red = ours.
WORLD_MAP_OFFSET = chart.WORLD_MAP_OFFSET   # chapter 2: 3 flags vs measured cells
WORLD_FLAG_ENEMY = (0, 64, 189)
WORLD_FLAG_OWN = (230, 56, 90)
WORLD_MARKER = (255, 24, 0)     # ▲ army marker on the Y view (probe-measured)


WORLD_COLOUR_TOL = 8           # live capture shifts colours by ~1 (g419: gold 255,181,0)


def _near_colour(pixel, colour, tol=WORLD_COLOUR_TOL):
    return all(abs(a - b) <= tol for a, b in zip(pixel, colour))


def world_flags(frame, chapter, cursor=None):
    """{castle: 'own'|'enemy'} read from the whole-island view, unread ones left out.

    A flag under the Y cursor is not read: the sortie cursor's G is the same
    blue as an enemy flag (isolated probe: the home flag read as enemy).

    The boss castle is not read either. Chapter 3 draws it as a skull with no
    flag, and g1075 15:10:55 read ボス as own from the face of アポロン, camped
    beside it, whose (230,56,90) pixels fell into the box. Its owner is never
    taken from flags (captures and losses skip it), so nothing is lost.
    """
    offset = WORLD_MAP_OFFSET.get(chapter)
    if not offset or frame is None:
        return {}
    out = {}
    for name, (wx, wy) in chart.castles(chapter).items():
        if chart.is_boss_castle(name):      # chapter 4's four sites are skulls too
            continue
        mx, my = round(wx / 8 + offset[0]), round(wy / 8 + offset[1])
        if cursor and abs(mx + 2 - cursor[0]) <= 10 and abs(my + 1 - cursor[1]) <= 10:
            continue
        box = {frame.pixel(x, y) for x in range(mx - 2, mx + 6) for y in range(my - 2, my + 5)
               if 0 <= x < frame.width and 0 <= y < frame.height}
        enemy = any(_near_colour(px, WORLD_FLAG_ENEMY) for px in box)
        own = any(_near_colour(px, WORLD_FLAG_OWN) for px in box)
        if enemy != own:
            out[name] = 'enemy' if enemy else 'own'
    return out


# Chapter 4's four 四季王 sites draw chapter 3's boss skull while the king
# holds the site and plain terrain once it is cleared. The skull is chapter 3's
# boss skull sprite, top-left (101, 63) in the chapter 3 view; a site's sprite
# sits at that anchor plus the site cell's view offset
# (tests/test_hanjuku_season_king_sites.py pins the same anchor).
# g1163 2026-11-09: a cleared site has no marker left at all — 北西/南西 read
# as plain terrain while the owner reported them cleared, and 南東 kept the
# skull as the remaining king. An orange pennant seen earlier at three sites
# was a marching army standing on them, not a cleared marker, so the cleared
# test is "no skull and nothing but terrain".
#
# g1342 2026-11-24 (owner confirmed): the 四季王 defeated in 北東 stopped
# drawing its skull after the 21:11-21:15 fight while the bot's own camp
# stayed on the site; the owner's only in-game evidence for a defeat is that
# disappearance. A live king keeps the full skull even when a general of ours
# stands on the site (北西 light 25 with ブカティーニ launched; 南東 light 8 is
# a *partial* skull behind アポロン and is not evidence either way). The pixel
# rule therefore has these outcomes beyond terrain (shape cells, see below):
#   blue cursor fill                 -> 'cursor' (nothing readable)
#   >= 12 light and >= 12 dark cells -> 'skull' (live king, even overlapped)
#   <= 2 light and <= 8 dark cells   -> 'hidden' (skull absent/covered; only a
#                                       confirmed streak may close it)
#   anything else                    -> 'ambiguous' (never closed, never first)
# Anything dim/transitional reads ambiguous or hidden with very few definite
# boxes; season_site_readings()["healthy"] says whether the frame reads normally.
SEASON_SITE_ANCHOR = (101, 63)          # chapter 3 skull top-left on the Y view
SEASON_SKULL_LIGHT = ((164, 165, 164), (255, 254, 255))
# The island's own palette inside a site box (grass, water, sand, forest).
SEASON_TERRAIN = ((24, 141, 24), (90, 214, 8), (84, 208, 9), (0, 72, 24),
                  (106, 165, 205), (57, 88, 123), (172, 157, 65), (197, 133, 49))
SEASON_TERRAIN_RATIO = 0.95
SEASON_SKULL_ABSENT = 2         # skull-light cells of the shape (measured 0 in 北東)
SEASON_CAMP_FLAG = (255, 181, 0)   # our army's orange pennant (a camp/march marker)
SEASON_CAMP_MIN = 6                # flag pixels that make a skull-less box 'with camp'
SEASON_SITE_HEALTHY_READS = 2   # other sites that must still read definitively
# g1342 2026-11-25 00:24: the Y view's own cursor (blue square, white G, orange
# corner brackets) sat on 北西 and its dark outline/white pixels counted as skull
# light. A skull is therefore matched by *shape*: the chapter 3 boss skull,
# 8 rows from the box origin (S = light, D = (32,32,32) outline), measured on a
# clean site (g1075 and g1342 21:20 北西/南西, 23:50 南東). A cursor box scores
# S 5 / D 0; an overlapped live king (00:24 南西, a general's face on it) S 15 /
# D 19; a partial 南東 skull at 21:20 S 8 / D 20; a defeated king's box with our
# flag only S 0 / D 4.
SEASON_SKULL_SHAPE = ('.DDDDD..',
                      'DSSSSSD.',
                      'SSSSSSSD',
                      'SDDSDDSD',
                      'SDDSDDSD',
                      'DSSSSSD.',
                      '.DSDSD..',
                      '..D.D...')
SEASON_SKULL_DARK = (32, 32, 32)
SEASON_SKULL_MIN_LIGHT = 12     # of 25 skull-light cells
SEASON_SKULL_MIN_DARK = 12      # of 25 outline cells
SEASON_SKULL_MAX_EXTRA = 4      # light pixels outside the skull's light cells
SEASON_HIDDEN_MAX_DARK = 8      # an absent skull leaves (almost) no outline
SEASON_CURSOR_BLUE = (0, 64, 189)
SEASON_CURSOR_MIN = 20          # cursor-fill pixels that make the box 'cursor'


def season_site_readings(frame, chapter, cursor=None):
    """Per-site Y-view marker classes plus whether the frame is trustworthy.

    ``readings`` keeps the sites the view made readable; a site under the
    cursor and a too-small box stay out. ``healthy`` is True when at least two
    sites read definitively (skull or clean terrain) and at least one of them
    shows a full skull: a dimmed/transitional frame turns every box ambiguous
    or hidden, while one site's camp/portrait leaves the rest definite.
    """
    empty = {'readings': {}, 'healthy': False}
    if chapter != 4 or frame is None or not hasattr(frame, 'pixel'):
        return empty
    anchor = chart.CASTLES[3].get(chart.boss_castle(3))
    if anchor is None:
        return empty
    readings = {}
    for name in chart.boss_sites(4):
        cell = chart.castles(4).get(name)
        if cell is None:
            continue
        x = SEASON_SITE_ANCHOR[0] + (cell[0] - anchor[0]) // 8
        y = SEASON_SITE_ANCHOR[1] + (cell[1] - anchor[1]) // 8
        if cursor and abs(x + 5 - cursor[0]) <= 10 and abs(y + 5 - cursor[1]) <= 10:
            continue
        pixels = [frame.pixel(px, py) for px in range(x - 2, x + 12)
                  for py in range(y - 2, y + 13)
                  if 0 <= px < frame.width and 0 <= py < frame.height]
        if len(pixels) < 100:
            continue
        light = sum(1 for px in pixels
                    if any(_near_colour(px, colour) for colour in SEASON_SKULL_LIGHT))
        terrain = sum(1 for px in pixels
                      if any(_near_colour(px, colour) for colour in SEASON_TERRAIN))
        blue = sum(1 for px in pixels if _near_colour(px, SEASON_CURSOR_BLUE))
        shape_light = shape_dark = 0
        for dy, row in enumerate(SEASON_SKULL_SHAPE):
            for dx, cell in enumerate(row):
                if cell == '.' or not (0 <= x + dx < frame.width and 0 <= y + dy < frame.height):
                    continue
                px = frame.pixel(x + dx, y + dy)
                if cell == 'S':
                    shape_light += any(_near_colour(px, colour) for colour in SEASON_SKULL_LIGHT)
                else:
                    shape_dark += px == SEASON_SKULL_DARK
        extra = light - shape_light
        if blue >= SEASON_CURSOR_MIN:
            kind = 'cursor'
        elif (shape_light >= SEASON_SKULL_MIN_LIGHT and shape_dark >= SEASON_SKULL_MIN_DARK
                and extra <= SEASON_SKULL_MAX_EXTRA):
            kind = 'skull'
        elif terrain >= len(pixels) * SEASON_TERRAIN_RATIO:
            kind = 'closed'
        elif shape_light <= SEASON_SKULL_ABSENT and shape_dark <= SEASON_HIDDEN_MAX_DARK:
            kind = 'hidden'
        else:
            kind = 'ambiguous'
        camp = sum(1 for px in pixels if _near_colour(px, SEASON_CAMP_FLAG))
        readings[name] = {'kind': kind, 'light': light, 'terrain': terrain, 'camp': camp,
                          'pixels': len(pixels), 'shape': [shape_light, shape_dark],
                          'extra_light': extra, 'blue': blue}
    definite = sum(1 for row in readings.values() if row['kind'] in ('skull', 'closed'))
    skulls = sum(1 for row in readings.values() if row['kind'] == 'skull')
    # At least one live skull must read in this very frame: that proves the
    # skull palette is being drawn normally, so another site's missing skull
    # is a measurement and not a dimmed/transitional frame.
    return {'readings': readings,
            'healthy': definite >= SEASON_SITE_HEALTHY_READS and skulls >= 1}


def season_sites(frame, chapter, cursor=None, detailed=False):
    """{site: 'closed'|'open'} from the Y view's four corner markers.

    ``detailed=True`` returns the ``season_site_readings`` payload instead
    (the world survey needs the hidden/partial classes and the frame health).

    Skull light is a live king. A box that is nothing but island terrain has
    no marker: the site is cleared. A partial skull, a general's portrait or
    an army marker is *not* a state: it is left out so the caller keeps its
    previous state and the richer reading (``season_site_readings``) can
    deprioritise it without ever closing the site.
    """
    payload = season_site_readings(frame, chapter, cursor)
    if detailed:
        return payload
    readings = payload['readings']
    out = {}
    for name, row in readings.items():
        if row['kind'] == 'skull':
            out[name] = 'open'
        elif row['kind'] == 'closed':
            out[name] = 'closed'
    return out


# Chapter 3's boss skull, measured on the Y view (top-left (101, 63) for the
# cell (306, 141)). The same sprite marks the chapter-4 sites, and a moving
# boss draws it too. A boss on the Y view is therefore locatable by its skull
# alone, without knowing a fixed cell in advance.
Y_VIEW_SKULL_TEMPLATE = (
    (1, 0, (32, 32, 32)), (2, 0, (32, 32, 32)), (3, 0, (32, 32, 32)),
    (4, 0, (32, 32, 32)), (5, 0, (32, 32, 32)),
    (0, 1, (32, 32, 32)), (1, 1, (164, 165, 164)), (2, 1, (164, 165, 164)),
    (3, 1, (164, 165, 164)), (4, 1, (164, 165, 164)), (5, 1, (164, 165, 164)),
    (6, 1, (32, 32, 32)),
    (0, 2, (164, 165, 164)), (1, 2, (164, 165, 164)), (2, 2, (255, 254, 255)),
    (3, 2, (255, 254, 255)), (4, 2, (255, 254, 255)), (5, 2, (164, 165, 164)),
    (6, 2, (164, 165, 164)), (7, 2, (32, 32, 32)),
    (0, 3, (164, 165, 164)), (1, 3, (32, 32, 32)), (2, 3, (32, 32, 32)),
    (3, 3, (255, 254, 255)), (4, 3, (32, 32, 32)), (5, 3, (32, 32, 32)),
    (6, 3, (164, 165, 164)), (7, 3, (32, 32, 32)),
    (0, 4, (164, 165, 164)), (1, 4, (32, 32, 32)), (2, 4, (32, 32, 32)),
    (3, 4, (255, 254, 255)), (4, 4, (32, 32, 32)), (5, 4, (32, 32, 32)),
    (6, 4, (164, 165, 164)), (7, 4, (32, 32, 32)),
)
Y_VIEW_SKULL_FIRST = (1, 1)          # the template's first gray pixel (anchor)
# The sprite's top-left sits 0.25/2.125 px above-left of the cell's view point
# (chapter 3 measured: skull (101, 63) = cell (306, 141)).
SKULL_CELL_DX = 0.25
SKULL_CELL_DY = 2.125
# RGB565 captures differ by a few levels between cores (the live g1190
# chapter-9 view has gray 160,164,160 and white 248,252,248 where the
# production fixtures hold 164,165,164 and 255,254,255), and the chapter-9
# moving boss draws the same skull sprite with red pupils instead of black
# eye sockets. Pixel equality therefore never saw the live boss at all
# (g1190 2026-11-15 71-3: a red-eyed skull beside イデ！ was missed for months).
Y_VIEW_SKULL_TOLERANCE = 8
Y_VIEW_SKULL_PUPILS = ((2, 4), (4, 4))       # red pupils in the chapter-9 variant


def _skull_colour(pixel, colour, tolerance=Y_VIEW_SKULL_TOLERANCE):
    return (abs(pixel[0] - colour[0]) <= tolerance
            and abs(pixel[1] - colour[1]) <= tolerance
            and abs(pixel[2] - colour[2]) <= tolerance)


def _skull_pupil(pixel):
    """The chapter-9 live boss's red pupil (measured (248, 24, 0))."""
    return pixel[0] >= 200 and pixel[1] <= 90 and pixel[2] <= 120


def y_view_skulls(frame):
    """Top-left positions of the boss skull sprite on the whole-island view."""
    if frame is None or not hasattr(frame, 'pixel'):
        return []
    gray = Y_VIEW_SKULL_TEMPLATE[[i for i, item in enumerate(Y_VIEW_SKULL_TEMPLATE)
                                  if item[2] == (164, 165, 164)][0]][2]
    first_x, first_y = Y_VIEW_SKULL_FIRST
    out = []
    for y in range(20, min(180, frame.height)):
        for x in range(20, min(236, frame.width)):
            if not _skull_colour(frame.pixel(x, y), gray):
                continue
            ox, oy = x - first_x, y - first_y
            for dx, dy, colour in Y_VIEW_SKULL_TEMPLATE:
                pixel = frame.pixel(ox + dx, oy + dy)
                if (dx, dy) in Y_VIEW_SKULL_PUPILS and _skull_pupil(pixel):
                    continue
                if not _skull_colour(pixel, colour):
                    break
            else:
                out.append((ox, oy))
    return out


def skull_cell_at(skull_xy, chapter):
    """The chart cell under a Y-view skull sprite (chapter 3's measured anchor)."""
    offset = chart.Y_JUMP_OFFSET.get(chapter)
    if not offset or not skull_xy:
        return None
    x, y = skull_xy
    return (round((x + SKULL_CELL_DX - offset[0]) * 8),
            round((y + SKULL_CELL_DY - offset[1]) * 8))


def world_markers(frame) -> list:
    """▲ army markers on the whole-island view, as (x0, y0, x1, y1) boxes.

    Owner rule (2026-09-28): the Y view also shows ▲ marks for units, and
    they must be checked on every survey. Measured in the isolated probe:
    an own marker is a ~7x6 bright red-orange triangle (255,24,0); castle
    flags (230,56,90)/(0,64,189) sit far outside that colour's tolerance.
    """
    if frame is None:
        return []
    pts = bit_points(frame, near_bits(frame, WORLD_MARKER, WORLD_COLOUR_TOL))
    seen, out = set(), []
    for p in sorted(pts):
        if p in seen:
            continue
        stack, comp = [p], []
        seen.add(p)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (x + dx, y + dy)
                    if (n not in seen and 0 <= n[0] < frame.width and 0 <= n[1] < frame.height
                            and _near_colour(frame.pixel(*n), WORLD_MARKER)):
                        seen.add(n)
                        stack.append(n)
        if not 6 <= len(comp) <= 60:
            continue
        xs = [c[0] for c in comp]
        ys = [c[1] for c in comp]
        if max(xs) - min(xs) > 12 or max(ys) - min(ys) > 12:
            continue
        out.append((min(xs), min(ys), max(xs), max(ys)))
    return out

# ------------------------------------------------------ general-list marker
# The しょうぐん list draws a white/gray inverted triangle over the selected
# general's current map position (measured on g1075 2026-11-05: the marker
# moves between トレビス / ブカティーニ / ココット, while the red triangle stays
# on the hero). The exact 7x7 sprite is stable across chapters: white cap,
# gray body, black outline. It is not the red army marker of world_markers.
GENERAL_MARKER_WHITE = (255, 254, 255)
GENERAL_MARKER_GRAY = (90, 88, 90)
GENERAL_MARKER_BLACK = (32, 32, 32)
GENERAL_MARKER_TOLERANCE = 3
MARKER_EDGE_CHROMA = 16        # gray/black edge pixels blended with the ground under them
MAP_PANEL_RIGHT = 154          # roster map panel's right edge (flag search bound below)

# Castle flag icons on the same small island map: red = own, blue = enemy.
# Their colour matches world_flags; the roster map is the same island at
# 1/8 of the chart-cell coordinates.
MAP_VIEW_SCALE = 8
MAP_VIEW_DEFAULT_OFFSET = (17, 30)
MAP_CASTLE_TOLERANCE = 96       # 12 screen px: the marker may sit over a unit
MAP_VIEW_FLAG_TOLERANCE = 6
MAP_VIEW_CALIBRATION_HITS = 3

# (dx, dy, colour) of the measured selection triangle.
_GENERAL_MARKER_PATTERN = (
    *((dx, 0, GENERAL_MARKER_WHITE) for dx in range(7)),
    (0, 1, GENERAL_MARKER_GRAY), *((dx, 1, GENERAL_MARKER_WHITE) for dx in range(1, 6)),
    (6, 1, GENERAL_MARKER_GRAY),
    (0, 2, GENERAL_MARKER_GRAY), (1, 2, GENERAL_MARKER_GRAY),
    *((dx, 2, GENERAL_MARKER_WHITE) for dx in range(2, 5)),
    (5, 2, GENERAL_MARKER_GRAY), (6, 2, GENERAL_MARKER_GRAY),
    (0, 3, GENERAL_MARKER_BLACK), (1, 3, GENERAL_MARKER_GRAY), (2, 3, GENERAL_MARKER_GRAY),
    (3, 3, GENERAL_MARKER_WHITE), (4, 3, GENERAL_MARKER_GRAY), (5, 3, GENERAL_MARKER_GRAY),
    (6, 3, GENERAL_MARKER_BLACK),
    (1, 4, GENERAL_MARKER_BLACK), (2, 4, GENERAL_MARKER_GRAY),
    (3, 4, GENERAL_MARKER_GRAY), (4, 4, GENERAL_MARKER_GRAY), (5, 4, GENERAL_MARKER_BLACK),
    (2, 5, GENERAL_MARKER_BLACK), (3, 5, GENERAL_MARKER_GRAY), (4, 5, GENERAL_MARKER_BLACK),
    (3, 6, GENERAL_MARKER_BLACK),
)


def _near_rgb(pixel, colour, tolerance=GENERAL_MARKER_TOLERANCE):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, colour))


def _marker_shade_matches(pixel, colour):
    """Match a grayscale sprite under a modest palette/brightness shift.

    A single exact RGB template missed the entire triangle when the game
    tinted the SNES grayscale palette by more than three levels. Preserve the
    exact match first, then accept only near-neutral light/mid/dark shades.
    The complete 7x7 triangle pattern is still required; coloured army
    markers and castle flags cannot pass the neutral-colour guard.
    """
    if _near_rgb(pixel, colour):
        return True
    # The sprite's gray/black edge is blended with the ground under it: g1163
    # 2026-11-11 00:26 drew (86,96,83) over grass (chroma 13), so the real map
    # marker failed by one level and the scan fell through to the list's
    # scroll arrow. White stays strict; the 7x7 shape is still required.
    chroma = max(pixel) - min(pixel)
    if chroma > (12 if colour == GENERAL_MARKER_WHITE else MARKER_EDGE_CHROMA):
        return False
    value = sum(pixel) / 3
    if colour == GENERAL_MARKER_WHITE:
        return value >= 210
    if colour == GENERAL_MARKER_GRAY:
        return 65 <= value <= 130
    return value <= 60


def selection_marker(frame):
    """The inverted white/gray triangle over the selected general, or None."""
    if frame is None:
        return None
    # The roster draws the triangle immediately left of the name column, which
    # starts at x=168 (house._names_at), so the sprite can begin past the old
    # x=154 edge. g1163 2026-11-09: 20 of 25 monthly markers were unbound with
    # no reason, and the live 299x224 frame is wider than the 256-wide design.
    # The same white/gray triangle is also the list's 「more names」 scroll
    # arrow under the name column (g1163 2026-11-11: box (192,161) for every
    # general, out_of_tolerance, a status panel each). A match inside the map
    # panel is the general's position and wins over one outside it.
    outside = None
    for y in range(8, min(frame.height - 7, 216)):
        for x in range(8, min(frame.width - 7, 200)):
            if all(_marker_shade_matches(frame.pixel(x + dx, y + dy), colour)
                   for dx, dy, colour in _GENERAL_MARKER_PATTERN):
                found = {'box': (x, y, x + 6, y + 6), 'center': (x + 3, y + 3),
                         'tip': (x + 3, y + 6)}
                if x + 6 < MAP_PANEL_RIGHT:
                    return found
                if outside is None:
                    outside = found
    return outside


def _map_icon_centres(frame):
    """Centres of the red/blue castle-flag icons inside the roster map panel."""
    if frame is None:
        return []
    labels = {}
    for y in range(16, min(frame.height, 178)):
        for x in range(16, min(frame.width, 154)):
            pixel = frame.pixel(x, y)
            if _near_rgb(pixel, (230, 56, 90), MAP_VIEW_FLAG_TOLERANCE):
                labels[(x, y)] = 'own'
            elif _near_rgb(pixel, (0, 64, 189), MAP_VIEW_FLAG_TOLERANCE):
                labels[(x, y)] = 'enemy'
    seen, out = set(), []
    for start in sorted(labels):
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbour = (x + dx, y + dy)
                    if neighbour not in seen and neighbour in labels:
                        seen.add(neighbour)
                        stack.append(neighbour)
        if not 6 <= len(comp) <= 60:
            continue
        xs = [p[0] for p in comp]
        ys = [p[1] for p in comp]
        if max(xs) - min(xs) > 12 or max(ys) - min(ys) > 12:
            continue
        out.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
    return out


def _map_view_offset(icons, castles):
    """Best 1/8-view offset for this frame, or the measured default.

    The roster map is the whole island in a fixed panel. Its exact origin
    shifts by a few pixels between chapters, so the flag icons themselves are
    matched to the chart cells; with too few icons the measured default keeps
    the reader deterministic instead of inventing a position.
    """
    best_hits = MAP_VIEW_CALIBRATION_HITS - 1
    best = MAP_VIEW_DEFAULT_OFFSET
    best_gap = 0
    for ox in range(5, 45):
        for oy in range(10, 60):
            hits = sum(1 for icon in icons if any(
                abs(icon[0] - (cell[0] / MAP_VIEW_SCALE + ox)) <= 8
                and abs(icon[1] - (cell[1] / MAP_VIEW_SCALE + oy)) <= 8
                for cell in castles.values()))
            gap = abs(ox - MAP_VIEW_DEFAULT_OFFSET[0]) + abs(oy - MAP_VIEW_DEFAULT_OFFSET[1])
            if hits > best_hits or (hits == best_hits and hits >= MAP_VIEW_CALIBRATION_HITS
                                    and gap < best_gap):
                best_hits, best, best_gap = hits, (ox, oy), gap
    return best


def marker_castle(frame, chapter, hint=None, evidence=None):
    """Bind the selected-general triangle to a chart castle, or None.

    Returns the castle cell under the marker together with the evidence used.
    The marker is only meaningful for a status that already read
    しろのなかにいます; a tent or marching unit must not be turned into a
    castle. No inference is made when the view cannot be calibrated or the
    nearest cell is beyond MAP_CASTLE_TOLERANCE.

    ``hint`` is the castle this general was last recorded in. When two castles
    sit nearly equidistant under the marker the reading is ambiguous, and the
    recorded castle settles it as long as it is itself inside the tolerance
    (g1163 2026-11-09: 29 unverified markers in 40 minutes each cost a full
    status panel).
    """
    def _why(reason):
        # Why a marker did not bind: the monthly walk pays a status panel for
        # every one of these, so the reason is worth recording (g1163
        # 2026-11-09: 53 unverified markers in 40 minutes, no reason logged).
        if isinstance(evidence, dict):
            evidence['reason'] = reason
        return None

    if frame is None or type(chapter) is not int:
        return _why('no_frame')
    castles = chart.castles(chapter)
    marker = selection_marker(frame)
    if not marker or not castles:
        return _why('no_marker')
    # A triangle outside the map panel is the roster's 「more names」 scroll
    # arrow, never a position marker: it can never bind a castle, so reject
    # it here instead of letting every monthly read burn an out_of_tolerance
    # fallback (#4410: the same box [192,161,198,167] repeated all walk).
    if marker['box'][2] >= MAP_PANEL_RIGHT:
        return _why('scroll_arrow')
    icons = _map_icon_centres(frame)
    offset = _map_view_offset(icons, castles)
    tip = marker['tip']
    icon = min(icons, key=lambda p: (p[0] - tip[0]) ** 2 + (p[1] - tip[1]) ** 2, default=None)
    if icon is not None and (icon[0] - tip[0]) ** 2 + (icon[1] - tip[1]) ** 2 <= 14 ** 2:
        point = icon
    else:
        point = tip
    field = ((point[0] - offset[0]) * MAP_VIEW_SCALE,
             (point[1] - offset[1]) * MAP_VIEW_SCALE)
    ordered = sorted(castles.items(), key=lambda item: (
        (item[1][0] - field[0]) ** 2 + (item[1][1] - field[1]) ** 2))
    name, cell = ordered[0]
    distance = ((cell[0] - field[0]) ** 2 + (cell[1] - field[1]) ** 2) ** 0.5
    if distance > MAP_CASTLE_TOLERANCE:
        # Chapter cells 7-12 are source-map estimates, so the whole map can sit
        # a little off and every marker lands outside the tolerance (g1163
        # 2026-11-10 00:35: 6 of 9 monthly markers). The recorded castle is
        # prior knowledge about this general, so accept it inside a wider bound
        # instead of paying a status panel for each one.
        if isinstance(hint, str) and hint in castles:
            cell = castles[hint]
            hinted = ((cell[0] - field[0]) ** 2 + (cell[1] - field[1]) ** 2) ** 0.5
            if hinted <= MAP_CASTLE_TOLERANCE * 1.5:
                if isinstance(evidence, dict):
                    evidence['reason'] = 'out_of_tolerance_hint'
                return {'castle': hint, 'field': [round(field[0]), round(field[1])],
                        'distance': round(hinted, 1), 'marker': list(marker['box']),
                        'offset': list(offset), 'icons': len(icons), 'point': list(point)}
            if isinstance(evidence, dict):
                evidence['hint'] = hint
                evidence['hint_distance'] = round(hinted, 1)
        if isinstance(evidence, dict):
            # Near-miss geometry for the unverified-marker log (docich#4745):
            # field position, nearest castle and its distance tell a
            # systematic chapter-cell offset from a single bad marker.
            evidence['field'] = [round(field[0]), round(field[1])]
            evidence['distance'] = round(distance, 1)
            evidence['nearest'] = name
        # #4745: chapters 7-12 use source-map-estimated castle cells, so a
        # systematic chart error throws every marker out of tolerance and the
        # monthly walk pays a status panel for each one (65 fallbacks in one
        # walk). On those chapters accept a unique nearest castle inside a
        # wider bound only when the runner-up is clearly farther away, so an
        # ambiguous reading still stays unbound.
        if chapter >= 7:
            runner = ((ordered[1][1][0] - field[0]) ** 2
                      + (ordered[1][1][1] - field[1]) ** 2) ** 0.5 if len(ordered) > 1 else None
            if distance <= MAP_CASTLE_TOLERANCE * 2 and (runner is None or runner >= distance * 2):
                if isinstance(evidence, dict):
                    evidence['reason'] = 'out_of_tolerance_estimated_ch'
                return {'castle': name, 'field': [round(field[0]), round(field[1])],
                        'distance': round(distance, 1), 'marker': list(marker['box']),
                        'offset': list(offset), 'icons': len(icons), 'point': list(point),
                        'estimated_cell': True}
        return _why('out_of_tolerance')
    if len(ordered) > 1:
        runner = ((ordered[1][1][0] - field[0]) ** 2
                  + (ordered[1][1][1] - field[1]) ** 2) ** 0.5
        if runner < distance * 1.5:
            settled = False
            if isinstance(hint, str) and hint in castles:
                cell = castles[hint]
                hinted = ((cell[0] - field[0]) ** 2 + (cell[1] - field[1]) ** 2) ** 0.5
                if hinted <= MAP_CASTLE_TOLERANCE:
                    name, distance, settled = hint, hinted, True
            if not settled:
                return _why('ambiguous')
    if isinstance(evidence, dict):
        evidence['reason'] = 'mapped'
    return {'castle': name, 'field': [round(field[0]), round(field[1])],
            'distance': round(distance, 1), 'marker': list(marker['box']),
            'offset': list(offset), 'icons': len(icons), 'point': list(point)}

                               # around a flag in g419 (ring and flag overlap: +-1 px jitter)
_Y_RING = [(dx, dy) for dx in range(-13, 14) for dy in range(-13, 14)
           if 100 <= dx * dx + dy * dy <= 169]          # radius 5-6.5 px, half-pixel units


def _is_blue_pixel(pixel) -> bool:
    r, g, b = pixel
    return r <= 40 and g <= 80 and b >= 150 and b - g >= 100


def _is_black_pixel(pixel) -> bool:
    r, g, b = pixel
    return r <= 70 and g <= 70 and b <= 70


def _blue_g_box_cursor(frame):
    """The blue "G" box cursor: a solid ~11x11 blue square holding a white G.

    Chapter 9's real cursor is this icon, ringed by thin gold corner brackets
    too sparse for the gold test. Detect the dense blue body instead — the
    blinking blue lake icon (~9 px) and pennant strips are far smaller, so a
    size+density floor keeps them out (g1190: only the ~98-px G box qualifies).
    """
    best_box = None
    for x0 in range(40, 220 - 12):
        for y0 in range(40, 200 - 12):
            inside = [(x, y) for x in range(x0, x0 + 12) for y in range(y0, y0 + 12)
                      if _is_blue_pixel(frame.pixel(x, y))]
            if len(inside) >= 40 and (best_box is None or len(inside) > len(best_box)):
                best_box = inside
    if not best_box:
        return None
    xs = [x for x, _ in best_box]; ys = [y for _, y in best_box]
    if max(xs) - min(xs) >= 8 and max(ys) - min(ys) >= 8:
        return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    return None


def world_cursor(frame, chapter=None):
    """Centre of the Y view's cursor.

    Detection order:
      1. gold G corners (chapter 1/2 sortie cursor, chapter 9);
      2. chapter-3 whole map: 3x3 blue centre dot inside a black dashed ring
         (radius 8-9 px) (g1075);
      3. white dashed ring (chapter 2 boss / snowy maps).
    """
    if frame is None:
        return None
    # 1. gold G corners, before the blue-dot ring. Chapter 9's Y view has a
    # blinking blue icon inside a black-outlined lake at (169, 116) that the
    # ring test accepts; while it blinked on, the real G at (65.5, 142.5) was
    # never returned and the target jump to ハヤク bounced between the two
    # until its move limit (g1163 2026-11-11 07:59 and 08:22). On every saved
    # Y view the gold test either finds the same cursor or finds none.
    gold = [(x, y) for x in range(40, 220) for y in range(40, 200)
            if _near_colour(frame.pixel(x, y), (255, 182, 0))]
    # Only the compact G corners (~15 px box): the map's gold edge arrows are
    # far apart and their joint box read as a cursor (g419 10:49), and chapter
    # 2's gold camp triangle beside フーリック widened the box so no target
    # jump there could read the G (isolated probe, g421 state). Take the
    # G-sized window holding the most gold instead of the whole box.
    windows = {}
    for x0 in sorted({x for x, _ in gold}):
        for y0 in sorted({y for _, y in gold}):
            inside = [(x, y) for x, y in gold if x0 <= x <= x0 + 18 and y0 <= y <= y0 + 18]
            if len(inside) >= 12:
                key = (round(min(x for x, _ in inside) / 4), round(min(y for _, y in inside) / 4))
                if len(inside) > len(windows.get(key) or []):
                    windows[key] = inside
    # The real G corner keeps its gold at the box's four corners and nowhere
    # else. A castle pennant or a unit ▲ (g1190 40-9: the ▲ cluster at
    # ~(126,96) read as the cursor, ~0.26 corner fill) has gold spread across
    # the whole box. Score every window on that corner fill and return the
    # true G rather than the densest blob, so a marker can't hide the cursor.
    scored = []
    for inside in windows.values():
        xs = [x for x, _ in inside]; ys = [y for _, y in inside]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if w < 10 or h < 10:
            continue
        corners = ((min(xs), min(ys)), (max(xs), min(ys)),
                   (min(xs), max(ys)), (max(xs), max(ys)))
        occupied_corners = sum(
            1 for x0, y0 in corners
            if any(abs(x - x0) <= 3 and abs(y - y0) <= 3 for x, y in inside))
        at_corner = sum(1 for x, y in inside
                        if any(abs(x - x0) <= 3 and abs(y - y0) <= 3
                               for x0, y0 in corners))
        ratio = at_corner / len(inside)
        if ratio >= 0.7:
            scored.append((occupied_corners == 4, ratio, len(inside),
                           (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
    scored.sort(reverse=True)
    # The solid blue "G" box cursor is unambiguous on every Y view (zero false
    # positives across all saved fixtures). Prefer it over unrelated gold unit
    # clusters elsewhere on the map (#4859: two unit ▲s produced 0.94-0.95
    # corner-fill false gold at (131.5, 70.0)/(151.0, 98.0), hijacking Y jumps
    # even while the real blue G box was visible). When a matching gold corner
    # box surrounds the blue G, keep its exact corner-box centre.
    blue_g = _blue_g_box_cursor(frame)
    if blue_g is not None:
        near = [cand for cand in scored
                if abs(cand[3] - blue_g[0]) <= 3 and abs(cand[4] - blue_g[1]) <= 3]
        if near:
            return (near[0][3], near[0][4])
        return blue_g
    if scored:
        return (scored[0][3], scored[0][4])
    if chapter == 9:
        # Otherwise unreadable is safer than a false cursor.
        return None
    # 2. Blue-dotted black dashed ring. The white ring vote cannot see this
    # cursor, and the gold map frame would otherwise be misread as a G.
    blue_pixels = [(x, y) for x in range(40, 220) for y in range(40, 200)
                   if _is_blue_pixel(frame.pixel(x, y))]
    blue_set = set(blue_pixels)
    seen = set()
    components = []
    for seed in blue_pixels:
        if seed in seen:
            continue
        stack = [seed]
        seen.add(seed)
        comp = []
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (-1, 1), (1, -1), (-1, -1)):
                nxt = (x + dx, y + dy)
                if nxt in blue_set and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(comp)
    # Blue-dotted black-ring candidates are normally single on a frame;
    # when more than one appears, the largest ring is taken (g1075 has one).
    best_ring = 0
    best_center = None
    best_size = 0
    for comp in components:
        if len(comp) < 8 or len(comp) > 10:
            continue
        xs = [p[0] for p in comp]
        ys = [p[1] for p in comp]
        if max(xs) - min(xs) > 2 or max(ys) - min(ys) > 2:
            continue
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        ring = 0
        ix = int(round(cx))
        iy = int(round(cy))
        for x in range(ix - 11, ix + 12):
            for y in range(iy - 11, iy + 12):
                if not (0 <= x < frame.width and 0 <= y < frame.height):
                    continue
                dx = x - cx
                dy = y - cy
                r2 = dx * dx + dy * dy
                if 56 <= r2 <= 110 and _is_black_pixel(frame.pixel(x, y)):
                    ring += 1
        if ring < 50:
            continue
        if ring > best_ring or (ring == best_ring and len(comp) > best_size):
            best_ring = ring
            best_size = len(comp)
            best_center = (cx, cy)
    if best_center is not None:
        return best_center

    # 3. white dashed ring
    votes = {}
    for x in range(40, 220):
        for y in range(40, 200):
            if _near_colour(frame.pixel(x, y), (255, 255, 255)):
                for dx, dy in _Y_RING:
                    key = (2 * x + dx, 2 * y + dy)
                    votes[key] = votes.get(key, 0) + 1
    # The ring only appears inside the island view (sea 64-191 x 47-174);
    # the ornate frame's highlights just outside voted as strongly (g419 10:49,
    # a frame where the blinking ring was off). The window is the whole sea:
    # the narrower 70-186 x 53-169 lost real rings at the sea's edge (g1075
    # 2026-10-29 12:20: centre (65, 81.5) west of リグア, 24 votes, so the
    # source jump closed as unreadable; a ring at (153, 50.5) likewise). On
    # every saved Y view the added band held no other centre above 1 vote.
    votes = {k: v for k, v in votes.items() if 128 <= k[0] <= 382 and 94 <= k[1] <= 348}
    if not votes:
        return None
    key, count = max(votes.items(), key=lambda kv: kv[1])
    return (key[0] / 2, key[1] / 2) if count >= 18 else None



UNDER_CURSOR_PX = 8            # roof target vs cursor cell when standing on a castle


def _roof_under_cursor(screen, frame) -> bool:
    """A castle roof whose selecting cell is the cursor's cell, read directly.

    No exclusion box: the cursor brackets overlap the roof they select.
    """
    s = _cursor(screen)
    if not s or frame is None:
        return True                  # nothing to check against: keep old behaviour
    return any(abs(r['target'][0] - s[0]) <= UNDER_CURSOR_PX
               and abs(r['target'][1] - s[1]) <= UNDER_CURSOR_PX
               for r in castle_roofs(frame))


def is_camp_menu(screen) -> bool:
    """Our tent's menu: いどう/ステータス/キャンプ/きかん (isolated probe, g436 22:04)."""
    return all(word in screen.text for word in ('いどう', 'ステータス', 'キャンプ', 'きかん'))


def is_fort_menu(screen) -> bool:
    """A 砦 (two or more generals camped together, drawn as a dome).

    Measured on g1097 (live 2026-11-01 19:24/19:33 and the isolated probe of
    its start state): A on the dome opens しゅつげき/こうかん, not the tent's
    いどう/ステータス/キャンプ/きかん, and the list of the generals inside opens
    beside it (``fort_list_open``). A castle menu also says しゅつげき, but it
    always has ステータス and is classified ``castle_menu``.
    """
    return (screen.kind == 'text' and screen.text.startswith('しゅつげき')
            and 'こうかん' in screen.text and 'ステータス' not in screen.text)


FORT_MENU_COMMANDS = ('しゅつげき', 'こうかん')


def fort_list_open(screen) -> bool:
    """The generals' list beside the 砦 menu, read from its rows, not the hand x.

    The old "hand x > 100" test assumed the menu always sits at the screen's
    left edge. In g1288 2026-11-20 05:46 the fort menu itself was drawn around
    x 93..133 (hand on しゅつげき at x≈103), so the test called the menu a
    list, ``boss_pursuit._fort_step`` read the menu's own しゅつげき/こうかん as
    members, and every pursuit closed ``general_not_in_fort`` without ever
    opening the list. The hand's row separates the two panels: a command on
    that row is the menu; a non-command row beside it is the member list.
    """
    if not is_fort_menu(screen):
        return False
    options = _options(screen)
    row = screen.hand[1] + 6 if screen.hand else None
    if row is not None and any(abs(y - row) <= 2 and word in FORT_MENU_COMMANDS
                               for _, y, word in options):
        return False
    return any(x > 100 and word not in FORT_MENU_COMMANDS for x, _, word in options)


def _empty_sortie_inventory(screen, *, occluded=0):
    """Measured g328 empty inventory, using exact text cells in its panels.

    Only these text baselines establish the receipt. Left-side stats, mark
    rows above glyphs and the hand left of the choices are outside them.
    No quantity or nonempty-inventory format is inferred here.

    ``occluded`` lets up to that many expected cells be unreadable. The
    source castle's sprite is drawn over the right panel for some castles
    (g1163 2026-11-11 いちじょうだに: は of きりふだは and four cells after
    きりふだ read as unknown on every frame), and the exact match then held a
    repair trip for its whole step limit each month. Every readable cell must
    still be the expected character, so a card name can never pass.
    """
    if screen.kind != 'sortie_confirm':
        return False
    rows = ((47, 136, ((136, 'きりふだ'),)),
            (63, 136, ((176, 'きりふだは'),)),
            (79, 136, ((176, 'ありません……'),)),
            (95, 136, ((136, 'ーしゅつげき'), (192, 'しますか?ー'))),
            (111, 160, ((160, 'うむッ!'),)),
            (127, 160, ((160, 'いかんッ!'),)))
    lost = 0
    for y, left, spans in rows:
        expected = tuple((x + 8 * index, ch) for x, text in spans for index, ch in enumerate(text))
        observed = tuple((x, ch) for line in screen.lines if line.y == y
                         for x, ch in line.cells if left <= x < 240)
        if observed == expected:
            continue
        if not occluded:
            return False
        want = dict(expected)
        seen = {x: ch for x, ch in observed if ch != UNKNOWN}
        if any(want.get(x) != ch for x, ch in seen.items()):
            return False
        lost += sum(1 for x in want if x not in seen)
        if lost > occluded:
            return False
    return not any(card in word for _, _, word in _options(screen) for card in ALL_CARD_NAMES)


def _sortie_inventory(screen, *, occluded=0):
    """Read complete card names from the fixed sortie panel slots.

    The one-card placement is live-measured. Additional names are classified
    only when each occupies a complete known slot and the whole panel matches;
    this recognizes text, not quantities or partial names. The receipt uses
    the canonical game catalogue, independently of the planning allowlist.

    ``occluded`` gives the fixed cells the same allowance
    ``_empty_sortie_inventory`` takes: a source castle's sprite drawn over the
    right panel reads those tiles as unknown on every frame (docich#5146:
    ながはま's sorties cancelled every unreadable confirmation). Card names
    are content, not fixed cells — a name split by an unknown glyph still
    fails its span check and keeps the panel unreadable instead of guessing
    the carried kit.
    """
    # g1133 decision-003: the same confirm cursor measured x0=139 (the
    # left-edge orange column jitters 138/139, see _measured_card_select).
    if (screen.kind != 'sortie_confirm'
            or screen.hand not in ((138, 105, 156, 118), (138, 105, 158, 118),
                                   (139, 105, 156, 118), (139, 105, 158, 118))):
        return None
    def cells(y, left=136):
        return tuple((x, ch) for line in screen.lines if line.y == y
                     for x, ch in line.cells if left <= x < 256)
    def text_cells(x, text):
        return tuple((x + 8*i, ch) for i, ch in enumerate(text))
    lost = 0
    def matches(observed, expected):
        nonlocal lost
        if observed == expected:
            return True
        if not occluded:
            return False
        want = dict(expected)
        seen = {x: ch for x, ch in observed if ch != UNKNOWN}
        if any(want.get(x) != ch for x, ch in seen.items()):
            return False
        lost += sum(1 for x in want if x not in seen)
        return lost <= occluded
    first = cells(47)
    heading = text_cells(136, 'きりふだ')
    first_spans = TextLine(47, tuple((x, ch) for x, ch in first if x >= 176)).spans()
    if len(first_spans) != 1 or first_spans[0][0] != 176 or first_spans[0][1] not in ALL_CARD_NAMES:
        return None
    cards = [first_spans[0][1]]
    expected = {47: heading + text_cells(176, cards[0]), 63: (), 79: (),
                95: text_cells(136, 'ーしゅつげき') + text_cells(192, 'しますか?ー'),
                111: text_cells(160, 'うむッ!'), 127: text_cells(160, 'いかんッ!')}
    empty_tail = False
    for y in (63, 79):
        row = cells(y)
        if not row:
            empty_tail = True
            continue
        if empty_tail:
            return None
        spans = TextLine(y, tuple((x, ch) for x, ch in row if x >= 176)).spans()
        if len(spans) != 1 or spans[0][0] != 176 or spans[0][1] not in ALL_CARD_NAMES:
            return None
        cards.append(spans[0][1])
        expected[y] = text_cells(176, spans[0][1])
    for y, wanted in expected.items():
        if not matches(cells(y, 160 if y in (111, 127) else 136), wanted):
            return None
    # Background and dakuten/cursor UNKNOWNs are not body text. Additional
    # known text anywhere else inside this right panel is uncalibrated.
    if any(ch != UNKNOWN and 136 <= x < 256
           and (line.y not in expected or (line.y in (111, 127) and x < 160))
           for line in screen.lines if 32 <= line.y < 135 for x, ch in line.cells):
        return None
    option_cards = [word for _, _, word in _options(screen) if word in ALL_CARD_NAMES]
    if occluded:
        # An occluded option span drops a carried name entirely; a readable
        # name that is not one of the slot cards is still uncalibrated text.
        if any(word not in cards for word in option_cards):
            return None
    elif option_cards != cards:
        return None
    return cards


# きりふだセレクト is a four-row window (y=55,71,87,103). A ▲ at
# x=184..190, y=41..44 means rows above are hidden; a ▼ at x=184..190,
# y=113..116 means rows below are hidden. Isolated probes of 180 frames each
# at the top, middle and bottom of the list found both arrows static (never a
# missing frame; prod1190 and ch9-local, 2026-11-22). The drawn arrow is 16
# near-white pixels (248,252,248), luminance 250. The green panel dither in
# the same boxes peaks at (0,149,0), luminance 87, so 140 separates them.
# Count lit pixels rather than match one bitmap: a one-pixel clip leaves 9-15
# lit, while an absent arrow has 0. Anything between is unreadable (None).
CARD_SCROLL_ABOVE = (184, 41, 191, 45)
CARD_SCROLL_BELOW = (184, 113, 191, 117)
CARD_ARROW_LUMINANCE = 140
CARD_ARROW_LIT = 8


def _card_scroll_arrow(frame, box):
    """True when the arrow is drawn, False when absent, None if unreadable."""
    if frame is None or box[2] > frame.width or box[3] > frame.height:
        return None
    lit = 0
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2]):
            r, g, b = frame.pixel(x, y)
            if (r * 299 + g * 587 + b * 114) // 1000 > CARD_ARROW_LUMINANCE:
                lit += 1
    if lit >= CARD_ARROW_LIT:
        return True
    if lit == 0:
        return False
    return None


def _measured_card_select(screen, frame=None):
    """Read contiguous rows in the measured g328 panel, never infer stock.

    Stock is right-aligned at x=232; live tens for 10-99 sit at x=224.
    Depleted final items disappear. Empty baselines must form a trailing
    suffix; UNKNOWN cells are unreadable rows, not evidence of emptiness.

    The returned inventory also carries ``more_above`` / ``more_below`` from
    the panel's ▲/▼ pixels (True/False/None). Only False for both proves the
    visible rows are the whole list. A Screen built without a frame reads the
    ``card_more_above`` / ``card_more_below`` attributes instead; None there
    means the caller supplied no arrow evidence, never "no hidden rows".
    """
    if screen.kind != 'card_select':
        return None
    def cells(y, left):
        return tuple((x, ch) for line in screen.lines if line.y == y
                     for x, ch in line.cells if left <= x < 256)
    def text_cells(x, text):
        return tuple((x + 8 * i, ch) for i, ch in enumerate(text))
    header = cells(31, 136)
    remaining = dict(header).get(224)
    if remaining not in ('0', '1', '2', '3'):
        return None
    if header != text_cells(136, 'きりふだセレクト') + text_cells(208, f'あと{remaining}こ'):
        return None
    # The description line is a static label, not data: an OCR dropout of one
    # glyph ('バトルよ' + 'のきりふだです' with う missing) rejected the whole
    # panel and cost the sortie (g1163 2026-11-10 07:4x: 15 holds an hour).
    note = cells(127, 136)
    note_text = ''.join(ch for _, ch in note)
    if not note_text.startswith('バトル') or len(note_text) < 10 or UNKNOWN in note_text:
        return None
    rows = []
    empty_tail = False
    for y in (55, 71, 87, 103):
        row = cells(y, 160)
        if not row:
            empty_tail = True
            continue
        if empty_tail:
            return None
        digits = dict(row)
        ones = digits.get(232)
        if ones not in tuple('0123456789'):
            return None
        tens = digits.get(224)
        # Live g328 stock is right-aligned at x=232; tens sit at x=224 (10-99).
        if tens in tuple('123456789'):
            stock_text = tens + ones
            name_limit = 224
            stock_cells = ((224, tens), (232, ones))
        else:
            stock_text = ones
            name_limit = 232
            stock_cells = ((232, ones),)
        names = TextLine(y, tuple((x, ch) for x, ch in row if x < name_limit)).spans()
        # Every real card name (ALL_CARD_NAMES, the 32-card gcgx table) is a
        # valid row, not just the selectable subset: g454 10:02 the panel
        # showed バルムンク (an event card outside CARD_NAMES) and every
        # reading was rejected, so the sortie held forever. Unknown cards are
        # read as rows but can never be selected by name.
        if len(names) != 1 or names[0][0] != 160 or names[0][1] not in ALL_CARD_NAMES:
            return None
        name = names[0][1]
        if row != text_cells(160, name) + stock_cells:
            return None
        rows.append({'y': y, 'card': name, 'stock': int(stock_text)})
    if not rows or len({row['card'] for row in rows}) != len(rows):
        return None
    # Extra text rows would be an uncalibrated inventory/scroll layout. Mark
    # rows above the known text can contain UNKNOWN and are not inventory.
    if any(ch != UNKNOWN and 136 <= x < 256
           and (line.y not in (55, 71, 87, 103) or x < 160)
           for line in screen.lines if 32 <= line.y < 127
           for x, ch in line.cells):
        return None
    if not screen.hand:
        return None
    x0, y0, x1, y1 = screen.hand
    selected_y = y0 + 6
    # g1133 2026-11-05: the same cursor's leftmost orange column jitters
    # between x=138 and x=139 across frames (decision-001/002 vs decision-081,
    # all row-1 selections). Accepting only 138 held every reading and
    # cancelled ヘスティア/トレビス's boss sorties after CARD_UNREADABLE_LIMIT.
    if (x0 not in (138, 139) or x1 not in (156, 158) or y1 != selected_y + 7
            or selected_y not in {row['y'] for row in rows}):
        return None
    if frame is not None:
        more_above = _card_scroll_arrow(frame, CARD_SCROLL_ABOVE)
        more_below = _card_scroll_arrow(frame, CARD_SCROLL_BELOW)
    else:
        more_above = getattr(screen, 'card_more_above', None)
        more_below = getattr(screen, 'card_more_below', None)
    return {'rows': rows, 'selected_y': selected_y, 'remaining': int(remaining),
            'more_above': more_above, 'more_below': more_below,
            'inventory_evidence': ('measured_four_row_card_select' if len(rows) == 4
                                   else 'structured_card_select'), 'observed_rows': len(rows)}


# フィールドの「きりふだ」一覧 (v1352/S3 で実測)。▲ は x=72..78,
# y=25..28、▼ は x=72..78, y=161..164。S1 のパネルと同じく、矢印は
# 16 個の近白色 (248,252,248) 画素で、緑のパネル地紋の輝度上限 87 とは
# 140 で分かれる。点滅しない (先頭・中間・末尾で各 180 フレーム静止)。
#
# ▼ の箱は y=161..162 の 2 行だけにする。一覧の下枠は y=163..165 が
# 明るい 1 本の罫線で、実測では末尾ページでも x=72..78 の罫線が 18 画素
# 点く。S1 と同じ 6 行の箱 (y=161..166) では「▼が無い」を 0 画素で
# 読めないので、矢印自身が太い上 2 行 (7+5=12 画素) だけを数える。
# 矢印があるページは 12、無いページは罫線の下なので 0。
FIELD_CARD_SCROLL_ABOVE = (72, 25, 79, 29)
FIELD_CARD_SCROLL_BELOW = (72, 161, 79, 163)


def _measured_field_card_list(screen, frame=None):
    """Read one page of the field きりふだ list, or None when it is unreadable.

    The list is the whole-inventory proof S3 needs: eight rows in card-ID
    order, each a card name from ``CARD_IDS`` and a 0..99 count. One bad row,
    a duplicate ID, or an out-of-order page is unreadable -- a partial page
    must never become "this card is absent". The arrows are True/False/None
    (S1's pixel rule with the field-list boxes); only False for both proves
    the visible rows are the whole list.
    """
    if screen.kind != 'field_card_list':
        return None
    rows = field_card_list_rows(screen)
    if rows is None:
        return None
    if frame is not None:
        more_above = _card_scroll_arrow(frame, FIELD_CARD_SCROLL_ABOVE)
        more_below = _card_scroll_arrow(frame, FIELD_CARD_SCROLL_BELOW)
    else:
        more_above = getattr(screen, 'card_more_above', None)
        more_below = getattr(screen, 'card_more_below', None)
    return {'rows': [{'card': card, 'stock': stock} for card, stock in rows],
            'more_above': more_above, 'more_below': more_below}


GENERAL_LIST_UI = frozenset({'しゅつげき', 'ステータス', 'しょうぐんは', 'おりません', 'おりません……'})

GENERAL_LIST_KINDS = frozenset({'general_list', 'castle_menu'})


def _present_generals(screen):
    """Names on a general list, [] for the empty-list message, None if unreadable."""
    if screen.kind not in GENERAL_LIST_KINDS:
        # Only a general list carries residents. The main menu answers with its
        # own items (しょうぐん/しろ/きりふだ/…) and a sortie confirmation with
        # へいし/にん/P, both of which read as "generals" when the frame is
        # asked anyway (g1163 2026-11-12 01:4x frames 101/108). Callers guard
        # the kind, so this is the second line that keeps menu labels out of a
        # castle's garrison.
        return None
    if 'おりません' in screen.text:
        return []
    if not screen.hand:
        return None
    if CASTLE_STATUS.search(screen.text or ''):
        # The castle's ステータス panel (castle name, しゅうにゅう, レベル, しょうぐん
        # and four resident names), not the sortie list (g1163 2026-11-11
        # 14:12/14:28: its words were recorded as ハヤク's garrison and ハヤク
        # itself was picked as a substitute general).
        return None
    names = [w for x, y, w in _options(screen) if x > 100 and w not in GENERAL_LIST_UI
             and not re.search(r'\d', w) and 'おりません' not in w]
    if not names or any(UNKNOWN in w for w in names):
        return None               # a partial reading is not evidence of who is absent
    return names


def _name_read_cleanly(line, name) -> bool:
    """The name is one whole word with no unread tile touching it.

    The hand cursor and the row's icon are unread tiles on the same row but
    one cell away from the name; counting them held the boss sortie for over
    80 minutes (g421 11:48: どうし read cleanly, hand on its row).
    """
    span = next((x for x, word in line.spans() if word == name), None)
    if span is None:
        return False
    cells = dict(line.cells)
    return (cells.get(span - 8) != UNKNOWN
            and cells.get(span + 8 * len(name)) != UNKNOWN)


# Chart label -> the name ステータス shows. Chapter 1's home castle is
# アルマムーン in the game and in this chart; it used to be labelled ほんじょう
# here, which sent every home sortie through a name check and switched the
# chapter detector (g436 21:19: every home sortie refused as "the wrong
# castle", 1-A1/1-V1 failed; g436 21:33: a defense of アルマムーン advanced a
# chapter 1 game to chapter 2). The labels now equal the on-screen names, so
# this map is empty; it stays for a genuine future alias.
STATUS_NAMES: dict[str, str] = {}
CASTLE_STATUS = re.compile(r'(?:しゅつげき)?([^\ufffd\s]+?)じょうステータスしゅうにゅう')


def _egg_row(screen):
    """(general, egg, uses) from card_select/sortie_confirm (measured rows:
    general at x=16 before しょうぐん, egg row「たまご エラベルエッグ 4」).
    たまごなし and unreadable counts return None."""
    general = egg = uses = None
    for line in screen.lines:
        cells = dict(line.spans())
        if cells.get(80) == 'しょうぐん' and cells.get(16):
            general = cells[16]
        if (cells.get(16) == 'たまご' and cells.get(48)
                and re.fullmatch(r'[0-9]', cells.get(112) or '')):
            egg, uses = cells[48], observed_uses(int(cells[112]))
    if not general or UNKNOWN in general or egg is None or UNKNOWN in egg or uses is None:
        return None
    return general, egg, uses


RECRUIT_INTRO = 'ども!しょうぐんえんごかいのものです。しょうぐんのぼしゅうでございますね?'
RECRUIT_GOODBYE = 'それではまたのきかいに。ごようのさいはいつでもおまかせを。'


def _month_dialog_body(screen):
    return ''.join(line.known.replace(' ', '') for line in screen.lines if line.y >= 175)


# The month menu's own help line for the row under the hand (measured live,
# g684 2-6 14:24:52: back on しょうにん after a paid recovery). It is not a
# pending dialogue, so it must not void a payment receipt.
MONTH_MENU_HELP = {'しょうにん': 'きりふだをうるしょうにんをよびつけますぞ!'}


def _month_menu_quiet(screen):
    """No dialogue over the menu: empty, or only the selected row's help line."""
    body = _month_dialog_body(screen)
    return not body or body == MONTH_MENU_HELP.get(screen.selected)


def month_menu_ready(screen):
    """The recruitment overlay can retain the background menu's upper hand."""
    return (screen.kind == 'month_menu' and bool(screen.hand and screen.hand[1] < 120)
            and not month_confirmation_positions(screen)
            and not screen.has('じゅうじキー')
            and _month_dialog_body(screen) not in (RECRUIT_INTRO, RECRUIT_GOODBYE))


# The general-trade event (花いちもんめ) must always be declined (owner rule
# 2026-09-28). 'トレード' is the measured question word, but OCR can drop that
# noun while still reading the surrounding "将軍同士" / "イキのいいの" wording.
# Treat those trade-specific fragments as sufficient evidence too, so a partial
# read fails closed instead of falling through to the generic accept rule.
TRADE_DECLINE_TOKENS = (
    'トレード', 'はないちもんめ', 'いちもんめ',
    'しょうぐんどうし', '将軍同士', 'イキのいいの', 'いきのいいの',
)


def _is_general_trade_prompt(text: str) -> bool:
    flat = ''.join(text.split())
    return any(tok in flat for tok in TRADE_DECLINE_TOKENS)
TRADE_NAMED = re.compile(r'そちらの([^\ufffd\s]+?)しょうぐん')


def _trade_named_general(text: str):
    match = TRADE_NAMED.search(''.join((text or '').split()))
    return match[1] if match else None


def _is_goninja_prompt(text: str) -> bool:
    # Measured monthly assassination offer; a generic 50G prompt is not enough.
    # The public native frame (gcgx img/ninja.png) says うちとってみせるが.
    # Keep the old synthetic wording readable without letting its mistaken
    # fixture send the real offer through unclassified_prompt's default yes.
    flat = ''.join(text.split())
    return any(word in flat for word in ('うちとってみせる', 'うちとおす'))


def _is_bijin_request(text: str) -> bool:
    # 月イチイベント「美女」の依頼文 (倒転王国 monthlyevent.html):
    # 「〜様……今日はお願いがあって来たの……聞いてくれる?」のあとに
    # うんデヘヘ！(購入へ) / けえんな！(断り) の2択。実機の文字はかな
    # 書き(計測済み: どれか ほしいなー / こうすい / くるま)なので、かな・
    # 漢字どちらの読みにも一致させる。商品を持ちかける前に断る判定へ渡す。
    flat = ''.join((text or '').split())
    return (('おねがい' in flat or 'お願い' in flat)
            and ('きいて' in flat or '聞いて' in flat))


# 月イチイベント「8月バカンス」の選択 (オーナー決定 2026-10-03):
# 「兵士はひとり1Gなので、兵士の数とお金の数の少ない方で選ぶ」。
# バカンス＝兵士半減、ボーナス＝お金半減、まとめて解雇＝何も無いか兵士全滅
# (odoru7094 のイベントデータ / gcgx event.html) なので常に非選択。
# 読めない時 (オーナー 2026-10-03) は保留せず、どちらかを1回だけ決めて選ぶ。
# 観測ごとに反転して動かないので、Aで確定するまで同じ選択を保つ。
def _summer_bonus_rows(screen):
    return [(x, line.y, key) for line in screen.lines for x, word in line.spans()
            for key, variants in SUMMER_BONUS_CHOICES.items()
            if any(word.startswith(v) for v in variants)]


# The skill table is matched kana-blind: the tile reader can drop a dakuten
# mark (live とけこむそー vs the transcribed とけこむぞー).
_KANA_FOLD = str.maketrans(
    'がぎぐげござじずぜぞだぢづでどばびぶべぼ'
    'ガギグゲゴザジズゼゾダヂヅデドバビブベボヴ'
    'ぱぴぷぺぽパピプペポ',
    'かきくけこさしすせそたちつてとはひふへほ'
    'カキクケコサシスセソタチツテトハヒフヘホウ'
    'はひふへほハヒフヘホ')


def _fold_skill(text: str) -> str:
    return text.replace(' ', '').replace('！', '!').replace('？', '?').translate(_KANA_FOLD)


# The same tile reader drops a voicing mark on general names too
# (g1075 2026-10-31: ポセイドン→ポセイトン, キャンディー→キャンティー,
# アマンディーヌ→アマンティーヌ stayed in the walked list and held the 4-B1
# sortie ~10 min until input_stalled). Folded roster names are unique, so an
# unknown reading maps back to exactly one general; anything else passes
# through unchanged.
_GENERAL_BY_FOLD = {name.translate(_KANA_FOLD): name for name in GENERAL_HP}


def _canonical_general(word: str) -> str:
    return _GENERAL_BY_FOLD.get(word.translate(_KANA_FOLD), word)
