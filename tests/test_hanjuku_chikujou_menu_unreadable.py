"""docich#5076: an unreadable ちくじょう row ended the whole month silently.

Live g1190 ch1 9-1 closed with 36G against a 30G reserve (the bot's own
affordability gate passes at 31G), every castle still buildable, and no
ちくじょう decision at all, so the month audit could only say 判定理由: なし.
`_month_chikujou` marked the row skipped and returned None whenever the
month menu frame could not be read, and the month then closed with its only
build slot unspent.
"""
from docich.hanjuku.flows import month as month_flow
from tests.test_hanjuku_multi_chikujou import HOME, OTHER, month


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
