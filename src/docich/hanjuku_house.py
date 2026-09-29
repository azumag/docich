"""Bounded, screen-driven broken-egg repair for every friendly general.

No ROM, emulator I/O, clocks or provider calls. The caller supplies the same
parsed native frame as the ordinary policy and persists ``mem['house']``.
Coordinates and menu paths are measured in the isolated libretro probe.
"""
from __future__ import annotations

import re

from .hanjuku_font import UNKNOWN

HOUSE_VIEW = {1: (118.5, 126.5), 2: (128.5, 94.5)}
# The heart's centre and the selectable cell differ by half a view pixel
# (four field pixels). Chapter 2's successful isolated trip used (128, 94).
HOUSE_TARGET = {chapter: (int(x), int(y)) for chapter, (x, y) in HOUSE_VIEW.items()}
GIFTS = (('スカーフ', 200), ('みずぎ', 100), ('ピアス', 50))
ROSTER_LIMIT = 128
STEP_LIMIT = 24
TRAVEL_LIMIT = 240
SESSION_LIMIT = 1200
SCAN_INTERVAL = 200


def general_status(screen):
    """Read the named general and egg row, never equate zero uses with broken.

    Main-menu panels use x=24/88, y=39 and egg y=119. Field/sortie panels
    use x=16/80, y=31 and egg y=111. Unknown tiles invalidate the egg row.
    """
    for x, y in ((24, 39), (16, 31)):
        row = next((r for r in screen.lines if r.y == y), None)
        egg = next((r for r in screen.lines if r.y == y + 80), None)
        if row is None or egg is None:
            continue
        name = row.span(x, x + 64).strip()
        if (not name or UNKNOWN in name or re.search(r'[\d\s]', name)
                or row.span(x + 64, x + 112) != 'しょうぐん'):
            continue
        text = egg.span(x, x + 112).replace(' ', '')
        if UNKNOWN in text or not text.startswith('たまご'):
            continue
        body = text[len('たまご'):]
        broken = body == 'こわれている'
        uses = re.fullmatch(r'(.+?)([0-4])', body)
        if not broken and body != 'なし' and not uses:
            continue
        return {'general': name, 'broken': broken,
                'egg': uses[1] if uses else None, 'uses': int(uses[2]) if uses else None,
                'location': 'castle' if 'しろのなかにいます' in screen.text else 'field'}
    return None


def roster(screen):
    if 'Aボタンでステータスひょうじ' not in screen.text or not screen.hand:
        return None
    names = _names_at(screen, 168, 39, 151)
    if not names or any(UNKNOWN in n or re.search(r'[\d\s]', n) for n in names):
        return None
    return names


def _names_at(screen, x, y0, y1):
    from . import hanjuku_policy as p
    names = []
    for row in screen.lines:
        if not y0 <= row.y <= y1 or (row.y - y0) % 16:
            continue
        name = dict(row.spans()).get(x)
        if dict(row.cells).get(x) == UNKNOWN:
            return []
        if name:
            if not p._name_read_cleanly(row, name):
                return []
            names.append(name)
    return names


def gift_prices(screen):
    listed = {}
    for row in screen.lines:
        if row.y not in (167, 183, 199):
            continue
        text = row.span(168, 232).replace(' ', '')
        match = re.fullmatch(r'(ピアス|みずぎ|スカーフ)(\d+)G', text)
        if match:
            listed[match[1]] = int(match[2])
    return listed if listed == dict(GIFTS) else None


def affordable_gift(gold, reserve):
    if type(gold) is not int:
        return None
    return next(((name, cost) for name, cost in GIFTS if gold >= cost + reserve), None)


def _record(mem, event, **fields):
    from . import hanjuku_policy as p
    state = mem.get('house') or {}
    p._record(mem, 'house_' + event, chart_step=None, general=state.get('general'),
              strategy_variant='broken_egg_repair', **fields)


def _phase(state, phase, **fields):
    state.update(phase=phase, age=0, **fields)


def _choose(screen, label):
    from . import hanjuku_policy as p
    move = p.menu_to(screen, label)
    return [p.pad('a')] if move == 'here' else [move] if move else []


def _exit(mem, reason):
    state = mem['house']
    _record(mem, 'deferred', reason=reason, observed_metric={'phase': state['phase']})
    _phase(state, 'close')
    return []


def _finish(mem):
    mem.pop('house', None)
    mem['house_scan_tick'] = int(mem.get('tick') or 0)
    mem['house_scan_month'] = mem.get('month')
    mem['uncertain'] = True
    for key in ('near_goal', 'nav_last', 'anchor', 'expect_menu'):
        mem.pop(key, None)


