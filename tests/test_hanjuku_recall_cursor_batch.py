"""The camp menu's cursor run is one action, not one observation per row.

g1163 2026-11-12 03:5x: four `camp_recall_cursor` observations per recall spent
the month's action budget and every recall was interrupted by the month scene
before it reached the destination (6 of 6 attempts, hour after hour).
"""
from docich.hanjuku.flows import recall


class _Line:
    def __init__(self, y, spans):
        self.y = y
        self._spans = spans
        self.cells = {}

    def spans(self):
        return list(self._spans)

    def span(self, x0, x1):
        return ''.join(w for x, w in self._spans if x0 <= x < x1)

    def words(self, x0, x1):
        return [w for x, w in self._spans if x0 <= x < x1]


class _Screen:
    def __init__(self, rows, cursor_row):
        self.kind = 'field_menu'
        self.text = 'いどうステータスキャンプきかん'
        self.hand = (60, cursor_row - 6, 72, cursor_row + 2)
        self.lines = [_Line(y, [(72, name)]) for y, name in rows]

    def has(self, *_a):
        return False


ROWS = [(31, 'いどう'), (47, 'ステータス'), (63, 'キャンプ'), (79, 'きかん')]


def test_the_whole_run_is_sent_as_one_action():
    screen = _Screen(ROWS, 31)
    out = recall._walk({'type': 'pad', 'buttons': ['down']}, screen, 'きかん')
    assert [a['buttons'] for a in out] == [['down'], ['down'], ['down']]


def test_a_run_beyond_the_batch_stays_measured():
    rows = [(31 + 16 * i, name) for i, name in enumerate(
        ['いどう', 'ステータス', 'キャンプ', 'きかん', 'その他'])]
    screen = _Screen(rows, 31)
    out = recall._walk({'type': 'pad', 'buttons': ['down']}, screen, 'その他')
    assert [a['buttons'] for a in out] == [['down']]      # 3 rows is the cap; 4 is not


def test_an_upward_target_is_walked_upwards():
    screen = _Screen(ROWS, 79)
    out = recall._walk({'type': 'pad', 'buttons': ['up']}, screen, 'いどう')
    assert [a['buttons'] for a in out] == [['up'], ['up'], ['up']]


def test_a_horizontal_move_is_not_batched():
    screen = _Screen(ROWS, 31)
    assert recall._walk({'type': 'pad', 'buttons': ['right']}, screen, 'きかん') == [
        {'type': 'pad', 'buttons': ['right']}]
