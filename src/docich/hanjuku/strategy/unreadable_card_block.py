"""切り札一覧を読めない出撃の blocked 状態。

``card_list_unreadable_depart`` が空の携行確認へ進み、empty-kit guard に
取消された出撃元だけがここへ入る。card_select の一覧は目標ごとではなく
出撃元が開く同じ在庫画面なので、将軍・step・目標だけを変えた波状攻撃が
同じ失敗をなぞらないよう、出撃元単位で止める。

ブロックは失敗を測った月の間だけ効く。月が替われば新しい試行窓として解除し、
月の表示が動かない場合も ``BLOCK_TICKS`` で上限を切る。解除の直接証拠
（同じ出撃元の切り札一覧の構造読み）が無いまま章の最後まで止めると、出撃元が
1つしかない章では候補が全て消えて可読性の証拠を取る機会自体が失われるため
（2026-11-28 ch5 10-2〜10-8: アルマムーンだけが在城で、取消後の全注文が
standing のまま出撃0が続いた）。
"""
from __future__ import annotations

from ... import hanjuku_reference as reference
from ..state.regions import plan as _plan_region

BOOK_KEY = 'unreadable_card_block'
BLOCK_LIMIT = 16
STEP_LIMIT = 16
ROUTE_LIMIT = 8
# 月の表示が進まない状況（長い戦闘・月次処理）でもブロックを章全体へ
# 広げないための上限。1 tick は約1.4秒の観測なので約21分。
BLOCK_TICKS = 900


def key_for(source):
    """The panel owner this evidence belongs to.

    Card-select inventory is not target-specific: the same source castle shows
    the same field stock for every attack. General, step, target and card
    composition are therefore wave details, not separate blocking conditions.
    """
    return source if isinstance(source, str) else ''


def _expired_window(mem, row):
    """Whether this chapter's row has outlived its retry window.

    A month boundary is a new attempt window: the empty-kit guard still refuses
    the departure, so the retry can only cost one cancelled sortie attempt,
    while keeping the block forever removed the only way to read the list.
    ``BLOCK_TICKS`` bounds the same window when the month label does not move.
    """
    month = mem.get('month')
    if isinstance(month, str) and isinstance(row.get('month'), str) and month != row['month']:
        return True
    tick = mem.get('tick')
    last = row.get('last_tick')
    if type(last) is not int:
        last = row.get('tick')
    return type(tick) is int and type(last) is int and tick - last >= BLOCK_TICKS


def _row_active(mem, row):
    return (isinstance(row, dict) and row.get('chapter') == mem.get('chapter')
            and not _expired_window(mem, row))


def entry(mem, purpose, source, target):
    """The active block row for this source panel, or None.

    ``purpose`` and ``target`` stay in the signature so callers and audits can
    ask with the order they are judging; they do not narrow the block.
    """
    book = mem.get(BOOK_KEY) or {}
    row = book.get(key_for(source))
    return row if _row_active(mem, row) else None


def _safe_autocarry(mem, step):
    """The egg-safe card ``kit._deploy_cards`` would add to an empty kit.

    This is the pure part of ``kit._auto_carry_safe_card``: judging readiness
    must not emit that function's decision record before a sortie is even
    allowed to start.
    """
    stock = mem.get('card_stock')
    if not isinstance(stock, dict):
        return None
    spent = set((mem.get('kit_spent') or {}).get(step) or ())
    dropped = set((mem.get('card_drop') or {}).get(step) or ())
    for name in sorted(stock):
        if not isinstance(name, str) or name in spent or name in dropped:
            continue
        try:
            count = int(stock.get(name) or 0)
        except (TypeError, ValueError):
            continue
        card = reference.CARDS.get(name)
        if count > 0 and isinstance(card, dict) and card.get('egg_drop') == 0:
            return name
    return None


def _effective_cards_nonempty(mem, order):
    """Whether the deployment-time kit for this order can be nonempty.

    The empty-kit guard compares the actual deployment kit, not just the raw
    chart card list. Step overrides, dropped/spent copies, strong/dropper
    supplements and the owner policy that auto-carries one egg-safe card all
    decide whether this order would enter the same contradiction again.
    """
    from .kit import (_cap_card_ids, _strict_boss_cards, _with_extra_card,
                      _with_strong_card)
    step = order.get('step')
    if not isinstance(step, str) or not step:
        # Interim candidates have no step yet, but once one is assigned the
        # same auto-carry policy applies. Their spent/drop sets are empty, so
        # a positive egg-safe stock alone is enough to make the future kit
        # nonempty.
        return bool(order.get('cards')) or _safe_autocarry(mem, None) is not None
    rare = (mem.get('rare_card_kit') or {}).get(step)
    strong = (mem.get('strong_card_kit') or {}).get(step) if rare is None else None
    if rare is not None:
        cards = list(rare)
    elif _strict_boss_cards(order, mem):
        cards = list(order.get('cards') or ())
    else:
        cards = list(_plan_region(mem).get('card_override', {}).get(step, order.get('cards') or ()))
    for key in ('card_drop', 'kit_spent'):
        for card in (mem.get(key) or {}).get(step) or ():
            if card in cards:
                cards.remove(card)
    if _strict_boss_cards(order, mem) and rare is None:
        return bool(_with_strong_card(cards, strong, mem, step))
    zero = mem.get('card_zero_at_start')
    if isinstance(zero, dict) and zero.get('step') == step:
        stock = mem.get('card_stock') if isinstance(mem.get('card_stock'), dict) else {}
        zero_cards = {c for c in (zero.get('cards') or ()) if int(stock.get(c) or 0) <= 0}
        cards = [card for card in cards if card not in zero_cards]
    carried = _with_extra_card(_cap_card_ids(cards),
                               (mem.get('dropper_card_kit') or {}).get(step), mem, step)
    if not carried:
        carried = _with_extra_card(carried, _safe_autocarry(mem, step), mem, step)
    return bool(_with_strong_card(carried, strong, mem, step))


