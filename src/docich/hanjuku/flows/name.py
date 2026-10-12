"""手順層: the hero name-entry procedure."""
from __future__ import annotations

from ...hanjuku_screen import Screen
from ...hanjuku_font import UNKNOWN
from ..decision_log import _record
from ..os_layer.commands import kana_cell, pad
from ..os_layer.readers import _current, _options
from ..os_layer.world import NAME


def name_step(screen: Screen, mem):
    box = next((line for line in screen.lines if 48 <= line.y <= 62), None)
    typed = ''.join(box.span(64, 160).split()) if box else ''
    name = mem.setdefault('name', {'target': NAME, 'done': False, 'inputs': 0})
    name['typed'] = typed
    if UNKNOWN in typed:
        _record(mem, 'name_wait', chart_step='name', typed=typed,
                reason='状況判定保留: 名前欄に未分類の文字があるため入力を保留')
        return []
    if typed == NAME:
        _record(mem, 'name_confirm', chart_step='name', typed=typed,
                reason='名前欄の実表示がどうしに一致したためSTARTで確定')
        name['done'] = True
        return [pad('start')]
    if not NAME.startswith(typed):
        _record(mem, 'name_delete', chart_step='name', typed=typed, reason='名前欄が目標の前方一致でないため1文字削除')
        return [pad('b')]
    want = NAME[len(typed)]
    if screen.hand and screen.hand[0] < 40:
        # Hand on the page menu (ひらがな/カタカナ/...; x0 about 17), not the
        # grid's first column (x0 about 58): enter the kana grid.
        return [pad('a')]
    cur = _current(screen)
    cells = [(x, y, w) for x, y, w in _options(screen) if w == want and x >= 80 and y >= 80]
    if not cells and kana_cell(want) and 'ひらがな' in screen.text:
        cells = [(*kana_cell(want), want)]
    if not cur or not cells:
        _record(mem, 'name_wait', chart_step='name', typed=typed, reason='カーソルまたは目標文字を判定保留')
        return []
    if cur[2] == want:
        _record(mem, 'name_type', chart_step='name', typed=typed, char=want,
                reason=f'カーソルが「{want}」上にあるためAで入力')
        return [pad('a')]
    tx, ty, _ = cells[0]
    if abs(ty - cur[1]) > 2:
        return [pad('down' if ty > cur[1] else 'up')]
    return [pad('right' if tx > cur[0] else 'left')]
