"""g684 13:05: the 花いちもんめ lead-in was plain text, its choice was not a
measured yes/no, A accepted and ヴィーナス (82) was traded for シェーブル (27)."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as policy
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen


def text(t, kind='text'):
    return Screen(lines=[], hand=None, text=t, kind=kind)


def choice(rows, selected):
    lines = [TextLine(176 + 16 * i, tuple((40 + 8 * j, ch) for j, ch in enumerate(w)))
             for i, w in enumerate(rows)]
    y = 176 + 16 * selected
    return Screen(lines=lines, hand=(18, y - 6, 38, y + 6), text=''.join(rows), kind='text')


def test_lead_in_arms_the_guard_and_advances_with_a():
    mem = {'tick': 10}
    assert policy.trade_guard_step(text('「ここでしょうぐんどうしのトレードをしようじゃないか。'), mem) is None
    assert mem['trade_guard']['tick'] == 10
    assert any(r['decision'] == 'trade_offer_seen' for r in mem['_records'])


def test_the_choice_is_moved_off_the_default_row_and_the_lower_row_chosen():
    mem = {'tick': 10}
    policy.trade_guard_step(text('トレードをしようじゃないか'), mem)
    mem['tick'] = 12
    assert policy.trade_guard_step(choice(['OK', 'NO'], 0), mem) == [policy.pad('down')]
    assert policy.trade_guard_step(choice(['OK', 'NO'], 1), mem) == [policy.pad('a')]
    rec = mem['_records'][-1]
    assert rec['strategy_variant'] == 'decline_general_trade'
    assert mem['trade_guard']['chosen'] is True
    assert policy.trade_guard_step(choice(['OK', 'NO'], 0), mem) is None   # guard released


def test_an_unread_choice_is_held_not_accepted():
    mem = {'tick': 1}
    policy.trade_guard_step(text('トレード'), mem)
    sc = Screen(lines=[], hand=(18, 170, 38, 182), text='?', kind='text')
    assert policy.trade_guard_step(sc, mem) == []


def test_measured_yes_no_keeps_its_own_decline():
    mem = {'tick': 1}
    policy.trade_guard_step(text('トレード'), mem)
    assert policy.trade_guard_step(text('うむッいかんッ', kind='yes_no'), mem) is None


def test_guard_expires_and_hero_name_does_not_arm_it():
    mem = {'tick': 1}
    assert policy.trade_guard_step(text('しょうぐんどうしがのりこんだ'), mem) is None
    assert 'trade_guard' not in mem
    policy.trade_guard_step(text('トレード'), mem)
    mem['tick'] = 1 + policy.TRADE_GUARD_OBSERVATIONS + 1
    assert policy.trade_guard_step(choice(['A', 'B'], 0), mem) is None
    assert 'trade_guard' not in mem
