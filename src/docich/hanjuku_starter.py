"""Chapter advance party: select on the upper list, verify the lower list.

SFC manual pp. 10-11/27 and the unedited SFC play video
https://www.youtube.com/watch?v=RMj5tDn7Ek8&t=1972s (32:52-32:57):
A adds a general to the lower panel; the third opens よろしいですか？.
This is a separate foreground transaction, never a merchant/event B fallback.
The g684 native frame measures names at x=48..104 and the pane's scrolling
arrow at x=112. Full selection/departure still needs live acceptance.
No planned key press creates a garrison/arrival receipt.
"""
from __future__ import annotations

from .hanjuku_egg_reference import GENERAL_HP, GENERAL_EGGS
from .hanjuku_font import UNKNOWN

MAX_PICKS = 3
MAX_RETRIES = 3
NAV_WAIT = 6
NAV_PRESS_LIMIT = 9
# Seven-glyph names fit here. x=112 is the panel's scroll arrow/border,
# measured in g684-starter-empty.png, never part of a general's name.
NAME_LEFT = 48
NAME_RIGHT = 112


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


def _record(mem, decision, **kwargs):
    from .hanjuku_policy import _record as record
    record(mem, decision, chart_step='starter', **kwargs)


def _navigation(mem, episode, move, label, hand, selected):
    signature = [label, list(hand)]
    nav = episode.get('nav') or {}
    if nav.get('signature') != signature:
        nav = {'signature': signature, 'presses': 0, 'burst': 0, 'waits': 0}
    episode['nav'] = nav
    if nav['presses'] >= NAV_PRESS_LIMIT:
        _record(mem, 'starter_navigation_stalled', observed_metric={'selected': selected},
                reason='先発隊カーソルが動かないため入力を保留して再観測する')
        return []
    # Intro animation may ignore initial arrows. Retry only arrows after a
    # short wait, within a total budget; never cancel the party with B.
    if nav['burst'] >= MAX_RETRIES:
        nav['waits'] += 1
        if nav['waits'] <= NAV_WAIT:
            return []
        nav.update(burst=0, waits=0)
    nav['presses'] += 1
    nav['burst'] += 1
    _record(mem, 'starter_navigation', observed_metric={'selected': selected},
            reason='実先発隊カーソルから選択・不足拒否の項目へ移動する')
    return [move]


def step(screen, mem, frame):
    from . import hanjuku_policy as p
    from .hanjuku_screen import find_hand
    seen = kind(screen)
    episode = mem.get('starter_party')
    if episode and (episode.get('run_identity') != mem.get('_run_identity')
                    or episode.get('chapter') != mem.get('chapter')):
        mem.pop('starter_party', None)
        episode = None
    if seen is None:
        if (episode and not episode.get('confirm_sent')
                and screen.kind in {'text', 'unknown', 'yes_no', 'shop_list'}):
            _record(mem, 'starter_unread', reason='先発隊の選択途中の未読画面を再観測し、既定入力で出発しない')
            return []
        if episode and episode.get('confirm_sent') and screen.kind not in {'unknown', 'transition'}:
            _record(mem, 'starter_departed_observed',
                    observed_metric={'selected': episode.get('selected'), 'screen': screen.kind},
                    reason='先発隊の確定後に別画面へ遷移したことを確認。次章の在城・到着は別途実測する')
            mem.pop('starter_party', None)
        return None
    identity = mem.get('_run_identity')
    if not episode or episode.get('run_identity') != identity or episode.get('chapter') != mem.get('chapter'):
        episode = mem['starter_party'] = {'run_identity': identity, 'chapter': mem.get('chapter'),
                                        'selected': [], 'attempts': {}, 'waits': 0}
    lower = rows(screen, selected=True)
    upper = rows(screen)
    selected = [w for _, _, w in lower] if lower is not None else None
    if selected is not None:
        if episode.get('selected') != selected:
            _record(mem, 'starter_selected_observed', observed_metric={'selected': selected},
                    reason='下側の実先発隊欄から選択済みの将軍を確認する')
        episode['selected'] = selected
    if seen == 'starter_confirm':
        label = 'うむッ!' if selected is not None and len(selected) == MAX_PICKS else 'いかんッ!'
        positions = [(x, line.y) for line in screen.lines for x, w in line.spans()
                     if w in ('うむッ!', 'いかんッ!') and line.y >= 176]
        if len(positions) != 2:
            return []
        screen.hand = find_hand(frame, option_positions=positions)
        move = p.menu_to(screen, label)
        if move is None:
            return []
        if move != 'here':
            return _navigation(mem, episode, move, label, screen.hand, selected)
        episode.pop('nav', None)
        episode['confirm_sent'] = label == 'うむッ!'
        _record(mem, 'starter_confirm' if episode['confirm_sent'] else 'starter_underfilled_rejected',
                observed_metric={'selected': selected}, expected_metric={'count': MAX_PICKS},
                reason='実先発隊3人を確認して出発を確定する' if episode['confirm_sent']
                       else '先発隊3人を確認できないため選択画面に戻る')
        return [p.pad('a')]
    if lower is None or upper is None:
        _record(mem, 'starter_unread', reason='先発隊の実名・人数を読み直し、未読のまま章を進めない')
        return []
    if len(selected) == MAX_PICKS:
        return []  # the game itself opens its final confirmation
    choices = [r for r in upper if r[2] not in selected]
    pending = episode.get('pending')
    if pending:
        if pending in selected:
            episode.pop('pending', None)
            episode['waits'] = 0
        else:
            episode['waits'] += 1
            if episode['waits'] < 3:
                return []
            episode.pop('pending', None)
            episode['waits'] = 0
    choices = [r for r in choices if episode['attempts'].get(r[2], 0) < MAX_RETRIES]
    if not choices:
        _record(mem, 'starter_unread', observed_metric={'selected': selected},
                reason='選択可能な実将軍を確認できないため0人・不足人数での出発を保留する')
        return []
    # Prefer an egg-bearing, durable general among actually visible names.
    # Do not pretend that a static egg capability proves a repaired live egg.
    target = max(choices, key=lambda r: (GENERAL_EGGS.get(r[2], (False, 0))[0], GENERAL_HP[r[2]], -r[1]))
    screen.hand = find_hand(frame, option_positions=[(x, y) for x, y, _ in upper])
    if screen.hand is None:
        return []
    # Limit navigation to this upper pane: the same selected name below must
    # never be treated as an available row or toggled back out.
    from dataclasses import replace
    upper_screen = replace(screen, lines=[l for l in screen.lines if 32 <= l.y < 112])
    move = p.menu_to(upper_screen, target[2])
    if move is None:
        return []
    if move != 'here':
        return _navigation(mem, episode, move, target[2], screen.hand, selected)
    episode.pop('nav', None)
    episode['pending'] = target[2]
    episode['attempts'][target[2]] = episode['attempts'].get(target[2], 0) + 1
    _record(mem, 'starter_select', general=target[2], observed_metric={'selected': selected},
            reason='上側の実一覧で先発将軍を選び、次の画面で下側への追加を確認する')
    return [p.pad('a')]
