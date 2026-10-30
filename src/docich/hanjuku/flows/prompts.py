"""手順層: event prompts (trade offers, yes/no, summer bonus, gifts).

Also observes chapter events from free text. Event-level judgments live in
``judgment.events``.
"""
from __future__ import annotations

import random
import re

from ..os_layer.screen import HEADER as HEADER_RE, SUMMER_BONUS_CHOICES, Screen
from ..decision_log import _record
from .shop import _observe_soldier_receipt
from ..judgment.battle import _hold_general_loss_metric, _migrate_card_evidence
from ..judgment.events import trade_accept_weak
from ..os_layer.font import UNKNOWN
from ..os_layer.commands import menu_to, pad
from ..os_layer.readers import (CASTLE_STATUS, STATUS_NAMES, _current, _egg_row, _is_bijin_request,
                                _is_general_trade_prompt, _is_goninja_prompt, _options,
                                _summer_bonus_rows, _trade_named_general)
from ..os_layer.world import _owned
from ..strategy.campaign import (_enter_chapter, _repair_home_alias_failures, _repair_home_name_chapter,
                                 observe_chapter_castle)
from ..strategy.economy import GONINJA_MIN_GOLD, _goninja_budget, _month_key


TRADE_GUARD_OBSERVATIONS = 40   # ~60 s: lead-in, the offer and its choice
# Arming needs trade-only wording; 'しょうぐんどうし' can be the hero's name.
TRADE_GUARD_TOKENS = ('トレード', 'いちもんめ', 'イキのいいの', 'いきのいいの')


TRADE_CHOICE_PAIRS = (('OK', 'NO'), ('そうする', 'やめる'))


BIJIN_MIN_GOLD = 1000   # 以上なら家を選ぶ（オーナー指示 2026-10-21）
# オーナー指示（issue #3011）: 半熟レベル14以上では半熟値を稼ぐ行為を控える。
# ボットが意図的に半熟値を稼ぐ唯一の行為は美女の「いえ」購入（+100）であり、
# 白兵・召喚・月イチの受動イベント・剣の稽古は稼ぎ目的の選択肢ではない。
HANJUKU_LEVEL_CAP = 14
BIJIN_GUARD_OBSERVATIONS = 40   # ~60 s: her speech and its choice
# A held request must never outlive the corner's 300 s unchanged-screen watchdog
# (retro_corner end_reason='screen_stalled', 2026-10-21): after ~60 s without a
# readable choice, decline by the measured row order (upper accept / lower
# refuse). The purchase menu has no refusal row, so its own bound ends the event
# with the cheapest non-house item (at most 100G) instead of stalling the run.
BIJIN_GUARD_HOLD_MAX = 40
BIJIN_GIFT_HOLD_MAX = 40
# 実機の断り行は標準の いかんッ! とは限らない（倒転王国 monthlyevent.html の
# うんデヘヘ！/けえんな！）。かな書きの揺れ（けーんな）も候補に含める。
BIJIN_DECLINE_TOKENS = ('いかんッ', 'けえんな', 'けーんな')
# このイベントだけが使う固有の選択表記。依頼文の読み取りが欠けても、この選択行が
# 選択肢帯に読めれば美女の依頼だと確定できる（月イチ画面の所有判定に使う）。
BIJIN_NATIVE_ACCEPT_TOKENS = ('うんデヘヘ',)
BIJIN_NATIVE_TOKENS = BIJIN_NATIVE_ACCEPT_TOKENS + ('けえんな', 'けーんな')

# 下部の会話パネル（g684/g745 で実測）: 本文の行は 135/151/167、選択肢帯は 176〜208
# （_trade_choice_pair と同じ帯）。本文の内側列だけを読み、パネル外枠のタイルは除く。
PROSE_ROWS = range(135, 176)
PROSE_COLUMNS = (24, 232)


def readable_prose_screen(screen: Screen) -> bool:
    """「完全に読める別の頁」を、生のグリフセルから判定する。

    trade_guard / bijin_guard は「依頼文が無い・選択肢帯が無い・手が無い」という
    証拠の*不在*を保留の解除に使う。その不在の証拠を ``screen.text`` に求めると
    足りない: ``screen.text`` は ``joined()`` が各行の両端の ``UNKNOWN`` を
    ``strip`` して作るため、行頭・行末の欠字や全UNKNOWN行が消え、所持金ヘッダだけの
    頁でも「読める本文」に見える（PR #2325 レビューのP1）。本文帯の生セルを読み、
    実際に読める本文が1つ以上あり、かつ部分的にしか読めなかった行が無い時だけ真を返す。

    帯に本文が無い（所持金ヘッダだけ）頁と、全部が未読の頁はどちらも偽になる。
    外枠は本文の内側列に含めない。濁点等はparseが原画像で付随markと確認したセル
    だけを除き、内側の全UNKNOWN行も終了を示す証拠にはしない。
    """
    if screen.kind != 'text' or screen.hand is not None:
        return False
    x0, x1 = PROSE_COLUMNS
    if any(176 <= line.y <= 208 and any(x0 <= x <= x1 for x, _ in line.cells)
           for line in screen.lines):
        return False
    readable = False
    for line in screen.lines:
        if line.y not in PROSE_ROWS:
            continue
        cells = [ch for x, ch in line.cells if x0 <= x < x1
                 and (x, line.y) not in screen.prose_attached_marks]
        known = [ch for ch in cells if ch != UNKNOWN and ch != ' ']
        if UNKNOWN in cells:
            return False   # 行頭・行末の欠字も全未読本文行も終了証拠にしない
        if known:
            readable = True
    return readable


