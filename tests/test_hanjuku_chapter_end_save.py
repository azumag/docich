"""The chapter-end save prompt must be accepted even when rows are unreadable.

Measured chapter 8 end (6ねん12のつき): 「セーブをしゅうりょうして
よろしいですか?」 held for minutes; the shop-phase no-input fallback sent B,
cancelling the save and re-asking.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku import orchestrator as O
from docich.hanjuku_screen import Screen


def _screen(text='セーブをしゅうりょうして よろしいですか?'):
    return Screen(kind='yes_no', text=text, hand=None, lines=[])


def test_save_prompt_is_accepted_after_the_bound():
    screen, mem = _screen(), {'_records': []}
    for _ in range(O.CHAPTER_END_SAVE_HOLD - 1):
        assert O.chapter_end_save_accept(screen, mem, 'yes_no', 'shop', []) is None
    assert O.chapter_end_save_accept(screen, mem, 'yes_no', 'shop', []) == [O.pad('a')]
    assert any(r['decision'] == 'chapter_end_save_accept' for r in mem['_records'])


def test_other_prompts_are_left_alone():
    screen, mem = _screen('このしょうぐんと トレードしよう'), {'_records': []}
    for _ in range(6):
        assert O.chapter_end_save_accept(screen, mem, 'yes_no', 'shop', []) is None


def test_planned_actions_win():
    assert O.chapter_end_save_accept(_screen(), {'_records': []}, 'yes_no', 'shop',
                                     [O.pad('b')]) is None
