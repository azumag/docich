"""フィールドの「きりふだ」一覧の読み取りと、全所持の読み直し手順 (S3, v1352).

The field list (X → きりふだ) is the only screen that proves the whole field
inventory: eight rows per page in card-ID order, one row per ↓, ▲/▼ drawn
only while rows are hidden above/below.  A full read overwrites ``card_stock``
so every catalogue kind absent from the list becomes a proven 0, and every
metered purchase/carry difference is logged as ``ledger_drift`` (basis
``full``) before the read wins.
"""
from pathlib import Path

import pytest

from docich.hanjuku.flows import card_census as census_flow
from docich.hanjuku.flows.shop import _holding_limit_step
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.font import TextLine
from docich.hanjuku.os_layer.pixels import Frame, read_png
from docich.hanjuku.os_layer.readers import _measured_field_card_list
from docich.hanjuku.os_layer.screen import FIELD_CARD_LIST_ROWS, Screen, parse
from docich.hanjuku.strategy import card_census as booking
from docich.hanjuku.strategy.campaign import _enter_chapter
from docich.hanjuku.strategy.kit import CARD_IDS, _observe_card_stock
from docich.hanjuku_bot import decide

FIXTURES = Path(__file__).parent / 'fixtures/hanjuku'
ORDER = sorted(CARD_IDS, key=lambda name: CARD_IDS[name])
MENU = (('ステータス', 39), ('しょうぐん', 55), ('しろ', 71), ('きりふだ', 87),
        ('システム', 103), ('けんのけいこ', 119), ('セーブ', 135))
IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g1288-abcdef',
            'generation': 1288, 'lease_id': 'unit-test'}


# ---------------------------------------------------------------- fixtures
def arrow_frame(above=False, below=False):
    """A 256x224 frame whose only lit pixels are the list's ▲/▼."""
    rgb = bytearray(256 * 224 * 3)

    def paint(x0, y0, x1, y1):
        for y in range(y0, y1):
            for x in range(x0, x1):
                i = (y * 256 + x) * 3
                rgb[i:i + 3] = b'\xf8\xfc\xf8'

    if above:
        paint(72, 25, 79, 29)
    if below:
        paint(72, 161, 79, 163)
    return Frame(256, 224, bytes(rgb))


def stock_rows(ids, *, base=3):
    """(card, stock) for catalogue IDs, so stocks follow the page order."""
    return [(ORDER[index], (index + 1) * base) for index in ids]


def list_screen(rows, above, below):
    lines = []
    for y, (card, stock) in zip(FIELD_CARD_LIST_ROWS, rows):
        text = str(stock)
        cells = tuple((40 + 8 * i, ch) for i, ch in enumerate(card))
        cells += tuple((112 - 8 * (len(text) - 1 - i), ch) for i, ch in enumerate(text))
        lines.append(TextLine(y, cells))
    for word, y in MENU:
        lines.append(TextLine(y, tuple((168 + 8 * i, ch) for i, ch in enumerate(word))))
    lines.append(TextLine(183, tuple((24 + 8 * i, ch)
                                     for i, ch in enumerate('AボタンかBボタンをおすと'))))
    text = ' '.join(word for word, _ in MENU) + ' コマンドせんたくに もどりますぞ!'
    return Screen(lines=lines, hand=None, text=text, kind='field_card_list')


def menu_screen(selected='きりふだ'):
    lines = [TextLine(y, tuple((168 + 8 * i, ch) for i, ch in enumerate(word)))
             for word, y in MENU]
    y = dict(MENU)[selected]
    hand = (160, y - 2, 176, y + 6)
    return Screen(lines=lines, hand=hand,
                  text=' '.join(word for word, _ in MENU), kind='main_menu')


def map_screen():
    return Screen(lines=[], hand=None, text='', kind='map')


def started(month='9-2', **extra):
    """A booked reservation on the first free map after the month's actions."""
    mem = {'chapter': 9, 'month': month, 'tick': 100,
           'shop': {'key': '9-1', 'closed': True}, **extra}
    booking.book(mem, 'chapter_start', month='9-1')
    assert census_flow.start_step(map_screen(), mem) == [pad('x')]
    assert census_flow.resume_step(menu_screen(), mem) == [pad('a')]
    return mem


