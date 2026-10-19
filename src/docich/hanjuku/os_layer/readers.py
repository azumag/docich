"""OS層: screen understanding helpers over a parsed ``Screen`` or ``Frame``.

Pure readers of the visible game: menu options and the hand row, world-map
flags and cursor, sortie inventories, monthly dialogue bodies, prompts and
fixed UI vocabularies. Nothing here reads or writes policy memory.
"""
from __future__ import annotations

import re

from .screen import SUMMER_BONUS_CHOICES, Screen, castle_roofs, month_confirmation_positions
# The policy's map readers. Flows import them from here (not from .screen) so a
# rebinding through the hanjuku_policy facade reaches every policy caller while
# screen.parse keeps its own binding, as in the single-module policy.
from .screen import own_camps  # noqa: F401
from .font import TextLine, UNKNOWN
from ... import hanjuku_chart as chart, hanjuku_reference as reference
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
ALL_CARD_NAMES = frozenset(reference.ALL_CARD_IDS)


# ---------------------------------------------------------------- menus
def _options(screen: Screen) -> list[tuple[int, int, str]]:
    return [(x, line.y, word) for line in screen.lines for x, word in line.spans()]


def _current(screen: Screen):
    if not screen.hand:
        return None
    x0, y0, x1, y1 = screen.hand
    row = y0 + 6
    cands = [(x, y, w) for x, y, w in _options(screen) if abs(y - row) <= 2 and x >= x1 - 4]
    # A blank cell (e.g. right of ょ in the kana grid) still has a position.
    return min(cands, default=None) or (((x1 - 4 + 7) // 8) * 8, row, '')


def _month_dialog_unread(screen: Screen) -> bool:
    """An unread tile in the dialogue window, other than a voicing mark.

    Dakuten/handakuten sit on the tile row 8 px above their kana and read as
    unknown tiles there; anything else unknown may be a choice.
    """
    rows = {line.y: line for line in screen.lines if line.y >= 167}
    for y, line in rows.items():
        unknown = [x for x, ch in line.cells if ch == UNKNOWN]
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


def _general_visible(screen, general):
    """Exact name with no adjacent unknown glyph; ignore hand/border pixels."""
    for line in screen.lines:
        for x, word in line.spans():
            if x <= 100 or word != general:
                continue
            neighbors = (x - 8, x + 8 * len(word))
            if not any(ch == UNKNOWN and cx in neighbors
                       and not (screen.hand and screen.hand[0] <= cx <= screen.hand[2])
                       for cx, ch in line.cells):
                return True
    return False


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
    """
    offset = WORLD_MAP_OFFSET.get(chapter)
    if not offset or frame is None:
        return {}
    out = {}
    for name, (wx, wy) in chart.castles(chapter).items():
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


def world_markers(frame) -> list:
    """▲ army markers on the whole-island view, as (x0, y0, x1, y1) boxes.

    Owner rule (2026-09-28): the Y view also shows ▲ marks for units, and
    they must be checked on every survey. Measured in the isolated probe:
    an own marker is a ~7x6 bright red-orange triangle (255,24,0); castle
    flags (230,56,90)/(0,64,189) sit far outside that colour's tolerance.
    """
    if frame is None:
        return []
    pts = [(x, y) for y in range(frame.height) for x in range(frame.width)
           if _near_colour(frame.pixel(x, y), WORLD_MARKER)]
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
                               # around a flag in g419 (ring and flag overlap: +-1 px jitter)
_Y_RING = [(dx, dy) for dx in range(-13, 14) for dy in range(-13, 14)
           if 100 <= dx * dx + dy * dy <= 169]          # radius 5-6.5 px, half-pixel units


def world_cursor(frame):
    """Centre of the Y view's cursor: gold G corners, else the white dashed ring."""
    gold = [(x, y) for x in range(40, 220) for y in range(40, 200)
            if _near_colour(frame.pixel(x, y), (255, 182, 0))]
    # Only the compact G corners (~15 px box): the map's gold edge arrows are
    # far apart and their joint box read as a cursor (g419 10:49), and chapter
    # 2's gold camp triangle beside フーリック widened the box so no target
    # jump there could read the G (isolated probe, g421 state). Take the
    # G-sized window holding the most gold instead of the whole box.
    best = None
    for x0 in sorted({x for x, _ in gold}):
        for y0 in sorted({y for _, y in gold}):
            inside = [(x, y) for x, y in gold if x0 <= x <= x0 + 18 and y0 <= y <= y0 + 18]
            if best is None or len(inside) > len(best):
                best = inside
    if best and len(best) >= 12:
        xs = [x for x, _ in best]; ys = [y for _, y in best]
        if max(xs) - min(xs) >= 10 and max(ys) - min(ys) >= 10:
            return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    votes = {}
    for x in range(40, 220):
        for y in range(40, 200):
            if _near_colour(frame.pixel(x, y), (255, 255, 255)):
                for dx, dy in _Y_RING:
                    key = (2 * x + dx, 2 * y + dy)
                    votes[key] = votes.get(key, 0) + 1
    # The ring only appears inside the island view (sea 64-191 x 47-174);
    # the ornate frame's highlights just outside voted as strongly (g419 10:49,
    # a frame where the blinking ring was off).
    votes = {k: v for k, v in votes.items() if 140 <= k[0] <= 372 and 106 <= k[1] <= 338}
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


def _empty_sortie_inventory(screen):
    """Measured g328 empty inventory, using exact text cells in its panels.

    Only these text baselines establish the receipt. Left-side stats, mark
    rows above glyphs and the hand left of the choices are outside them.
    No quantity or nonempty-inventory format is inferred here.
    """
    if screen.kind != 'sortie_confirm':
        return False
    rows = ((47, 136, ((136, 'きりふだ'),)),
            (63, 136, ((176, 'きりふだは'),)),
            (79, 136, ((176, 'ありません……'),)),
            (95, 136, ((136, 'ーしゅつげき'), (192, 'しますか?ー'))),
            (111, 160, ((160, 'うむッ!'),)),
            (127, 160, ((160, 'いかんッ!'),)))
    for y, left, spans in rows:
        expected = tuple((x + 8 * index, ch) for x, text in spans for index, ch in enumerate(text))
        observed = tuple((x, ch) for line in screen.lines if line.y == y
                         for x, ch in line.cells if left <= x < 240)
        if observed != expected:
            return False
    return not any(card in word for _, _, word in _options(screen) for card in ALL_CARD_NAMES)


def _sortie_inventory(screen):
    """Read complete card names from the fixed sortie panel slots.

    The one-card placement is live-measured. Additional names are classified
    only when each occupies a complete known slot and the whole panel matches;
    this recognizes text, not quantities or partial names. The receipt uses
    the canonical game catalogue, independently of the planning allowlist.
    """
    if screen.kind != 'sortie_confirm' or screen.hand not in ((138, 105, 156, 118), (138, 105, 158, 118)):
        return None
    def cells(y, left=136):
        return tuple((x, ch) for line in screen.lines if line.y == y
                     for x, ch in line.cells if left <= x < 256)
    def text_cells(x, text):
        return tuple((x + 8*i, ch) for i, ch in enumerate(text))
    first = cells(47)
    heading = text_cells(136, 'きりふだ')
    first_spans = TextLine(47, tuple((x, ch) for x, ch in first if x >= 176)).spans()
    if len(first_spans) != 1 or first_spans[0][0] != 176 or first_spans[0][1] not in ALL_CARD_NAMES:
        return None
    cards = [first_spans[0][1]]
    if first != heading + text_cells(176, cards[0]):
        return None
    expected = {47: first, 63: (), 79: (),
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
        if row != text_cells(176, spans[0][1]):
            return None
        cards.append(spans[0][1])
        expected[y] = row
    for y, wanted in expected.items():
        if cells(y, 160 if y in (111, 127) else 136) != wanted:
            return None
    # Background and dakuten/cursor UNKNOWNs are not body text. Additional
    # known text anywhere else inside this right panel is uncalibrated.
    if any(ch != UNKNOWN and 136 <= x < 256
           and (line.y not in expected or (line.y in (111, 127) and x < 160))
           for line in screen.lines if 32 <= line.y < 135 for x, ch in line.cells):
        return None
    if [word for _, _, word in _options(screen) if word in ALL_CARD_NAMES] != cards:
        return None
    return cards


def _measured_card_select(screen):
    """Read contiguous rows in the measured g328 panel, never infer stock.

    Stock is right-aligned at x=232; live tens for 10-99 sit at x=224.
    Depleted final items disappear. Empty baselines must form a trailing
    suffix; UNKNOWN cells are unreadable rows, not evidence of emptiness.
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
    if cells(127, 136) != text_cells(136, 'バトルようのきりふだです'):
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
    if (x0 != 138 or x1 not in (156, 158) or y1 != selected_y + 7
            or selected_y not in {row['y'] for row in rows}):
        return None
    return {'rows': rows, 'selected_y': selected_y, 'remaining': int(remaining),
            'inventory_evidence': ('measured_four_row_card_select' if len(rows) == 4
                                   else 'structured_card_select'), 'observed_rows': len(rows)}


GENERAL_LIST_UI = frozenset({'しゅつげき', 'ステータス', 'しょうぐんは', 'おりません', 'おりません……'})


def _present_generals(screen):
    """Names on a general list, [] for the empty-list message, None if unreadable."""
    if 'おりません' in screen.text:
        return []
    if not screen.hand:
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