def _trade_choice_pair(options):
    band = [(x, y, word) for x, y, word in options if 176 <= y <= 208]
    pairs = []
    for yes, no in TRADE_CHOICE_PAIRS:
        accepts = [row for row in band if row[2] == yes]
        declines = [row for row in band if row[2] == no]
        if len(accepts) != 1 or len(declines) != 1:
            continue
        accept, decline = accepts[0], declines[0]
        if accept[0] != decline[0] or abs(accept[1] - decline[1]) != 16:
            continue
        if len(band) == 2:
            pairs.append((accept, decline))
    return pairs[0] if len(pairs) == 1 else None



def trade_guard_step(screen: Screen, mem, *, phase=None):
    """Fail closed on a 花いちもんめ offer whose choice is not a measured yes/no.

    g684 1-10 13:05: 「ここで しょうぐんどうしの トレードを しようじゃないか。」
    was read as plain text, the choice that followed never matched the
    うむッ/いかんッ yes/no, every frame got the generic A, and ヴィーナス (82,
    スーパーエッグ) was traded for シェーブル (27). Owner (2026-10-11): the
    trade swaps strong generals for weak ones; refuse when a strong general
    is named (always declined here, as before).

    Only an explicit, complete label pair in the bottom choice band permits
    selecting. Its hand must match one of those exact rows. Readable trade
    dialogue advances with A before any choice is seen; incomplete labels,
    missing hands and unknown wording hold for measurement.
    """
    guard = mem.get('trade_guard')
    if isinstance(guard, dict):
        guard.pop('held', None)  # ownership is recomputed for this observation
    if mem.get('month_sub') or phase not in (None, 'dialogue'):
        mem.pop('trade_guard', None)
        return None   # a monthly owner keeps its dialogue (measured yes/no still declines)
    tick = int(mem.get('tick') or 0)
    flat = ''.join(screen.text.split())
    if (any(tok in flat for tok in TRADE_GUARD_TOKENS)
            and screen.kind in ('text', 'unknown')):
        if not isinstance(guard, dict):
            guard = mem['trade_guard'] = {'tick': tick, 'chosen': False}
            _record(mem, 'trade_offer_seen', screen=screen.kind,
                    observed_metric={'text': screen.text[-80:], 'hand': screen.hand is not None},
                    reason='将軍トレード（花いちもんめ）の申し出を検出。選択肢は断る側を選ぶ')
        guard['tick'] = tick
    if isinstance(guard, dict) and screen.kind in ('text', 'unknown', 'yes_no'):
        named = _trade_named_general(screen.text)
        if named and named != guard.get('named'):
            guard['named'] = named
            guard['accept'], guard['strength'] = trade_accept_weak(mem, named)
    if not isinstance(guard, dict):
        return None
    if guard.get('chosen') or screen.kind in ('map', 'map_target', 'month_menu', 'battle', 'world_map'):
        mem.pop('trade_guard', None)
        return None
    # The lead-in lease expires as before. Once a choice is seen, time alone
    # is not evidence that it ended: a blinking hand or partial OCR must not
    # release the same pending dialogue to the generic A.
    if tick - int(guard.get('tick') or 0) > TRADE_GUARD_OBSERVATIONS and not guard.get('choice_seen'):
        mem.pop('trade_guard', None)
        return None
    if screen.kind == 'yes_no':
        return None   # the measured yes/no path declines the trade itself
    if screen.kind not in ('text', 'unknown'):
        return None
    options = _options(screen)
    pair = (None if any(ch == UNKNOWN for line in screen.lines
                      if 176 <= line.y <= 208
                      for x, ch in line.cells if PROSE_COLUMNS[0] <= x <= PROSE_COLUMNS[1])
            else _trade_choice_pair(options))
    labels = {word for words in TRADE_CHOICE_PAIRS for word in words}
    if (guard.get('choice_seen') and readable_prose_screen(screen)
            and not _trade_named_general(flat)
            and not any(tok in flat for tok in TRADE_GUARD_TOKENS)
            and not any(word in labels for _, _, word in options)):
        mem.pop('trade_guard', None)  # a readable different prose screen
        return None
    suspect_choice = (screen.hand is not None or pair is not None
                      or any(word in labels for _, _, word in options)
                      or any(176 <= y <= 208 and not (
                          _trade_named_general(word) or any(tok in word for tok in TRADE_GUARD_TOKENS))
                             for _, y, word in options))
    if suspect_choice:
        guard['choice_seen'] = True
    if (not guard.get('choice_seen') and screen.hand is None and screen.kind == 'text'
            and flat and UNKNOWN not in flat and any(tok in flat for tok in TRADE_GUARD_TOKENS)):
        return None   # positively readable trade prose, before a choice
    cur = _current(screen)
    if pair is None or cur not in pair or not 0 <= cur[0] - screen.hand[2] <= 8:
        guard['held'] = True
        if not guard.get('unread_choice_recorded'):
            guard['unread_choice_recorded'] = True
            _record(mem, 'situation_held', screen=screen.kind,
                    observed_metric={'trade_guard': True, 'text': screen.text[-80:]},
                    reason='トレードの二択・現在の選択行を確定できないため決定せず再観測')
        return []
    accept = guard.get('accept') is True
    target = pair[0] if accept else pair[1]
    if cur != target:
        return [pad('down' if target[1] > cur[1] else 'up')]
    guard['chosen'] = True
    _record(mem, 'prompt', choice=cur[2], prompt=screen.text[-60:],
            strategy_variant='accept_weak_general_trade' if accept else 'decline_general_trade',
            observed_metric={'rows': sorted(row[1] for row in pair), 'cursor_y': cur[1],
                             'strength': guard.get('strength')},
            reason=('指定された将軍が弱い（最大HP50未満・戦闘10未満、チャート外）ため花いちもんめを受ける'
                    if accept else
                    '花いちもんめで強い将軍・主人公・チャートの将軍・判定不能の将軍が指定されたため断る'))
    return [pad('a')]