def inventory(rows, *, more_above=None, more_below=None):
    return {'rows': [{'card': card, 'stock': stock} for card, stock in rows],
            'more_above': more_above, 'more_below': more_below}


# ---------------------------------------------------------------- reader
@pytest.mark.parametrize('name,rows,above,below', [
    ('field-card-list-top.png',
     [('イッテツーン', 99), ('ブラッキー', 99), ('フットバース', 99), ('ダンスライン', 99),
      ('グリンボー', 99), ('カルゲンジー', 99), ('ピッグローラー', 99), ('ラピニアール', 99)],
     False, True),
    ('field-card-list-mid.png',
     [('ダンスライン', 99), ('グリンボー', 99), ('カルゲンジー', 99), ('ピッグローラー', 99),
      ('ラピニアール', 99), ('ノリウツール', 99), ('クースカン', 99), ('ゼンマイン', 99)],
     True, True),
    ('field-card-list-end.png',
     [('バルムンク', 2), ('ミックミー', 99), ('ブラックホール', 99), ('ころぼぐんだん', 99),
      ('バグストーム', 99), ('リューキーシ', 77), ('キャトルミュー', 6), ('ビッグウェイブ', 92)],
     True, False),
    # The measured 7-kind one-page list (local emulator 2026-11-22, ch1
    # 9年1月): fewer than eight rows means neither arrow is drawn.
    ('field-card-list-short.png',
     [('ダイチスイム', 2), ('ブラッキー', 2), ('フットバース', 2), ('ノリウツール', 33),
      ('バルムンク', 4), ('バグストーム', 20), ('キャトルミュー', 1)],
     False, False),
])
def test_reader_reports_the_measured_rows_and_arrows(name, rows, above, below):
    frame = read_png(FIXTURES / name)
    screen = parse(frame)
    assert screen.kind == 'field_card_list'
    result = _measured_field_card_list(screen, frame)
    assert [(row['card'], row['stock']) for row in result['rows']] == rows
    assert result['more_above'] is above
    assert result['more_below'] is below


def test_reader_without_a_frame_keeps_the_arrows_unknown():
    """No pixels is unreadable, never "nothing is scrolled off"."""
    screen = parse(read_png(FIXTURES / 'field-card-list-top.png'))
    result = _measured_field_card_list(screen)
    assert result is not None
    assert result['more_above'] is None and result['more_below'] is None


def test_a_page_whose_rows_are_out_of_id_order_is_unreadable():
    screen = list_screen([(ORDER[2], 1), (ORDER[1], 2)], False, False)
    assert _measured_field_card_list(screen, arrow_frame()) is None


def test_a_partially_read_row_rejects_the_page():
    screen = list_screen([(ORDER[0], 1), (ORDER[1], 2)], False, False)
    broken = [line for line in screen.lines if line.y == FIELD_CARD_LIST_ROWS[1]][0]
    cells = tuple(sorted(broken.cells + ((120, '9'),)))
    screen.lines = [TextLine(broken.y, cells) if line.y == broken.y else line
                    for line in screen.lines]
    assert _measured_field_card_list(screen, arrow_frame()) is None


def test_a_non_catalogue_name_is_not_an_inventory_row():
    """CARD_IDS only: event cards outside the priced table never fill a row."""
    screen = list_screen([('ゲンゴナイト', 1)], False, False)
    assert _measured_field_card_list(screen, arrow_frame()) is None


# ---------------------------------------------------------------- cleanup
def test_field_card_list_without_a_census_is_closed_with_b():
    frame = read_png(FIXTURES / 'field-card-list-top.png')
    screen = parse(frame)
    mem = {'chapter': 9, 'month': '9-1'}
    actions, out = decide(frame, {'step': 0, 'phase': 'field', 'policy': mem,
                                 'hanjuku': IDENTITY}, run_identity=IDENTITY)
    assert actions == [pad('b')]
    assert out['screen_kind'] == 'field_card_list'
    assert any(record['decision'] == 'close_panel' for record in out['_records'])
    # The flow's own entry point does the same when called directly.
    assert census_flow.field_card_list_step(screen, {'chapter': 9}, None) == [pad('b')]