def _observe_status(screen, mem):
    info = general_status(screen)
    if info:
        name = info['general']
        mem.setdefault('house_eggs', {})[name] = {**info, 'month': mem.get('month')}
        if info['uses'] is not None:
            mem.setdefault('egg_uses', {})[name] = info['uses']
            mem.setdefault('egg_types', {})[name] = info['egg']
        _record(mem, 'egg_seen', observed_metric=info, reason='将軍のステータスで卵の状態を確認')
    return info


def _view_move(screen, mem, frame, target, next_phase, tolerance=0.6):
    from . import hanjuku_policy as p
    state = mem['house']
    if screen.kind != 'world_map':
        return []
    cursor = p.world_cursor(frame)
    if cursor is None:
        return []
    flags = p.world_flags(frame, mem.get('chapter'), cursor)
    if flags:
        p._apply_world_flags(mem, flags)
    dx, dy = target[0] - cursor[0], target[1] - cursor[1]
    if abs(dx) <= tolerance and abs(dy) <= tolerance:
        _phase(state, next_phase)
        return [p.pad('a')]
    return [p.pad('right' if dx > 0 else 'left', min(56, max(1, round(abs(dx) * 2))))] if abs(dx) > tolerance else [
        p.pad('down' if dy > 0 else 'up', min(56, max(1, round(abs(dy) * 2))))]


def _castle_view(mem, name):
    from . import hanjuku_policy as p
    x, y = p.chart.castles(mem['chapter'])[name]
    ox, oy = p.Y_JUMP_OFFSET[mem['chapter']]
    return x / 8 + ox, y / 8 + oy


def _next_general(mem):
    from . import hanjuku_policy as p
    state = mem['house']
    pending = state.get('pending') or []
    if not pending or not affordable_gift(mem.get('gold'), p.WAGE_RESERVE):
        _phase(state, 'close')
        return
    name = pending.pop(0)
    info = (mem.get('house_eggs') or {}).get(name) or {}
    state.update(general=name, pending=pending, source=None, purchased=False, purchase=None,
                 returning=False)
    state.pop('return_goal', None)
    if info.get('location') == 'castle':
        owned = p._owned(mem) & p.chart.castles(mem['chapter']).keys()
        known = [c for c in sorted(owned) if name in (mem.get('garrison') or {}).get(c, [])]
        state['castles'] = known + [c for c in sorted(owned) if c not in known]
        _phase(state, 'find_castle')
    else:
        _phase(state, 'find_field')