def _level_capped(mem):
    """Observed 半熟レベル at or above the owner cap (#3011).

    Levels never decrease within a run, so the latest observation stays
    authoritative even a month later; an unobserved level never engages.
    """
    level = mem.get('hanjuku_level')
    return type(level) is int and level >= HANJUKU_LEVEL_CAP


def _bijin_gold(screen: Screen):
    gold = (screen.header or {}).get('gold')
    if type(gold) is not int or gold < 0:
        return None
    return gold


def _bijin_native_choice(screen: Screen) -> bool:
    """このイベント固有の選択行（うんデヘヘ/けえんな）が選択肢帯に読める。"""
    return any(176 <= y <= 208 and word.startswith(token)
               for _, y, word in _options(screen) for token in BIJIN_NATIVE_TOKENS)


def _bijin_choice_rows(screen: Screen):
    """The two measured choice rows, readable or not (upper accept / lower refuse).

    The glyphs can be lost to OCR while the rows and the hand stay visible; the
    raw cells still give the two y positions. Anything other than exactly two
    rows is not the measured request (e.g. the three-row purchase menu).
    """
    rows = sorted({line.y for line in screen.lines
                   if 176 <= line.y <= 208 and line.cells})
    return rows if len(rows) == 2 else None


def _bijin_decline_by_position(screen: Screen):
    """One press toward the measured refusal row; 'a' when already on it.

    Never guesses an accept: without a readable hand or the two measured rows
    this returns None and the caller keeps observing.
    """
    rows = _bijin_choice_rows(screen)
    if rows is None:
        return None
    target = rows[1]          # うんデヘヘ above / けえんな below (measured)
    cur = _current(screen)
    if cur is None or min(abs(cur[1] - y) for y in rows) > 2:
        return None
    if abs(cur[1] - target) <= 2:
        return 'a'
    return 'down' if target > cur[1] else 'up'


def _bijin_hold_fallback(screen: Screen, mem, guard, gold):
    """Bounded fail-safe for a held request: decline by the measured rows.

    The corner ends after 300 s without a screen change, so an unreadable choice
    may not hold forever. After ``BIJIN_GUARD_HOLD_MAX`` observations (~60 s),
    move to the lower (refusal) row and confirm it. A move is returned first so
    the next observation can confirm; no A is sent without a visible hand.
    """
    held = int(guard.get('held_ticks') or 0) + 1
    guard['held_ticks'] = held
    if held <= BIJIN_GUARD_HOLD_MAX:
        return None
    move = _bijin_decline_by_position(screen)
    if move is None:
        if not guard.get('bound_unactionable'):
            guard['bound_unactionable'] = True
            _record(mem, 'situation_held', screen=screen.kind,
                    observed_metric={'bijin_guard': True, 'gold': gold, 'held_observations': held},
                    reason='美女の保留上限に達したが、選択行と手を読めず位置でも断れない')
        return None
    if move == 'a':
        guard['chosen'] = True
        _record(mem, 'prompt', choice='けえんな（位置）', prompt=screen.text[-60:],
                strategy_variant='decline_bijin_position',
                observed_metric={'gold': gold, 'held_observations': held},
                reason='美女の選択行を読めないまま保留上限を超えたため、実測の行順（下=断り）で断る')
        return [pad('a')]
    return [pad(move)]


