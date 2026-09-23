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
                                'yes_no', 'shop_list'})
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


def next_order(mem):
    status = mem.setdefault('orders', {})
    for order in chart.orders(mem.get('chapter') or 0):
        if status.get(order['step']) in (None, 'pending') and _ready(order, mem):
            return order
    return None


def _order(mem):
    step = mem.get('active')
    return next((o for o in chart.orders(mem.get('chapter') or 0) if o['step'] == step), None)


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
            update_world(screen, mem, frame)
            return []           # nothing charted: let real time advance
        mem['active'] = order['step']
        mem['picked'] = []
        _record(mem, 'order_start', chart_step=order['step'], general=order['general'],
                source=order['source'], target=order['target'], cards=list(order['cards']),
                reason=order['note'])
    source = mem.get('source_override', {}).get(order['step'], order['source'])
    goal = chart.castles(mem['chapter'])[source]
    result = nav_step(screen, mem, frame, goal)
    if result == 'arrived':
        # If no castle menu follows, the cell was wrong: re-localize.
        mem['expect_menu'] = True
        return [pad('a')]
    return result or []


def target_step(screen: Screen, mem, frame):
    order = _order(mem)
    if order is None:
        # A marker we did not request: cancel instead of sending a general.
        _record(mem, 'unexpected_target', reason='指示中でない出撃先選択画面のためBで取消')
        return [pad('b')]
    goal = chart.castles(mem['chapter'])[order['target']]
    result = nav_step(screen, mem, frame, goal)
    if result == 'arrived':
        _finish_order(mem, 'launched', general=order['general'], target=order['target'],
                      cursor=mem.get('cursor'), anchor=mem.get('anchor'),
                      expected_metric='のりこんだ表示で目標城を確認',
                      reason=f"{order['general']}を{order['target']}へ出撃")
        general = mem.get('general_override', {}).get(order['step'], order['general'])
        mem.setdefault('launched', {})[order['target']] = {'general': general, 'step': order['step']}
        return [pad('a')]
    return result or []


def deploy_step(screen: Screen, mem):
    order = _order(mem)
    kind = screen.kind
    if order is None:
        # Menus we did not open (e.g. confirm pressed by an earlier fallback).
        return [pad('b')]
    if kind == 'castle_menu':
        move = menu_to(screen, 'しゅつげき')
        return [pad('a')] if move == 'here' else [move] if move else []
    if kind == 'general_list':
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
        return [pad('a')] if move == 'here' else [move]
    if kind == 'card_select':
        picked = mem.setdefault('picked', [])
        wanted = list(mem.get('card_override', {}).get(order['step'], order['cards']))
        for card in picked:
            if card in wanted:
                wanted.remove(card)
        if not wanted:
            return [pad('b')]
        card = wanted[0]
        move = menu_to(screen, card)
        if move is None:
            _record(mem, 'card_missing', deviation_reason=f'{card}が在庫一覧に見えない',
                    expected_metric=list(order['cards']), observed_metric=picked,
                    reason='在庫にない切り札は持たずに出撃')
            mem['picked'] = picked + [card]
            return []
        if move == 'here':
            picked.append(card)
            _record(mem, 'card_pick', card=card, reason=order['note'])
            return [pad('a')]
        return [move]
    if kind == 'sortie_confirm':
        carried = re.findall('|'.join(sorted(CARD_NAMES, key=len, reverse=True)), screen.text)
        move = menu_to(screen, 'うむッ!')
        if move == 'here':
            want = sorted(mem.get('card_override', {}).get(order['step'], order['cards']))
            got = sorted(carried)
            _record(mem, 'sortie_confirm', general=order['general'], cards=carried,
                    expected_metric=want, observed_metric=got,
                    deviation_reason=None if got == want else 'carried_cards_differ',
                    reason='出撃確認で所持切り札を照合')
            source = mem.get('source_override', {}).get(order['step'], order['source'])
            mem['cursor'] = list(chart.castles(mem['chapter'])[source])
            mem['uncertain'] = False      # the target marker starts on the source castle
            return [pad('a')]
        return [move] if move else []
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
        return {'castle': attack.get('castle'), 'side': side, 'step': attack.get('step')}
    captured = set(mem.get('captured', []))
    for castle, info in (mem.get('launched') or {}).items():
        if info.get('general') == ally and castle not in captured:
            return {'castle': None, 'side': None, 'step': info.get('step'),
                    'context': 'unclassified_location'}
    return {'castle': None, 'side': None, 'step': None, 'context': 'unclassified'}