def step(screen, mem, frame):
    """Actions, or None to let battle/month/ordinary map policy run.

    A repair owns only its bounded menu transaction. Battles and monthly
    events retain their existing handlers; a changed chapter drops its route.
    """
    from . import hanjuku_policy as p
    state = mem.get('house')
    if state and screen.kind == 'name_entry':
        _finish(mem)
        return None  # let the caller initialize the new game immediately
    if state and mem.get('recall'):
        _record(mem, 'aborted', reason='既存の緊急帰還を優先し、卵修理の移動を中断')
        _finish(mem)
        return None
    if state is None:
        tick = int(mem.get('tick') or 0)
        if (screen.kind != 'map' or mem.get('chapter') not in HOUSE_VIEW
                or any(mem.get(k) for k in ('active', 'recall', 'y_jump', 'sortie_attempt', 'month_sub', 'battle'))
                or tick - int(mem.get('house_scan_tick', 0)) < SCAN_INTERVAL
                or ('house_scan_month' in mem and mem['house_scan_month'] == mem.get('month'))):
            return None
        state = mem['house'] = {'phase': 'open_roster', 'age': 0, 'total': 0,
                                'chapter': mem['chapter'], 'seen': [], 'pending': []}
        _record(mem, 'scan_started', reason='月ごとに全将軍の卵を確認する')
        return [p.pad('x')]
    if state.get('chapter') != mem.get('chapter'):
        _finish(mem)
        return None
    phase = state['phase']
    state['total'] += 1
    if mem.get('battle') and screen.kind == 'map':
        return []  # battle_end needs two map observations; do not start an unrelated sortie between them
    # Repair must never consume unrelated battle commands or monthly menus.
    if (mem.get('battle') or screen.kind in {'battle', 'battle_menu', 'egg_battle_menu',
            'monster_menu', 'okunote_menu', 'attack_started', 'defense_started', 'boss_attack_started',
            'month_menu', 'shop_quantity', 'shop_quantity_prompt', 'shop_exit_confirm', 'discharge_menu'}
            or mem.get('month_sub')):
        if phase not in ('travel', 'close'):
            state['interrupted'] = True
        return None
    if state.pop('interrupted', False):
        return _exit(mem, '割り込みでメニューの同一性を失ったため修理操作を中断')
    state['age'] += 1
    if state['total'] > SESSION_LIMIT or state['age'] > (TRAVEL_LIMIT if phase == 'travel' else STEP_LIMIT):
        if phase == 'close':
            _record(mem, 'aborted', reason='閉じる操作も上限に達したため入力を解放')
            _finish(mem)
            return None
        return _exit(mem, '卵修理の観測回数上限に達したため入力を中止')
    if phase == 'close':
        if screen.kind == 'map':
            _finish(mem)
            return []
        return [p.pad('y' if screen.kind == 'world_map' else 'b')]
    if phase == 'open_roster':
        if screen.kind == 'main_menu' and screen.hand:
            if not affordable_gift((screen.header or {}).get('gold'), p.WAGE_RESERVE):
                return _exit(mem, '賃金を残して贈り物を買う資金がないため次月に確認')
            actions = _choose(screen, 'しょうぐん')
            if actions == [p.pad('a')]:
                _phase(state, 'roster')
            return actions
        return []
    if phase == 'roster':
        names = roster(screen)
        if names is None:
            return []
        selected = screen.selected
        if selected not in names:
            return []
        if selected in state['seen'] or len(state['seen']) >= ROSTER_LIMIT:
            _phase(state, 'leave_roster')
            return [p.pad('b')]
        state['selected'] = selected
        _phase(state, 'status')
        return [p.pad('a')]
    if phase == 'status':
        info = _observe_status(screen, mem)
        if not info or info['general'] != state.get('selected'):
            return []
        name = info['general']
        state['seen'].append(name)
        if info['broken']:
            state['pending'].append(name)
        _phase(state, 'roster_next')
        return [p.pad('b')]
    if phase == 'roster_next':
        if roster(screen) is None:
            return []
        _phase(state, 'roster_advance')
        return [p.pad('down')]
    if phase == 'roster_advance':
        names = roster(screen)
        if names is None:
            return []
        if screen.selected != state['selected']:
            _phase(state, 'roster')
            return []
        if state['age'] in (3, 6):
            return [p.pad('down')]
        if state['age'] >= 9:
            _phase(state, 'leave_roster')
            return [p.pad('b')]
        return []
    if phase == 'leave_roster':
        if screen.kind == 'map':
            _record(mem, 'scan_complete', observed_metric={'generals': list(state['seen']), 'broken': list(state['pending'])},
                    reason='一覧で確認した将軍の卵状態から修理対象を決定')
            _next_general(mem)
            return []
        return [p.pad('b')]
    return _repair_step(screen, mem, frame)