def bijin_guard_step(screen: Screen, mem, *, phase=None):
    """1000G以上なら依頼を受けて家へ進み、それ以外・読取不明は断る。

    選択肢と現在行を確認できない間は決定せず、既存の画面所有境界を保つ。
    実測の月イチイベント画面は紅幕の会場で ``phase='concert'`` になるため、
    依頼文か固有の選択行が見えている間は phase 境界より先に所有する。
    """
    if mem.get('bijin_gift_hold') is True:
        if (mem.get('month_sub') or screen.kind in (
                'map', 'map_target', 'world_map', 'month_menu', 'battle',
                'battle_menu', 'egg_battle_menu', 'monster_menu')
                or readable_prose_screen(screen)):
            mem.pop('bijin_gift_hold', None)
            mem.pop('bijin_gift_waits', None)
        elif screen.kind != 'gift_request':
            # Losing the text classifier is not evidence that the item menu
            # ended. Keep its owner across OCR misses instead of blind A/B.
            return []
    guard = mem.get('bijin_guard')
    if isinstance(guard, dict):
        guard.pop('held', None)  # ownership is recomputed for this observation
    # The measured monthly event is drawn on the red-curtain scene: classify()
    # returns 'concert' for the frame (gcgx ninja.png), never 'dialogue', so
    # the old phase boundary skipped this guard in production and the
    # generic A accepted the request. Take ownership whenever the request is
    # on screen -- by its wording or by the event's own choice labels -- and
    # keep the phase boundary only for unrelated frames. A pending monthly
    # purchase still owns its own dialogue first (the event fires before the
    # monthly command, so the two never share a frame in the measured flow).
    native = _bijin_native_choice(screen)
    request_seen = native or _is_bijin_request(screen.text)
    if mem.get('month_sub') or (phase not in (None, 'dialogue') and not request_seen):
        mem.pop('bijin_guard', None)
        return None   # 月イチの購入額確定などの所有者を邪魔しない
    tick = int(mem.get('tick') or 0)
    if request_seen and screen.kind in ('text', 'unknown', 'yes_no'):
        if not isinstance(guard, dict):
            guard = mem['bijin_guard'] = {'tick': tick, 'chosen': False}
            _record(mem, 'bijin_request_seen', screen=screen.kind, phase=phase,
                    observed_metric={'text': screen.text[-80:], 'hand': screen.hand is not None,
                                     'native_choice': native, 'gold': _bijin_gold(screen)},
                    reason='美女の依頼（月イチイベント）を検出。所持金1000G以上なら家へ進み、未満・読取不明なら断る')
        guard['tick'] = tick
    if not isinstance(guard, dict):
        return None
    if guard.get('chosen') or screen.kind in ('map', 'map_target', 'month_menu', 'battle', 'world_map'):
        mem.pop('bijin_guard', None)
        return None
    # The lead-in lease expires as before. Once a choice is seen, time alone is
    # not evidence that it ended: a blinking hand or partial OCR must not
    # release the same pending request to the generic A.
    if tick - int(guard.get('tick') or 0) > BIJIN_GUARD_OBSERVATIONS and not guard.get('choice_seen'):
        mem.pop('bijin_guard', None)
        return None
    gold = _bijin_gold(screen)
    capped = _level_capped(mem)
    accept = gold is not None and gold >= BIJIN_MIN_GOLD and not capped
    if not request_seen:
        if screen.kind == 'gift_request':
            mem.pop('bijin_guard', None)
            return None   # the purchase menu has its own bounded owner (gift_step)
        if guard.get('choice_seen') and screen.kind in ('text', 'unknown', 'yes_no'):
            # A positively readable page without the request wording, without
            # any bottom choice row and without a blinking hand is the same end
            # evidence trade_guard accepts ("a readable different prose screen",
            # L114-L119). Without this boundary the hold below stays true on an
            # unrelated readable dialogue and orchestrator L407 skips the
            # no-input fallback for good, so the bot sends nothing at all.
            # Read the raw cells of the message band: a partly unread page (or
            # a page left with nothing but the gold header) is not end evidence,
            # so this can never re-open the owner-review blind A.
            if readable_prose_screen(screen):
                mem.pop('bijin_guard', None)
                return None
            # Owner review (2026-10-15, PR #2286): the choices of this request
            # have already been seen, so a partly unread question is not end
            # evidence. The hand may still blink over the accept line (a 500G
            # request could be confirmed), so keep the pending episode held
            # instead of returning None and letting the generic A through.
            # Release only happens on an explicit end condition: a read
            # decline/accept row, a new monthly/frame owner or a map/battle
            # screen. The monthly-event phase ('concert') does not release it.
            guard['held'] = True
            action = _bijin_hold_fallback(screen, mem, guard, gold)
            if action is not None:
                return action
            if not guard.get('unread_question_recorded'):
                guard['unread_question_recorded'] = True
                _record(mem, 'situation_held', screen=screen.kind,
                        observed_metric={'bijin_guard': True, 'gold': gold,
                                         'text': screen.text[-80:]},
                        reason='美女の依頼の設問が一時的に読めないため保留する（選択肢は確認済みで終了証拠ではない）')
            return []
        return None
    band = [word for _, y, word in _options(screen) if 176 <= y <= 208 and word]
    if screen.hand is None and not band:
        return None   # 依頼文の読み上げだけが見えている頁。進行は既存の経路に任せる
    guard['choice_seen'] = True
    move = None
    for label in (BIJIN_NATIVE_ACCEPT_TOKENS + ('うむッ',) if accept
                  else BIJIN_DECLINE_TOKENS):
        move = menu_to(screen, label, exact=False)
        if move is not None:
            break
    if move is None:
        guard['held'] = True
        action = _bijin_hold_fallback(screen, mem, guard, gold)
        if action is not None:
            return action
        if not guard.get('unread_choice_recorded'):
            guard['unread_choice_recorded'] = True
            _record(mem, 'situation_held', screen=screen.kind,
                    observed_metric={'bijin_guard': True, 'gold': gold, 'text': screen.text[-80:]},
                    reason='美女の依頼の選択行と現在行を確定できないため決定せず再観測')
        return []
    guard.pop('held_ticks', None)
    guard.pop('bound_unactionable', None)
    if move != 'here':
        return [move]
    guard['chosen'] = True
    cur = _current(screen)
    _record(mem, 'prompt', choice=(cur[2] if cur else label), prompt=screen.text[-60:],
            strategy_variant=('decline_bijin_level_cap' if capped else
                              'accept_bijin_house' if accept else
                              'decline_bijin_unread_gold' if gold is None else 'decline_bijin_below_floor'),
            observed_metric={'gold': gold,
                             **({'hanjuku_level': mem['hanjuku_level']}
                                if type(mem.get('hanjuku_level')) is int else {})},
            reason=('半熟レベル14以上を確認したため半熟値を稼ぐ家の依頼を断る' if capped else
                    '所持金1000G以上を確認したため依頼を受けて家を選ぶ' if accept else
                    '所持金1000G以上を確認できないため依頼を断る'))
    return [pad('a')]


