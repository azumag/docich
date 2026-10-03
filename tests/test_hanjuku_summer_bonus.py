"""8月の月イチイベント「バカンス」は損失の小さい方を選ぶ。

オーナー決定 (2026-10-03): 兵士はひとり1Gなので、兵士の数とお金の数の
少ない方の選択肢を選ぶ。バカンス＝兵士半減、ボーナス＝お金半減、
まとめて解雇＝何も無いか兵士全滅（odoru7094 / gcgx event.html）なので
常に非選択。読めない時は**保留せずどちらかを選ぶ**（観測ごとに反転しない
よう、Aで確定するまで同じ選択を保つ）。選択位置が読めない時も保留せず、
従来どおりそのまま確認する。
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as p
from docich.hanjuku_commentary import compose
from docich.hanjuku_screen import Screen, parse
from docich.hanjuku_font import TextLine
from docich.hanjuku_bot import decide
from test_hanjuku_chart_bot import Canvas

LABELS = ('バカンス', 'ボーナス', 'まとめて解雇')


def summer_screen(labels=LABELS, selected=0, hand=True, gold=7572):
    lines = [TextLine(176 + 16 * i, tuple((176 + 8 * j, ch) for j, ch in enumerate(label)))
             for i, label in enumerate(labels)]
    hand_box = (138, 169 + 16 * selected, 156, 181 + 16 * selected) if hand else None
    screen = Screen(lines, hand_box, ''.join(labels), kind='summer_bonus')
    if gold is not None:
        screen.header = {'chapter': 1, 'year': 5, 'month': 8, 'gold': gold}
    screen.menu_rows = lines
    screen.menu_cursor = None if hand_box is None else 176 + 16 * selected
    return screen


def memory(soldiers=30):
    mem = {'_records': []}
    if soldiers is not None:
        mem['soldiers_seen'] = soldiers
        mem['soldiers_seen_key'] = (5, 8)
    return mem


KANA_LABELS = ('ばかんす', 'ぼなす', 'まとめてかいほう')


def prompt_canvas(labels=KANA_LABELS, gold=7572):
    c = Canvas((16, 72, 57))
    if gold is not None:
        c.text(8, 8, f'5ねん8のつき{gold}G')       # 損失比較に要る所持金
    for i, label in enumerate(labels):
        c.text(176, 176 + 16 * i, label)
    return c.frame()


def never_random(monkeypatch):
    monkeypatch.setattr(p.random, 'choice',
                        lambda seq: (_ for _ in ()).throw(AssertionError('読めるのにランダム選択した')))


def test_classify_reads_the_prompt_only_when_both_outcomes_are_legible():
    assert parse(prompt_canvas()).kind == 'summer_bonus'
    assert parse(prompt_canvas(('ばかんす', 'まとめてかいほう'))).kind != 'summer_bonus'
    assert parse(prompt_canvas(('ぼーなす', 'ばかんす'))).kind == 'summer_bonus'


def test_smaller_loss_wins_and_the_discharge_option_is_never_chosen(monkeypatch):
    never_random(monkeypatch)
    # 兵士30人 < 7572G → 損失の小さい兵士半減のバカンス。
    assert p.summer_bonus_step(summer_screen(), memory(30)) == [p.pad('a')]
    assert p.summer_bonus_step(summer_screen(selected=1), memory(30)) == [p.pad('up')]
    assert p.summer_bonus_step(summer_screen(selected=2), memory(30)) == [p.pad('up')]
    # 所持金のほうが少ない → お金半減のボーナス。
    assert p.summer_bonus_step(summer_screen(selected=1, gold=500), memory(900)) == [p.pad('a')]
    assert p.summer_bonus_step(summer_screen(gold=500), memory(900)) == [p.pad('down')]
    assert p.summer_bonus_step(summer_screen(selected=2, gold=500), memory(900)) == [p.pad('up')]
    # 同数なら損失は同じ。兵士側を取り、まとめて解雇には決して行かない。
    assert p.summer_bonus_step(summer_screen(), memory(7572)) == [p.pad('a')]


def test_the_choice_records_the_compared_numbers_and_never_discharge():
    for soldiers, gold, row, variant in ((30, 7572, 0, 'summer_bonus_vacation'),
                                        (900, 500, 1, 'summer_bonus_bonus')):
        mem = memory(soldiers)
        assert p.summer_bonus_step(summer_screen(selected=row, gold=gold), mem) == [p.pad('a')]
        rec = mem['_records'][-1]
        assert rec['decision'] == 'prompt' and rec['strategy_variant'] == variant
        assert rec['observed_metric']['soldiers'] == soldiers
        assert rec['observed_metric']['gold'] == gold
        assert rec['observed_metric']['selection'] == 'min_loss'
        assert rec['choice'] != 'discharge'
        key, text = compose(rec)
        assert key == 'summer_bonus' and text and '損失の小さい' in text


def test_an_unreadable_number_latches_one_choice_instead_of_holding(monkeypatch):
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'vacation')
    mem = memory(None)                     # 兵士数が一度も画面に出ていない
    # カーソルはボーナスの行 → まずバカンスの行へ移動する（保留しない）。
    assert p.summer_bonus_step(summer_screen(selected=1), mem) == [p.pad('up')]
    # 次の観測では再抽選しない。Aで確定するまで同じ選択を保つ。
    monkeypatch.setattr(p.random, 'choice',
                        lambda seq: (_ for _ in ()).throw(AssertionError('観測ごとに再抽選した')))
    assert p.summer_bonus_step(summer_screen(selected=0), mem) == [p.pad('a')]
    rec = mem['_records'][-1]
    assert rec['strategy_variant'] == 'summer_bonus_vacation'
    assert rec['observed_metric']['selection'] == 'random'
    assert 'summer_bonus_choice' not in mem
    # header が無い場合（所持金が読めない）も同じく保留しない。
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'bonus')
    mem = memory(30)
    assert p.summer_bonus_step(summer_screen(selected=0, gold=None), mem) == [p.pad('down')]


def test_an_unreadable_cursor_never_holds_the_event_screen():
    mem = memory(30)
    assert p.summer_bonus_step(summer_screen(hand=False), mem) == [p.pad('a')]
    rec = mem['_records'][-1]
    assert rec['decision'] == 'prompt' and rec['strategy_variant'] == 'summer_bonus_no_cursor'
    assert rec['observed_metric']['hand_visible'] is False
    assert compose(rec)[0] == 'summer_bonus'


def test_decide_routes_the_prompt_to_the_summer_bonus_step():
    actions, state = decide(prompt_canvas(), {})
    assert actions == [p.pad('a')]
    assert state['screen_kind'] == 'summer_bonus'
    assert state['policy']['gold'] == 7572        # header から所持金を観測済み
    assert state['policy'].get('soldiers_seen') is None
    actions, state = decide(prompt_canvas(), {'policy': {'soldiers_seen': 30}})
    assert actions == [p.pad('a')]
    assert state['screen_kind'] == 'summer_bonus'
    assert state['_records'][-1]['strategy_variant'] == 'summer_bonus_no_cursor'