def order_blocked(mem, order, *, source=None):
    """Whether a card-carrying order uses a blocked unreadable-list source.

    A genuinely cardless order is outside the empty-kit contradiction and may
    still run; if its card list becomes readable, that read is one of the
    release conditions. Blocking it too would remove the cheapest recovery
    evidence without protecting any planned kit.
    """
    if not isinstance(order, dict):
        return False
    if not _effective_cards_nonempty(mem, order):
        return False
    from .kit import _strict_boss_cards
    if isinstance(order.get('step'), str) and _strict_boss_cards(order, mem):
        # A strict base-chart boss kit never takes the unreadable-list
        # departure fallback; it holds inside card_select instead. It is not
        # part of this contradiction, and a later readable panel there is valid
        # release evidence for the source.
        return False
    return entry(mem, order.get('purpose'), source if source is not None else order.get('source'),
                 order.get('target')) is not None


def mark(mem, order, *, source, cards, step):
    """Block this route for the failure month and return ``(row, created)``.

    The block is confined to the month that measured the failure (and to
    ``BLOCK_TICKS`` when the month label does not advance), so a chapter with a
    single source cannot be starved of every candidate by one unreadable panel.
    Within that window only a structured read of the same source's sortie card
    list is direct readability evidence. A completed inventory census is not:
    it proves stock, and a zero stock can make the next plan cardless, but it
    does not prove that this source's occluded/unreadable panel can be read.
    A new failure after the window re-marks the source with the new month.
    Chapter transition clears the whole key. The book itself stays bounded.
    """
    book = mem.setdefault(BOOK_KEY, {})
    for key, row in list(book.items()):
        if not _row_active(mem, row):
            book.pop(key, None)
    purpose = order.get('purpose')
    target = order.get('target')
    key = key_for(source)
    row = book.get(key)
    created = not isinstance(row, dict)
    if created:
        row = {'chapter': mem.get('chapter'), 'month': mem.get('month'),
               'purpose': purpose, 'source': source, 'target': target,
               'purposes': [], 'targets': [],
               'cards': list(cards or ()), 'steps': [], 'tick': mem.get('tick')}
    else:
        row['purpose'] = purpose
        row['target'] = target
        row['cards'] = list(cards or ())
    for field, value in (('purposes', purpose), ('targets', target)):
        values = row.setdefault(field, [])
        if isinstance(value, str) and value and value not in values:
            values.append(value)
            del values[:-ROUTE_LIMIT]
    steps = row.setdefault('steps', [])
    if isinstance(step, str) and step and step not in steps:
        steps.append(step)
        del steps[:-STEP_LIMIT]
    row['last_tick'] = mem.get('tick')
    book[key] = row
    while len(book) > BLOCK_LIMIT:
        book.pop(next(iter(book)), None)
    return row, created


def expire(mem):
    """Drop rows whose retry window passed; return the released rows.

    Rows of another chapter are bookkeeping cleanup and are not returned: only
    a same-chapter month/tick expiry re-opens the source for a card-carrying
    attempt, so only those are worth recording. Callers record the release and
    then rebuild the plan; the next empty-kit cancellation re-marks the source
    in the new window, so the retry rate stays bounded.
    """
    book = mem.get(BOOK_KEY) or {}
    released = []
    for key, row in list(book.items()):
        if not isinstance(row, dict):
            book.pop(key, None)
            continue
        if _row_active(mem, row):
            continue
        book.pop(key, None)
        if row.get('chapter') == mem.get('chapter'):
            released.append(row)
    return released


def clear(mem, *, source=None):
    """Release blocks after new card-list evidence; return rows.

    A sortie-card-list read is evidence for the source whose panel was read.
    ``source=None`` is the explicit whole-chapter form used by tests and state
    maintenance; production evidence passes its measured source.

    The read is direct evidence even when the month/tick window already passed:
    the row still records that source's unreadable panel, so removing it keeps
    a later ``expire`` from reporting a release the read superseded.
    """
    book = mem.get(BOOK_KEY) or {}
    rows = []
    for key, row in list(book.items()):
        if not isinstance(row, dict) or row.get('chapter') != mem.get('chapter'):
            continue
        if source is not None and row.get('source') != source:
            continue
        rows.append(row)
        book.pop(key, None)
    return rows
