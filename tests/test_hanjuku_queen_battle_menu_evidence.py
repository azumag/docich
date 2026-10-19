"""g713 ゼウス対クイーン: the command box owner, its input window and the
card evidence are kept as separate facts (issue #2076).

UID16216..16233 of the recorded natural run are the evidence:

* 16216  panel ゼウス 85 / クイーン 70 (a B was sent on the same decision id)
* 16227  the real card list is クースカン / ノリウツール; the charted イッテツーン
         is absent, recorded as a missing card and closed with B
* 16228  the next card (クースカン) is planned, no selection/use receipt exists
* 16229  the panel is still drawing and reads 'unknown'; the default A was sent
* 16230..16232 the command box (たまごをつかう / きりふだ / たいきゃく) is
         observed with the cursor on y176 and byte-identical RGB
         (ec79c498…) while one down per observation did not move it
* 16233  the next observation is the enemy summon conversation

Sending an input never proves it was accepted or that a card was consumed, so
these tests assert only what the evidence supports: no blind A on the draw
window, no repeated ineffective down on an identical frame, and a selection
that needs a re-read real list plus a measured cursor before any A.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pytest  # noqa: E402

from docich import hanjuku_bot  # noqa: E402
from docich import hanjuku_policy as p  # noqa: E402
from docich.hanjuku_font import TextLine  # noqa: E402
from docich.hanjuku_pixels import Frame  # noqa: E402
from docich.hanjuku_screen import Screen  # noqa: E402

# UID16231/16232: the same command box came back byte-identical.
STALL_SHA = 'ec79c498bbc98f6c1c8401d4d581872d5cb52d3919f4c646e4a9ac1f932ad330'
# UID16227: the real two-card list image.
LIST_SHA = '0fde35ecc161cf19760540d102cda235ad10bbf389a3b94fb76899ff4a861615'


def command_box(cursor=176, *, sha=STALL_SHA):
    """The three-row 将軍コマンド box with the measured row cursor."""
    rows = [TextLine(176, ((176, 'たまごをつかう'),)),
            TextLine(192, ((176, 'きりふだ'),)),
            TextLine(208, ((176, 'たいきゃく'),))]
    return Screen(rows, None, 'たまごをつかうきりふだたいきゃく',
                  kind='battle_menu', menu_rows=rows, menu_cursor=cursor,
                  frame_sha256=sha)


def card_list(cards, *, cursor=None, hand=True, text=None):
    """The きりふだ list as the reader delivers it (rows + measured cursor)."""
    rows = [TextLine(176 + 16 * i, ((176, card),)) for i, card in enumerate(cards)]
    return Screen(rows, None, text if text is not None else ''.join(cards),
                  kind='text', menu_rows=rows,
                  menu_cursor=cursor if cursor is not None else (176 if hand and cards else None),
                  frame_sha256=LIST_SHA)


def battle_memory(**extra):
    cur = {'ally': 'ゼウス', 'enemy': 'クイーン', 'ally_hp': 85, 'enemy_hp': 70,
           'start_ally_hp': 85, 'start_enemy_hp': 70, 'side': 'defense',
           'cards_used': [], 'cards_selected': [], 'cards_missing': [],
           'card_evidence_version': 1, 'card_consumption_complete': True}
    cur.update(extra)
    return {'chapter': 1, 'battle': cur}


def unknown_panel(color=(128, 128, 128)):
    """A still-drawing panel: no readable text or cursor, coarse phase 'event'."""
    return Frame(256, 224, bytes(color) * (256 * 224))


# ------------------------------------------------- 16229: the draw window


def test_unreadable_draw_window_holds_the_default_confirm():
    state = {'chapter': 1, 'policy': battle_memory(), 'screen_kind': 'battle_menu'}
    actions, state = hanjuku_bot.decide(unknown_panel(), state)
    assert actions == [{'type': 'wait', 'ms': 50}]
    assert 'a' not in [b for a in actions for b in a.get('buttons', [])]
    hold = next(r for r in state['_records'] if r['decision'] == 'battle_draw_hold')
    assert hold['observed_metric']['previous_screen'] == 'battle_menu'
    assert hold['observed_metric']['held'] == 1


def test_a_readable_box_hands_the_frame_back_to_its_own_owner():
    state = {'chapter': 1, 'policy': battle_memory(), 'screen_kind': 'battle_menu'}
    _, state = hanjuku_bot.decide(unknown_panel(), state)
    # Still unreadable one observation later: the episode owns its own retry.
    actions, state = hanjuku_bot.decide(unknown_panel(), state)
    assert actions == [{'type': 'wait', 'ms': 50}]
    assert next(r for r in state['_records']
                if r['decision'] == 'battle_draw_hold')['observed_metric']['held'] == 2


def test_no_battle_in_progress_keeps_the_reviewed_phase_default():
    state = {'chapter': 1, 'policy': {'chapter': 1}, 'screen_kind': 'map'}
    actions, _ = hanjuku_bot.decide(unknown_panel(), state)
    assert actions == [hanjuku_bot.pad('a')]


def test_the_hold_is_finite_and_never_becomes_a_blind_a_loop():
    state = {'chapter': 1, 'policy': battle_memory(), 'screen_kind': 'battle_menu'}
    seen = []
    for _ in range(8):
        actions, state = hanjuku_bot.decide(unknown_panel(), state)
        seen.append(actions)
    assert seen[:3] == [[{'type': 'wait', 'ms': 50}]] * 3
    # Past the window the ordinary phase default resumes; the bot does not hang.
    assert seen[3] == [hanjuku_bot.pad('a')]


def test_a_phase_that_already_plans_no_input_is_left_untouched(monkeypatch):
    # The covered-HP panel keeps its reviewed empty default: this guard only
    # replaces a confirm, never invents an input where the phase planned none.
    monkeypatch.setattr(hanjuku_bot, 'classify', lambda frame: 'battle')
    state = {'chapter': 1, 'policy': battle_memory(), 'screen_kind': 'battle'}
    actions, state = hanjuku_bot.decide(unknown_panel(), state)
    assert actions == []
    assert 'battle_draw_hold' not in state['policy']


# ------------------------------------- 16227/16228: missing card, next card


def test_absent_charted_card_is_recorded_missing_and_closes_without_a():
    mem = battle_memory(card_flow={'card': 'イッテツーン', 'stage': 'list'}, planned_cards=['イッテツーン'])
    screen = card_list(['クースカン', 'ノリウツール'])
    assert p.card_list_step(screen, mem) == []
    actions = p.card_list_step(screen, mem)
    assert [a['buttons'] for a in actions] == [['b'], ['b']]
    assert mem['battle']['cards_missing'] == ['イッテツーン']
    record = mem['_records'][-1]
    assert record['decision'] == 'battle_card_missing'
    assert record['resulting_event'] == 'card_not_selected'
    assert mem['battle']['cards_selected'] == [] and mem['battle']['cards_used'] == []
    assert mem['battle'].get('card_flow') is None


def test_the_next_card_needs_a_measured_cursor_on_a_re_read_list():
    mem = battle_memory(card_flow={'card': 'クースカン', 'stage': 'list'})
    # A list without a readable cursor is not licence to press A.
    assert p.card_list_step(card_list(['クースカン', 'ノリウツール'], hand=False), mem) == []
    # The measured cursor on the real row selects, and the receipt is still
    # "planned", never a use.
    actions = p.card_list_step(card_list(['クースカン', 'ノリウツール'], cursor=176), mem)
    assert actions == [p.pad('a')]
    assert mem['battle']['cards_selected'] == ['クースカン']
    assert mem['battle']['cards_used'] == []
    assert mem['battle']['card_consumption_complete'] is False
    flow = mem['battle']['card_flow']
    assert (flow['card'], flow['stage'], flow['selection_planned']) == ('クースカン', 'announce', True)


def test_a_cursor_on_another_row_moves_before_any_confirm():
    mem = battle_memory(card_flow={'card': 'クースカン', 'stage': 'list'})
    actions = p.card_list_step(card_list(['クースカン', 'ノリウツール'], cursor=192), mem)
    assert [a['buttons'] for a in actions] == [['up']]
    assert mem['battle']['cards_selected'] == []


def test_an_unreadable_list_is_not_a_missing_card_receipt():
    mem = battle_memory(card_flow={'card': 'クースカン', 'stage': 'list'})
    blank = Screen([], None, '', kind='text', menu_rows=[], menu_cursor=None)
    for _ in range(3):
        assert p.card_list_step(blank, mem) == []
    assert mem['battle']['cards_missing'] == []
    assert mem['battle'].get('card_flow') is not None


# ---------------------------------- 16216/16230: panel and command box facts


def test_panel_and_box_are_read_as_separate_observations():
    from docich.hanjuku_screen import Battle
    panel = Screen([], None, '', kind='battle')
    panel.battle = Battle('クイーン', 70, 'ゼウス', 85)
    mem = battle_memory()
    mem['battle'] = None
    p.battle_step(panel, mem)
    p.battle_step(panel, mem)
    assert (mem['battle']['ally'], mem['battle']['enemy']) == ('ゼウス', 'クイーン')
    assert (mem['battle']['ally_hp'], mem['battle']['enemy_hp']) == (85, 70)
    # The box is not the panel: its menu cursor owns the row choice.
    box = command_box()
    assert box.kind == 'battle_menu' and box.menu_cursor == 176
    assert p._battle_menu_to(box, 'きりふだ')['buttons'] == ['down']
    assert p._battle_menu_to(box, 'たまごをつかう') == 'here'


@pytest.mark.parametrize('cursor,label,expected', [
    (176, 'たまごをつかう', 'here'),
    (176, 'きりふだ', 'down'),
    (176, 'たいきゃく', 'down'),
    (192, 'きりふだ', 'here'),
    (192, 'たいきゃく', 'down'),
    (208, 'たいきゃく', 'here'),
])
def test_the_box_cursor_rows_are_the_measured_layout(cursor, label, expected):
    move = p._battle_menu_to(command_box(cursor), label)
    assert move == 'here' if expected == 'here' else move['buttons'] == [expected]


def test_the_charted_card_reopen_never_confirms_on_the_stuck_box():
    """UID16230..16232 drove this owner: the box never moved to きりふだ."""
    mem = battle_memory(card_flow={'card': 'クースカン', 'stage': 'down'})
    buttons = []
    for _ in range(20):
        actions = p.battle_menu_step(command_box(), mem)
        buttons.extend(tuple(a.get('buttons') or ()) for a in actions)
        if [a.get('buttons') for a in actions] == [['b']]:
            break
    assert ('a',) not in buttons
    # The one down, its bounded retry, then the close: nothing else was pressed.
    assert [b for b in buttons if b == ('down',)] == [('down',), ('down',)]
    assert buttons[-1] == ('b',)
    assert mem['battle']['cards_used'] == []
    assert mem['battle']['cards_selected'] == []
    # Nothing was selected, so no consumption is pending and none is claimed.
    assert mem['battle']['card_consumption_complete'] is True
    assert 'battle_card_selected' not in [r.get('decision') for r in mem['_records']]