def test_an_abandoned_census_list_is_still_closed_with_b():
    screen = parse(read_png(FIXTURES / 'field-card-list-top.png'))
    mem = {'chapter': 9, 'card_census_abort': {'chapter': 9, 'month': '9-1',
                                               'reason': 'same_page'}}
    assert census_flow.field_card_list_step(screen, mem, None) == [pad('b')]


# ---------------------------------------------------------------- flow
def test_three_pages_fill_the_ledger_and_log_full_drift():
    mem = started(card_stock={'イッテツーン': 50})
    actions = census_flow.resume_step(
        list_screen(stock_rows(range(0, 8)), False, True), mem, arrow_frame(False, True))
    assert actions == [pad('down')] * 7
    actions = census_flow.resume_step(
        list_screen(stock_rows(range(7, 15)), True, True), mem, arrow_frame(True, True))
    assert actions == [pad('down')] * 7
    actions = census_flow.resume_step(
        list_screen(stock_rows(range(14, 22)), True, False), mem, arrow_frame(True, False))
    assert actions == [pad('b')]                     # the end closes the list
    stock = mem['card_stock']
    assert stock['イッテツーン'] == 3                # the read replaced the stale 50
    assert stock[ORDER[21]] == 66
    # Every catalogue kind is either written or reported as dropped by the
    # unchanged CARD_STOCK_LIMIT: no kind silently becomes "no key".
    record = [r for r in mem['_records'] if r['decision'] == 'card_census'][0]
    dropped = record['observed_metric']['dropped']
    for card in CARD_IDS:
        assert card in stock or card in dropped, card
    assert record['observed_metric']['kinds'] == 22
    assert record['observed_metric']['total'] == sum((i + 1) * 3 for i in range(22))
    assert record['observed_metric']['pages'] == 3
    assert record['observed_metric']['observations'] == 4
    drift = [r for r in mem['_records'] if r['decision'] == 'ledger_drift']
    assert drift and all(r['observed_metric']['basis'] == 'full' for r in drift)
    assert booking.due(mem) is None                  # the reservation is consumed
    assert mem['card_stock']['エンジェリン'] == 0     # absent from the whole list
    assert mem.get('card_bought_unseen') in (None, {})
    # B (list) -> B (menu) -> map, then the census state is gone.
    assert census_flow.resume_step(
        list_screen(stock_rows(range(14, 22)), True, False), mem, arrow_frame(True, False)) == [pad('b')]
    assert census_flow.resume_step(menu_screen(), mem) == [pad('b')]
    assert census_flow.resume_step(map_screen(), mem) is None
    assert mem.get('card_census') is None


def test_a_page_without_overlap_goes_back_up_then_single_steps():
    mem = started()
    census_flow.resume_step(
        list_screen(stock_rows(range(0, 8)), False, True), mem, arrow_frame(False, True))
    # The batch ran past the next page: the new page starts nine rows down.
    assert census_flow.resume_step(
        list_screen(stock_rows(range(9, 17)), True, True), mem,
        arrow_frame(True, True)) == [pad('up')]
    assert mem['card_census']['mode'] == 'recover'
    assert any(r['decision'] == 'card_census_overlap_miss' for r in mem['_records'])
    # Still no overlap -> one more row up ...
    assert census_flow.resume_step(
        list_screen(stock_rows(range(8, 16)), True, True), mem,
        arrow_frame(True, True)) == [pad('up')]
    # ... and the page now overlaps the previous window: single-step from here.
    assert census_flow.resume_step(
        list_screen(stock_rows(range(7, 15)), True, True), mem,
        arrow_frame(True, True)) == [pad('down')]
    assert mem['card_census']['mode'] is None
    assert len(mem['card_census']['rows']) == 15


def test_the_same_page_twice_aborts_without_touching_the_ledger():
    mem = started(card_stock={'イッテツーン': 50})
    page = list_screen(stock_rows(range(0, 8)), False, True)
    census_flow.resume_step(page, mem, arrow_frame(False, True))
    actions = census_flow.resume_step(page, mem, arrow_frame(False, True))
    assert actions == [pad('b')]
    assert mem['card_stock'] == {'イッテツーン': 50}
    assert mem['card_census']['stage'] == 'abort_close'
    abort = [r for r in mem['_records'] if r['decision'] == 'card_census_abort'][0]
    assert abort['observed_metric']['reason'] == 'same_page'
    assert abort['observed_metric']['pages']
    assert mem['card_census_abort']['month'] == '9-2'
    assert booking.due(mem) is not None              # not consumed by an abort
    # The abort frame is closed with B, never left without input.
    assert census_flow.resume_step(page, mem, arrow_frame(False, True)) == [pad('b')]
    assert census_flow.resume_step(menu_screen(), mem) == [pad('b')]
    assert census_flow.resume_step(map_screen(), mem) is None


