"""Bounded advances for event/dialogue beats and the clash dim window."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku import orchestrator as O
from docich.hanjuku_screen import Screen


def _screen(text='', kind='text', hand=None):
    return Screen(kind=kind, text=text, hand=hand, lines=[])


def test_an_unreadable_or_plain_dialogue_advances_after_the_bound():
    mem = {'_records': []}
    screen = _screen('クランベリーのヒットポイント')
    for _ in range(O.EMPTY_DIALOGUE_HOLD_LIMIT - 1):
        assert O.empty_dialogue_advance(screen, mem, 'dialogue', []) is None
    assert O.empty_dialogue_advance(screen, mem, 'dialogue', []) == [O.pad('a')]
    assert any(r['decision'] == 'empty_dialogue_advance' for r in mem['_records'])


def test_a_choice_box_with_a_hand_is_left_alone():
    mem = {'_records': []}
    screen = _screen('うむッ!', hand=(40, 20, 60, 34))
    for _ in range(10):
        assert O.empty_dialogue_advance(screen, mem, 'dialogue', []) is None


def test_a_planned_action_is_never_overridden():
    mem = {'_records': []}
    assert O.empty_dialogue_advance(_screen('x'), mem, 'dialogue', [O.pad('b')]) is None
