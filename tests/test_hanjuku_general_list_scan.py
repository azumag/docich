"""A source castle's general list is walked to its end before it is trusted (#3077).

g1075-1d5f65e9 2026-10-30: every read of アルマムーン's list held exactly four
names (08:58-10:19) while three recruits joined (ブカティーニ 09:36, ヴィーナス
09:54, ヘスティア 10:05) and the 11:17 month roster counted 16 generals; eight
were in no castle list. Every castle then read one general, and no 四季王
site found a source from 09:50 (owner: 全然しきおうのボス城出撃してないね).
The list reader kept only the visible rows and never pressed down.

The window size of this list is unmeasured, so the walk relies on no row
count: a list of four or more is walked down until the cursor returns to
where it started, stops moving, or a press limit.
"""
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas

ROSTER = ['アルファルファ', 'ゼウス', 'ラズベリー', 'キャンディー', 'ココット', 'リースリング']


def general_list(names, row=0):
    c = Canvas()
    c.text(64, 31, 'しゅつげき')
    c.text(64, 47, 'ステータス')
    for i, name in enumerate(names):
        c.text(144, 39 + 16 * i, name)
    c.hand(122, 33 + 16 * row)
    screen = parse(c.frame())
    assert screen.kind == 'general_list'
    return screen


def memory():
    return {'chapter': 1, 'tick': 500, 'active': '1-C1', 'orders': {'1-C1': 'pending'},
            'card_stock': {'グリンボー': 59}}


def window(top, row):
    """The four-row window starting at ``top`` with the hand on ``row``."""
    return general_list(ROSTER[top:top + 4], row)


def walk(mem, screens):
    actions = []
    for screen in screens:
        mem['tick'] += 1
        actions.append(policy.deploy_step(screen, mem))
    return actions


def test_a_full_window_is_walked_down_until_the_cursor_wraps():
    mem = memory()
    pages = [window(0, 0), window(0, 1), window(0, 2), window(0, 3),
             window(1, 3), window(2, 3)]
    for actions in walk(mem, pages):
        assert actions == [policy.pad('down')]
    # Wrapped to the first row: the whole list is known and recorded.
    walk(mem, [window(0, 0)])
    assert mem['garrison']['アルマムーン'] == ROSTER
    (record,) = [r for r in mem['_records'] if r['decision'] == 'general_list_scanned']
    assert record['observed_metric'] == {'generals': ROSTER, 'end': 'wrapped', 'presses': 6}


def test_the_chart_general_below_the_window_is_reached_not_replaced():
    mem = memory()
    walk(mem, [window(0, 0), window(0, 1), window(0, 2), window(0, 3), window(1, 3), window(2, 3)])
    # 1-C1 sends ココット, fifth in the list: the cursor goes down to him.
    (actions,) = walk(mem, [window(0, 0)])
    assert actions == [policy.pad('down')]
    assert not [r for r in mem['_records'] if r['decision'] == 'order_substitute']
    (actions,) = walk(mem, [window(1, 3)])
    assert actions == [policy.pad('a')]                       # A on ココット's row
    assert mem['orders']['1-C1'] != 'failed'
    assert not (mem.get('general_override') or {}).get('1-C1')


def test_a_short_list_is_read_as_before():
    mem = memory()
    (actions,) = walk(mem, [general_list(['アルファルファ', 'ココット', 'ゼウス'], 0)])
    assert actions == [policy.pad('down')]                    # toward ココット, no walk
    assert mem['garrison']['アルマムーン'] == ['アルファルファ', 'ココット', 'ゼウス']
    assert 'general_list_scan' not in mem


def test_four_names_in_a_longer_window_end_when_the_cursor_wraps():
    mem = memory()
    four = ['アルファルファ', 'ココット', 'ゼウス', 'ラズベリー']
    actions = walk(mem, [general_list(four, row) for row in range(4)])
    assert actions == [[policy.pad('down')]] * 4
    walk(mem, [general_list(four, 0)])
    assert mem['garrison']['アルマムーン'] == four


def test_a_cursor_that_stops_moving_ends_the_walk():
    mem = memory()
    pages = [window(0, 0), window(0, 1), window(0, 2), window(0, 3), window(1, 3), window(2, 3)]
    walk(mem, pages)
    # The last row never wraps: three unchanged frames after a press end it.
    walk(mem, [window(2, 3)] * 3)
    assert mem['garrison']['アルマムーン'] == ROSTER
    (record,) = [r for r in mem['_records'] if r['decision'] == 'general_list_scanned']
    assert record['observed_metric']['end'] == 'stalled'


