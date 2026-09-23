"""Chart-driven deterministic Hanjuku policy (no model, provider or network).

``step`` maps one parsed frame and the persisted policy memory to bounded pad
actions plus structured decision records. Every non-trivial choice records the
situation, the chart step, the strategy variant and, when the chart is not
followed exactly, the deviation reason with the expected and observed metrics.
Unknown screens and unreadable values are recorded as ``unclassified``; the
policy never invents a battle result, stage or amount.
"""
from __future__ import annotations

import re

from . import hanjuku_chart as chart
from . import hanjuku_chart_adjust as chart_adjust
from .hanjuku_font import UNKNOWN, TextLine
from .hanjuku_screen import HEADER as HEADER_RE, Screen, castle_roofs

NAME = chart.HERO
FPS = 60
MAX_HOLD_FRAMES = 28          # 1 px/frame below 30 frames; beyond it accelerates
EDGE_X = (8, 232)             # cursor cell clamps; the camera scrolls beyond
EDGE_Y = (8, 200)
ARRIVE_PX = 4
ANCHOR_PX = 24                # roof-to-castle association radius (castles >=100 px apart)
BATTLE_MENU = ('たまごをつかう', 'きりふだ', 'たいきゃく')
AFTER_BATTLE_KINDS = frozenset({'map', 'map_target', 'castle_menu', 'general_list', 'month_menu',
                                'attack_started', 'defense_started', 'castle_info', 'main_menu',
                                'yes_no', 'shop_list', 'boss_attack_started'})
CARD_NAMES = frozenset({
    'イッテツーン', 'ダイチスイム', 'ブラッキー', 'フットバース', 'グリンボー', 'ピッグローラー',
    'カンケリン', 'ノリウツール', 'クースカン', 'ゼンマイン', 'ミックミー', 'デッドガン',
    'ブレイコウ', 'ブンシーン', 'ファイアーボイス', 'ファバード', 'エンジェリン', 'マグネガキン',
    'ハリケーン'})


def pad(button, frames=6):
    return {'type': 'pad', 'buttons': [button], 'hold_ms': max(16, round(frames * 1000 / FPS))}


def _record(mem, kind, **fields):
    rec = {'decision': kind, 'chart_step': fields.pop('chart_step', mem.get('active')),
           'strategy_variant': fields.pop('strategy_variant', mem.get('variant', 'chart')),
           'chapter': mem.get('chapter'), 'deviation_reason': None,
           'expected_metric': None, 'observed_metric': None,
           'resulting_stage': None, 'resulting_event': None}
    rec.update(fields)
    mem.setdefault('_records', []).append(rec)
    return rec


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


def name_step(screen: Screen, mem):
    box = next((line for line in screen.lines if 48 <= line.y <= 62), None)
    typed = ''.join(box.span(64, 160).split()) if box else ''
    name = mem.setdefault('name', {'target': NAME, 'done': False, 'inputs': 0})
    name['typed'] = typed
    if UNKNOWN in typed:
        _record(mem, 'name_wait', chart_step='name', typed=typed,
                reason='状況判定保留: 名前欄に未分類の文字があるため入力を保留')
        return []
    if typed == NAME:
        _record(mem, 'name_confirm', chart_step='name', typed=typed,
                reason='名前欄の実表示がどうしに一致したためSTARTで確定')
        name['done'] = True
        return [pad('start')]
    if not NAME.startswith(typed):
        _record(mem, 'name_delete', chart_step='name', typed=typed, reason='名前欄が目標の前方一致でないため1文字削除')
        return [pad('b')]
    want = NAME[len(typed)]
    if screen.hand and screen.hand[0] < 40:
        # Hand on the page menu (ひらがな/カタカナ/...; x0 about 17), not the
        # grid's first column (x0 about 58): enter the kana grid.
        return [pad('a')]
    cur = _current(screen)
    cells = [(x, y, w) for x, y, w in _options(screen) if w == want and x >= 80 and y >= 80]
    if not cells and kana_cell(want) and 'ひらがな' in screen.text:
        cells = [(*kana_cell(want), want)]
    if not cur or not cells:
        _record(mem, 'name_wait', chart_step='name', typed=typed, reason='カーソルまたは目標文字を判定保留')
        return []
    if cur[2] == want:
        _record(mem, 'name_type', chart_step='name', typed=typed, char=want,
                reason=f'カーソルが「{want}」上にあるためAで入力')
        return [pad('a')]
    tx, ty, _ = cells[0]
    if abs(ty - cur[1]) > 2:
        return [pad('down' if ty > cur[1] else 'up')]
    return [pad('right' if tx > cur[0] else 'left')]


# ---------------------------------------------------------------- map cursor
def _cursor(screen: Screen):
    return screen.marker or screen.cursor


def _at_edge(value, bounds):
    return value <= bounds[0] + 1 or value >= bounds[1] - 1


def _localize(roofs, castles, predicted_cam):
    """Camera offset from visible roofs by constellation voting.

    Each (roof, castle) pairing implies a camera offset; the offset that
    places the most roofs on known castles wins, ties broken by closeness to
    the prediction. Returns (camera, castle name of the anchor roof) or None.
    """
    best = None
    for roof in roofs:
        for name, (cx, cy) in castles.items():
            cam = (cx - roof['target'][0], cy - roof['target'][1])
            support = 0
            for other in roofs:
                wx, wy = other['target'][0] + cam[0], other['target'][1] + cam[1]
                if any(abs(wx - x) + abs(wy - y) <= 12 for x, y in castles.values()):
                    support += 1
            drift = (abs(cam[0] - predicted_cam[0]) + abs(cam[1] - predicted_cam[1])
                     if predicted_cam else 0)
            key = (support, -drift)
            if best is None or key > best[0]:
                best = (key, cam, name)
    if best is None:
        return None
    (support, neg_drift), cam, name = best
    # A single roof far from the prediction is ambiguous: do not trust it.
    if support < 2 and predicted_cam and -neg_drift > 80:
        return None
    return cam, name


def update_world(screen: Screen, mem, frame):
    """Track the cursor's map cell from measured screen motion and roof anchors.

    Leaving the map (battles, events, month menus) can move the cursor, so the
    estimate becomes uncertain until roofs re-anchor it; ``nav_step`` never
    confirms a cell while uncertain.
    """
    s = _cursor(screen)
    if not s:
        return None
    castles = chart.castles(mem.get('chapter') or 1)
    world = mem.get('cursor')
    last = mem.get('nav_last')
    if world and last and last.get('screen') and not mem.get('uncertain'):
        moved = []
        for axis, bounds in ((0, EDGE_X), (1, EDGE_Y)):
            if _at_edge(s[axis], bounds) or _at_edge(last['screen'][axis], bounds):
                moved.append(last['expected'][axis])      # camera scrolled: unmeasured
            else:
                moved.append(s[axis] - last['screen'][axis])
        world = [world[0] + moved[0], world[1] + moved[1]]
    mem['nav_last'] = None
    box = (s[0] - 2, s[1] - 2, s[0] + 18, s[1] + 18)
    roofs = [r for r in castle_roofs(frame, exclude=box) if not r['clipped']]
    predicted = (world[0] - s[0], world[1] - s[1]) if world else None
    found = _localize(roofs, castles, predicted)
    anchored = None
    if found:
        cam, anchored = found
        new = [cam[0] + s[0], cam[1] + s[1]]
        if mem.get('uncertain') or not world or abs(new[0] - world[0]) + abs(new[1] - world[1]) <= 48:
            world = new
            mem['uncertain'] = False
        else:
            anchored = None
    mem['cursor'] = world
    mem['anchor'] = anchored
    return world


def nav_step(screen: Screen, mem, frame, goal):
    """Holds toward ``goal`` (map cell); 'arrived' within ARRIVE_PX."""
    world = update_world(screen, mem, frame)
    s = _cursor(screen)
    if not world or not s:
        return None
    dx, dy = goal[0] - world[0], goal[1] - world[1]
    if abs(dx) <= ARRIVE_PX and abs(dy) <= ARRIVE_PX:
        if mem.get('uncertain'):
            # Never confirm an unverified cell: nudge to reveal more roofs.
            mem['nav_last'] = {'screen': list(s), 'expected': [0, 0]}
            return [pad('up', 12)] if s[1] > 100 else [pad('down', 12)]
        return 'arrived'
    actions, expected = [], [0, 0]
    for axis, d, neg, pos in ((0, dx, 'left', 'right'), (1, dy, 'up', 'down')):
        if abs(d) > ARRIVE_PX:
            frames = min(abs(d), MAX_HOLD_FRAMES)
            actions.append(pad(pos if d > 0 else neg, frames))
            expected[axis] = frames if d > 0 else -frames
    mem['nav_last'] = {'screen': list(s), 'expected': expected}
    return actions