def yes_no_step(screen: Screen, mem):
    text = screen.text
    metric = None
    if re.search(r'\d+Gでいい', text):
        choice, reason, variant = 'いかんッ!', '追加のおねだりは所持金を月一購入に残すため断る', 'decline_extra_gift'
    elif _is_goninja_prompt(text):
        # 月イチイベント「ゴニンジャー」: 50Gで敵将軍の暗殺を依頼する。成功率は低い。
        # Owner rule (2026-10-17): 所持金300G未満なら断る。
        budget = metric = _goninja_budget(mem, screen.header)
        if budget is None:
            choice, reason, variant = ('いかんッ!', '所持金が読めないためゴニンジャーへの依頼を見送る',
                                       'decline_goninja_unreadable')
        elif budget['gold'] >= GONINJA_MIN_GOLD:
            choice, reason, variant = ('うむッ!', f'所持金が{GONINJA_MIN_GOLD}G以上あるためゴニンジャーへ依頼する',
                                       'accept_goninja')
        else:
            choice, reason, variant = ('いかんッ!', f'所持金が{GONINJA_MIN_GOLD}G未満のためゴニンジャーへの依頼を断る',
                                       'decline_goninja_below_floor')
    elif _is_bijin_request(text):
        # Owner rule (2026-10-15, 2026-10-21): the monthly 美女 request is
        # declined unless the 1000G floor is proven. Its measured frame is
        # phase 'concert' and a pending monthly owner can preempt
        # bijin_guard_step, so this measured yes/no listing must apply the same
        # floor instead of falling through to the default accept.
        gold = _bijin_gold(screen)
        metric = {'gold': gold, 'minimum_gold': BIJIN_MIN_GOLD}
        if _level_capped(mem):
            metric['hanjuku_level'] = mem['hanjuku_level']
            choice, reason, variant = ('いかんッ!', '半熟レベル14以上のため半熟値が上がる家の依頼を断る',
                                       'decline_bijin_level_cap')
        elif gold is not None and gold >= BIJIN_MIN_GOLD:
            choice, reason, variant = ('うむッ!', '所持金1000G以上を確認したため美女の依頼を受ける',
                                       'accept_bijin_house')
        elif gold is None:
            choice, reason, variant = ('いかんッ!', '所持金1000G以上を確認できないため美女の依頼を断る',
                                       'decline_bijin_unread_gold')
        else:
            choice, reason, variant = ('いかんッ!', '所持金1000G未満のため美女の依頼を断る',
                                       'decline_bijin_below_floor')
    elif 'はたしあい' in text or 'ごあいて' in text:
        # Owner decision (2026-09-25): accept. A duel fought with the blue
        # gauge spent properly is a near-certain win, so the hero no longer
        # needs shielding from the offer.
        choice, reason = 'うむッ!', '一騎打ちは青ゲージを消費する前提で受ける'
        variant = 'accept_duel'
    elif _is_general_trade_prompt(text):
        # Owner rule (2026-09-28): the general trade (花いちもんめ) almost
        # always offers an unfair deal (odoru7094: ろくでもないのしか手に
        # 入らないのでやってはいけない), so decline it. Measured question
        # (倒転王国 月イチイベント): 「ここで将軍同士のトレードをしようじゃ
        # ないか。そちらの◯◯将軍と我が軍のイキのいいのとではどーだ?」
        accept, metric = trade_accept_weak(mem, _trade_named_general(text))
        if accept:
            choice, reason = 'うむッ!', '指定された将軍が弱い（最大HP50未満・戦闘10未満、チャート外）ため花いちもんめを受ける'
            variant = 'accept_weak_general_trade'
        else:
            choice, reason = 'いかんッ!', '将軍トレード（花いちもんめ）で強い・判定不能の将軍が指定されたため断る'
            variant = 'decline_general_trade'
    else:
        choice, reason, variant = 'うむッ!', '未分類の確認は既定で進行', 'unclassified_prompt'
    move = menu_to(screen, choice)
    if move == 'here':
        _record(mem, 'prompt', choice=choice, prompt=text[-40:], reason=reason,
                strategy_variant=variant, observed_metric=metric)
        return [pad('a')]
    if move is None:
        _record(mem, 'situation_held', screen=screen.kind,
                observed_metric={'choice': choice, 'hand_visible': screen.hand is not None,
                                 **({'prompt_metric': metric} if metric else {})},
                reason='確認画面の選択位置を読めないため決定せず再観測')
    return [move] if move else []


def _summer_bonus_choice_band(screen: Screen) -> bool:
    """True while the 8月 prompt's own choice band can still be on screen.

    ``confirmation_planned`` only says A was sent; it is not evidence that the
    menu went away. The band is denied by its own drawn rows and by the choice
    cursor the prompt always shows: either one means the 3-choice menu is still
    up, so an unread row (まとめて解雇 included) must not be confirmed.

    The band covers the third choice row at y=208, not 207: the upper two rows
    and the hand can be the only things that disappear, and a lone 208 row is
    still the menu.

    Measured dialogue window (g825 evidence, 2ねん8がつ): every response page
    keeps the panel's outer frame tiles — a single unknown cell at x=248 on
    the band rows — so an edge-only row is a response page, not a choice row
    (tests/fixtures/hanjuku/g825-summer-response.png). Only cells inside the
    panel's inner columns (PROSE_COLUMNS) keep the menu alive: the label rows
    (x=176) and the post-A fade (inner tiles at x=32..224,
    g825-summer-post-a-fade.png) still re-observe; the border-only response
    page does not.
    """
    if screen.hand is not None:
        return True
    x0, x1 = PROSE_COLUMNS
    # The choice label's tail can land exactly on the inner column boundary
    # (x=232): the tail is a real choice label cell, so the inner check must
    # include it. The response page's outer frame stays at x=248 and is still
    # denied. The x=232 case comes from a synthetic reproduction; the x=248
    # outer-frame case comes from the measured g825 fixture.
    return any(176 <= line.y <= 208 and any(x0 <= x <= x1 for x, _ in line.cells)
               for line in screen.lines)


