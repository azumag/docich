"""#5010 — キャンプ帰還失敗: ch2 将軍不明

After PR #4736 (2026-11-19), camp return unconditionally chose いどう, which
opens the field cursor (screen.kind=='map'). The 'dest' stage abandons the
recall on 'map' without move_visible, so chapter 2 (and every non-corridor,
non-gateway chapter) could not return from camp.

Fix: when move_visible is not set, fall back to きかん's whole-island picker
(map_target), which 'dest' handles for every chapter. A boss redirect or
boss_move keeps いどう (owner-measured #4736 path to the boss).
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.state.regions import flow as _flow
from docich.hanjuku_screen import Screen


from docich.hanjuku.os_layer.screen import Screen as _Screen


def memory(chapter=2, move_visible=None):
    mem = {'chapter': chapter, 'tick': 100, 'captured': [], '_records': []}
    state = _flow(mem).setdefault('recall', {})
    state.update(stage='menu', steps=0, target=[80, 90])
    if move_visible is not None:
        state['move_visible'] = move_visible
    return mem


def _camp_menu_screen(selected=0):
    # Same layout as test_hanjuku_chart_bot.camp_menu: menu_to needs the
    # TextLine rows to locate the cursor and the target label.
    from docich.hanjuku_font import TextLine
    rows = ['いどう', 'ステータス', 'キャンプ', 'きかん']
    return _Screen(
        lines=[TextLine(39+16*i, tuple((80+8*j, ch) for j, ch in enumerate(word)))
               for i, word in enumerate(rows)],
        hand=(58, 33+16*selected, 75, 45+16*selected),
        text='いどうステータスキャンプきかん', kind='text')


def test_chapter2_menu_chooses_kikan_without_move_visible():
    """ch2 has no corridor/gateway, so move_visible is never set:
    いどう would abandon the recall; きかん opens map_target instead."""
    mem = memory(chapter=2)
    screen = _camp_menu_screen(0)
    actions = recall_flow.camp_recall_step(screen, mem, None)
    # Cursor is on row 0 (いどう); きかん is row 3 → walk down.
    assert actions == [recall_flow.pad('down')]
    assert _flow(mem)['recall']['stage'] == 'menu'


def test_chapter2_menu_chooses_idou_with_move_visible():
    """Relay/corridor chapters still use いどう for field-cursor moves."""
    mem = memory(chapter=2, move_visible={'castle': 'アルマムーン'})
    screen = _camp_menu_screen(0)
    actions = recall_flow.camp_recall_step(screen, mem, None)
    # いどう is row 0 → A confirms at once.
    assert actions == [recall_flow.pad('a')]
    assert _flow(mem)['recall']['stage'] == 'dest'


def test_chapter9_menu_chooses_kikan_without_move_visible():
    """ch9 is corridor but without visible castles move_visible is not set,
    so it also falls back to きかん (consistent with ch1/ch2)."""
    mem = memory(chapter=9)
    screen = _camp_menu_screen(0)
    actions = recall_flow.camp_recall_step(screen, mem, None)
    assert actions == [recall_flow.pad('down')]
    assert _flow(mem)['recall']['stage'] == 'menu'


def test_chapter1_menu_chooses_kikan_without_move_visible():
    """ch1 is not corridor/gateway: same fallback to きかん as ch2."""
    mem = memory(chapter=1)
    screen = _camp_menu_screen(0)
    actions = recall_flow.camp_recall_step(screen, mem, None)
    assert actions == [recall_flow.pad('down')]
    assert _flow(mem)['recall']['stage'] == 'menu'


def test_boss_redirect_keeps_idou_without_move_visible():
    """#4736 (owner 2026-11-19) measured the boss redirect via いどう.
    A boss / boss_move recall keeps いどう even without move_visible."""
    for flag in ('boss', 'boss_move'):
        mem = memory(chapter=9)
        _flow(mem)['recall'][flag] = True
        assert recall_flow.camp_recall_step(_camp_menu_screen(3), mem, None) == [recall_flow.pad('up')]
        assert recall_flow.camp_recall_step(_camp_menu_screen(0), mem, None) == [recall_flow.pad('a')]
        assert _flow(mem)['recall']['stage'] == 'dest'
