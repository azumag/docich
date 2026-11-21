"""戦略層: the cards a sortie carries and the charted card tactics.

Card ids, carry slots, boss kits, the strong-card supplement and the per-step
tactics list the judgment layer evaluates in battle. No screen access.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart, hanjuku_reference as reference
from ...hanjuku_egg_reference import general_max_hp
from ..decision_log import _record
from ..state.regions import plan as _plan_region


def _is_boss_order(order, mem) -> bool:
    chapter = mem.get('chapter') or 0
    return bool(order) and order.get('target') in (chart.boss_castle(chapter), *chart.boss_sites(chapter))


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
    04:03: J1 wanted ミックミー, none was owned, and card_select held for good).

    The strict kit relies on the month plan buying its cards back
    (``strategy.boss_cards``), which covers the single boss order only. A
    chapter 4 四季王 site has no such plan, so it leaves a missing card behind
    (g1075 2026-10-30 08:14-08:16: 4-B1 waited for マグネガキン/ファバード past
    the list's last row and restarted the sortie every ~30 s)."""
    return (_is_boss_order(order, mem) and order.get('target') not in chart.boss_sites(4)
            and any(o['step'] == order['step'] for o in chart.orders(mem.get('chapter') or 0)))


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
        cards = list(order['cards'])
        # Only a card the measured panel proved out of stock leaves the boss
        # kit (flows.deploy._card_proven_absent); one copy per drop entry.
        for card in (mem.get('card_drop') or {}).get(order['step']) or ():
            if card in cards:
                cards.remove(card)
        return _with_strong_card(cards, strong, mem, order['step'])
    cards = list(rare_kit if rare_kit is not None else
                 _plan_region(mem).get('card_override', {}).get(order['step'], order['cards']))
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
    # A card measured out of stock before this attempt began is not searched
    # for again: every chapter 4 sortie of g1075 (2026-10-29 17:40-17:50)
    # scrolled and waited 5 readings for エンジェリン at stock 0. A zero read
    # during the attempt keeps its bounded readings (flows.deploy._drop_card).
    zero = mem.get('card_zero_at_start')
    if isinstance(zero, dict) and zero.get('step') == order['step']:
        live_stock = mem.get('card_stock') if isinstance(mem.get('card_stock'), dict) else {}
        zero_cards = {c for c in (zero.get('cards') or ())
                      if int(live_stock.get(c) or 0) <= 0}
        cards = [card for card in cards if card not in zero_cards]
    capped = _cap_card_ids(cards)
    if capped != cards and (mem.get('cards_capped') or {}).get(order['step']) != capped:
        mem.setdefault('cards_capped', {})[order['step']] = capped
        _record(mem, 'cards_capped', chart_step=order['step'],
                observed_metric={'planned': cards, 'carried': capped,
                                 'id_sum': sum(CARD_IDS[c] for c in capped)},
                reason='切り札IDの合計が48以上だと敵がエッグを使うため、47以下になるよう携行札を絞る')
    # 出撃時の卵落札 (#1553): 対戦相手が実測記録で判明している出撃は、卵落札を
    # 1枠に加える。追加後もID合計48未満を守る (``_with_extra_card``)。
    dropper = (mem.get('dropper_card_kit') or {}).get(order['step'])
    carried = _with_extra_card(capped, dropper, mem, order['step'])
    return _with_strong_card(carried, strong, mem, order['step'])


def _extra_card_name(extra):
    """The card an add-on kit entry names: a bare name or its record dict."""
    return extra if isinstance(extra, str) else (extra or {}).get('card')


