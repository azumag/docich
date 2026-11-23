"""The card inventory is a field ledger (unknown = no key), bought by difference.

Owner decision 2026-11-22: the per-kind limit is 99 copies of the *field*
inventory, so a merchant visit buys only what the ledger says is missing. A
carried card already left the field when the sortie panel took it (S0b) and a
battle spends the carried copy, so neither moves the field stock again — the
v1333 estimate ``stock + bought - used`` subtracted those copies twice and
asked for more than the hand could take.

Screens here are synthetic; no live purchase is claimed.
"""
from docich import hanjuku_policy as p
from docich.hanjuku.flows.deploy import _cursor_card_step
from docich.hanjuku.strategy import bargains as b
from docich.hanjuku.strategy import boss_cards
from docich.hanjuku.strategy.economy import KNOWN_PRICES
from docich.hanjuku.strategy.kit import _observe_card_stock
from docich.hanjuku.strategy.orders import _finish_order
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_merchant_reserve import memory, quote

# The seven kinds production v1333 refused on 2026-11-21 23:13 (all at 99).
SEVEN = {'ノリウツール': 99, 'ミックミー': 99, 'グリンボー': 99, 'イッテツーン': 99,
         'ころぼぐんだん': 99, 'フットバース': 99, 'ピッグローラー': 99}


def shop_state(gold=9000, **extra):
    return {'key': '1-9', 'items': [], 'soldiers': 0, 'soldiers_done': True,
            'gold_start': gold, 'merchant_done': False, **extra}


def plan(mem, shop, listed, gold=9000, month=9):
    b.plan_bargains(mem, shop, {'year': 1, 'month': month, 'gold': gold}, listed=listed)
    return shop


def listing(gold=300, rows=(('ノリウツール', 18),)):
    c = Canvas(); c.text(48, 15, f'1ねん 5のつき {gold}G')
    for i, (name, price) in enumerate(rows):
        c.text(120, 175 + i * 16, name); c.text(216, 175 + i * 16, f'{price}G')
    c.hand(98, 169)
    screen = parse(c.frame()); screen.kind = 'shop_list'
    return screen


def records(mem, kind):
    return [r for r in mem.get('_records', ()) if r['decision'] == kind]


# ------------------------------------------------------------ the ledger
def test_a_missing_key_is_unknown_and_never_zero():
    mem = {'card_stock': {'ノリウツール': 97}}
    assert b.held(mem, 'ノリウツール') == 97 and b.hold_room(mem, 'ノリウツール') == 2
    assert b.held(mem, 'クースカン') is None and b.hold_room(mem, 'クースカン') is None
    # A malformed value is unknown too, not a zero.
    assert b.held({'card_stock': {'クースカン': None}}, 'クースカン') is None
    assert b.held({'card_stock': {'クースカン': -1}}, 'クースカン') is None


def test_a_confirmed_purchase_of_a_known_kind_moves_only_the_difference():
    mem = memory(complete=True)
    mem['card_stock'] = {'ノリウツール': 90}
    mem['shop'].update(egg='done', items=[['ノリウツール', 9]], want=['ノリウツール', 9, 18])
    assert p.quantity_step(quote(999, count=9, cost=162), mem) == [p.pad('a')]
    assert mem['card_stock']['ノリウツール'] == 99
    # The retired estimate ledger is not written any more.
    assert 'card_bought_since_read' not in mem and 'card_used_since_read' not in mem


def test_a_purchase_of_an_unknown_kind_goes_to_unseen_not_to_the_ledger():
    mem = memory(complete=True)          # chapter 1 buys クースカン for 1-B1
    mem['card_stock'] = {}
    mem['shop'].update(egg='done', items=[['クースカン', 1]], want=['クースカン', 1, 24])
    assert p.quantity_step(quote(999, count=1, cost=24), mem) == [p.pad('a')]
    assert 'クースカン' not in mem['card_stock']          # still unknown, not 0/1
    assert mem['card_bought_unseen'] == {'クースカン': 1}


def test_a_purchase_of_a_known_kind_never_enters_unseen():
    mem = memory(complete=True)
    mem['card_stock'] = {'クースカン': 0}
    mem['shop'].update(egg='done', items=[['クースカン', 1]], want=['クースカン', 1, 24])
    assert p.quantity_step(quote(999, count=1, cost=24), mem) == [p.pad('a')]
    assert mem['card_stock']['クースカン'] == 1
    assert not mem.get('card_bought_unseen')