def summer_bonus_continue(screen: Screen, mem):
    """Keep a known choice episode through partial text or cursor loss."""
    if screen.kind == 'summer_bonus':
        return True
    plan = mem.get('summer_bonus_plan')
    old_choice = mem.get('summer_bonus_choice') in ('vacation', 'bonus')
    if not isinstance(plan, dict):
        plan = None
    if not plan and not old_choice:
        return False
    rows = _summer_bonus_rows(screen)
    same_menu = (any([x, y] in plan.get('rows', []) for x, y, _ in rows)
                 if plan else bool(rows))
    if screen.kind in ('text', 'unknown'):
        # Post-choice dialogue from the SFC text block 0D2AAF:
        # https://www.yumesaki-hoshi.jp/analysis/sfc/hanjyuku/
        # Label disappearance alone is not evidence that A was applied.
        messages = {'vacation': ('ひゃっほ', 'バカザンス', 'へいしのはんぶんは', 'かえってはこなかった'),
                    'bonus': ('さすがは', 'ふとっぱら', 'たたかいますとも', 'しょじきんがはんぶんに')}
        flat = ''.join(screen.text.split())
        response_seen = (plan and plan.get('confirmation_planned') and not same_menu
                         and any(t in flat for t in messages.get(plan.get('target'), ())))
        if response_seen:
            screen.kind = 'summer_bonus_message'
            _record(mem, 'summer_bonus_message', target=plan['target'],
                    reason='夏イベントの選択後に出る固有の応答文を読んだため会話を進める')
        # A planned A is not evidence that the menu went away: the prompt's own
        # question and a single unread tile both live in the prose band, so the
        # band alone cannot distinguish the response page from the prompt. Only
        # advance when the choice band itself is denied (see
        # ``_summer_bonus_choice_band``): while the band or its hand can still be
        # on screen, re-observe instead of confirming an unread choice.
        elif plan and plan.get('confirmation_planned') and not same_menu:
            band_gone = not _summer_bonus_choice_band(screen)
            has_dialogue_content = any(
                line.y in PROSE_ROWS and (line.cells or line.spans())
                for line in screen.lines)
            if band_gone and has_dialogue_content:
                screen.kind = 'summer_bonus_message'
                _record(mem, 'summer_bonus_message', target=plan['target'],
                        reason='夏イベントの選択確定後に選択肢帯とカーソルが消え、応答文領域に内容があるため会話を進める（応答文は未読）')
            else:
                return True  # the choice band cannot be denied; re-observe
        elif plan or old_choice:
            return True
    # A different recognized screen or the observed post-choice response ends
    # the episode. Do not carry its target into another year's event.
    mem.pop('summer_bonus_plan', None)
    mem.pop('summer_bonus_choice', None)
    return False


def summer_bonus_step(screen: Screen, mem):
    rows = _summer_bonus_rows(screen)
    visible = {key for _, _, key in rows}
    plan = mem.get('summer_bonus_plan')
    if not isinstance(plan, dict) or plan.get('target') not in ('vacation', 'bonus'):
        gold = (screen.header or {}).get('gold')
        if type(gold) is not int:
            gold = mem.get('gold')
        soldiers = mem.get('soldiers_seen')
        # A saved month is not proof of this event's date. Without a header,
        # even two matching saved keys can both belong to last month/year.
        current_month = _month_key(screen.header)
        soldiers_current = (current_month is not None
                            and (screen.header or {}).get('month') == 8
                            and mem.get('soldiers_seen_key') == current_month)
        readable = (type(gold) is int and gold >= 0 and type(soldiers) is int
                    and soldiers >= 0 and soldiers_current)
        target = mem.get('summer_bonus_choice')   # pending v129 random choice
        if readable and target not in ('vacation', 'bonus'):
            target = 'vacation' if soldiers <= gold else 'bonus'
            selection = 'min_loss'
            reason = (f'兵士{soldiers}人と所持金{gold}Gを比較し、損失の小さい'
                      + ('兵士半減のバカンス' if target == 'vacation' else 'お金半減のボーナス')
                      + 'を選び、まとめて解雇は選ばない')
        else:
            if target not in ('vacation', 'bonus'):
                target = random.choice(('vacation', 'bonus'))
            selection = 'random'
            reason = ('当月の比較値が揃わない時に決めた'
                      + ('兵士半減のバカンス' if target == 'vacation' else 'お金半減のボーナス')
                      + 'を保ち、まとめて解雇は選ばない')
        plan = {'target': target, 'reason': reason, 'confirmation_planned': False,
                'rows': [[x, y] for x, y, _ in rows],
                'metric': {'gold': gold, 'soldiers': soldiers,
                           'soldiers_seen_month': mem.get('soldiers_seen_key'),
                           'current_month': current_month, 'soldiers_current': soldiers_current,
                           'selection': selection}}
        mem['summer_bonus_plan'] = plan
        mem['summer_bonus_choice'] = target
    target = plan['target']
    metric = {**plan['metric'], 'visible': sorted(visible)}
    move = None
    if target in visible:
        for variant in SUMMER_BONUS_CHOICES[target]:
            move = menu_to(screen, variant, exact=False)
            if move:
                break
    if move == 'here':
        plan['confirmation_planned'] = True
    if move is None:
        # Move once, then read the next frame. Neither menu wrapping nor a
        # cancellation behavior is assumed, and an unseen row is never accepted.
        _record(mem, 'prompt', choice=None, target=target, prompt=screen.text[-40:],
                strategy_variant='summer_bonus_no_cursor',
                reason='夏バテの選択位置が読めないため上を1回押して再観測し、確認できるまで確定しない',
                observed_metric={**metric, 'hand_visible': screen.hand is not None})
        return [pad('up')]
    if move == 'here':
        _record(mem, 'prompt', choice=target, prompt=screen.text[-40:],
                strategy_variant=f'summer_bonus_{target}', reason=plan['reason'],
                observed_metric=metric)
        return [pad('a')]
    return [move]


