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

from ...hanjuku_egg_reference import GENERAL_HP, GENERAL_EGGS
# The party-screen readers live in the OS layer; re-exported for existing callers.
from ..os_layer.starter_view import NAME_LEFT, NAME_RIGHT, UPPER_ROW_Y, kind, rows  # noqa: F401

MAX_PICKS = 3
MAX_RETRIES = 3
NAV_WAIT = 6
NAV_PRESS_LIMIT = 9
# The chapter-end save and the advance party were 2-3 observations apart in
# both measured transitions; 600 observations (~15 min at 1.5 s) bounds a
# marker that never gets its party screen.
CHAPTER_END_SAVE_WINDOW = 600


def _record(mem, decision, **kwargs):
    from ..decision_log import _record as record
    record(mem, decision, chart_step='starter', **kwargs)


def _chapter_boss_finished(mem, chapter):
    """True when this chapter's boss is the battle in memory or already beaten."""
    from ... import hanjuku_chart as chart
    boss = chart.BOSSES.get(chapter)
    battle = mem.get('battle')
    if isinstance(battle, dict) and boss and battle.get('enemy') == boss:
        return True
    if mem.get('boss_defeated') == chapter:
        return True
    return chart.boss_castle(chapter) in (mem.get('captured') or ())


def _enter_next_chapter_after_save(mem):
    """Advance on the measured chapter-end save + advance-party pair.

    g1153 (2026-11-07) beat クイーン, answered the save prompt
    「セーブをしゅうりょうして…」 at 22:48:34 and got the advance party at
    22:48:37, but kept chapter 1 until the next month's HUD「2わ」at 22:56:20.
    The party selection therefore ran with chapter-1 coordinates and the owner
    saw the game sitting in 第2話 while the bot still thought 第1話 (report
    22:58). The same pair precedes chapter 3 in g1059 (2026-10-28 05:21:42 →
    05:21:45), where the にせヒーロー fight was still the battle memory.

    Fail-closed: only the measured prompt arms the marker (prompts.py), only
    this party screen consumes it, and only while the chapter's boss is the
    battle in memory / already finished. A refused save or a game-over
    restart party therefore cannot advance anything.
    """
    armed = mem.get('chapter_end_save')
    chapter = mem.get('chapter')
    if not isinstance(armed, dict):
        return
    if type(chapter) is not int:
        mem.pop('chapter_end_save', None)
        return
    tick, now = armed.get('tick'), mem.get('tick')
    if (armed.get('chapter') != chapter or not _chapter_boss_finished(mem, chapter)
            or type(tick) is not int or type(now) is not int
            or not 0 <= now - tick <= CHAPTER_END_SAVE_WINDOW):
        mem.pop('chapter_end_save', None)
        return
    mem.pop('chapter_end_save', None)
    from ..strategy.campaign import _enter_chapter
    _enter_chapter(mem, chapter + 1, evidence='chapter_end_save_party',
                   reason='章末セーブの確認直後に先発隊の選択が始まったため次章を確定し、前章の座標・出撃・購入状態を初期化')


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
    from ..os_layer.commands import menu_to, pad
    from ..os_layer.screen import find_hand
    seen = kind(screen)
    if seen is not None:
        _enter_next_chapter_after_save(mem)
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
        move = menu_to(screen, label)
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
        return [pad('a')]
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
    screen.hand = find_hand(frame, option_positions=[(NAME_LEFT, y) for y in UPPER_ROW_Y])
    if screen.hand is None:
        return []
    # Limit navigation to this upper pane: the same selected name below must
    # never be treated as an available row or toggled back out.
    from dataclasses import replace
    upper_screen = replace(screen, lines=[l for l in screen.lines if 32 <= l.y < 112])
    move = menu_to(upper_screen, target[2])
    if move is None:
        return []
    if move != 'here':
        return _navigation(mem, episode, move, target[2], screen.hand, selected)
    episode.pop('nav', None)
    episode['pending'] = target[2]
    episode['attempts'][target[2]] = episode['attempts'].get(target[2], 0) + 1
    _record(mem, 'starter_select', general=target[2], observed_metric={'selected': selected},
            reason='上側の実一覧で先発将軍を選び、次の画面で下側への追加を確認する')
    return [pad('a')]
