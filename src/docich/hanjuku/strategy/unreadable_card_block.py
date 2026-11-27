"""切り札一覧を読めない出撃の blocked 状態。

``card_list_unreadable_depart`` が空の携行確認へ進み、empty-kit guard に
取消された出撃元だけがここへ入る。card_select の一覧は目標ごとではなく
出撃元が開く同じ在庫画面なので、将軍・step・目標だけを変えた波状攻撃が
同じ失敗をなぞらないよう、出撃元単位で止める。
"""
from __future__ import annotations

from ... import hanjuku_reference as reference
from ..state.regions import plan as _plan_region

BOOK_KEY = 'unreadable_card_block'
BLOCK_LIMIT = 16
STEP_LIMIT = 16
ROUTE_LIMIT = 8


def key_for(source):
    """The panel owner this evidence belongs to.

    Card-select inventory is not target-specific: the same source castle shows
    the same field stock for every attack. General, step, target and card
    composition are therefore wave details, not separate blocking conditions.
    """
    return source if isinstance(source, str) else ''


def _row_active(mem, row):
    return isinstance(row, dict) and row.get('chapter') == mem.get('chapter')


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
    """Block this route for the chapter and return ``(row, created)``.

    The block deliberately has no automatic monthly expiry: the failed empty-kit
    attempt is deterministic while the card list remains unreadable, so a month
    boundary alone is not new evidence. Chapter transition clears the whole key.
    Within the chapter, only a structured read of the same source's sortie card
    list is direct readability evidence. A completed inventory census is not:
    it proves stock, and a zero stock can make the next plan cardless, but it
    does not prove that this source's occluded/unreadable panel can be read.
    The book itself stays bounded.
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


def clear(mem, *, source=None):
    """Release blocks after new card-list evidence; return rows.

    A sortie-card-list read is evidence for the source whose panel was read.
    ``source=None`` is the explicit whole-chapter form used by tests and state
    maintenance; production evidence passes its measured source.
    """
    book = mem.get(BOOK_KEY) or {}
    rows = []
    for key, row in list(book.items()):
        if _row_active(mem, row) and (source is None or row.get('source') == source):
            rows.append(row)
            book.pop(key, None)
    return rows