def _repair_step(screen, mem, frame):
    from . import hanjuku_policy as p
    state = mem['house']
    phase, name = state['phase'], state['general']
    if phase == 'find_castle':
        if screen.kind != 'map':
            return [p.pad('b')]
        castles = state.get('castles') or []
        if not castles:
            _record(mem, 'general_deferred', reason='修理対象の在城を確認できないため次の将軍へ進む')
            _next_general(mem)
            return []
        state['source'] = castles.pop(0)
        if state['source'] not in p._owned(mem):
            return []
        _phase(state, 'castle_view')
        return [p.pad('y')]
    if phase == 'castle_view':
        # The ring jitters over a flag. Use the existing castle-jump tolerance;
        # roof alignment and the castle's written name still verify the source.
        return _view_move(screen, mem, frame, _castle_view(mem, state['source']), 'castle_open', p.Y_JUMP_TOL)
    if phase == 'castle_open':
        if screen.kind == 'map':
            aligned = p._align_on_roof(screen, mem, frame, {'own'}, 'HOUSE:source')
            if aligned == 'on':
                _phase(state, 'castle_verify')
                return [p.pad('a')]
            return aligned or []
        return []
    if phase == 'castle_verify':
        match = p.CASTLE_STATUS.search(screen.text)
        if match:
            source = state['source']
            if match[1] not in (source, p.STATUS_NAMES.get(source)):
                _phase(state, 'find_castle')
                return [p.pad('b')]
            count = re.search(r'しょうぐん(\d+)めい', screen.text)
            names = _names_at(screen, 144, 95, 159)
            if not count or len(names) != int(count[1]) or any(UNKNOWN in g for g in names):
                return _exit(mem, '出撃元の在城人数を完全に読めないため派遣しない')
            mem.setdefault('garrison', {})[source] = names
            if name not in names:
                _phase(state, 'find_castle')
            elif len(names) < 2:
                _record(mem, 'general_deferred', reason='修理派遣で城が無人になるため守備将軍を残す')
                _next_general(mem)
            else:
                _phase(state, 'castle_sortie')
            return [p.pad('b')]
        if screen.kind == 'castle_menu':
            return _choose(screen, 'ステータス')
        if screen.kind == 'text' and p.is_camp_menu(screen):
            _phase(state, 'find_castle')
            return [p.pad('b')]
        return []
    if phase == 'castle_sortie':
        if screen.kind == 'castle_menu':
            actions = _choose(screen, 'しゅつげき')
            if actions == [p.pad('a')]:
                _phase(state, 'castle_pick')
            return actions
        return []
    if phase == 'castle_pick':
        if screen.kind != 'general_list':
            return []
        present = p._present_generals(screen)
        if present is None:
            return []
        if len(present) < 2 or name not in present or state['source'] not in p._owned(mem):
            return _exit(mem, '出撃直前の在城・守備条件を満たさないため派遣しない')
        actions = _choose(screen, name)
        if actions == [p.pad('a')]:
            _phase(state, 'castle_kit')
        return actions
    if phase == 'castle_kit':
        if screen.kind != 'card_select':
            return []
        info = general_status(screen)
        if info is None:
            return []
        if info['general'] != name or not info['broken']:
            return _exit(mem, '出撃画面で対象本人の卵割れを確認できないため派遣しない')
        _phase(state, 'castle_confirm')
        return [p.pad('b')]  # no battle cards are spent on a repair trip
    if phase == 'castle_confirm':
        if screen.kind != 'sortie_confirm':
            return []
        info = general_status(screen)
        if (not info or info['general'] != name or not info['broken']
                or not p._empty_sortie_inventory(screen)):
            return []
        if not affordable_gift(mem.get('gold'), p.WAGE_RESERVE):
            return _exit(mem, '派遣前に修理資金が不足したため出撃を取り消す')
        actions = _choose(screen, 'うむッ!')
        if actions == [p.pad('a')]:
            _phase(state, 'out_target')
        return actions
    if phase == 'out_target':
        if screen.kind == 'map_target':
            _phase(state, 'house_view')
            return [p.pad('y')]
        return []
    if phase == 'house_view':
        return _view_move(screen, mem, frame, HOUSE_TARGET[mem['chapter']], 'house_confirm')
    if phase == 'house_confirm':
        if screen.kind == 'map_target':
            _phase(state, 'travel', departure_pending=True)
            _record(mem, 'dispatch_requested', source=state.get('source'),
                    observed_metric={'view': HOUSE_TARGET[mem['chapter']]},
                    reason='卵が壊れた本人をあたしの家へ派遣する入力を確定')
            return [p.pad('a')]
        return []
    if phase in ('find_field', 'field_roster_open', 'field_pick', 'field_open', 'field_status', 'field_read', 'unit_move'):
        return _field_step(screen, mem, frame)
    if phase == 'travel':
        if screen.kind == 'map':
            if state.pop('departure_pending', False):
                p._garrison_move(mem, name, source=state.get('source'))
                for sortie in (mem.get('sorties') or {}).values():
                    if sortie.get('general') == name and sortie.get('status') in ('en_route', 'launched_unconfirmed'):
                        sortie['status'] = 'redirected_house'
                tick = int(mem.get('tick') or 0)
                mem.setdefault('sorties', {})[f'HOUSE_OUT:{tick}'] = {
                    'general': name, 'target': None, 'status': 'en_route', 'purpose': 'house', 'tick': tick}
                _record(mem, 'departed', reason='移動先選択からフィールドへ戻ったことを確認')
            return []
        discovery = re.search(r'([^\ufffd\s]+)しょうぐんがあたし.*いえを', screen.text)
        if discovery and discovery[1] != name:
            return _exit(mem, '家を訪れた将軍が派遣対象と異なるため購入しない')
        if gift_prices(screen):
            gold = (screen.header or {}).get('gold')
            gift = affordable_gift(gold, p.WAGE_RESERVE)
            if not gift:
                _record(mem, 'purchase_skipped', reason='到着時の所持金では賃金を残せないため購入しない')
                _phase(state, 'decline')
                return []
            state['purchase'] = {'item': gift[0], 'cost': gift[1], 'gold_before': gold,
                                 'month': mem.get('month')}
            _phase(state, 'buy')
            return []
        if screen.kind == 'text' and any(s in screen.text for s in ('あたし', 'たまご', 'かって')):
            return [p.pad('a')]
        return None
    if phase == 'buy':
        if not gift_prices(screen):
            return []
        buy = state['purchase']
        gold = (screen.header or {}).get('gold')
        if type(gold) is not int or gold < buy['cost'] + p.WAGE_RESERVE:
            return _exit(mem, '購入直前の所持金を確認できないため購入しない')
        move = p.menu_to(screen, buy['item'], exact=False)
        if move == 'here':
            buy['gold_before'] = gold
            _phase(state, 'receipt')
            _record(mem, 'purchase_requested', observed_metric=dict(buy),
                    reason='実表示の価格と所持金を確認して贈り物を一度だけ選択')
            return [p.pad('a')]
        return [move] if move else []
    if phase == 'receipt':
        buy = state['purchase']
        gold = (screen.header or {}).get('gold')
        if type(gold) is int and gold == buy['gold_before'] - buy['cost'] and mem.get('month') == buy['month']:
            buy['gold_after'] = gold
        if 'おかねがたりません' in screen.text:
            return _exit(mem, '所持金不足の実表示を確認したため購入失敗として扱う')
        if screen.kind == 'map':
            _phase(state, 'verify_open')
            return [p.pad('x')]
        if gift_prices(screen):
            return []  # the old list may persist during a fade; never buy twice
        if screen.kind == 'text':
            return [p.pad('a')]
        return None
    if phase == 'decline':
        gold = (screen.header or {}).get('gold')
        if gift_prices(screen) and type(gold) is int and gold < 200:
            # This forced gift list ignores B. An unaffordable choice closes
            # it with the measured おかねがたりません message, without spending.
            move = p.menu_to(screen, 'スカーフ', exact=False)
            if move == 'here':
                _phase(state, 'decline_done')
                return [p.pad('a')]
            return [move] if move else []
        return []
    if phase == 'decline_done':
        if screen.kind == 'map':
            _phase(state, 'find_field', returning=True)
            return []
        if screen.kind == 'text':
            return [p.pad('a')]
        return []
    if phase == 'verify_open':
        if screen.kind == 'main_menu' and screen.hand:
            actions = _choose(screen, 'しょうぐん')
            if actions == [p.pad('a')]:
                _phase(state, 'verify_pick', scrolls=0)
            return actions
        return []
    if phase == 'verify_pick':
        names = roster(screen)
        if names is None:
            return []
        if name in names:
            actions = _choose(screen, name)
            if actions == [p.pad('a')]:
                _phase(state, 'verify_status')
            return actions
        if state['scrolls'] >= ROSTER_LIMIT:
            return _exit(mem, '購入後の本人を一覧で確認できないため成功扱いしない')
        state['scrolls'] += 1
        state['age'] = 0
        return [p.pad('down')]
    if phase == 'verify_status':
        info = general_status(screen)
        if not info or info['general'] != name:
            return []
        buy = state['purchase']
        gold = (screen.header or {}).get('gold')
        if type(gold) is int and gold == buy['gold_before'] - buy['cost'] and mem.get('month') == buy['month']:
            buy['gold_after'] = gold
        repaired = not info['broken'] and info['uses'] is not None and 'gold_after' in buy
        state['purchased'] = repaired
        _observe_status(screen, mem)
        _record(mem, 'repair_verified' if repaired else 'repair_unconfirmed',
                observed_metric={**buy, 'status': info},
                reason='購入後の本人の卵表示と支払額を照合した結果')
        _phase(state, 'return_close', returning=True)
        return [p.pad('b')]
    if phase == 'return_close':
        if screen.kind == 'map':
            _phase(state, 'find_field')
            return []
        return [p.pad('b')]
    if phase in ('return_view', 'return_confirm'):
        return _return_step(screen, mem, frame)
    if phase == 'return_done':
        if screen.kind == 'map':
            tick = int(mem.get('tick') or 0)
            for sortie in (mem.get('sorties') or {}).values():
                if sortie.get('general') == name and sortie.get('purpose') == 'house':
                    sortie['status'] = 'recalled'
            mem.setdefault('sorties', {})[f'HOUSE:{tick}'] = {
                'general': name, 'target': state['return_goal'], 'status': 'en_route',
                'purpose': 'move', 'tick': tick}
            _next_general(mem)
        return []
    return []