# ---------------------------------------------------------------- orders
def _ready(order, mem) -> bool:
    after = order['after']
    captured = set(mem.get('captured', []))
    if after is None:
        return True
    if after[0] == 'captured':
        return after[1] in captured
    if after[0] == 'all_captured':
        chapter_castles = set(chart.castles(mem.get('chapter') or 1)) - {'ほんじょう', 'けっかい'}
        return chapter_castles <= captured
    return False


def _orders(mem):
    """The order list in force: an adopted adjusted chart, else the base chart.

    While an adjusted chart is pending, one JEV-chosen interim order may run.
    """
    state = mem.get('chart_adjust') or {}
    if state.get('orders'):
        return state['orders']
    interim = state.get('interim_order')
    return (*chart.orders(mem.get('chapter') or 0), *((interim,) if interim else ()))


def next_order(mem):
    status = mem.setdefault('orders', {})
    for order in _orders(mem):
        if status.get(order['step']) in (None, 'pending') and _ready(order, mem):
            return order
    return None


def _order(mem):
    step = mem.get('active')
    return next((o for o in (*_orders(mem), *chart.orders(mem.get('chapter') or 0))
                 if o['step'] == step), None)


INTERIM_LIMIT = 2             # JEV answers per off-chart situation
INTERIM_MIN_CONFIDENCE = 0.7
BOSS_CASTLE = 'けっかい'


def interim_candidates(mem) -> dict:
    """Deterministic interim orders JEV may choose from while a chart is pending.

    Only re-attacks of uncaptured non-boss castles by a general the base chart
    already sends there, without cards (stock is not verified). ``hold`` is
    always available. JEV picks a label; it never produces keys or orders.
    """
    chapter = mem.get('chapter') or 0
    castles = chart.castles(chapter)
    owned = set(mem.get('captured') or []) | {'ほんじょう'}
    out, seen = {}, set()
    for order in chart.orders(chapter):
        target = order['target']
        if target in owned or target == BOSS_CASTLE or target not in castles:
            continue
        if (order['general'], target) in seen:
            continue
        seen.add((order['general'], target))
        out[f'attack_{len(out) + 1}'] = {
            'general': order['general'], 'target': target, 'cards': [],
            'source': order['source'] if order['source'] in owned else 'ほんじょう',
            'after': None, 'note': f"暫定: {order['general']}で{target}を再攻撃"}
    return out


def _adopt_interim(mem, state, rid):
    answer = mem.get('_interim')
    if (not isinstance(answer, dict) or answer.get('request_id') != rid
            or answer.get('seq') != state.get('interim_count', 0)):
        return
    state['interim_count'] = state.get('interim_count', 0) + 1
    state['interim_wanted'] = False
    candidates = interim_candidates(mem)
    choice, confidence = answer.get('choice'), answer.get('confidence')
    order = candidates.get(choice)
    confident = type(confidence) in (int, float) and confidence >= INTERIM_MIN_CONFIDENCE
    if answer.get('status') != 'ok' or order is None or not confident:
        _record(mem, 'chart_interim_hold', chart_step=None, strategy_variant='chart_adjust_pending',
                request_id=rid, choice=choice, confidence=confidence, jev_status=answer.get('status'),
                deviation_reason=None if choice == 'hold' else 'interim_rejected',
                reason='JEV暫定判断が保留・低確信・候補外のため無入力で調整チャートを待つ')
        return
    step = f"{chart_adjust.INTERIM_PREFIX}{rid[:6]}:{state['interim_count']}"
    state['interim_order'] = {**order, 'step': step}
    _record(mem, 'chart_interim_order', chart_step=step, strategy_variant='chart_interim_jev',
            request_id=rid, choice=choice, confidence=confidence, general=order['general'],
            source=order['source'], target=order['target'],
            reason='調整チャート待ちの間、JEVが決定的候補から暫定出撃を選択')


def _off_chart(mem):
    """No order is ready: request an adjusted chart, or adopt one that answered.

    The base chart is never mutated. A request is recorded once per distinct
    situation (chapter, captures, order states); an adjusted chart is adopted
    only when it answers exactly that request, as a complete order list.
    Until then a bounded number of JEV-chosen interim orders may run.
    """
    state = mem.setdefault('chart_adjust', {})
    rid = chart_adjust.request_id(mem)
    if state.get('request_id') != rid:
        orders = [o for o in _orders(mem) if not o['step'].startswith(chart_adjust.INTERIM_PREFIX)]
        status = mem.get('orders') or {}
        blocked = [{'step': o['step'], 'after': list(o['after'] or ())} for o in orders
                   if status.get(o['step']) in (None, 'pending')]
        reason = ('chart_unavailable' if not orders
                  else 'orders_locked' if blocked else 'orders_exhausted')
        state.clear()
        state['request_id'] = rid
        state['interim_wanted'] = bool(interim_candidates(mem))
        _record(mem, 'chart_adjust_request', chart_step=None, strategy_variant='chart_adjust_pending',
                request_id=rid, off_chart_reason=reason, blocked=blocked,
                captured=sorted(mem.get('captured') or []), orders=dict(status),
                gold=mem.get('gold'), month=mem.get('month'),
                reason='チャート外: 出撃可能な指示がないため調整チャートを非同期に要求し入力を保留')
        return
    doc = mem.get('_adjusted')
    if (doc and doc.get('request_id') == rid and doc.get('chapter') == mem.get('chapter')
            and state.get('applied') != rid):
        state['applied'] = rid
        state['interim_wanted'] = False
        state.pop('interim_order', None)
        state['orders'] = [{**o, 'cards': list(o['cards']),
                            'after': list(o['after']) if o['after'] else None}
                           for o in doc['orders']]
        state['purchases'] = ({**doc['purchases'], 'month': list(doc['purchases']['month']),
                               'cards': [list(c) for c in doc['purchases']['cards']]}
                              if doc.get('purchases') else None)
        mem['variant'] = 'chart_adjusted'
        _record(mem, 'chart_adjust_applied', chart_step=None, strategy_variant='chart_adjusted',
                request_id=rid, source=doc.get('source'),
                steps=[o['step'] for o in doc['orders']],
                reason='調整チャートを受信したため独自の指示列で出撃を再開')
        return
    if state.get('applied') == rid:
        return
    _adopt_interim(mem, state, rid)
    interim = state.get('interim_order')
    busy = interim and (mem.get('orders') or {}).get(interim['step']) in (None, 'pending')
    state['interim_wanted'] = (not busy and state.get('interim_count', 0) < INTERIM_LIMIT
                               and bool(interim_candidates(mem)))


def _finish_order(mem, state, **fields):
    step = mem.get('active')
    if step:
        mem.setdefault('orders', {})[step] = state
        _record(mem, 'order_' + state, chart_step=step, **fields)
    mem['active'] = None
    mem['picked'] = []


def map_step(screen: Screen, mem, frame):
    if mem.pop('expect_menu', False):
        mem['uncertain'] = True
        _record(mem, 'localize', reason='城で決定したがメニューが出ないため位置を再測定',
                observed_metric=mem.get('cursor'))
    order = _order(mem)
    if order is None:
        order = next_order(mem)
        if order is None:
            _off_chart(mem)
            order = next_order(mem)
        if order is None:
            update_world(screen, mem, frame)
            return []           # nothing charted: let real time advance
        mem['active'] = order['step']
        mem['picked'] = []
        _record(mem, 'order_start', chart_step=order['step'], **_deploy_context(order, mem),
                source=order['source'], target=order['target'], cards=list(order['cards']),
                reason=order['note'])
    source = mem.get('source_override', {}).get(order['step'], order['source'])
    goal = chart.castles(mem['chapter'])[source]
    result = nav_step(screen, mem, frame, goal)
    if result == 'arrived':
        # If no castle menu follows, the cell was wrong: re-localize.
        mem['expect_menu'] = True
        return _deploy_input(screen, mem, order, [pad('a')], '出撃元の城を選択')
    return _deploy_input(screen, mem, order, result or [], '出撃元の城へカーソルを移動')


