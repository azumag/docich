"""A readable screen that ignores the default A gets one bounded B (#4543).

g1190 2026-11-18 10:40: 「どのしろを そうたく なさいますか?」 stayed up while the
phase-default A was sent every observation; the run ended screen_stalled.
A probe on the saved state showed one B returns to the castle menu.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku import orchestrator as O

TEXT = 'どのしろを そうたく なさいますか? これいじょうの ぎょうらくは できませんぞ!!'


def test_ten_identical_a_screens_earn_one_b():
    mem = {}
    for i in range(O.PHASE_A_ESCAPE_MAX - 1):
        assert O.phase_a_escape_step(mem, 'dialogue', 'text', TEXT, [O.pad('a')]) is None
    assert O.phase_a_escape_step(mem, 'dialogue', 'text', TEXT, [O.pad('a')]) == [O.pad('b')]
    record = mem['_records'][-1]
    assert record['decision'] == 'phase_a_escape'
    assert record['observed_metric']['identical_observations'] == O.PHASE_A_ESCAPE_MAX
    # the count restarts: the next observation does not press again
    assert O.phase_a_escape_step(mem, 'dialogue', 'text', TEXT, [O.pad('a')]) is None


def test_a_changed_line_resets_the_count():
    mem = {}
    for _ in range(O.PHASE_A_ESCAPE_MAX - 1):
        O.phase_a_escape_step(mem, 'dialogue', 'text', TEXT, [O.pad('a')])
    assert O.phase_a_escape_step(mem, 'dialogue', 'text', TEXT + '。', [O.pad('a')]) is None


def test_other_actions_reset_the_count():
    mem = {}
    for _ in range(O.PHASE_A_ESCAPE_MAX - 1):
        O.phase_a_escape_step(mem, 'dialogue', 'text', TEXT, [O.pad('a')])
    assert O.phase_a_escape_step(mem, 'dialogue', 'text', TEXT, [O.pad('b')]) is None
    assert O.phase_a_escape_step(mem, 'dialogue', 'text', TEXT, [O.pad('a')]) is None


def test_other_kinds_are_not_escaped_here():
    mem = {}
    for _ in range(O.PHASE_A_ESCAPE_MAX * 2):
        assert O.phase_a_escape_step(mem, 'field', 'map', TEXT, [O.pad('a')]) is None