def test_the_forty_observation_limit_aborts():
    mem = started()
    mem['card_census']['observations'] = census_flow.OBSERVATION_LIMIT
    actions = census_flow.resume_step(menu_screen(), mem)
    assert actions == [pad('b')]
    abort = [r for r in mem['_records'] if r['decision'] == 'card_census_abort'][0]
    assert abort['observed_metric']['reason'] == 'observation_limit'


def test_a_map_that_never_opens_the_menu_aborts():
    mem = {'chapter': 9, 'month': '9-2', 'tick': 100,
           'shop': {'key': '9-1', 'closed': True}}
    booking.book(mem, 'chapter_start', month='9-1')
    assert census_flow.start_step(map_screen(), mem) == [pad('x')]
    for _ in range(census_flow.MENU_OPEN_LIMIT):
        assert census_flow.resume_step(map_screen(), mem) == [pad('x')]
    assert census_flow.resume_step(map_screen(), mem) is None
    assert mem.get('card_census') is None
    assert mem['card_census_abort']['reason'] == 'menu_not_opened'


def test_three_unreadable_screens_abort():
    mem = started()
    unreadable = list_screen([], False, False)       # no rows at all
    assert census_flow.resume_step(unreadable, mem, arrow_frame(False, False)) == []
    assert census_flow.resume_step(unreadable, mem, arrow_frame(False, False)) == []
    assert census_flow.resume_step(unreadable, mem, arrow_frame(False, False)) == [pad('b')]
    assert mem['card_census_abort']['reason'] == 'unreadable'


def test_an_in_flight_procedure_keeps_the_read_for_the_next_free_map():
    mem = {'chapter': 9, 'month': '9-2', 'tick': 100,
           'shop': {'key': '9-1', 'closed': True},
           'post_month_status': {'stage': 'roster', 'scope': [9, '9-2']}}
    booking.book(mem, 'chapter_start', month='9-1')
    assert census_flow.start_step(map_screen(), mem) is None
    census_flow.wait_step(map_screen(), mem)
    waits = [r for r in mem['_records'] if r['decision'] == 'card_census_wait']
    assert waits and waits[0]['observed_metric']['blockers'] == ['post_month_status']
    # The same wait is not logged again on every observation.
    census_flow.wait_step(map_screen(), mem)
    assert len([r for r in mem['_records'] if r['decision'] == 'card_census_wait']) == 1


def test_the_month_actions_must_be_over_before_the_read():
    mem = {'chapter': 9, 'month': '9-1', 'shop': {'key': '9-1', 'closed': True}}
    booking.book(mem, 'chapter_start', month='9-1')
    assert census_flow.start_step(map_screen(), mem) is None   # menu still open
    mem['month'] = '9-2'
    assert census_flow.start_step(map_screen(), mem) == [pad('x')]


def test_two_consecutive_aborts_disable_the_chapter():
    mem = {'chapter': 9, 'month': '9-2', 'tick': 100,
           'shop': {'key': '9-1', 'closed': True},
           'card_census_abort_prev': {'chapter': 9, 'streak': 1}}
    booking.book(mem, 'chapter_start', month='9-1')
    census_flow.start_step(map_screen(), mem)
    census_flow.resume_step(menu_screen(), mem)
    page = list_screen(stock_rows(range(0, 8)), False, True)
    census_flow.resume_step(page, mem, arrow_frame(False, True))
    census_flow.resume_step(page, mem, arrow_frame(False, True))
    assert mem['card_census_disabled'] == {'chapter': 9, 'reason': 'same_page', 'streak': 2}
    mem.pop('card_census', None)
    assert census_flow.start_step(map_screen(), mem) is None


# ---------------------------------------------------------------- booking
def test_a_new_chapter_clears_the_ledger_and_books_the_read():
    mem = {'chapter': 1, 'card_stock': {'イッテツーン': 9}}
    _enter_chapter(mem, 2, reason='unit test')
    assert mem['chapter'] == 2
    assert 'card_stock' not in mem
    assert mem['card_census_due']['reason'] == 'chapter_start'