def target_step(screen: Screen, mem, frame):
    order = _order(mem)
    if order is None:
        # A marker we did not request: cancel instead of sending a general.
        _record(mem, 'unexpected_target', reason='指示中でない出撃先選択画面のためBで取消')
        return [pad('b')]
    if order['step'] == '1-B1':
        context = (mem.get('order_context') or {}).get(order['step']) or {}
        if (context.get('actual_general') != order['general']
                or (context.get('observed_metric') or {}).get('cards') != sorted(order['cards'])):
            return _hold_deploy(screen, mem, order, 'ボス出撃の主人公と携行品の確認証拠がないため目標確定を保留')
    goal = chart.castles(mem['chapter'])[order['target']]
    result = nav_step(screen, mem, frame, goal)
    if result == 'arrived':
        context = _deploy_context(order, mem, expected_metric='のりこんだ表示で目標城を確認')
        _finish_order(mem, 'launched', **context, target=order['target'],
                      cursor=mem.get('cursor'), anchor=mem.get('anchor'),
                      reason=f"{context['general']}を{order['target']}へ出撃")
        general = context['general']
        mem.setdefault('launched', {})[order['target']] = {'general': general, 'step': order['step']}
        return [pad('a')]
    return _deploy_input(screen, mem, order, result or [], '出撃先へ目標カーソルを移動')


def _deploy_cards(order, mem):
    return list(order['cards'] if order['step'] == '1-B1'
                else mem.get('card_override', {}).get(order['step'], order['cards']))


def _deploy_context(order, mem, *, expected_metric=None):
    general = (order['general'] if order['step'] == '1-B1'
               else mem.get('general_override', {}).get(order['step'], order['general']))
    context = {'general': general, 'planned_general': order['general'],
            'expected_metric': expected_metric,
            'strategy_variant': 'substitute_general' if general != order['general'] else mem.get('variant', 'chart'),
            'deviation_reason': (f"計画の{order['general']}に代わり{general}を出撃させる"
                                 if general != order['general'] else None)}
    retry = (mem.get('retry_context') or {}).get(order['step'])
    if retry:
        context.update(strategy_variant=retry.get('strategy_variant', 'retry_with_opening_cards'),
                       deviation_reason=retry.get('deviation_reason'),
                       expected_metric=retry.get('expected_metric'))
    return context


def _deploy_input(screen, mem, order, actions, reason):
    # Retry navigation used to have no decision record, losing its context in
    # action_plan. Ordinary first-attempt records retain their existing shape.
    if (mem.get('retry_context') or {}).get(order['step']):
        _record(mem, 'sortie_input', **_deploy_context(order, mem), screen=screen.kind,
                observed_metric={'planned_buttons': [a.get('buttons') for a in actions]},
                reason=reason)
    return actions


def _hold_deploy(screen, mem, order, reason, *, card=None, carried=None):
    context = _deploy_context(order, mem, expected_metric={'cards': _deploy_cards(order, mem)})
    if not (mem.get('retry_context') or {}).get(order['step']):
        context.update(strategy_variant='sortie_cards_unclassified' if screen.kind == 'sortie_confirm'
                       else 'sortie_menu_unclassified', deviation_reason=reason)
    _record(mem, 'situation_held', **context, screen=screen.kind, card=card,
            observed_metric={'hand': screen.hand,
                             'candidates': [{'x': x, 'y': y, 'text': w[:80]} for x, y, w in _options(screen)[:32]],
                             'carried_cards_read': carried, 'confirmation': 'unclassified'},
            reason=reason)
    return []


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
    return not any(card in word for _, _, word in _options(screen) for card in CARD_NAMES)