def battle_step(screen: Screen, mem):
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
                               **context}
        planned = [t['card'] for t in chart.tactics(mem.get('chapter') or 0)
                   if t['enemy'] == b.enemy and t.get('step') in (None, cur['step'])]
        planned += list(mem.get('card_override', {}).get(cur['step']) or [])
        _record(mem, 'battle_start', chart_step=cur['step'], enemy=b.enemy, ally=b.ally,
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
        return []
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
            _record(mem, 'battle_card', chart_step=cur['step'], card=tactic['card'], enemy=b.enemy,
                    enemy_hp=b.enemy_hp, ally_hp=b.ally_hp, reason=tactic['note'],
                    expected_metric='敵HP減少')
            return [pad('b')]
    return []


def battle_menu_step(screen: Screen, mem):
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
    """Battle card list: order is the carried order; the cursor starts at the top."""
    cur = mem.get('battle') or {}
    flow = cur.get('card_flow')
    names = [w for _, _, w in _options(screen) if w in CARD_NAMES]
    if not flow:
        return [pad('b')]
    if flow['stage'] == 'list':
        if flow['card'] not in names:
            _record(mem, 'battle_card_missing', card=flow['card'], observed_metric=names,
                    deviation_reason='切り札を携行していない', reason='白兵を継続')
            cur['cards_used'].append(flow['card'])
            cur['card_flow'] = None
            return [pad('b'), pad('b')]
        index = names.index(flow['card'])
        flow['stage'] = 'announce'
        cur['cards_used'].append(flow['card'])
        return [pad('down')] * index + [pad('a')]
    if flow['stage'] == 'announce' and len(names) == 1:
        _record(mem, 'battle_card_used', card=names[0], expected_metric=flow['card'],
                observed_metric=names[0], reason='切り札の告知表示を確認')
        cur['card_flow'] = None
    return []


def battle_end(mem, next_kind):
    cur = mem.get('battle')
    if not cur:
        return
    # A panel can blink during the melee; end only on the second
    # consecutive observation without it.
    cur['away'] = cur.get('away', 0) + 1
    if cur['away'] < 2:
        return
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
                                     'generals_lost': 0})
    stats[{'win': 'wins', 'loss': 'losses'}.get(outcome, 'unclassified')] += 1
    stats['cards_used'] += len(cur.get('cards_used', []))
    if outcome == 'loss':
        stats['generals_lost'] += 1
    step = cur.get('step')
    if (outcome == 'loss' and cur.get('side') == 'attack' and step
            and castle not in mem.get('captured', [])):
        retries = mem.setdefault('retries', {})
        retries[step] = retries.get(step, 0) + 1
        if retries[step] <= 2:
            mem.setdefault('orders', {})[step] = 'pending'
            # Better than repeating a lost melee: open with two イッテツーン
            # (10 general damage each per the chart's card table).
            extra = ['イッテツーン', 'イッテツーン']
            mem.setdefault('card_override', {})[step] = extra
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
    _record(mem, 'battle_result', chart_step=cur.get('step'), enemy=cur.get('enemy'),
            ally=cur.get('ally'), castle=castle, side=cur.get('side'), outcome=outcome,
            observed_metric={'enemy_hp': enemy_hp, 'ally_hp': ally_hp,
                             'cards_used': cur.get('cards_used', [])},
            resulting_event=resulting or outcome, resulting_stage=None, next_screen=next_kind,
            reason='戦闘終了時のHP表示から判定' if outcome != 'unclassified'
            else '最終HPが0/非0で確定しないため未分類')


ATTACK = re.compile(r'(\S+?)しょうぐんが(\S+?)じょうにのりこんだ')
DEFENSE = re.compile(r'(\S+?)じょうがてきにせめこまれ')


def message_step(screen: Screen, mem):
    text = screen.text
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


def _plan(mem, header):
    spec = chart.purchases(mem.get('chapter') or 0)
    key = _month_key(header)
    if not spec or key != f'{spec["month"][0]}-{spec["month"][1]}':
        return None
    shop = mem.get('shop')
    if shop and shop.get('key') == key:
        return shop
    gold = header['gold']
    if gold >= spec['chart_gold']:
        items = [list(i) for i in spec['cards']]
        soldiers, variant, deviation = spec['soldiers'], 'chart', None
    else:
        items, left = [], gold
        prices = {'イッテツーン': 1, 'ノリウツール': 18, 'クースカン': 24, 'ゼンマイン': 32}
        for name, qty in spec['priority']:
            n = min(qty, left // prices[name])
            if n:
                items.append([name, n])
                left -= n * prices[name]
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
                        'nav_last', 'orders', 'picked', 'retries', 'shop',
                        'source_override', 'uncertain', 'month'):
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
    as_int = lambda v: v if type(v) is int and 0 <= v <= 10**6 else None
    return {
        'chapter': as_int(mem.get('chapter')),
        'chart_step': mem.get('active') if isinstance(mem.get('active'), str) else None,
        'strategy_variant': mem.get('variant') if isinstance(mem.get('variant'), str) else None,
        'orders_launched': sum(1 for v in orders.values() if v == 'launched'),
        'orders_failed': sum(1 for v in orders.values() if v == 'failed'),
        'captured': len(mem.get('captured') or []),
        'wins': as_int(stats.get('wins')), 'losses': as_int(stats.get('losses')),
        'unclassified': as_int(stats.get('unclassified')),
        'cards_used': as_int(stats.get('cards_used')),
        'generals_lost': as_int(stats.get('generals_lost')),
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