def test_a_holding_limit_refusal_unknowns_the_kind_and_books_the_read():
    screen = Screen(lines=[], hand=None, header={'gold': 100}, kind='text',
                    text='う〜ん それいじょうもてないようですな!')
    mem = {'chapter': 9, 'month': '9-1', 'card_stock': {'イッテツーン': 99}}
    shop = {'want': ['イッテツーン', 2, 5], 'items': [['イッテツーン', 2]], 'bought': []}
    _holding_limit_step(screen, mem, shop)
    assert 'イッテツーン' not in mem['card_stock']
    assert mem['card_census_due']['reason'] == 'holding_limit_refusal'


def test_a_ledger_drift_books_the_read():
    mem = {'chapter': 9, 'month': '9-1', 'card_stock': {'ブラッキー': 99}}
    _observe_card_stock(mem, inventory([('ブラッキー', 5)], more_above=None, more_below=None))
    assert mem['card_stock']['ブラッキー'] == 5
    assert mem['card_census_due']['reason'] == 'ledger_drift'


def test_the_reservation_records_its_first_month_and_survives_rebooking():
    mem = {'month': '9-1'}
    booking.book(mem, 'chapter_start', month='9-1')
    booking.book(mem, 'ledger_drift', month='9-4')
    due = mem['card_census_due']
    assert due['month'] == '9-1'
    assert due['reason'] == 'ledger_drift'
    assert due['reasons'] == ['chapter_start', 'ledger_drift']


def test_a_hotload_of_the_same_runtime_keeps_the_ledger():
    """A bot start/hotload/deploy keeps its runtime identity: no clear, no book."""
    frame = read_png(FIXTURES / 'g1075-ch4-snow-field.png')
    mem = {'chapter': 9, 'month': '9-1', 'tick': 100,
           'survey_run_identity': IDENTITY, 'card_stock': {'イッテツーン': 42}}
    state = {'step': 0, 'phase': 'field', 'policy': mem, 'hanjuku': IDENTITY}
    actions, out = decide(frame, state, run_identity=IDENTITY)
    assert out['policy']['card_stock'] == {'イッテツーン': 42}
    assert 'card_census_due' not in out['policy']


def test_a_changed_runtime_generation_clears_the_ledger_and_books():
    frame = read_png(FIXTURES / 'g1075-ch4-snow-field.png')
    mem = {'chapter': 9, 'month': '9-1', 'tick': 100,
           'survey_run_identity': {**IDENTITY, 'runtime_id': 'g1287-abcdef', 'generation': 1287},
           'card_stock': {'イッテツーン': 42}}
    state = {'step': 0, 'phase': 'field', 'policy': mem, 'hanjuku': IDENTITY}
    actions, out = decide(frame, state, run_identity=IDENTITY)
    assert 'card_stock' not in out['policy']
    assert out['policy']['card_census_due']['reason'] == 'runtime_generation'


def test_the_title_return_after_gameplay_clears_the_ledger():
    """The game-over signature: the title comes back over a played run."""
    frame = read_png(FIXTURES / 'g1190-title-after-chapter.png')
    mem = {'chapter': 9, 'month': '9-1', 'tick': 100,
           'survey_run_identity': IDENTITY, 'card_stock': {'イッテツーン': 42}}
    state = {'step': 0, 'phase': 'field', 'policy': mem, 'hanjuku': IDENTITY}
    actions, out = decide(frame, state, run_identity=IDENTITY)
    assert 'card_stock' not in out['policy']
    assert out['policy']['card_census_due']['reason'] == 'game_over'


def test_a_missing_or_overdue_reservation_does_not_start_an_old_month():
    mem = {'chapter': 9, 'month': '9-4',
           'shop': {'key': '9-3', 'closed': True}}
    assert census_flow.start_step(map_screen(), mem) is None
    booking.book(mem, 'chapter_start', month='9-1')
    census_flow.wait_step(map_screen(), mem)
    overdue = [r for r in mem['_records'] if r['decision'] == 'card_census_overdue']
    assert overdue and overdue[0]['observed_metric']['booked_month'] == '9-1'