# ------------------------------------------------------------ the plan
def test_the_v1333_seven_kinds_at_99_are_not_planned_again():
    """v1333 refused 40 asks on cards the bot already held at 99."""
    mem = {'chapter': 1, 'month': '1-9', 'tick': 1, 'card_stock': dict(SEVEN)}
    shop = plan(mem, shop_state(), {name: KNOWN_PRICES[name] for name in SEVEN})
    assert shop['rich_merchant'] is True
    assert shop['items'] == []


def test_unknown_kinds_are_never_bought_with_spare_cash():
    # Only ノリウツール is known: every other rich/bargain target is unknown.
    mem = {'chapter': 1, 'month': '1-9', 'tick': 1, 'card_stock': {'ノリウツール': 10}}
    listed = {name: KNOWN_PRICES[name] for name in b.RICH_MERCHANT_CARDS}
    listed.update({'クースカン': 24, 'ゼンマイン': 32, 'マグネガキン': 34})
    shop = plan(mem, shop_state(), listed)
    planned = dict(shop['items'])
    assert planned.get('ノリウツール') == 89        # 99 - 10, the only known kind
    for name in (*b.RICH_MERCHANT_CARDS, *b.BARGAIN_TARGETS):
        if name != 'ノリウツール':
            assert name not in planned, name


def test_an_ordinary_merchant_also_leaves_unknown_kinds_alone():
    mem = {'chapter': 1, 'month': '1-9', 'tick': 1, 'card_stock': {}}
    shop = plan(mem, shop_state(gold=600), {'クースカン': 24, 'ゼンマイン': 32}, gold=600)
    assert shop.get('rich_merchant') is not True
    assert shop['items'] == []


def test_a_charted_purchase_stays_planned_when_its_kind_is_unknown():
    mem = {'chapter': 1, 'month': '1-9', 'tick': 1, 'card_stock': {'ノリウツール': 10}}
    shop = shop_state(items=[['クースカン', 1]])
    plan(mem, shop, {'クースカン': 24})
    assert ['クースカン', 1] in shop['items']


def test_a_rich_merchant_buys_only_the_difference_to_99():
    mem = {'chapter': 1, 'month': '1-9', 'tick': 1, 'card_stock': {'ノリウツール': 90}}
    shop = plan(mem, shop_state(), {'ノリウツール': 18})
    assert shop['items'] == [['ノリウツール', 9]]


def test_a_rich_merchant_takes_a_bargain_target_to_99_too():
    """Owner 2026-11-22: BARGAIN_TARGETS are stocked to 99 at 1000G+ as well."""
    mem = {'chapter': 1, 'month': '1-9', 'tick': 1, 'card_stock': {'クースカン': 50}}
    shop = plan(mem, shop_state(), {'クースカン': 24})
    assert 'クースカン' not in b.RICH_MERCHANT_CARDS      # only a bargain target
    assert shop['items'] == [['クースカン', 49]]


def test_an_ordinary_merchant_keeps_the_monthly_cap_for_a_bargain_target():
    mem = {'chapter': 1, 'month': '1-9', 'tick': 1, 'card_stock': {'クースカン': 50}}
    shop = plan(mem, shop_state(gold=600), {'クースカン': 24}, gold=600)
    assert shop.get('rich_merchant') is not True
    assert 0 < dict(shop['items'])['クースカン'] <= b.BARGAIN_MONTHLY_COPIES


def test_a_full_known_kind_is_skipped_without_asking():
    mem = memory(complete=True)
    mem['egg_uses'] = {}
    mem['card_stock'] = {'ノリウツール': 99}
    mem['shop'].update(items=[['ノリウツール', 1]], bargain_listing_planned=True)
    assert p.shop_step(listing(), mem) == [p.pad('b')]
    assert not mem['shop'].get('bought')
    assert records(mem, 'buy_skip')[0]['deviation_reason'] == 'holding_cap'


def test_an_unknown_kind_is_asked_as_a_necessity():
    mem = memory(complete=True)
    mem['egg_uses'] = {}
    mem['card_stock'] = {}
    mem['shop'].update(items=[['ノリウツール', 1]], bargain_listing_planned=True)
    assert p.shop_step(listing(), mem) == [p.pad('a')]
    assert mem['shop']['want'] == ['ノリウツール', 1, 18]   # a missing key is not 99


