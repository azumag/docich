"""戦略層: the cards a sortie carries and the charted card tactics.

Card ids, carry slots, boss kits, the strong-card supplement and the per-step
tactics list the judgment layer evaluates in battle. No screen access.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart, hanjuku_reference as reference
from ..decision_log import _record


def _is_boss_order(order, mem) -> bool:
    chapter = mem.get('chapter') or 0
    return bool(order) and order.get('target') == chart.boss_castle(chapter)


# Owner (2026-09-29): 敵にエッグを使わせない. gcgx ai.html: an enemy general
# uses its egg when the battle's card IDs total 48 or more (patterns 1-3).
CARD_IDS = reference.ALL_CARD_IDS
ENEMY_EGG_CARD_ID_SUM = reference.ENEMY_EGG_CARD_ID_SUM


def _cap_card_ids(cards):
    """Keep cards in plan order while their ID total stays under the enemy egg threshold."""
    kept, total = [], 0
    for card in cards:
        card_id = CARD_IDS.get(card)
        if card_id is None or total + card_id >= ENEMY_EGG_CARD_ID_SUM:
            continue
        kept.append(card)
        total += card_id
    return kept


def _strict_boss_cards(order, mem) -> bool:
    """The base chart's boss sortie carries exactly its charted cards; an
    adjusted/interim boss order may leave an unavailable card behind (g438
    04:03: J1 wanted ミックミー, none was owned, and card_select held for good)."""
    return _is_boss_order(order, mem) and any(
        o['step'] == order['step'] for o in chart.orders(mem.get('chapter') or 0))


def _deploy_cards(order, mem):
    from .execution import bound_fields
    bound = bound_fields(order, mem)
    if order.get('_execution_bound') is True and bound is None:
        return []
    if bound is not None:
        cards = list(bound['cards'])  # actual carried copies, never a later kit
        for card in (mem.get('kit_spent') or {}).get(order['step']) or ():
            if card in cards:
                cards.remove(card)
        return cards
    rare_kit = (mem.get('rare_card_kit') or {}).get(order['step'])
    strong = (mem.get('strong_card_kit') or {}).get(order['step']) if rare_kit is None else None
    if _strict_boss_cards(order, mem) and rare_kit is None:
        return _with_strong_card(list(order['cards']), strong, mem, order['step'])
    cards = list(rare_kit if rare_kit is not None else
                 mem.get('card_override', {}).get(order['step'], order['cards']))
    # One planned copy leaves the kit per drop entry. g454 12:22: a planned
    # イッテツーン x2 had one copy already picked and the second recorded as
    # dropped; removing every copy of the card shrank the plan below what the
    # sortie actually carried and the confirmation held forever.
    for card in (mem.get('card_drop') or {}).get(order['step']) or ():
        if card in cards:
            cards.remove(card)
    # One carried copy leaves the kit per attempted use in this sortie (g452:
    # ヴィーナス spent both イッテツーン in one battle and the next battle
    # re-planned them, opened an empty きりふだ list and stalled).
    for card in (mem.get('kit_spent') or {}).get(order['step']) or ():
        if card in cards:
            cards.remove(card)
    capped = _cap_card_ids(cards)
    if capped != cards and (mem.get('cards_capped') or {}).get(order['step']) != capped:
        mem.setdefault('cards_capped', {})[order['step']] = capped
        _record(mem, 'cards_capped', chart_step=order['step'],
                observed_metric={'planned': cards, 'carried': capped,
                                 'id_sum': sum(CARD_IDS[c] for c in capped)},
                reason='切り札IDの合計が48以上だと敵がエッグを使うため、47以下になるよう携行札を絞る')
    return _with_strong_card(capped, strong, mem, order['step'])


def _with_strong_card(cards, strong, mem, step):
    """A carried strong card only when a slot, a budget and the kit allow it.

    予定札(チャート/調整/レア札)は外さない。gcgx: 携行札のID合計が48以上だと敵が
    エッグを使うため、追加後も ``< 48`` を満たす時だけ1枚足す。同じ札がもう1枚
    携行済みなら足さない(在庫を無駄にしない)。この出撃で消費・破棄した札は
    再び足さない: 一覧に無い札を開くと空の一覧で止まる(g438 04:18)。
    """
    if not strong or strong in cards or len(cards) >= CARRY_SLOTS:
        return cards
    if strong in ((mem.get('kit_spent') or {}).get(step) or ()) or strong in ((mem.get('card_drop') or {}).get(step) or ()):
        return cards
    if sum(CARD_IDS[c] for c in cards) + CARD_IDS.get(strong, ENEMY_EGG_CARD_ID_SUM) >= ENEMY_EGG_CARD_ID_SUM:
        return cards
    return [*cards, strong]


RARE_SCAN_LIMIT = 32          # the entire 32-card catalogue, once per observed month
CARRY_SLOTS = 3               # card_select「あとNこ」の携行枠(実測 header は 0..3)
CARD_SCROLL_LIMIT = 8         # downward presses looking for a card below a full panel
CARD_MISS_LIMIT = 5           # card_select readings before a missing card is left behind
CARD_UNREADABLE_LIMIT = 6     # unreadable card_select readings before cancelling the sortie
SORTIE_CONFIRM_LIMIT = 8      # readings before a readable but mismatched kit is approved
CARD_STOCK_LIMIT = 24         # observed card names kept for the adjusted-chart request


def _observe_card_stock(mem, inventory):
    """Remember the stocks a sortie card panel showed (chart-adjust grounding).

    Fewer than four rows is the whole inventory (depleted items disappear), so
    a known card absent from it is out of stock now and recorded as 0; a
    four-row panel is a window and only its rows are updated. g438 04:04: the
    adjusted chart planned ミックミー and エンジェリン for chapter 1, where
    neither was ever in the panel -- the model was never told what the player
    actually holds. The request now carries this so it plans from observed
    stock.
    """
    stock = mem.setdefault('card_stock', {})
    shown = {row['card']: row['stock'] for row in inventory['rows']}
    stock.update(shown)
    if len(inventory['rows']) < 4:
        for card in stock:
            if card not in shown:
                stock[card] = 0
    while len(stock) > CARD_STOCK_LIMIT:
        stock.pop(next(iter(stock)))