def _with_extra_card(cards, extra, mem, step):
    """A carried add-on card only when a slot, a budget and the kit allow it.

    予定札(チャート/調整/レア札)は外さない。追加後も携行ID合計が48未満の時
    だけ1枚足す (gcgx: 48以上だと敵がエッグを使う)。同じ札がもう1枚携行済み
    なら足さない(在庫を無駄にしない)。この出撃で消費・破棄した札は再び足さ
    ない: 一覧に無い札を開くと空の一覧で止まる (g438 04:18)。
    """
    card = _extra_card_name(extra)
    if not card or card in cards or len(cards) >= CARRY_SLOTS:
        return cards
    if card in ((mem.get('kit_spent') or {}).get(step) or ()) or card in ((mem.get('card_drop') or {}).get(step) or ()):
        return cards
    if sum(CARD_IDS[c] for c in cards) + CARD_IDS.get(card, ENEMY_EGG_CARD_ID_SUM) >= ENEMY_EGG_CARD_ID_SUM:
        return cards
    return [*cards, card]


def _sortie_defender(mem, order):
    """The opposing general this sortie's target castle is known to hold.

    #1553: 出撃前の画面に対戦相手の表示は無いため、判明の根拠はその城での
    前回戦闘パネル (``mem['castle_defenders']``、flows.battle が記録) だけ。
    章が違う記録・ボス城・最大HPが読めない将軍は判明扱いしない (fail-closed)。
    """
    if _is_boss_order(order, mem):
        return None
    record = (mem.get('castle_defenders') or {}).get(order.get('target'))
    if not isinstance(record, dict) or record.get('chapter') != (mem.get('chapter') or 0):
        return None
    enemy = record.get('general')
    if (not isinstance(enemy, str) or enemy in reference.BOSS_GENERALS
            or enemy in chart.BOSSES.values() or general_max_hp(enemy) is None):
        return None
    return enemy


def _with_strong_card(cards, strong, mem, step):
    """A carried strong card only when a slot, a budget and the kit allow it.

    予定札(チャート/調整/レア札)は外さない。gcgx: 携行札のID合計が48以上だと敵が
    エッグを使うため、追加後も ``< 48`` を満たす時だけ1枚足す。同じ札がもう1枚
    携行済みなら足さない(在庫を無駄にしない)。この出撃で消費・破棄した札は
    再び足さない: 一覧に無い札を開くと空の一覧で止まる(g438 04:18)。
    """
    return _with_extra_card(cards, strong, mem, step)


RARE_SCAN_LIMIT = 32          # the entire 32-card catalogue, once per observed month
CARRY_SLOTS = 3               # card_select「あとNこ」の携行枠(実測 header は 0..3)
# One Down moves the cursor one row and, from the fourth row, scrolls the panel
# one row. Reaching the last row of the catalogue therefore costs up to
# ``rows - 1`` presses, so the fixed 8 only walked four rows of a full list.
# g1163 2026-11-08 23:55-23:56: the panel held 15 rows in card-ID order
# (1,2,3,5,6,7,8,11,...); 8 Downs reached ノリウツール(11), and the planned
# エンジェリン(22), マグネガキン(27) and ファバード(31) were never shown, so
# each boss sortie held five readings and then dropped the card (196 scroll
# decisions, 68 holds and 16 drops of those three cards in 1.5 h). Bound the
# search by the whole catalogue, like the rare scan, instead of by a guess.
CARD_SCROLL_LIMIT = len(reference.ALL_CARD_IDS)
CARD_MISS_LIMIT = 5           # card_select readings before a missing card is left behind
CARD_UNREADABLE_LIMIT = 6     # unreadable card_select readings before cancelling the sortie
NORMAL_CARD_UNREADABLE_LIMIT = 3  # an ordinary sortie escapes the list sooner (owner: depart anyway)
SORTIE_CONFIRM_UNREADABLE_LIMIT = 3  # an ordinary unreadable confirmation departs after this many
SORTIE_CONFIRM_LIMIT = 8      # readings before a readable but mismatched kit is approved
CARD_STOCK_LIMIT = 24         # observed card names kept for the adjusted-chart request


