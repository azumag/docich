"""docich#5076: an unreadable ちくじょう row ended the whole month silently.

Live g1190 ch1 9-1 closed with 36G against a 30G reserve (the bot's own
affordability gate passes at 31G), every castle still buildable, and no
ちくじょう decision at all, so the month audit could only say 判定理由: なし.
`_month_chikujou` marked the row skipped and returned None whenever the
month menu frame could not be read, and the month then closed with its only
build slot unspent.
"""
from docich.hanjuku.flows import month as month_flow
from docich import hanjuku_policy as policy
from tests.test_hanjuku_multi_chikujou import HOME, OTHER, line, month
from docich.hanjuku.os_layer.screen import _header


def menu_without_build(gold=36):
    """The measured month menu with the ちくじょう row unread (OCR miss)."""
    screen = month(gold)
    screen.lines = [line for line in screen.lines if 'ちくじょう' not in line.known]
    screen.text = screen.text.replace('ちくじょう', '')
    return screen


def memory(**shop_updates):
    shop = {'key': '1-9', 'chikujou': 'check', 'gold_start': 36,
            'soldiers_done': True, 'merchant_done': True, 'items': [],
            'reserve': 30, 'egg': 'done', 'recruit_reserve': 0,
            **shop_updates}
    return {'chapter': 1, '_records': [], 'captured': [OTHER],
            'garrison': {HOME: ['ゼウス'], OTHER: ['ココット']}, 'shop': shop}


def records_of(mem):
    return [row for row in mem['_records']
            if row.get('decision') == 'chikujou_menu_unreadable']


def test_unreadable_row_holds_the_month_before_giving_up():
    mem = memory()
    shop = mem['shop']
    for expected in range(1, month_flow.CHIKUJOU_MENU_WAIT + 1):
        assert month_flow._month_chikujou(menu_without_build(), mem, shop) == []
        # The build slot stays open while the row is merely unread.
        assert shop['chikujou'] == 'check'
        assert shop['chikujou_menu_wait'] == expected
    assert month_flow._month_chikujou(menu_without_build(), mem, shop) is None
    assert shop['chikujou'] == 'skipped'


def test_the_give_up_records_why_the_month_ended_without_building():
    mem = memory()
    shop = mem['shop']
    for _ in range(month_flow.CHIKUJOU_MENU_WAIT + 1):
        month_flow._month_chikujou(menu_without_build(), mem, shop)
    rows = records_of(mem)
    assert [row['observed_metric']['waits'] for row in rows] == list(
        range(1, month_flow.CHIKUJOU_MENU_WAIT + 2))
    last = rows[-1]
    assert last['deviation_reason'] == 'menu_row_unreadable'
    assert last['observed_metric']['skipped'] is True
    assert last['observed_metric']['gold'] == 36
    assert last['observed_metric']['reserve'] == 30
    assert last['month'] == '1-9'


def test_a_readable_row_still_opens_without_waiting():
    """The ordinary path is unchanged: a readable ちくじょう row opens at once."""
    mem = memory()
    shop = mem['shop']
    actions = month_flow._month_chikujou(month(36), mem, shop)
    assert shop['chikujou'] == 'opened'
    assert records_of(mem) == []
    assert actions is not None


def test_unread_gold_waits_then_opens_when_the_balance_is_readable():
    """A transient unread balance must not consume the month's build slot."""
    mem = memory()
    shop = mem['shop']
    screen = month(150)
    screen.header['gold'] = None

    assert month_flow._month_chikujou(screen, mem, shop) == []
    assert shop['chikujou'] == 'check'
    assert shop['chikujou_affordability_wait'] == 1

    screen.header['gold'] = 150
    assert month_flow._month_chikujou(screen, mem, shop) == [month_flow.pad('a')]
    assert shop['chikujou'] == 'opened'
    assert 'chikujou_affordability_wait' not in shop
    assert not records_of(mem)


def test_unread_reserve_is_bounded_and_ends_unverified_without_spending():
    """Unknown necessity cash never authorizes optional construction."""
    mem = memory(hero_repair_reserve=None)
    shop = mem['shop']
    screen = month(216)

    for expected in range(1, month_flow.CHIKUJOU_AFFORDABILITY_WAIT + 1):
        assert month_flow._month_chikujou(screen, mem, shop) == []
        assert shop['chikujou'] == 'check'
        assert shop['chikujou_affordability_wait'] == expected

    assert month_flow._month_chikujou(screen, mem, shop) is None
    assert shop['chikujou'] == 'unverified'
    rows = [row for row in mem['_records']
            if row.get('decision') == 'chikujou_affordability_wait']
    assert [row['observed_metric']['waits'] for row in rows] == [1, 2, 3]
    terminal = [row for row in mem['_records']
                if row.get('decision') == 'chikujou_affordability_unverified']
    assert len(terminal) == 1
    assert terminal[0]['observed_metric']['waits'] == 4
    assert terminal[0]['observed_metric']['skipped'] is True
    assert terminal[0]['observed_metric']['gold'] == 216
    assert terminal[0]['observed_metric']['reserve'] is None
    assert not mem.get('month_sub')