# ------------------------------------------------------------ carrying
def test_one_carried_copy_is_the_row_minus_one():
    from test_hanjuku_boss_card_proven_absent import memory as boss_memory, panel
    mem = boss_memory(picked=('ノリウツール',))
    mem['card_stock'] = {'クースカン': 93}
    screen = panel(('ブラッキー', 'フットバース', 'ノリウツール', 'クースカン'),
                   stocks=('1', '1', '2', '93'), remaining='3', selected=3)
    assert p.deploy_step(screen, mem) == [p.pad('a')]
    assert mem['card_stock']['クースカン'] == 92
    assert mem['picked'] == ['ノリウツール', 'クースカン']


def test_the_batched_carry_path_takes_the_whole_row_off_the_ledger():
    """Chapter 9 takes the highlighted row's remaining slots with one A run."""
    mem = {'chapter': 9, 'variant': 'chart', 'tick': 1, 'picked': []}
    order = {'step': '9-1', 'general': 'どうし', 'target': 'アルマムーン', 'cards': ()}
    inventory = {'rows': [{'card': 'クースカン', 'stock': 93, 'y': 55}],
                 'selected_y': 55, 'remaining': 3, 'inventory_evidence': {},
                 'observed_rows': []}
    actions = _cursor_card_step(None, mem, order, inventory, mem['picked'])
    assert actions[0] == p.pad('a') and len(actions) == 5   # A, wait, A, wait, A
    assert mem['picked'] == ['クースカン'] * 3
    assert mem['card_stock']['クースカン'] == 90


def test_a_battle_card_use_never_moves_the_field_ledger():
    """A spent card left the field when it was carried (S0b)."""
    from test_hanjuku_retreat_recheck import memory as battle_memory
    mem = battle_memory()
    mem['card_stock'] = {'イッテツーン': 90}
    mem['battle'] = {'ally': 'ゼウス', 'enemy': 'タピオカ', 'ally_hp': 6, 'enemy_hp': 35,
                     'start_ally_hp': 24, 'side': 'attack', 'castle': 'キカンドン',
                     'away': 1, 'cards_used': ['イッテツーン'],
                     'card_consumption_complete': True}
    p.battle_end(mem, 'map')
    assert mem['card_stock'] == {'イッテツーン': 90}
    assert 'card_used_since_read' not in mem
    assert not hasattr(b, 'note_card_used')


def test_a_cancelled_sortie_makes_its_carried_cards_unknown_again():
    mem = {'chapter': 1, 'tick': 5, 'card_stock': {'クースカン': 91, 'ノリウツール': 5},
           'picked': ['クースカン', 'クースカン'], 'orders': {'1-B1': 'pending'},
           'active': '1-B1'}
    _finish_order(mem, 'failed', reason='テスト用の取り消し')
    assert 'クースカン' not in mem['card_stock']          # unknown, not a guess
    assert mem['card_stock'] == {'ノリウツール': 5}
    assert mem['picked'] == []


def test_an_unconfirmed_sortie_makes_its_carried_cards_unknown_again():
    """A picker closed without a recorded send may return the carried kit.

    The field ledger had already dropped the three carried copies (99 -> 96),
    so leaving 96 there made the next merchant refuse them as over the limit.
    """
    mem = {'chapter': 1, 'tick': 5, 'card_stock': {'イッテツーン': 96, 'ノリウツール': 5},
           'picked': ['イッテツーン', 'イッテツーン', 'イッテツーン'],
           'orders': {'1-B1': 'pending'}, 'active': '1-B1'}
    _finish_order(mem, 'launched_unconfirmed', reason='テスト用の未確定')
    assert 'イッテツーン' not in mem['card_stock']       # unknown, not a guess
    assert mem['card_stock'] == {'ノリウツール': 5}
    assert mem['picked'] == []
    assert b.hold_room(mem, 'イッテツーン') is None


def test_a_launched_sortie_keeps_the_carried_ledger():
    mem = {'chapter': 1, 'tick': 5, 'card_stock': {'イッテツーン': 96, 'ノリウツール': 5},
           'picked': ['イッテツーン', 'イッテツーン', 'イッテツーン'],
           'orders': {'1-B1': 'pending'}, 'active': '1-B1'}
    _finish_order(mem, 'launched', reason='テスト用の出撃確定')
    assert mem['card_stock']['イッテツーン'] == 96      # the copies left the field
    assert mem['card_stock']['ノリウツール'] == 5
    assert mem['picked'] == []