def _card_panel_complete(inventory) -> bool:
    """True only when the measured panel drew neither scroll arrow.

    A きりふだ panel is a four-row window, so a short panel is not a complete
    inventory when a row above disappeared (▲) or more rows follow (▼). Only
    the measured absence of both arrows proves the visible rows are all rows;
    a missing key or None means the arrows could not be read and is never
    treated as complete.
    """
    return inventory.get('more_above') is False and inventory.get('more_below') is False


def _note_ledger_drift(mem, card, read, basis):
    """Record a read that contradicts the field-inventory ledger before fixing it.

    The read wins (the panel is the measurement), but a difference means the
    ledger moved without the bot's own actions: a purchase the merchant did
    not actually take, a card returned by a cancelled sortie, or a kind the
    bot wrote twice. One record per card and read, so a whole-window read
    leaves each mismatch visible in the decision log.
    """
    from .bargains import held
    previous = held(mem, card)
    if previous is None or previous == read:
        return False
    _record(mem, 'ledger_drift', card=card,
            observed_metric={'ledger': previous, 'read': read, 'basis': basis},
            reason='切り札の台帳と実測の在庫が食い違ったため、読みを正として台帳を上書きする'
                   '(購入・携行・帰還のいずれかで台帳が動いた証拠)')
    from . import card_census as _card_census
    _card_census.book(mem, 'ledger_drift')
    return True


def _observe_card_stock(mem, inventory):
    """Remember the stocks a sortie card panel showed and reconcile the ledger.

    A complete panel -- neither the ▲ nor the ▼ scroll arrow is drawn -- lists
    the whole inventory (depleted items disappear), so a known card absent
    from it is out of stock now and recorded as 0. A panel with either arrow,
    or with unreadable arrows (None), is a window and only its rows are
    updated. Row count alone proves nothing: g1288 2026-11-21 23:10:40 read a
    three-row tail (キャトルミュー was carried, ▲ still drawn) and the old
    "fewer than four rows is everything" rule zeroed the 17 cards above it,
    including イッテツーン at 99.

    g438 04:04: the adjusted chart planned ミックミー and エンジェリン for
    chapter 1, where neither was ever in the panel -- the model was never told
    what the player actually holds. The request now carries this so it plans
    from observed stock.

    Rows are listed in card-ID order, so a known card whose ID lies between
    two shown rows' IDs is out of stock too (the proof of #2374's
    ``_card_proven_absent``). g825 2-5..2-9: 1-B1's クースカン(13) was gone
    from ブラッキー/フットバース/ノリウツール(11)/バルムンク(16) panels while
    card_stock kept its stale 1, so no month saw the boss card missing.
    A card read here is no longer an unseen purchase (``boss_cards.UNSEEN``).
    ``card_stock`` is the field-inventory ledger (unknown = no key), so a read
    that differs from it is logged as ``ledger_drift`` before the read wins.
    """
    stock = mem.setdefault('card_stock', {})
    shown = {row['card']: row['stock'] for row in inventory['rows']}
    for card, read in shown.items():
        _note_ledger_drift(mem, card, read, 'window')
    stock.update(shown)
    read_cards = set(shown)
    unseen = mem.get('card_bought_unseen')
    unseen = unseen if isinstance(unseen, dict) else {}
    ids = [CARD_IDS.get(row['card']) for row in inventory['rows']]
    ordered = bool(ids) and None not in ids and ids == sorted(ids)
    complete = _card_panel_complete(inventory)
    for card in [*stock, *(c for c in unseen if c not in stock)]:
        if card not in shown and (complete or (
                ordered and min(ids) < CARD_IDS.get(card, -1) < max(ids))):
            _note_ledger_drift(mem, card, 0, 'proof')
            stock[card] = 0
            read_cards.add(card)
    for card in read_cards.intersection(unseen):
        unseen.pop(card)
    revived = False
    zero = mem.get('card_zero_at_start')
    if isinstance(zero, dict) and isinstance(zero.get('cards'), list):
        kept = [c for c in zero['cards'] if shown.get(c, 0) <= 0]
        if len(kept) != len(zero['cards']):
            zero['cards'] = kept
            revived = True
    while len(stock) > CARD_STOCK_LIMIT:
        stock.pop(next(iter(stock)))
    return revived


