"""A stuck field cursor tries the Y view before the castle-list jump.

g1190 2026-11-19 run 3/4: cursor_still_search repeated (X→しろ→アルマムーン)
without ever anchoring, and the sortie plan never dispatched — the Y view's
own-position cursor is the direct anchor.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku.flows import map as m


def memory():
    return {'chapter': 11, 'tick': 100, '_records': [],
            'cursor': [454, 868], 'nav_search': None,
            'plan': {'active': 'S1', 'orders': {'S1': 'pending'}}}


def test_the_first_stuck_cursor_asks_for_the_y_view():
    mem = memory()
    watch = None
    for i in range(m.CURSOR_STILL_LIMIT + 1):
        result = m._watch_cursor_still(mem)
        if result:
            break
    assert result is True
    assert mem.get('y_anchor_due') is True
    assert mem.get('y_anchor_attempts') == 1
    assert not mem.get('nav_search')
    assert any(r.get('decision') == 'cursor_still_yview' for r in mem['_records'])


def test_after_two_y_attempts_the_castle_jump_takes_over():
    mem = memory()
    mem['y_anchor_attempts'] = m.Y_ANCHOR_ATTEMPTS
    mem['cursor_still'] = {'cell': [454, 868], 'n': m.CURSOR_STILL_LIMIT}
    assert m._watch_cursor_still(mem) is True
    assert mem.get('nav_search') is True
    assert any(r.get('decision') == 'cursor_still_search' for r in mem['_records'])
