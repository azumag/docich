"""docich#5108 — [半熟英雄] キャンプ帰還失敗: ch10 将軍不明

A non-operable screen (an unrelated text panel, a battle foreground) used to
spend the recall's 90-observation step budget, so one long message ended the
return at camp_recall_limit while the camped general stayed in the field.
The recall intent is now held instead: ``steps`` never advances while the
screen cannot be operated, ``hold_since`` bounds the wait at
``RECALL_HOLD_TICK_LIMIT`` ticks, and a resumable screen clears it. The
step-limit abort itself is unchanged — including ``RECALL_LIMIT == 90``.
"""
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.os_layer.screen import Frame, Screen
from docich.hanjuku.state.regions import flow as _flow
from test_hanjuku_chart_bot import camp_menu

FRAME = Frame(256, 224, b'\x00' * (256 * 224 * 3))


def memory(stage='dest', tick=100):
    mem = {'chapter': 10, 'tick': tick, 'captured': ['ドミノーラ'], '_records': []}
    _flow(mem)['recall'] = {'stage': stage, 'steps': 5, 'target': [135, 125]}
    return mem


def test_recall_limit_is_unchanged():
    assert recall_flow.RECALL_LIMIT == 90


def test_a_non_operable_text_screen_holds_the_recall_without_steps():
    """91 text observations must not burn the step budget (the old #5108
    failure aborted at 90 while a message it could not operate was shown)."""
    mem = memory()
    text = Screen([], None, 'つぎのひはつづく', kind='text')
    for _ in range(91):
        assert recall_flow.camp_recall_step(text, mem, FRAME) is None
    state = _flow(mem)['recall']
    assert state is not None and state['stage'] == 'dest' and state['steps'] == 5
    assert state['hold_since'] == 100
    assert not any(r['decision'] == 'camp_recall_aborted' for r in mem['_records'])


def test_a_battle_foreground_holds_the_recall_without_steps():
    mem = memory()
    battle = Screen([], None, '', kind='battle')
    for _ in range(91):
        assert recall_flow.camp_recall_step(battle, mem, FRAME) is None
    state = _flow(mem)['recall']
    assert state is not None and state['steps'] == 5 and state['hold_since'] == 100


def test_the_hold_aborts_only_after_the_tick_limit():
    mem = memory()
    text = Screen([], None, 'つぎのひはつづく', kind='text')
    assert recall_flow.camp_recall_step(text, mem, FRAME) is None
    assert _flow(mem)['recall']['hold_since'] == 100
    mem['tick'] = 100 + recall_flow.RECALL_HOLD_TICK_LIMIT - 1
    assert recall_flow.camp_recall_step(text, mem, FRAME) is None
    assert _flow(mem).get('recall') is not None            # still held, no early abort
    mem['tick'] = 100 + recall_flow.RECALL_HOLD_TICK_LIMIT
    assert recall_flow.camp_recall_step(text, mem, FRAME) == []
    assert not _flow(mem).get('recall')
    assert _flow(mem)['recall_skip']['tick'] == mem['tick']
    aborted = [r for r in mem['_records'] if r['decision'] == 'camp_recall_aborted']
    assert aborted and aborted[0]['observed_metric']['held_ticks'] \
        == recall_flow.RECALL_HOLD_TICK_LIMIT


def test_a_resumable_screen_clears_the_hold_and_continues_steps():
    """After the held frame the recall picks up where it left off — the same
    steps value, not a restarted or aborted flow."""
    mem = memory(stage='menu', tick=300)
    text = Screen([], None, 'つぎのひはつづく', kind='text')
    assert recall_flow.camp_recall_step(text, mem, FRAME) is None
    assert _flow(mem)['recall']['hold_since'] == 300
    assert recall_flow.camp_recall_step(camp_menu(1), mem, FRAME) == [recall_flow.pad('up')]
    state = _flow(mem)['recall']
    assert 'hold_since' not in state and state['steps'] == 6


def test_a_camp_menu_is_never_treated_as_a_hold():
    """The operable camp menu clears a stale hold instead of being held."""
    mem = memory(stage='menu')
    _flow(mem)['recall']['hold_since'] = 50
    assert recall_flow.camp_recall_step(camp_menu(0), mem, FRAME) == [recall_flow.pad('a')]
    assert 'hold_since' not in _flow(mem)['recall']