def _buy_targets(mem):
    """Card kinds some current plan can still ask a merchant to sell.

    A proven 0 that no plan will ever buy may be dropped when the ledger is
    trimmed, but a 0 the month plan can buy must stay: S2 skips an unknown
    kind, so a dropped 0 is never bought again (owner policy: 99 copies of
    each kind). Imports are local: bargains/boss_cards both read this module.
    """
    from .bargains import BARGAIN_TARGETS, RICH_MERCHANT_CARDS
    targets = set(RICH_MERCHANT_CARDS) | set(BARGAIN_TARGETS)
    from .boss_cards import boss_kit
    kit = boss_kit(mem)
    if kit is not None:
        targets.update(kit[1])
    chapter = mem.get('chapter')
    if type(chapter) is int:
        for plan in chart.purchases(chapter):
            for row in plan.get('cards') or ():
                if row:
                    targets.add(row[0])
    return targets


def observe_full_card_inventory(mem, rows):
    """Replace the field-inventory ledger from a complete field-list read.

    The field list (X → きりふだ) is the whole inventory: every catalogue card
    absent from it is proven at 0, and every shown row is its measured count.
    A difference from the previous ledger is recorded per card as
    ``ledger_drift`` with ``basis='full'`` before the read wins (a purchase the
    merchant refused, a card returned or spent outside the bot's ledger).

    Returns ``(read_cards, dropped)``: the cards written, and the entries the
    unchanged ``CARD_STOCK_LIMIT`` (24) dropped -- the read is still the proof,
    the dropped names are only reported so a truncated ledger stays visible.

    The limit keeps the cards the bot can actually use first: positive stock
    (in count order), then the proven zeros some plan can still buy
    (``_buy_targets``), then the other zeros; card ID inside each rank.
    S2's window reads only inserted the rows they saw, but a full read always
    rebuilds all 32 catalogue kinds. Dropping the lowest IDs threw away the
    common 99s (イッテツーン at ID 0) that sorties carry; dropping a usable
    0 loses it for good, because S2 never buys an unknown kind (ファバード
    and エンジェリン are high-ID rich-merchant cards). Ties stay in card-ID
    order, the list's own order, so the choice is deterministic.
    """
    shown = {row['card']: row['stock'] for row in rows}
    stock = mem.setdefault('card_stock', {})
    for card, read in shown.items():
        _note_ledger_drift(mem, card, read, 'full')
    ordered = sorted(CARD_IDS, key=lambda name: CARD_IDS[name])
    for card in ordered:
        if card not in shown:
            _note_ledger_drift(mem, card, 0, 'full')
    targets = _buy_targets(mem)

    def rank(name):
        count = shown.get(name, 0)
        if count > 0:
            return (0, -count, CARD_IDS[name])
        if name in targets:
            return (1, 0, CARD_IDS[name])
        return (2, 0, CARD_IDS[name])

    keep = set(sorted(ordered, key=rank)[:CARD_STOCK_LIMIT])
    dropped = {card: shown.get(card, 0) for card in ordered if card not in keep}
    stock.clear()
    for card in ordered:
        if card in keep:
            stock[card] = shown.get(card, 0)
    unseen = mem.get('card_bought_unseen')
    if isinstance(unseen, dict):
        unseen.clear()
    zero = mem.get('card_zero_at_start')
    if isinstance(zero, dict) and isinstance(zero.get('cards'), list):
        zero['cards'] = [c for c in zero['cards'] if stock.get(c, 0) <= 0]
    return [card for card in ordered if card in keep], dropped