def _field_step(screen, mem, frame):
    from . import hanjuku_policy as p
    state = mem['house']
    phase, name = state['phase'], state['general']
    if phase == 'find_field':
        if screen.kind != 'map':
            return [p.pad('b')]
        if name == p.NAME:
            _phase(state, 'field_open')
            return [p.pad('select')]
        _phase(state, 'field_roster_open')
        return [p.pad('x')]
    if phase == 'field_roster_open':
        if screen.kind == 'main_menu' and screen.hand:
            actions = _choose(screen, 'しょうぐん')
            if actions == [p.pad('a')]:
                _phase(state, 'field_pick', scrolls=0)
            return actions
        return []
    if phase == 'field_pick':
        names = roster(screen)
        if names is None:
            return []
        if name not in names:
            if state['scrolls'] >= ROSTER_LIMIT:
                return _exit(mem, '対象本人を一覧で確認できないため位置選択を中止')
            state['scrolls'] += 1
            state['age'] = 0
            return [p.pad('down')]
        move = p.menu_to(screen, name)
        if move == 'here':
            # In the main roster SELECT jumps directly to the selected
            # general. Field SELECT alone would always focus the hero.
            _phase(state, 'field_open')
            return [p.pad('select')]
        return [move] if move else []
    if phase == 'field_open':
        if screen.kind == 'map':
            _phase(state, 'field_status')
            return [p.pad('a')]
        return []
    if phase == 'field_status':
        if p.is_camp_menu(screen):
            actions = _choose(screen, 'ステータス')
            if actions == [p.pad('a')]:
                _phase(state, 'field_read')
            return actions
        if screen.kind in ('castle_menu', 'castle_info') or (screen.kind == 'map' and state['age'] > 4):
            return _exit(mem, '本人の部隊メニューを確認できないため派遣を保留')
        return []
    if phase == 'field_read':
        info = general_status(screen)
        if info is None:
            return []
        if info['general'] != name or (not state.get('returning') and not info['broken']):
            return _exit(mem, '本人の卵割れまたは帰還対象を確認できないため操作を中止')
        _phase(state, 'unit_move')
        return [p.pad('b')]
    if phase == 'unit_move':
        if not p.is_camp_menu(screen):
            return []
        label = 'きかん' if state.get('returning') else 'いどう'
        actions = _choose(screen, label)
        if actions == [p.pad('a')]:
            _phase(state, 'return_view' if state.get('returning') else 'out_target')
        return actions
    return []