def test_the_next_rich_month_does_not_plan_the_forgotten_kind():
    from test_hanjuku_rich_merchant_entry import memory as rich_memory
    mem = rich_memory({'イッテツーン': 96, 'ノリウツール': 5})
    mem.update(active='1-B1', orders={'1-B1': 'pending'},
               picked=['イッテツーン', 'イッテツーン', 'イッテツーン'])
    _finish_order(mem, 'launched_unconfirmed', reason='テスト用の未確定')
    assert b.hold_room(mem, 'イッテツーン') is None
    shop = p._plan(mem, {'year': 1, 'month': 3, 'gold': 9999})
    assert 'イッテツーン' not in dict(shop['items'])
    assert 'ノリウツール' in dict(shop['items'])        # the plan still ran


# ------------------------------------------------------------ reads
def test_a_window_read_that_differs_from_the_ledger_is_logged_once():
    mem = {'card_stock': {'ノリウツール': 99}}
    _observe_card_stock(mem, {'rows': [{'card': 'ノリウツール', 'stock': 97}], 'remaining': 1})
    assert mem['card_stock']['ノリウツール'] == 97
    drift = records(mem, 'ledger_drift')
    assert len(drift) == 1
    assert drift[0]['card'] == 'ノリウツール'
    assert drift[0]['observed_metric'] == {'ledger': 99, 'read': 97, 'basis': 'window'}
    _observe_card_stock(mem, {'rows': [{'card': 'ノリウツール', 'stock': 97}], 'remaining': 1})
    assert len(records(mem, 'ledger_drift')) == 1        # the same value is not drift


def test_an_id_gap_proof_that_zeroes_a_kind_is_logged_as_such():
    mem = {'card_stock': {'イッテツーン': 5, 'フットバース': 3, 'ハリケーン': 5}}
    rows = [{'card': 'イッテツーン', 'stock': 7}, {'card': 'ブラッキー', 'stock': 2},
            {'card': 'ダンスライン', 'stock': 4}]
    _observe_card_stock(mem, {'rows': rows})
    drift = {r['card']: r['observed_metric'] for r in records(mem, 'ledger_drift')}
    assert drift == {'イッテツーン': {'ledger': 5, 'read': 7, 'basis': 'window'},
                     'フットバース': {'ledger': 3, 'read': 0, 'basis': 'proof'}}
    assert mem['card_stock']['ハリケーン'] == 5          # outside the window, unchanged


# ------------------------------------------------------------ double counting
def test_a_stale_unseen_receipt_is_not_added_on_top_of_the_ledger():
    """Old saved state kept both; summing them hid a real shortage."""
    mem = {'card_stock': {'クースカン': 0}, 'card_bought_unseen': {'クースカン': 1}}
    assert boss_cards._shortage(mem, {'クースカン': 1}, []) == {'クースカン': 1}
    mem = {'card_stock': {'クースカン': 1}}
    assert boss_cards._shortage(mem, {'クースカン': 1}, []) == {}


def test_a_known_purchase_feeds_the_rebuy_shortage_exactly_once():
    mem = {'chapter': 1, 'month': '1-9', 'card_stock': {'クースカン': 0},
           'orders': {'1-B1': 'pending'}}
    b.note_card_bought(mem, 'クースカン', 1)
    boss_cards.note_card_purchase(mem, 'クースカン', 1)
    assert mem['card_stock']['クースカン'] == 1 and not mem.get('card_bought_unseen')
    assert boss_cards._shortage(mem, {'クースカン': 1}, []) == {}


def test_a_known_kind_ask_is_capped_by_its_room():
    """A necessity keeps its ask when unknown, but never past 99 when known."""
    mem = memory(complete=True)
    mem['egg_uses'] = {}
    mem['card_stock'] = {'ノリウツール': 97}
    mem['shop'].update(items=[['ノリウツール', 5]], bargain_listing_planned=True)
    assert p.shop_step(listing(), mem) == [p.pad('a')]
    assert mem['shop']['want'] == ['ノリウツール', 2, 18]