def test_known_insufficient_balance_still_skips_without_waiting():
    mem = memory()
    shop = mem['shop']
    assert month_flow._month_chikujou(month(30), mem, shop) is None
    assert shop['chikujou'] == 'skipped'
    assert 'chikujou_affordability_wait' not in shop
    assert not [row for row in mem['_records']
                if row.get('decision') == 'chikujou_affordability_wait']

def test_malformed_or_negative_gold_waits_instead_of_confirming_a_shortage():
    """Only a non-negative integer balance is sufficient to close the gate."""
    for malformed in (-1, True, 1.5, '150'):
        mem = memory()
        shop = mem['shop']
        screen = month(150)
        screen.header['gold'] = malformed

        assert month_flow._month_chikujou(screen, mem, shop) == []
        assert shop['chikujou'] == 'check'
        assert shop['chikujou_affordability_wait'] == 1
        row = next(row for row in mem['_records']
                   if row.get('decision') == 'chikujou_affordability_wait')
        assert row['observed_metric']['gold'] is None
        assert row['observed_metric']['gold_readable'] is False
        assert row['observed_metric']['reserve_readable'] is True

        screen.header['gold'] = 150
        assert month_flow._month_chikujou(screen, mem, shop) == [month_flow.pad('a')]
        assert shop['chikujou'] == 'opened'
        assert 'chikujou_affordability_wait' not in shop




def test_month_controller_retries_when_the_entire_hud_header_is_unreadable():
    """A torn gold digit must not bypass the scoped, unfinished build plan."""
    mem = memory()
    shop = mem['shop']
    shop['key'] = '1-7'
    mem.update(month='1-7', month_chapter=1)
    unread = month(150)
    unread.lines.append(line(0, 16, '1ねん7のつき1?0G'))
    unread.header = _header(unread.lines)
    assert unread.header is None

    # The first unread header is a no-input wait, not an exit confirmation.
    assert month_flow.month_step(unread, mem) == []
    assert shop['chikujou'] == 'check'
    assert shop['chikujou_affordability_wait'] == 1
    assert not [row for row in mem['_records'] if row.get('decision') == 'month_done']

    # A fresh, complete header restores the ordinary build path immediately.
    assert month_flow.month_step(month(150), mem) == [month_flow.pad('a')]
    assert shop['chikujou'] == 'opened'
    assert 'chikujou_affordability_wait' not in shop


def test_month_controller_records_unread_header_after_the_bounded_wait():
    mem = memory()
    shop = mem['shop']
    shop['key'] = '1-7'
    mem.update(month='1-7', month_chapter=1)
    unread = month(150)
    unread.lines.append(line(0, 16, '1ねん7のつき1?0G'))
    unread.header = _header(unread.lines)
    assert unread.header is None

    for _ in range(month_flow.CHIKUJOU_AFFORDABILITY_WAIT):
        assert month_flow.month_step(unread, mem) == []
    assert shop['chikujou'] == 'check'

    month_flow.month_step(unread, mem)
    assert shop['chikujou'] == 'unverified'
    assert shop['chikujou_header_unverified'] is True
    terminal = [row for row in mem['_records']
                if row.get('decision') == 'chikujou_affordability_unverified']
    assert len(terminal) == 1
    assert terminal[0]['deviation_reason'] == 'month_header_unreadable'
    assert terminal[0]['observed_metric']['header_readable'] is False
    assert terminal[0]['observed_metric']['gold_readable'] is False


def test_pending_soldier_receipt_does_not_spend_unread_header_build_retries():
    """Wait for the refill debit receipt before deciding whether construction is affordable."""
    mem = memory()
    shop = mem['shop']
    shop['key'] = '1-7'
    mem.update(month='1-7', month_chapter=1)
    shop['soldier_receipt'] = {
        'status': 'pending', 'key': '1-7', 'qty': 25,
        'gold_before': 175, 'soldiers_before': 20,
    }

    unread = month(150)
    unread.lines.append(line(0, 16, '1ねん7のつき1?0G'))
    unread.header = _header(unread.lines)
    assert unread.header is None

    policy.observe_events(unread, mem)
    assert month_flow.month_step(unread, mem) == []
    assert shop['soldier_receipt']['status'] == 'pending'
    assert shop['chikujou'] == 'check'
    assert 'chikujou_affordability_wait' not in shop
    assert not [row for row in mem['_records']
                if row.get('decision') == 'chikujou_affordability_wait']

    # The next complete frame proves the exact 25G debit. Only then may the
    # ordinary affordability gate open the construction menu.
    readable = month(150)
    policy.observe_events(readable, mem)
    assert shop['soldier_receipt']['status'] == 'paid'
    assert month_flow.month_step(readable, mem) == [month_flow.pad('a')]
    assert shop['chikujou'] == 'opened'
    assert 'chikujou_affordability_wait' not in shop