def _sortie_inventory(screen):
    """Read complete card names from the fixed sortie panel slots.

    The one-card placement is live-measured. Additional names are classified
    only when each occupies a complete known slot and the whole panel matches;
    this recognizes text, not quantities or partial names.
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
    if len(first_spans) != 1 or first_spans[0][0] != 176 or first_spans[0][1] not in CARD_NAMES:
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
        if len(spans) != 1 or spans[0][0] != 176 or spans[0][1] not in CARD_NAMES:
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
    if [word for _, _, word in _options(screen) if word in CARD_NAMES] != cards:
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
        if len(names) != 1 or names[0][0] != 160 or names[0][1] not in CARD_NAMES:
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


def deploy_step(screen: Screen, mem):
    order = _order(mem)
    kind = screen.kind
    if order is None:
        # Menus we did not open (e.g. confirm pressed by an earlier fallback).
        return [pad('b')]
    if kind == 'castle_menu':
        move = menu_to(screen, 'しゅつげき')
        return _deploy_input(screen, mem, order, [pad('a')] if move == 'here' else [move] if move else [],
                             '出撃メニューを選択')
    if kind == 'general_list':
        if order['step'] == '1-B1':
            mem.setdefault('sortie_general', {}).pop(order['step'], None)
            mem.setdefault('order_context', {}).pop(order['step'], None)
            move = menu_to(screen, order['general'])
            if move is None or any(UNKNOWN in line.text and order['general'] in line.text for line in screen.lines):
                return _hold_deploy(screen, mem, order, 'ボス出撃の主人公を一覧とカーソルで確認できないため代役を選ばず保留')
            if move == 'here':
                mem.setdefault('general_override', {}).pop(order['step'], None)
                mem['sortie_general'][order['step']] = order['general']
                return _deploy_input(screen, mem, order, [pad('a')], 'ボス戦へ主人公を選択')
            return _deploy_input(screen, mem, order, [move], 'ボス戦の主人公へ選択カーソルを移動')
        if not screen.hand:
            return _hold_deploy(screen, mem, order, '将軍選択カーソルを判定できないため入力を保留')
        general = mem.get('general_override', {}).get(order['step'], order['general'])
        move = menu_to(screen, general)
        if move is None:
            # The chart's general is not at this castle (routed or lost).
            # Whoever is actually here goes instead, so the castle, later
            # steps and the boss condition stay reachable.
            ui = {'しゅつげき', 'ステータス'}
            present = [w for x, y, w in _options(screen) if x > 100 and w not in ui
                       and not re.search(r'\d', w)]
            present.sort(key=lambda w: w == NAME)      # risk the hero last
            if present and not mem.get('general_override', {}).get(order['step']):
                mem.setdefault('general_override', {})[order['step']] = present[0]
                _record(mem, 'order_substitute', strategy_variant='substitute_general',
                        deviation_reason=f"{order['general']}が出撃元にいないため{present[0]}が代わりに出撃",
                        observed_metric=present[:8], expected_metric=f"{order['target']}の占領",
                        reason='後続手順とボス条件を満たすため')
                move = menu_to(screen, present[0])
                return [pad('a')] if move == 'here' else [move] if move else []
            if not mem.get('source_override', {}).get(order['step']):
                mem.setdefault('source_override', {})[order['step']] = 'ほんじょう'
                mem.setdefault('general_override', {}).pop(order['step'], None)
                mem['orders'][order['step']] = 'pending'
                _record(mem, 'order_source_changed', strategy_variant='source_fallback',
                        deviation_reason=f"{order['source']}に出撃できる将軍がいない",
                        observed_metric=present[:8], reason='本城から出撃し直す')
                mem['active'] = None
                return [pad('b'), pad('b')]
        if move is None:
            _finish_order(mem, 'failed', deviation_reason=f"{order['general']}が出撃一覧に見えない",
                          observed_metric=[w for _, _, w in _options(screen)][:12],
                          reason='チャートの将軍が出撃元の城にいない')
            return [pad('b')]
        return _deploy_input(screen, mem, order, [pad('a')] if move == 'here' else [move], '出撃将軍を選択')
    if kind in {'card_select', 'sortie_confirm'} and order['step'] == '1-B1':
        mem.setdefault('order_context', {}).pop(order['step'], None)
        if (mem.get('sortie_general') or {}).get(order['step']) != order['general']:
            return _hold_deploy(screen, mem, order, 'ボス出撃の主人公選択を確認できないため保留')
    if kind == 'card_select':
        mem.setdefault('order_context', {}).pop(order['step'], None)
        picked = mem.setdefault('picked', [])
        wanted = _deploy_cards(order, mem)
        for card in picked:
            if card in wanted:
                wanted.remove(card)
        card = wanted[0] if wanted else None
        inventory = _measured_card_select(screen)
        if inventory is None:
            return _hold_deploy(screen, mem, order,
                                '切り札一覧の名前・数量・配置またはカーソルが実測構造と一致しないため保留', card=card)
        if not wanted:
            return _deploy_input(screen, mem, order, [pad('b')], '予定切り札の選択入力後に携行確認へ進む')
        row = next((row for row in inventory['rows'] if row['card'] == card), None)
        if row is None or row['stock'] == 0 or inventory['remaining'] == 0:
            return _hold_deploy(screen, mem, order,
                                '予定切り札の正数在庫と携行余枠を確認できないため選択せず保留', card=card)
        if inventory['selected_y'] == row['y']:
            picked.append(card)
            _record(mem, 'card_pick', **_deploy_context(order, mem), card=card,
                    observed_metric={'stock': row['stock'], 'remaining': inventory['remaining'],
                                     'inventory_evidence': inventory['inventory_evidence'],
                                 'observed_rows': inventory['observed_rows']},
                    resulting_event='selection_planned_not_yet_confirmed', reason=order['note'])
            return [pad('a')]
        move = pad('down' if row['y'] > inventory['selected_y'] else 'up')
        selected = next(item for item in inventory['rows'] if item['y'] == inventory['selected_y'])
        _record(mem, 'sortie_input',
                **_deploy_context(order, mem, expected_metric={'card': card, 'goal': '予定切り札へカーソルを合わせる'}),
                screen=screen.kind, card=card,
                observed_metric={'desired_card': card, 'selected_y': selected['y'],
                                 'selected_card': selected['card'], 'target_y': row['y'],
                                 'target_stock': row['stock'], 'remaining': inventory['remaining'],
                                 'inventory_evidence': inventory['inventory_evidence'],
                                 'observed_rows': inventory['observed_rows']},
                reason='実測配置の正数在庫と携行余枠を確認し、予定切り札へカーソルを移動')
        return [move]
    if kind == 'sortie_confirm':
        mem.setdefault('order_context', {}).pop(order['step'], None)
        empty_receipt = _empty_sortie_inventory(screen)
        slot_receipt = _sortie_inventory(screen)
        carried = [] if empty_receipt else slot_receipt
        inventory_evidence = ('measured_empty_sortie' if empty_receipt else
                              'measured_single_card_sortie' if slot_receipt and len(slot_receipt) == 1 else
                              'structured_card_slots')
        ambiguous = carried is None
        want = sorted(_deploy_cards(order, mem))
        got = sorted(carried) if carried is not None else None
        if ambiguous or got != want:
            return _hold_deploy(screen, mem, order,
                                '携行切り札の読取が不確実または計画と不一致のため出撃承認を保留', carried=carried)
        move = menu_to(screen, 'うむッ!')
        if move is None:
            return _hold_deploy(screen, mem, order, '出撃確認カーソルまたは承認項目を読めないため保留', carried=carried)
        if move == 'here':
            context = _deploy_context(order, mem, expected_metric=want)
            mem['order_context'][order['step']] = {
                'strategy_variant': context['strategy_variant'],
                'deviation_reason': context['deviation_reason'],
                'actual_general': context['general'], 'planned_general': order['general'],
                'expected_metric': (context['expected_metric'] if (mem.get('retry_context') or {}).get(order['step'])
                                    else {'general': order['general'], 'cards': want}),
                'observed_metric': {'general': context['general'], 'cards': got}}
            _record(mem, 'sortie_confirm', **context, cards=carried,
                    inventory_evidence=inventory_evidence,
                    observed_metric=got,
                    reason='出撃確認で読み取った切り札が計画と一致したため承認を予定')
            source = mem.get('source_override', {}).get(order['step'], order['source'])
            mem['cursor'] = list(chart.castles(mem['chapter'])[source])
            mem['uncertain'] = False      # the target marker starts on the source castle
            return [pad('a')]
        return _deploy_input(screen, mem, order, [move], '携行品一致を確認したため出撃承認項目へ移動')
    return []


# ---------------------------------------------------------------- battles
def _battle_context(mem, ally):
    """Only a matching attack message establishes a battle location and side."""
    attack = mem.get('attack') or {}
    captured = set(mem.get('captured', []))
    if attack.get('side') == 'defense' or (attack.get('general') and attack.get('general') == ally):
        side = attack.get('side')
        if side == 'attack' and attack.get('castle') in captured:
            side = 'defense'          # a battle at a castle we hold is not a capture attempt
        return {'castle': attack.get('castle'), 'side': side, 'step': attack.get('step'),
                'entry_evidence': attack.get('entry_evidence')}
    captured = set(mem.get('captured', []))
    for castle, info in (mem.get('launched') or {}).items():
        if info.get('general') == ally and castle not in captured:
            return {'castle': None, 'side': None, 'step': info.get('step'),
                    'context': 'unclassified_location'}
    return {'castle': None, 'side': None, 'step': None, 'context': 'unclassified'}


def _battle_labels(cur):
    return {'chart_step': cur.get('step'),
            'strategy_variant': cur.get('strategy_variant', 'chart'),
            'deviation_reason': cur.get('deviation_reason')}


def _bind_battle_strategy(mem, cur):
    if 'strategy_variant' in cur:
        return
    step = cur.get('step')
    retry = (mem.get('retry_context') or {}).get(step)
    sortie = (mem.get('order_context') or {}).get(step) or {}
    if sortie.get('actual_general') != cur.get('ally'):
        sortie = {}
    if retry or (mem.get('card_override') or {}).get(step):
        cur['strategy_variant'] = (retry or {}).get('strategy_variant', 'retry_with_opening_cards')
        cur['deviation_reason'] = (retry or {}).get('deviation_reason') or '保存済みの再攻撃用切り札計画を適用（元の敗北詳細は未分類）'
        cur['strategy_expected'] = (retry or {}).get('expected_metric') or {'goal': '敵HP減少と戦闘勝利'}
    elif sortie:
        cur['strategy_variant'] = sortie.get('strategy_variant', 'chart')
        cur['deviation_reason'] = sortie.get('deviation_reason')
        cur['strategy_expected'] = sortie.get('expected_metric')
    else:
        cur['strategy_variant'] = mem.get('variant', 'chart')
        cur['deviation_reason'] = None
        cur['strategy_expected'] = {'goal': 'チャート戦術による戦闘勝利'}


def _migrate_card_evidence(mem):
    """Legacy cards_used mixed selections/missing cards with actual use."""
    for scope in ('stats', 'previous_stats'):
        stats = mem.get(scope)
        if not isinstance(stats, dict) or stats.get('card_evidence_version') == 1:
            continue
        had_previous = 'cards_used' in stats
        previous = stats.get('cards_used')
        stats.update(cards_used=None, cards_confirmed=0, card_evidence_version=1)
        if had_previous:
            _record(mem, 'metric_invalidated', metric='cards_used', metric_scope=scope,
                    observed_metric={'previous_inferred_count': previous if type(previous) is int else None,
                                     'cards_used': None},
                    reason='旧切り札集計は予定・欠品を含むため未分類化。以後は実使用証拠のみ別途加算')
    cur = mem.get('battle')
    if not isinstance(cur, dict):
        return
    _bind_battle_strategy(mem, cur)
    if cur.get('card_evidence_version') == 1:
        return
    legacy = list(cur.get('cards_used') or [])
    cur.update(cards_used=[], cards_selected=[], cards_missing=[],
               cards_unclassified=legacy, card_consumption_complete=False, card_evidence_version=1)
    flow = cur.get('card_flow')
    if flow and flow.get('stage') == 'announce':
        pending = flow.get('card')
        if pending and pending not in legacy:
            cur['cards_unclassified'].append(pending)
        cur['card_flow'] = None
    _record(mem, 'battle_card_evidence_migrated', **_battle_labels(cur),
            observed_metric={'legacy_cards_unclassified': legacy},
            reason='旧戦闘の選択済みリストは実使用証拠でないためafter_cardを解禁しない')


def _card_use_unclassified(mem, cur, reason):
    flow = cur.get('card_flow') or {}
    card = flow.get('card')
    if card:
        cur.setdefault('cards_unclassified', []).append(card)
    cur['card_consumption_complete'] = False
    if not cur.get('deviation_reason'):
        cur['strategy_variant'] = 'card_use_unclassified'
        cur['deviation_reason'] = reason
    _record(mem, 'battle_card_unclassified', **_battle_labels(cur), card=card,
            expected_metric='選択した切り札の実使用告知',
            observed_metric={'confirmation': 'unclassified'}, reason=reason)
    cur['card_flow'] = None


def battle_step(screen: Screen, mem):
    _migrate_card_evidence(mem)
    b = screen.battle
    cur = mem.get('battle')
    if cur is None:
        # Fades dim the panel and can drop dakuten; open a battle record only
        # after two consecutive identical readings of both names.
        reading = [b.enemy, b.ally]
        if mem.get('battle_seen') != reading:
            mem['battle_seen'] = reading
            return []
        mem['battle_seen'] = None
        context = _battle_context(mem, b.ally)
        cur = mem['battle'] = {'enemy': b.enemy, 'ally': b.ally, 'start_enemy_hp': b.enemy_hp,
                               'start_ally_hp': b.ally_hp, 'cards_used': [], 'plan': None,
                               'cards_selected': [], 'cards_missing': [], 'cards_unclassified': [],
                               'card_consumption_complete': True, 'card_evidence_version': 1,
                               **context}
        _bind_battle_strategy(mem, cur)
        planned = [t['card'] for t in chart.tactics(mem.get('chapter') or 0)
                   if t['enemy'] == b.enemy and t.get('step') in (None, cur['step'])]
        planned += list(mem.get('card_override', {}).get(cur['step']) or [])
        _record(mem, 'battle_start', **_battle_labels(cur), enemy=b.enemy, ally=b.ally,
                expected_metric=cur['strategy_expected'],
                observed_metric={'enemy_hp': b.enemy_hp, 'ally_hp': b.ally_hp},
                planned_cards=planned, context=cur.get('context', 'message'),
                enemy_hp=b.enemy_hp, ally_hp=b.ally_hp, castle=cur['castle'],
                reason='戦闘パネルの将軍名とHPを確認')
    if (b.enemy, b.ally) != (cur['enemy'], cur['ally']):
        return []            # faded/partial panel: keep the last clear reading
    cur['away'] = 0
    cur['enemy_hp'], cur['ally_hp'] = b.enemy_hp, b.ally_hp
    if b.enemy_hp is not None and cur.get('start_enemy_hp') is not None and b.enemy_hp < cur['start_enemy_hp']:
        cur['clashed'] = True
    if cur.get('card_flow'):
        flow = cur['card_flow']
        flow['battle_frames_without_receipt'] = flow.get('battle_frames_without_receipt', 0) + 1
        if flow['battle_frames_without_receipt'] >= 2:
            _card_use_unclassified(mem, cur, '実使用告知を確認できないまま白兵戦へ復帰')
        return []
    if b.enemy_hp == 0 or b.ally_hp == 0:
        return []  # Do not open a card menu after the human panel has ended.
    extra = [{'enemy': b.enemy, 'card': card, 'open': True, 'step': cur.get('step'),
              'note': '再攻撃の開幕切り札(チャート逸脱)'}
             for card in mem.get('card_override', {}).get(cur.get('step')) or []]
    done = cur.setdefault('tactics_done', [])
    for index, tactic in enumerate([*extra, *chart.tactics(mem.get('chapter') or 0)]):
        tid = f"{'x' if index < len(extra) else 'c'}{index}:{tactic['card']}"
        if tactic['enemy'] != b.enemy or tid in done:
            continue
        if tactic.get('step') and tactic['step'] != cur.get('step'):
            continue
        due = (tactic.get('open')
               or (tactic.get('when_hp_at_most') is not None and b.enemy_hp is not None
                   and b.enemy_hp <= tactic['when_hp_at_most'])
               or (tactic.get('after_clash') and cur.get('clashed'))
               or (tactic.get('after_card') and tactic['after_card'] in cur['cards_used']))
        if due:
            done.append(tid)
            cur['card_flow'] = {'card': tactic['card'], 'stage': 'menu', 'note': tactic['note']}
            _record(mem, 'battle_card', **_battle_labels(cur), card=tactic['card'], enemy=b.enemy,
                    enemy_hp=b.enemy_hp, ally_hp=b.ally_hp, reason=tactic['note'],
                    expected_metric='選択後の実使用告知と敵HP減少',
                    observed_metric={'enemy_hp': b.enemy_hp, 'ally_hp': b.ally_hp},
                    resulting_event='card_planned')
            return [pad('b')]
    return []


def battle_menu_step(screen: Screen, mem):
    _migrate_card_evidence(mem)
    cur = mem.get('battle') or {}
    flow = cur.get('card_flow')
    if screen.kind == 'battle_menu':
        if not flow:
            return [pad('b')]
        if flow['stage'] == 'menu':
            flow['stage'] = 'down'
            return [pad('down')]
        flow['stage'] = 'list'
        return [pad('a')]
    return []


def card_list_step(screen: Screen, mem):
    """Keep card intention/selection separate from observed consumption."""
    _migrate_card_evidence(mem)
    cur = mem.get('battle') or {}
    flow = cur.get('card_flow')
    names = [w for _, _, w in _options(screen) if w in CARD_NAMES]
    if not flow:
        return [pad('b')]
    if flow['stage'] == 'list':
        if flow['card'] not in names:
            cur.setdefault('cards_missing', []).append(flow['card'])
            if not cur.get('deviation_reason'):
                cur['strategy_variant'] = 'chart_card_unavailable'
                cur['deviation_reason'] = 'チャートで予定した切り札を携行していない'
            _record(mem, 'battle_card_missing', **_battle_labels(cur), card=flow['card'],
                    expected_metric={'carried_card': flow['card']}, observed_metric={'listed_cards': names},
                    resulting_event='card_not_selected', reason='切り札を携行していないため白兵を継続')
            cur['card_flow'] = None
            return [pad('b'), pad('b')]
        index = names.index(flow['card'])
        flow['stage'] = 'announce'
        flow['selection_planned'] = True
        cur['card_consumption_complete'] = False
        cur.setdefault('cards_selected', []).append(flow['card'])
        _record(mem, 'battle_card_selected', **_battle_labels(cur), card=flow['card'],
                expected_metric='実使用告知', observed_metric={'listed_cards': names},
                resulting_event='selection_planned_not_yet_confirmed', reason='切り札選択入力を予定。消費は未確定')
        return [pad('down')] * index + [pad('a')]
    if flow['stage'] == 'announce' and flow.get('selection_planned'):
        # No live frame/parser signature has calibrated a use receipt yet.
        # Keep capturing this flow, but never unlock after_card from guessed text.
        cur['card_consumption_complete'] = False
        if not flow.get('uncalibrated_candidate_recorded'):
            flow['uncalibrated_candidate_recorded'] = True
            _record(mem, 'battle_card_candidate', **_battle_labels(cur), card=flow['card'],
                    expected_metric='実画面とparserで校正済みの切り札使用証拠',
                    observed_metric={'confirmation': 'unclassified', 'screen_kind': screen.kind},
                    resulting_event='card_use_unclassified', reason='告知署名が未校正のため消費を確定せず画像収集')
    return []


def _hold_general_loss_metric(mem):
    """HP defeat is not proof of death; invalidate old inferred counters once.

    No death/roster-loss observer exists yet. Preserve HP battle counters and
    original JSONL history, but mark both current and archived in-memory loss
    totals unknown on the next observation, even if the old total was zero.
    """
    for scope in ('stats', 'previous_stats'):
        old_stats = mem.get(scope)
        if not isinstance(old_stats, dict):
            continue
        previous = old_stats.get('generals_lost')
        mem[scope] = {**old_stats, 'generals_lost': None}
        if previous is not None:
            bounded_previous = previous if type(previous) is int and 0 <= previous <= 10**6 else None
            _record(mem, 'metric_invalidated', metric='generals_lost', metric_scope=scope,
                    expected_metric='将軍の死亡または喪失を直接確認する証拠',
                    observed_metric={'previous_inferred_count': bounded_previous,
                                     'generals_lost': None, 'status': 'unclassified'},
                    reason='HP敗北から将軍喪失は確定できず、喪失観測器がないため旧集計を未分類化')


def battle_end(mem, next_kind):
    _hold_general_loss_metric(mem)
    _migrate_card_evidence(mem)
    cur = mem.get('battle')
    if not cur:
        return
    # A panel can blink during the melee; end only on the second
    # consecutive observation without it.
    cur['away'] = cur.get('away', 0) + 1
    if cur['away'] < 2:
        return
    if (cur.get('card_flow') or {}).get('stage') == 'announce':
        _card_use_unclassified(mem, cur, '実使用告知がないまま戦闘が終了')
    mem['battle_seen'] = None
    mem.pop('battle', None)
    mem['attack'] = None
    enemy_hp, ally_hp = cur.get('enemy_hp'), cur.get('ally_hp')
    if enemy_hp == 0 and ally_hp not in (None, 0):
        outcome = 'win'
    elif ally_hp == 0 and enemy_hp not in (None, 0):
        outcome = 'loss'
    else:
        outcome = 'unclassified'
    castle = cur.get('castle')
    if outcome == 'win' and castle and cur.get('side') == 'attack':
        captured = mem.setdefault('captured', [])
        if castle not in captured:
            captured.append(castle)
    if outcome == 'loss' and castle and cur.get('side') == 'defense':
        mem['captured'] = [c for c in mem.get('captured', []) if c != castle]
    stats = mem.setdefault('stats', {'wins': 0, 'losses': 0, 'unclassified': 0, 'cards_used': 0,
                                     'generals_lost': None, 'cards_confirmed': 0, 'card_evidence_version': 1})
    stats[{'win': 'wins', 'loss': 'losses'}.get(outcome, 'unclassified')] += 1
    confirmed = len(cur.get('cards_used', []))
    stats['cards_confirmed'] = stats.get('cards_confirmed', 0) + confirmed
    if stats.get('cards_used') is not None and cur.get('card_consumption_complete') is True:
        stats['cards_used'] += confirmed
    else:
        stats['cards_used'] = None
    step = cur.get('step')
    boss_attempt = (mem.get('chapter') == 1 and step == '1-B1'
                    and cur.get('enemy') == chart.BOSSES.get(1)
                    and cur.get('castle') == 'けっかい' and cur.get('side') == 'attack'
                    and cur.get('entry_evidence') == 'measured_boss_entry')
    if outcome == 'loss' and boss_attempt:
        retries = mem.setdefault('retries', {})
        retries[step] = retries.get(step, 0) + 1
        for key in ('general_override', 'card_override', 'order_context', 'sortie_general'):
            mem.setdefault(key, {}).pop(step, None)
        if retries[step] <= 3:
            mem.setdefault('orders', {})[step] = 'pending'
            context = {'strategy_variant': 'retry_chart_boss_kit',
                       'deviation_reason': 'ボス戦のHP敗北を確認。主人公と既定切り札を再確認して再試行',
                       'expected_metric': {'general': NAME, 'cards': ['クースカン', 'ノリウツール'],
                                           'goal': 'クイーン戦勝利'}}
            mem.setdefault('retry_context', {})[step] = context
            _record(mem, 'order_retry', chart_step=step, **context,
                    observed_metric={'outcome': outcome, 'enemy_hp': enemy_hp, 'ally_hp': ally_hp},
                    resulting_event=f'retry:{retries[step]}', reason='既定のチャート装備を確認してボスへ再挑戦')
        else:
            mem.setdefault('orders', {})[step] = 'failed'
            _record(mem, 'situation_held', chart_step=step, strategy_variant='boss_retry_exhausted',
                    observed_metric={'retries': retries[step]}, reason='ボス再試行の上限に到達したため保留')
    elif outcome == 'loss' and (step == '1-B1' or cur.get('enemy') == chart.BOSSES.get(1)):
        _record(mem, 'situation_held', chart_step=step, strategy_variant='boss_entry_unclassified',
                observed_metric={'castle': castle, 'side': cur.get('side'),
                                 'entry_evidence': cur.get('entry_evidence')},
                reason='実測ボス突入の証拠がないため再出撃を保留')
    elif (outcome == 'loss' and cur.get('side') == 'attack' and step
            and castle not in mem.get('captured', [])):
        retries = mem.setdefault('retries', {})
        retries[step] = retries.get(step, 0) + 1
        if retries[step] <= 2:
            mem.setdefault('orders', {})[step] = 'pending'
            # Better than repeating a lost melee: open with two イッテツーン
            # (10 general damage each per the chart's card table).
            extra = ['イッテツーン', 'イッテツーン']
            mem.setdefault('card_override', {})[step] = extra
            mem.setdefault('retry_context', {})[step] = {
                'deviation_reason': 'チャートの白兵で敗北したため、開幕イッテツーン2枚で再攻撃',
                'expected_metric': {'enemy_hp_after_open': max(0, (enemy_hp or 0) - 20)}}
            _record(mem, 'order_retry', chart_step=step, strategy_variant='retry_with_opening_cards',
                    deviation_reason='チャートの白兵で敗北したため、開幕イッテツーン2枚で再攻撃',
                    expected_metric={'enemy_hp_after_open': max(0, (enemy_hp or 0) - 20)},
                    observed_metric={'outcome': outcome, 'enemy_hp': enemy_hp, 'ally_hp': ally_hp},
                    resulting_event=f'retry:{retries[step]}', reason='城を取らないと後続手順とボスへ進めない')
    boss = chart.BOSSES.get(mem.get('chapter') or 0)
    resulting = None
    if outcome == 'win' and boss and cur.get('enemy') == boss:
        resulting = f"chapter_{mem['chapter']}_boss_defeated"
        # A boss HP reading proves this battle result, not the next chapter.
        # Only a visible chapter header may advance and reset route state.
    elif outcome == 'win' and castle and cur.get('side') == 'attack':
        resulting = f'captured:{castle}'
    elif outcome == 'loss' and castle and cur.get('side') == 'defense':
        resulting = f'lost:{castle}'
    _record(mem, 'battle_result', **_battle_labels(cur), enemy=cur.get('enemy'),
            expected_metric=cur.get('strategy_expected'),
            ally=cur.get('ally'), castle=castle, side=cur.get('side'), outcome=outcome,
            observed_metric={'enemy_hp': enemy_hp, 'ally_hp': ally_hp,
                             'cards_used': cur.get('cards_used', []),
                             'cards_selected': cur.get('cards_selected', []),
                             'cards_missing': cur.get('cards_missing', []),
                             'cards_unclassified': cur.get('cards_unclassified', []),
                             'card_consumption_complete': cur.get('card_consumption_complete', False),
                             'general_loss': 'unclassified'},
            resulting_event=resulting or outcome, resulting_stage=None, next_screen=next_kind,
            reason='戦闘終了時のHP表示から判定' if outcome != 'unclassified'
            else '最終HPが0/非0で確定しないため未分類')


ATTACK = re.compile(r'(\S+?)しょうぐんが(\S+?)じょうにのりこんだ')
DEFENSE = re.compile(r'(\S+?)じょうがてきにせめこまれ')


def message_step(screen: Screen, mem):
    text = screen.text
    boss_entry = re.fullmatch(r'([^\ufffd\s]+)しょうぐんがボスじょうにせめこんだ!!', text)
    if boss_entry:
        general = boss_entry.group(1)
        launched = (mem.get('launched') or {}).get('けっかい') or {}
        if mem.get('chapter') != 1 or launched.get('step') != '1-B1' or launched.get('general') != general:
            _record(mem, 'situation_held', screen='boss_attack_started',
                    observed_metric={'message': text}, reason='実測ボス突入文を読んだが出撃注文と一致しないため保留')
            return []
        mem['attack'] = {'general': general, 'castle': 'けっかい', 'side': 'attack', 'step': '1-B1',
                         'entry_evidence': 'measured_boss_entry'}
        _record(mem, 'attack_observed', chart_step='1-B1', general=general, castle='けっかい',
                expected_metric={'general': launched['general']}, observed_metric={'general': general, 'message': text},
                reason='実測済みのボス城突入文と出撃将軍が一致')
        return [pad('a')]
    m = ATTACK.search(text)
    if m:
        general, castle = m.groups()
        launched = (mem.get('launched') or {}).get(castle) or {}
        ours = general == launched.get('general') or general in (NAME, 'ヴィーナス', 'ココット', 'ゼウス')
        side = 'attack' if ours else 'enemy'
        step = launched.get('step') if launched.get('general') == general else None
        if (mem.get('attack') or {}).get('castle') != castle or (mem.get('attack') or {}).get('general') != general:
            _record(mem, 'attack_observed', chart_step=step, general=general, castle=castle,
                    expected_metric=launched.get('general'), observed_metric=general,
                    deviation_reason=None if step or not ours else 'unplanned_attack',
                    reason='のりこんだ表示')
        mem['attack'] = {'general': general, 'castle': castle, 'side': side, 'step': step}
        return [pad('a')]
    m = DEFENSE.search(text)
    if m:
        castle = m.group(1)
        if (mem.get('attack') or {}).get('castle') != castle or (mem.get('attack') or {}).get('side') != 'defense':
            _record(mem, 'defense_observed', castle=castle, reason='せめこまれました表示')
        mem['attack'] = {'general': None, 'castle': castle, 'side': 'defense', 'step': None}
        return [pad('a')]
    return None


# ---------------------------------------------------------------- month
def _month_key(header):
    return f"{header['year']}-{header['month']}" if header else None


KNOWN_PRICES = {'イッテツーン': 1, 'ノリウツール': 18, 'クースカン': 24, 'ゼンマイン': 32}


def _adjusted_plan(mem, header, key):
    """Month purchases from an adopted adjusted chart (#1085 L2).

    Cards reuse the merchant flow (unlisted/unaffordable items are skipped
    and recorded there); soldiers reuse the refill editor, capped by the gold
    left after known card prices. General recruitment has no measured menu
    yet, so it is recorded as a deviation and not attempted.
    """
    spec = (mem.get('chart_adjust') or {}).get('purchases')
    if not spec or key != f'{spec["month"][0]}-{spec["month"][1]}':
        return None
    gold = header['gold']
    items = [list(i) for i in spec['cards']]
    left = gold - sum(KNOWN_PRICES.get(name, 0) * qty for name, qty in items)
    soldiers = max(0, min(spec['soldiers'], left, 99))
    shop = mem['shop'] = {'key': key, 'items': items, 'soldiers': soldiers, 'merchant_done': False,
                          'variant': 'chart_adjusted', 'soldiers_done': soldiers == 0,
                          'gold_start': gold}
    _record(mem, 'month_plan', chart_step='adjusted-month', strategy_variant='chart_adjusted',
            month=key, gold=gold, plan={'cards': items, 'soldiers': soldiers},
            deviation_reason=('recruit_menu_unmeasured' if spec.get('generals') else None),
            expected_metric={'adjusted_cards': [list(i) for i in spec['cards']],
                             'adjusted_soldiers': spec['soldiers'],
                             'adjusted_generals': spec.get('generals', 0)},
            reason=spec.get('note') or '調整チャートの月次購入')
    return shop


def _plan(mem, header):
    spec = chart.purchases(mem.get('chapter') or 0)
    key = _month_key(header)
    shop = mem.get('shop')
    if shop and shop.get('key') == key:
        return shop
    adjusted = _adjusted_plan(mem, header, key) if header else None
    if adjusted:
        return adjusted
    if not spec or key != f'{spec["month"][0]}-{spec["month"][1]}':
        return None
    gold = header['gold']
    if gold >= spec['chart_gold']:
        items = [list(i) for i in spec['cards']]
        soldiers, variant, deviation = spec['soldiers'], 'chart', None
    else:
        items, left = [], gold
        for name, qty in spec['priority']:
            n = min(qty, left // KNOWN_PRICES[name])
            if n:
                items.append([name, n])
                left -= n * KNOWN_PRICES[name]
        soldiers = min(spec['soldiers'], left)
        variant = 'budget_boss_kit_first'
        deviation = f"所持金{gold}Gがチャート想定{spec['chart_gold']}G未満"
    shop = mem['shop'] = {'key': key, 'items': items, 'soldiers': soldiers, 'merchant_done': False,
                          'variant': variant,
                          'soldiers_done': soldiers == 0, 'gold_start': gold}
    _record(mem, 'month_plan', chart_step='1-month', strategy_variant=variant, month=key, gold=gold,
            plan={'cards': items, 'soldiers': soldiers}, deviation_reason=deviation,
            expected_metric={'chart_cards': [list(i) for i in spec['cards']],
                             'chart_soldiers': spec['soldiers'], 'chart_gold': spec['chart_gold']},
            reason=spec['note'])
    return shop


def month_step(screen: Screen, mem):
    shop = _plan(mem, screen.header)
    if screen.has('じゅうじキー'):
        return quantity_step(screen, mem, soldiers=True)
    if screen.has('うむッ') and screen.has('いかんッ'):
        # "よろしいですかな?" after も〜おしまい!: confirm only our own exit.
        choice = 'うむッ!' if mem.get('month_exit') else 'いかんッ!'
        move = menu_to(screen, choice)
        if move == 'here':
            if choice == 'うむッ!':
                mem['month_exit'] = False
            _record(mem, 'month_confirm', choice=choice, reason='月初メニュー終了の確認')
            return [pad('a')]
        return [move] if move else [pad('b')]
    if shop and not shop['merchant_done'] and shop['items']:
        move = menu_to(screen, 'しょうにん')
        return [pad('a')] if move == 'here' else [move] if move else []
    if shop and not shop['soldiers_done']:
        move = menu_to(screen, 'へいしほじゅう')
        return [pad('a')] if move == 'here' else [move] if move else []
    if shop and not shop.get('closed'):
        shop['closed'] = True
        _record(mem, 'month_done', month=shop['key'], gold_after=(screen.header or {}).get('gold'),
                observed_metric={'bought': shop.get('bought', []),
                                 'gold_start': shop['gold_start'],
                                 'gold_end': (screen.header or {}).get('gold')},
                reason='月一の購入を終了')
    move = menu_to(screen, 'も〜おしまい!')
    if move == 'here':
        mem['month_exit'] = True
        return [pad('a')]
    return [move] if move else []


def shop_step(screen: Screen, mem):
    shop = mem.get('shop')
    kind = screen.kind
    if kind == 'shop_exit_confirm':
        move = menu_to(screen, 'うむッ!')
        if shop:
            shop['merchant_done'] = True
        # Without a readable hand, the measured default cursor is うむッ (#960).
        return [move] if move and move != 'here' else [pad('a')]
    if kind == 'shop_quantity_prompt':
        return [pad('a')]
    if kind == 'shop_quantity':
        return quantity_step(screen, mem)
    # shop_list
    gold = (screen.header or {}).get('gold')
    if not shop or shop.get('key') != _month_key(screen.header) or not shop['items']:
        return [pad('b')]
    listed = {}
    for line in screen.lines:
        joined = ''.join(line.words(120, 256))
        m = re.match(r'^(\S+?)(\d+)G$', joined)
        if m:
            listed[m.group(1)] = int(m.group(2))
    while shop['items']:
        name, qty = shop['items'][0]
        price = listed.get(name)
        if price is None or gold is None or price > gold:
            _record(mem, 'buy_skip', card=name, qty=qty, price=price, gold=gold,
                    deviation_reason='not_listed' if price is None else 'insufficient_gold',
                    reason='商人の品揃えまたは所持金の制約')
            shop['items'].pop(0)
            continue
        n = min(qty, gold // price)
        shop['items'][0] = [name, n]
        shop['want'] = [name, n, price]
        move = menu_to(screen, name)
        return [pad('a')] if move == 'here' else [move] if move else []
    return [pad('b')]


def quantity_step(screen: Screen, mem, soldiers=False):
    shop = mem.get('shop') or {}
    if soldiers:
        # Digits sit at x=216 (tens) and x=224 (ones). The hand on the ones
        # digit hides the tens digit, so keep the last tens digit seen.
        line = next((l for l in screen.lines if 'かわりますぞ' in l.known.replace(' ', '')), None)
        cells = dict(line.cells) if line else {}
        ones_ch, tens_ch = cells.get(224), cells.get(216)
        if tens_ch and tens_ch.isdigit() and screen.hand and screen.hand[0] < 200:
            shop['soldier_tens'] = int(tens_ch)
        tens_seen = shop.get('soldier_tens')
        if ones_ch and ones_ch.isdigit() and tens_seen is not None:
            value = tens_seen * 10 + int(ones_ch)
        else:
            value = None
            if screen.hand and screen.hand[0] >= 200:
                return [pad('left')]     # reveal the tens digit first
        target = shop.get('soldiers', 0)
    else:
        m = re.search(r'(\d+)こで', screen.text)
        value = int(m.group(1)) if m else None
        want = shop.get('want') or [None, 0, 0]
        target = want[1]
        if screen.hand and 'うむッ' in screen.text and screen.selected in ('うむッ!', 'いかんッ!'):
            if value == target and target:
                move = menu_to(screen, 'うむッ!')
                if move == 'here':
                    bought = shop.setdefault('bought', [])
                    bought.append([want[0], value, want[2]])
                    _record(mem, 'buy', card=want[0], qty=value, price=want[2],
                            chart_step='1-month', strategy_variant=shop.get('variant', 'chart'),
                            reason='チャート/予算計画どおりの個数を確認して購入')
                    if shop.get('items') and shop['items'][0][0] == want[0]:
                        shop['items'].pop(0)
                    shop['want'] = None
                    return [pad('a')]
                return [move] if move else []
            return [pad('b')]
    if value is None or not screen.hand:
        return []
    if not target:
        return [pad('b')]
    # Digit editor: hand x>=200 is the ones digit, a smaller x the tens digit.
    ones_selected = screen.hand[0] >= 200
    tens, ones = divmod(value % 100, 10)
    want_tens, want_ones = divmod(min(target, 99), 10)
    if tens != want_tens:
        if ones_selected:
            return [pad('left')]
        if soldiers:
            shop['soldier_tens'] = None      # re-read after the change
        return [pad('up' if (want_tens - tens) % 10 <= 5 else 'down')]
    if ones != want_ones:
        if not ones_selected:
            return [pad('right')]
        return [pad('up' if (want_ones - ones) % 10 <= 5 else 'down')]
    if soldiers:
        shop['soldiers_done'] = True
        _record(mem, 'soldier_refill', qty=value, chart_step='1-month',
                strategy_variant=shop.get('variant', 'chart'),
                expected_metric=(chart.purchases(mem.get('chapter') or 0) or {}).get('soldiers'),
                observed_metric=value, reason='兵士補充数を確認して確定')
    return [pad('a')]


# ---------------------------------------------------------------- prompts
def yes_no_step(screen: Screen, mem):
    text = screen.text
    if re.search(r'\d+Gでいい', text):
        choice, reason, variant = 'いかんッ!', '追加のおねだりは所持金を月一購入に残すため断る', 'decline_extra_gift'
    elif 'はたしあい' in text or 'ごあいて' in text:
        # A lost duel can cost the hero; the chart does not require it.
        choice, reason = 'いかんッ!', '主人公の損失リスクを避け一騎打ちを断る'
        variant = 'decline_duel'
    else:
        choice, reason, variant = 'うむッ!', '未分類の確認は既定で進行', 'unclassified_prompt'
    move = menu_to(screen, choice)
    if move == 'here':
        _record(mem, 'prompt', choice=choice, prompt=text[-40:], reason=reason, strategy_variant=variant)
        return [pad('a')]
    return [move] if move else []


def observe_events(screen: Screen, mem):
    """Record chart-relevant facts that need no input (month header, harvest)."""
    _hold_general_loss_metric(mem)
    _migrate_card_evidence(mem)
    header = screen.header
    if header:
        chapter = header.get('chapter')
        if chapter and chapter != mem.get('chapter'):
            previous = mem.get('chapter')
            # Route state belongs to the measured map of one chapter. Keep
            # run-wide counters/name evidence, never carry coordinates/orders.
            for key in ('active', 'anchor', 'attack', 'battle', 'battle_seen',
                        'captured', 'card_override', 'cursor', 'egg_battle',
                        'expect_menu', 'general_override', 'launched', 'month_exit',
                        'nav_last', 'orders', 'picked', 'retries', 'retry_context', 'shop',
                        'source_override', 'uncertain', 'month', 'order_context', 'sortie_general',
                        'chart_adjust'):
                mem.pop(key, None)
            mem['chapter'] = chapter
            mem['variant'] = 'chart' if chart.orders(chapter) else 'chart_unavailable'
            _record(mem, 'chapter_seen', previous_stage=previous,
                    observed_metric={'chapter': chapter}, resulting_stage=chapter,
                    reason='画面の章表示を確認し、前章の座標・出撃・購入状態を初期化')
        key = _month_key(header)
        if mem.get('month') != key:
            mem['month'] = key
            mem['gold'] = header['gold']
            _record(mem, 'month_seen', month=key, gold=header['gold'], reason='月の表示')
        mem['gold'] = header['gold']
    if 'きょうさく' in screen.text and not mem.get('poor_harvest_' + str(mem.get('month'))):
        mem['poor_harvest_' + str(mem.get('month'))] = True
        _record(mem, 'poor_harvest', deviation_reason='reset_forbidden',
                reason='チャートは凶作でリセット指示だがbotはリセットしない',
                expected_metric='収入減')


def summary(mem: dict | None) -> dict:
    """Bounded, secret-free chart progress for corner state and diagnostics."""
    mem = mem if isinstance(mem, dict) else {}
    stats = mem.get('stats') if isinstance(mem.get('stats'), dict) else {}
    orders = mem.get('orders') if isinstance(mem.get('orders'), dict) else {}
    battle = mem.get('battle') if isinstance(mem.get('battle'), dict) else {}
    chart_step = battle.get('step') if battle else mem.get('active')
    strategy_variant = battle.get('strategy_variant') if battle else mem.get('variant')
    as_int = lambda v: v if type(v) is int and 0 <= v <= 10**6 else None
    return {
        'chapter': as_int(mem.get('chapter')),
        'chart_step': chart_step if isinstance(chart_step, str) else None,
        'strategy_variant': strategy_variant if isinstance(strategy_variant, str) else None,
        'orders_launched': sum(1 for v in orders.values() if v == 'launched'),
        'orders_failed': sum(1 for v in orders.values() if v == 'failed'),
        'captured': len(mem.get('captured') or []),
        'wins': as_int(stats.get('wins')), 'losses': as_int(stats.get('losses')),
        'unclassified': as_int(stats.get('unclassified')),
        'cards_used': (as_int(stats.get('cards_used')) if stats.get('card_evidence_version') == 1
                       and not battle.get('card_flow')
                       and battle.get('card_consumption_complete', True) is True else None),
        # Lower bound confirmed under the new evidence contract, not total use.
        'cards_confirmed': as_int(stats.get('cards_confirmed')),
        # No direct loss observer exists: even a legacy zero is not evidence.
        'generals_lost': None,
        'gold': as_int(mem.get('gold')),
        'month': mem.get('month') if isinstance(mem.get('month'), str) else None,
        'name_entered': bool((mem.get('name') or {}).get('done')),
        'name_matches': (mem.get('name') or {}).get('typed') == NAME,
    }


def egg_battle_step(screen: Screen, mem):
    """Summoned-monster battle: a turn menu that waits for a command.

    The chart avoids provoking enemy eggs but gives no command for this
    battle; the default こうげき (top item, where the cursor starts) keeps the
    game moving instead of stalling. Messages inside it advance with A.
    """
    if screen.kind == 'egg_battle_menu':
        if not mem.get('egg_battle'):
            mem['egg_battle'] = True
            _record(mem, 'egg_battle', strategy_variant='egg_battle_attack',
                    deviation_reason='チャート外: 敵の卵召喚戦', expected_metric='召喚獣の撃破',
                    reason='コマンド待ちで停止しないよう既定のこうげきを選ぶ')
        return [pad('a')]
    return [pad('a')]


def gift_step(screen: Screen, mem):
    """「なにを かいあたえますか?」: the chart resets here; the bot cannot, so
    it buys the cheapest listed item and records the deviation."""
    items = []
    for line in screen.lines:
        joined = ''.join(line.words(64, 256))
        m = re.match(r'^(\S+?)(\d+)G$', joined)
        if m and not HEADER_RE.search(line.known.replace(' ', '')):
            items.append((int(m.group(2)), m.group(1)))
    if not items:
        return []
    price, name = min(items)
    move = menu_to(screen, name, exact=False)    # the price can abut the name
    if move == 'here':
        _record(mem, 'gift', strategy_variant='cheapest_gift', item=name, price=price,
                gold=(screen.header or {}).get('gold'), deviation_reason='reset_forbidden',
                expected_metric={'chart': 'おねだりはリセット'},
                observed_metric={'price': price, 'affordable': (screen.header or {}).get('gold', 0) >= price},
                reason='リセットできないため最安の品を選ぶ')
        return [pad('a')]
    return [move] if move else []
