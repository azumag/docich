"""A card below the four-row きりふだ panel is scrolled to, not dropped.

g1163 2026-11-08 23:55-23:56 measured the panel the old fixed budget could not
walk: 15 rows in card-ID order (ダイチスイム(1) … ノリウツール(11) …), where
8 Downs only reached ノリウツール(11). The planned エンジェリン(22),
マグネガキン(27) and ファバード(31) were never shown, so every boss sortie
held five readings and then dropped the card.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from docich import hanjuku_policy as policy
from docich import hanjuku_reference as reference
from test_hanjuku_sortie_evidence import measured_card_select

# The live panel's rows in card-ID order (g1163 2026-11-08 23:55-23:56); the
# prepared cards エンジェリン/マグネガキン/ファバード are on the same list.
CATALOGUE = ('ダイチスイム', 'ブラッキー', 'フットバース', 'グリンボー',
             'カルゲンジー', 'ピッグローラー', 'ラピニアール', 'ノリウツール',
             'クースカン', 'ゼンマイン', 'ミックミー', 'エンジェリン',
             'ころぼぐんだん', 'バグストーム', 'キャトルミュー', 'ファバード')


def _c2_memory(card):
    # rare_scan_month closes the bounded rare-card discovery: this test is
    # about the planned-card search that follows it.
    return {'chapter': 1, 'active': '1-C2', 'variant': 'chart', 'captured': ['ジョンリギ'],
            'orders': {'1-C2': 'pending'}, 'picked': [],
            'rare_scan_month': 'chapter-1:unknown',
            'card_override': {'1-C2': [card]}}


def _panel(rows, selected):
    return measured_card_select(tuple(rows), stocks=('1',) * len(rows),
                                remaining='3', selected=selected)


def _walk(mem, catalogue, *, steps=80):
    """Drive the panel like the game: Down moves the cursor, then scrolls.

    A held reading (no input) keeps the same window; the search ends when the
    card is picked or the bounded drop is recorded.
    """
    offset, selected = 0, 0
    seen = []
    for _ in range(steps):
        rows = catalogue[offset:offset + 4]
        actions = policy.deploy_step(_panel(rows, selected), mem)
        seen.append(actions)
        if (mem.get('_records') or [{}])[-1].get('decision') == 'card_dropped':
            return seen
        if actions == [policy.pad('a')]:
            return seen
        if actions == [policy.pad('down')]:
            if selected < 3:
                selected += 1
            elif len(catalogue) > 4:
                offset = min(len(catalogue) - 4, offset + 1)
    raise AssertionError('the card search never terminated')


def test_the_scroll_budget_covers_the_whole_card_catalogue():
    assert policy.CARD_SCROLL_LIMIT >= len(reference.ALL_CARD_IDS)
    # A card at the catalogue's end costs (rows - 1) presses.
    assert policy.CARD_SCROLL_LIMIT >= len(reference.ALL_CARD_IDS) - 1


def test_a_planned_card_below_eight_downwards_is_reached_and_picked():
    mem = _c2_memory('エンジェリン')
    actions = _walk(mem, CATALOGUE)
    assert actions[-1] == [policy.pad('a')]
    assert mem['picked'] == ['エンジェリン']
    assert not mem.get('card_drop')
    # The old fixed limit of 8 stopped before the card's row was visible.
    assert sum(1 for action in actions if action == [policy.pad('down')]) > 8
    assert mem['_records'][-1]['decision'] == 'card_pick'


def test_a_card_absent_from_the_end_of_the_list_is_dropped_within_the_bound():
    mem = _c2_memory('ファバード')
    # キャトルミュー is the rare-card discovery path, not this search.
    catalogue = [name for name in CATALOGUE if name not in ('ファバード', 'キャトルミュー')]
    actions = _walk(mem, catalogue)
    records = [r['decision'] for r in mem['_records']]
    assert 'card_dropped' in records
    assert mem['card_drop'] == {'1-C2': ['ファバード']}
    assert not mem.get('picked')
    # Bounded: the catalogue walk plus the bounded readings, never unbounded.
    assert len(actions) <= policy.CARD_SCROLL_LIMIT + policy.CARD_MISS_LIMIT + 1