def observe_events(screen: Screen, mem):
    """Record chart-relevant facts that need no input (month header, harvest)."""
    _repair_home_name_chapter(mem)
    _repair_home_alias_failures(mem)
    mem['tick'] = int(mem.get('tick') or 0) + 1      # observations: ages sorties (_en_route)
    _hold_general_loss_metric(mem)
    _migrate_card_evidence(mem)
    if (mem.get('resume_chapter_pending') is True and not mem.get('chapter')
            and screen.kind == 'text' and screen.header is None):
        # Native restored party-picker -> chapter-2 introduction (public
        # frame fixtures). Require both independent lines, never the source
        # run's final chapter or the year/month header. Neither line alone
        # identifies a map. This recognition is limited to chapterless resume.
        clues = mem.setdefault('resume_intro_clues', {})
        if 'かんじゅくクイーンめをたおしてから' in screen.text:
            clues['queen'] = True
        if 'どうしににたヤツ「フフフ' in screen.text:
            clues['double'] = True
        if clues.get('queen') is True and clues.get('double') is True:
            mem.pop('resume_chapter_pending', None)
            mem.pop('resume_intro_clues', None)
            _enter_chapter(mem, 2, evidence='native_chapter_2_intro',
                           reason='再開後のクイーン撃破と主人公の偽物の導入会話を両方確認して第2話を確定')
    if screen.kind in ('card_select', 'sortie_confirm'):
        row = _egg_row(screen)
        eggs = mem.setdefault('egg_uses', {})
        if row:
            mem.setdefault('egg_types', {})[row[0]] = row[1]
            if row[0] in (mem.get('egg_recheck') or []):
                mem['egg_recheck'].remove(row[0])
        if row and eggs.get(row[0]) != row[2]:
            eggs[row[0]] = row[2]
            _record(mem, 'egg_uses_seen', general=row[0], egg=row[1], observed_metric=row[2],
                    reason='出撃画面のたまご行から卵の残り使用回数を記録')
    header = screen.header
    if header:
        chapter = header.get('chapter')
        if chapter and chapter != mem.get('chapter'):
            _enter_chapter(mem, chapter, reason='画面の章表示を確認し、前章の座標・出撃・購入状態を初期化')
        key = _month_key(header)
        if mem.get('month') != key:
            mem['month'] = key
            mem['gold'] = header['gold']
            _record(mem, 'month_seen', month=key, gold=header['gold'], reason='月の表示')
        mem['gold'] = header['gold']
    castle = CASTLE_STATUS.search(screen.text)
    if castle and mem.get('resume_chapter_pending') is True:
        observe_chapter_castle(mem, castle[1])
    if mem.get('resume_chapter_pending') is True and type(mem.get('chapter')) is int and 1 <= mem['chapter'] <= 12:
        mem.pop('resume_chapter_pending', None)
    income = re.search(r'しゅうにゅう(\d{1,3})Gレベル\d+しょうぐん', screen.text)
    if castle and income and mem.get('chapter') and mem.get('month'):
        label = STATUS_NAMES.get(castle[1], castle[1])
        if label in {STATUS_NAMES.get(c, c) for c in _owned(mem)}:
            mem.setdefault('castle_income', {})[label] = {
                'chapter': mem['chapter'], 'month': mem['month'], 'tick': mem['tick'], 'income': int(income[1])}
    if 'がはいかにくわわった!' in screen.text.replace('！', '!'):
        from ..os_layer.roster import invalidate
        invalidate(mem)
    from .house import roster
    from ..os_layer.roster import page
    page(mem, roster(screen))
    if 'きょうさく' in screen.text and not mem.get('poor_harvest_' + str(mem.get('month'))):
        mem['poor_harvest_' + str(mem.get('month'))] = True
        _record(mem, 'poor_harvest', deviation_reason='reset_forbidden',
                reason='チャートは凶作でリセット指示だがbotはリセットしない',
                expected_metric='収入減')
    # Owner rule (2026-09-28): with 50+ soldiers on hand, egg recovery wins
    # over buying more soldiers. The army total is read wherever the game
    # states it (e.g. 「げんざい わがぐんの へいしすうは Nめいです」).
    pattern = r'へいしすうは([0-9０-９]+)めい'
    compact = screen.text.replace(' ', '')
    matches = list(re.finditer(pattern, compact))
    m = matches[0] if len(matches) == 1 else None
    # Screen.text joins known glyphs and drops UNKNOWN. A partial "0?" must
    # never collapse to an actual zero count. Retain unknown cells when
    # validating the count row; unrelated unknown background glyphs are fine.
    raw = ''.join(line.text.replace(' ', '') for line in screen.lines)
    raw_counts = re.findall(pattern, raw)
    if len(matches) > 1 or ('へいしすうは' in raw and raw_counts != [m.group(1) if m else None]):
        m = None
        mem.pop('soldiers_seen_key', None)
        mem.pop('soldiers_seen_scope', None)
        mem['soldiers_seen_basis'] = 'unread_total'
    if m:
        seen = int(m.group(1).translate(str.maketrans('０１２３４５６７８９', '0123456789')))
        mem['soldiers_seen_key'] = mem.get('month')
        mem['soldiers_seen_basis'] = 'screen_total'
        from ..strategy.economy import _soldier_count_scope
        scope = _soldier_count_scope(mem, screen.header)
        if scope is not None:
            mem['soldiers_seen_scope'] = scope
        else:
            mem.pop('soldiers_seen_scope', None)
        if seen != mem.get('soldiers_seen'):
            mem['soldiers_seen'] = seen
            _record(mem, 'soldiers_seen', observed_metric={'soldiers': seen},
                    reason='兵士の総数を画面の文言から読み取った')
    _observe_soldier_receipt(screen, mem, directly_seen=seen if m else None)