def _return_step(screen, mem, frame):
    from . import hanjuku_policy as p
    state = mem['house']
    if screen.kind == 'world_map':
        cursor = p.world_cursor(frame)
        if cursor is None:
            return []
        # きかん uses a castle picker with an R ring, not the movable Y/G
        # cursor. Its blue/white ring cannot create an own red flag, which
        # remains readable underneath it (chapter 2 isolated measurement).
        flags = p.world_flags(frame, mem['chapter'])
        ox, oy = p.WORLD_MAP_OFFSET[mem['chapter']]
        nearby = [c for c, (x, y) in p.chart.castles(mem['chapter']).items()
                  if abs(x / 8 + ox - cursor[0]) <= 6 and abs(y / 8 + oy - cursor[1]) <= 6]
        if len(nearby) == 1 and flags.get(nearby[0]) == 'own':
            state['return_goal'] = nearby[0]
            if state['phase'] == 'return_view':
                _phase(state, 'return_confirm')
            else:
                _return_requested(mem)
            return [p.pad('a')]
        if state['phase'] == 'return_confirm':
            return _exit(mem, '帰還確定前に自軍旗と選択城の一致を確認できないため中止')
        return [p.pad('right')]  # bounded by STEP_LIMIT; never approve an enemy flag
    if state['phase'] != 'return_confirm':
        return []
    if screen.kind == 'map_target':
        goal = state['return_goal']
        aligned = p._align_on_roof(screen, mem, frame, {'own'}, 'HOUSE:return')
        if aligned == 'on':
            _return_requested(mem)
            return [p.pad('a')]
        return aligned or []
    if screen.kind == 'map':
        _return_requested(mem)
    return []


def _return_requested(mem):
    state = mem['house']
    _record(mem, 'return_requested', target=state['return_goal'],
            reason='本人の部隊メニューから自軍城への帰還を指示（到着は未確認）')
    _phase(state, 'return_done')