def test_g1075_a_hidden_recruit_goes_to_the_season_king_site():
    """The 11:16 lists left no source for 4-B2; the hero's castle hid two egg holders
    (ヴィーナス in production; the test font has no ヴ, so アポロン stands in)."""
    from docich import hanjuku_chart as chart
    home = ['どうし', 'ピスタチオ', 'キッシュ', 'クランベリー', 'アポロン', 'ヘスティア']
    normal = [c for c in chart.CASTLE_NAMES[4] if c not in ('アルマムーン', 'ボス')]
    mem = {'chapter': 4, 'tick': 140000, 'active': '4-B2', 'orders': {'4-B2': 'pending'},
           'captured': normal, 'lost': [], 'card_stock': {'クースカン': 3, 'グリンボー': 40},
           'source_override': {'4-B2': 'アルマムーン'}}

    def page(top, row):
        return general_list(home[top:top + 4], row)

    walk(mem, [page(0, 0), page(0, 1), page(0, 2), page(0, 3), page(1, 3), page(2, 3)])
    (actions,) = walk(mem, [page(0, 0)])
    assert mem['garrison']['アルマムーン'] == home
    # アポロン (74/14, egg) is the strongest egg holder and leaves the hero four.
    assert actions == [policy.pad('down')]
    assert mem['general_override']['4-B2'] == 'アポロン'


def test_a_dropped_dakuten_in_the_walk_is_folded_back_to_the_roster():
    """g1075 2026-10-31: キャンティー/ポセイトン readings held 4-B1 ~10 min."""
    mem = memory()
    misread = ['アルファルファ', 'ゼウス', 'ラズベリー', 'キャンティー']
    pages = [window(0, 0), window(0, 1), window(0, 2), general_list(misread, 3),
             window(1, 3), window(2, 3)]
    for actions in walk(mem, pages):
        assert actions == [policy.pad('down')]
    walk(mem, [window(0, 0)])
    assert mem['garrison']['アルマムーン'] == ROSTER


def boss_site_memory():
    """Chapter 4 4-B2 sortie from the hero's castle (as in the g1075 test)."""
    from docich import hanjuku_chart as chart
    normal = [c for c in chart.CASTLE_NAMES[4] if c not in ('アルマムーン', 'ボス')]
    return {'chapter': 4, 'tick': 140000, 'active': '4-B2', 'orders': {'4-B2': 'pending'},
            'captured': normal, 'lost': [], 'card_stock': {'クースカン': 3, 'グリンボー': 40},
            'source_override': {'4-B2': 'アルマムーン'}}


HOME = ['どうし', 'ピスタチオ', 'キッシュ', 'クランベリー', 'アポロン', 'ヘスティア']


def test_a_dropped_dakuten_in_the_walk_does_not_hold_a_boss_sortie():
    """The same misread during the walk held the g1075 4-B1 sortie as an
    unverifiable identity until the input stall."""
    mem = boss_site_memory()
    misread = ['どうし', 'ピスタチオ', 'キッシュ', 'クランヘリー']
    pages = [general_list(HOME[0:4], r) for r in range(3)] + [general_list(misread, 3),
            general_list(HOME[1:5], 3), general_list(HOME[2:6], 3)]
    for actions in walk(mem, pages):
        assert actions == [policy.pad('down')]
    (actions,) = walk(mem, [general_list(HOME[0:4], 0)])
    # The walked list folds back to the roster; the アポロン pick keeps moving.
    assert actions == [policy.pad('down')]
    assert mem['garrison']['アルマムーン'] == HOME
    held = [r for r in mem['_records']
            if r.get('reason') == '出撃一覧の本人・カーソルを確認できないため保留']
    assert not held


def test_a_dropped_dakuten_on_the_visible_page_does_not_hold_a_boss_sortie():
    mem = boss_site_memory()
    pages = [general_list(HOME[0:4], r) for r in range(4)] + [
        general_list(HOME[1:5], 3), general_list(HOME[2:6], 3)]
    walk(mem, pages)
    misread = ['どうし', 'ピスタチオ', 'キッシュ', 'クランヘリー']
    (actions,) = walk(mem, [general_list(misread, 0)])
    assert actions == [policy.pad('down')]
    held = [r for r in mem['_records']
            if r.get('reason') == '出撃一覧の本人・カーソルを確認できないため保留']
    assert not held