def _bijin_gift_bound(screen: Screen, mem, gold, items):
    """Bounded escape for the measured purchase menu (no refusal row).

    Only an item choice ends this menu; a hold without input would leave the
    frame unchanged until the 300 s corner watchdog ends the run (retro_corner
    end_reason='screen_stalled', 2026-10-21, 907G). After
    ``BIJIN_GIFT_HOLD_MAX`` observations (~60 s), end the event with the
    cheapest readable non-house item (こうすい100G), never いえ. This can spend
    at most 100G and only fires after the decision to not buy the house.
    """
    waits = int(mem.get('bijin_gift_waits') or 0) + 1
    mem['bijin_gift_waits'] = waits
    if waits <= BIJIN_GIFT_HOLD_MAX:
        return []
    candidates = [(price, name) for price, name in items if name != 'いえ']
    if not candidates:
        return []
    price, name = min(candidates)
    move = menu_to(screen, name, exact=False)
    if move == 'here':
        mem.pop('bijin_gift_waits', None)
        _record(mem, 'gift', strategy_variant='bijin_hold_bound_cheapest',
                item=name, price=price, gold=gold, deviation_reason='bijin_item_hold_bound',
                observed_metric={'price': price, 'waits': waits,
                                 'affordable': gold is not None and gold >= price},
                reason='美女の購入画面で家（または断り）を確定できないまま保留上限を超えたため、最安品でイベントを終える（停滞によるrun終了を防ぐ）')
        return [pad('a')]
    return [move] if move else []


def gift_step(screen: Screen, mem):
    """美女は1000G以上で家を選ぶ。それ以外の贈り物は従来の方針。"""
    mem.pop('bijin_gift_hold', None)
    items = []
    for line in screen.lines:
        # The live gift prompt shares the first price row. Restrict the
        # item/price to the right menu, not the dialogue at its left.
        joined = ''.join(word for x, word in line.spans() if x >= 144)
        m = re.match(r'^(\S+?)(\d+)G$', joined)
        if m and not HEADER_RE.search(line.known.replace(' ', '')):
            items.append((int(m.group(2)), m.group(1)))
    bijin = ('どれかほしい' in screen.text.replace(' ', '')
             or any(name in ('こうすい', 'くるま', 'いえ') for _, name in items))
    if bijin:
        mem['bijin_gift_hold'] = True
        gold = _bijin_gold(screen)
        if gold is None or gold < BIJIN_MIN_GOLD:
            for label in BIJIN_DECLINE_TOKENS:
                move = menu_to(screen, label, exact=False)
                if move is not None:
                    mem.pop('bijin_gift_waits', None)
                    return [pad('a')] if move == 'here' else [move]
            # Already in the item menu: never purchase the house without the
            # cash condition; the bound ends the menu cheaply if it never clears.
            return _bijin_gift_bound(screen, mem, gold, items)
        if _level_capped(mem):
            # Issue #3011: the house earns 半熟値; at the owner cap the event
            # still has to end, so the cheapest non-earning item is bought.
            others = [(price, name) for price, name in items if name != 'いえ']
            if not others:
                return _bijin_gift_bound(screen, mem, gold, items)
            price, name = min(others)
            move = menu_to(screen, name, exact=False)
            if move is None:
                return _bijin_gift_bound(screen, mem, gold, items)
            if move == 'here':
                mem.pop('bijin_gift_waits', None)
                _record(mem, 'gift', strategy_variant='bijin_level_cap', item=name, price=price,
                        gold=gold, observed_metric={'price': price, 'hanjuku_level': mem.get('hanjuku_level'),
                                                    'affordable': gold >= price},
                        reason='半熟レベル14以上のため半熟値を稼ぐ家を避け最安品でイベントを終える')
                return [pad('a')]
            return [move]
        houses = [(price, name) for price, name in items if name == 'いえ' and price == 500]
        if len(houses) != 1:
            return _bijin_gift_bound(screen, mem, gold, items)
        price, name = houses[0]
        move = menu_to(screen, name, exact=False)
        if move == 'here':
            mem.pop('bijin_gift_waits', None)
            _record(mem, 'gift', strategy_variant='bijin_house', item=name, price=price,
                    gold=gold, observed_metric={'price': price, 'affordable': True},
                    reason='所持金1000G以上のため半熟値を上げる家を選ぶ')
            return [pad('a')]
        return [move] if move else []
    mem.pop('bijin_gift_waits', None)
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
