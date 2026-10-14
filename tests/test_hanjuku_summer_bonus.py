"""8月の月イチイベント「バカンス」は損失の小さい方を選ぶ。

オーナー決定 (2026-10-03): 兵士はひとり1Gなので、兵士の数とお金の数の
少ない方の選択肢を選ぶ。バカンス＝兵士半減、ボーナス＝お金半減、
まとめて解雇＝何も無いか兵士全滅（odoru7094 / gcgx event.html）なので
常に非選択。読めない時は**保留せずどちらかを選ぶ**（観測ごとに反転しない
よう、Aで確定するまで同じ選択を保つ）。選択位置が読めない時は方向入力で
再観測し、目標行を確認するまでAで確定しない。
"""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as p
from docich.hanjuku_commentary import compose
from docich.hanjuku_screen import Screen, find_hand, parse
from docich.hanjuku_font import TextLine
from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, decide
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
    mem = {'_records': [], 'month': '5-8'}
    if soldiers is not None:
        mem['soldiers_seen'] = soldiers
        mem['soldiers_seen_key'] = '5-8'
    return mem


KANA_LABELS = ('ばかんす', 'ぼなす', 'まとめてかいほう')


def prompt_canvas(labels=KANA_LABELS, gold=7572, selected=None, extra_hands=()):
    c = Canvas((16, 72, 57))
    if gold is not None:
        c.text(8, 8, f'5ねん8のつき{gold}G')       # 損失比較に要る所持金
    for i, label in enumerate(labels):
        c.text(176, 176 + 16 * i, label)
    if selected is not None:
        c.hand(138, 170 + 16 * selected)
    for x, y in extra_hands:
        c.hand(x, y)
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
    assert mem['summer_bonus_choice'] == 'vacation'
    assert not p.summer_bonus_continue(Screen([], None, '', kind='month_menu'), mem)
    assert 'summer_bonus_choice' not in mem and 'summer_bonus_plan' not in mem
    # header が無い場合（所持金が読めない）も同じく保留しない。
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'bonus')
    mem = memory(30)
    assert p.summer_bonus_step(summer_screen(selected=0, gold=None), mem) == [p.pad('down')]


def test_an_unreadable_cursor_moves_without_confirming_an_unknown_option():
    mem = memory(30)
    assert p.summer_bonus_step(summer_screen(hand=False), mem) == [p.pad('up')]
    rec = mem['_records'][-1]
    assert rec['decision'] == 'prompt' and rec['strategy_variant'] == 'summer_bonus_no_cursor'
    assert rec['observed_metric']['hand_visible'] is False
    assert rec['choice'] is None and rec['target'] == 'vacation'
    assert compose(rec)[0] == 'summer_bonus'
    assert '上へ動かして' in compose(rec)[1]


def test_decide_routes_the_prompt_to_the_summer_bonus_step(monkeypatch):
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'vacation')
    actions, state = decide(prompt_canvas(selected=0), {})
    assert actions == [p.pad('a')]
    assert state['screen_kind'] == 'summer_bonus'
    assert state['policy']['gold'] == 7572        # header から所持金を観測済み
    assert state['policy'].get('soldiers_seen') is None
    actions, state = decide(prompt_canvas(), {'policy': {'soldiers_seen': 30}})
    assert actions == [p.pad('up')]
    assert state['screen_kind'] == 'summer_bonus'
    assert state['_records'][-1]['strategy_variant'] == 'summer_bonus_no_cursor'


@pytest.mark.parametrize('seen_month', ['5-7', '4-8', None])
def test_stale_or_undated_soldiers_do_not_claim_a_minimum_loss(seen_month, monkeypatch):
    # Real persisted month keys are strings. Reproduce the previous-month save
    # through parse/observe/decide, including its JSON round trip.
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'bonus')
    state = json.loads(json.dumps({'policy': {
        'month': '5-7', 'gold': 20, 'soldiers_seen': 30,
        'soldiers_seen_key': seen_month}}))
    actions, state = decide(prompt_canvas(gold=100, selected=0), state)
    assert actions == [p.pad('down')]
    assert state['policy']['month'] == '5-8'
    assert state['policy']['summer_bonus_choice'] == 'bonus'
    never_random(monkeypatch)
    actions, state = decide(prompt_canvas(gold=100, selected=1), state)
    assert actions == [p.pad('a')]
    rec = state['_records'][-1]
    assert rec['choice'] == 'bonus'
    assert rec['observed_metric']['selection'] == 'random'
    assert rec['observed_metric']['soldiers_current'] is False
    assert rec['observed_metric']['current_month'] == '5-8'


@pytest.mark.parametrize('saved_month', ['5-7', '5-8', '4-8'])
def test_an_unread_header_cannot_make_matching_saved_months_current(saved_month, monkeypatch):
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'bonus')
    mem = json.loads(json.dumps({**memory(30), 'gold': 100, 'month': saved_month,
                                'soldiers_seen_key': saved_month}))
    assert p.summer_bonus_step(summer_screen(gold=None), mem) == [p.pad('down')]
    metric = mem['summer_bonus_plan']['metric']
    assert metric['selection'] == 'random'
    assert metric['soldiers_current'] is False and metric['current_month'] is None


def test_cursor_loss_keeps_the_random_choice_until_the_target_is_visible(monkeypatch):
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'bonus')
    mem = memory(None)
    assert p.summer_bonus_step(summer_screen(hand=False), mem) == [p.pad('up')]
    never_random(monkeypatch)
    for _ in range(3):
        assert p.summer_bonus_step(summer_screen(hand=False), mem) == [p.pad('up')]
        assert mem['summer_bonus_choice'] == 'bonus'
        assert mem['_records'][-1]['choice'] is None
    assert p.summer_bonus_step(summer_screen(selected=0), mem) == [p.pad('down')]
    assert p.summer_bonus_step(summer_screen(selected=1), mem) == [p.pad('a')]


@pytest.mark.parametrize('discharge', ['まとめてかいほう', 'まとめてかいこ'])
def test_an_event_sprite_cannot_hide_the_discharge_cursor(discharge):
    frame = prompt_canvas(labels=(*KANA_LABELS[:2], discharge), selected=2,
                          extra_hands=((40, 90),))
    assert find_hand(frame) is None              # global ambiguity reproduced
    screen = parse(frame)
    assert screen.kind == 'summer_bonus'
    assert screen.selected == discharge
    actions, state = decide(frame, {'policy': memory()})
    assert actions == [p.pad('up')]              # leave discharge; never confirm it


def test_two_hands_at_real_options_remain_ambiguous():
    frame = prompt_canvas(selected=2, extra_hands=((138, 170),))
    screen = parse(frame)
    assert screen.kind == 'summer_bonus' and screen.hand is None
    actions, state = decide(frame, {'policy': memory()})
    assert actions == [p.pad('up')]
    assert state['_records'][-1]['choice'] is None


def test_hotloaded_no_input_streak_cannot_confirm_an_unread_option():
    frame = prompt_canvas()
    actions, state = decide(frame, {'policy': memory(), 'no_input_frames': [frame.digest()],
                                   'no_input_streak': NO_INPUT_HOLD_MAX})
    assert actions == [p.pad('up')]
    assert state['no_input_streak'] == 0


def test_the_final_no_input_fallback_also_moves_instead_of_confirming(monkeypatch):
    # Exercise the final fallback independently of the normal recovery input.
    monkeypatch.setattr(p, 'summer_bonus_step', lambda screen, mem: [])
    frame = prompt_canvas()
    actions, state = decide(frame, {'policy': memory(), 'no_input_frames': [frame.digest()],
                                   'no_input_streak': NO_INPUT_HOLD_MAX})
    assert actions == [p.pad('up')]
    assert state['_records'][-1]['decision'] == 'no_input_fallback'


@pytest.mark.parametrize('labels', [('ばかんす', '', 'まとめてかいこ'),
                                   ('', '', 'まとめてかいこ'), ('', '', '')])
def test_a_partial_menu_cannot_escape_to_legacy_confirm(labels, monkeypatch):
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'bonus')
    _, state = decide(prompt_canvas(selected=2), {})
    state = json.loads(json.dumps(state))
    never_random(monkeypatch)
    partial = prompt_canvas(labels=labels, selected=2)
    assert parse(partial).kind != 'summer_bonus'
    actions, state = decide(partial, state)
    assert state['screen_kind'] == 'summer_bonus'
    assert actions == [p.pad('up')]
    assert state['policy']['summer_bonus_choice'] == 'bonus'
    assert state['_records'][-1]['choice'] is None
    actions, state = decide(prompt_canvas(selected=1), state)
    assert actions == [p.pad('a')]


def test_a_late_frame_after_confirm_keeps_the_choice_until_the_menu_is_gone(monkeypatch):
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'bonus')
    _, state = decide(prompt_canvas(selected=1), {})
    never_random(monkeypatch)
    actions, state = decide(prompt_canvas(labels=('ばかんす', '', 'まとめてかいこ'),
                                         selected=2), state)
    assert state['screen_kind'] == 'summer_bonus' and actions == [p.pad('up')]
    assert state['policy']['summer_bonus_choice'] == 'bonus'
    actions, state = decide(prompt_canvas(selected=1), state)
    assert actions == [p.pad('a')]
    result = Canvas((16, 72, 57))
    result.text(24, 80, 'しょじきんが はんぶんに')
    result.text(24, 96, 'なってしまった!')
    actions, state = decide(result.frame(), state)
    assert actions == [p.pad('a')]
    assert 'summer_bonus_plan' not in state['policy']
    assert 'summer_bonus_choice' not in state['policy']


@pytest.mark.parametrize('labels', [('ばかんす', '', 'まとめてかいこ'), ('', '', '')])
def test_label_disappearance_after_a_planned_confirm_is_not_a_result(labels, monkeypatch):
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'bonus')
    _, state = decide(prompt_canvas(selected=1), {})
    actions, state = decide(prompt_canvas(labels=labels), state)
    assert actions == [p.pad('up')]
    assert state['screen_kind'] == 'summer_bonus'
    assert state['policy']['summer_bonus_choice'] == 'bonus'


def test_vacation_response_advances_and_clears_the_choice(monkeypatch):
    monkeypatch.setattr(p.random, 'choice', lambda seq: 'vacation')
    _, state = decide(prompt_canvas(selected=0), {})
    response = Canvas((16, 72, 57))
    response.text(24, 80, 'バカンス バカンス バカザンス')
    actions, state = decide(response.frame(), state)
    assert actions == [p.pad('a')]
    assert state['screen_kind'] == 'summer_bonus_message'
    assert 'summer_bonus_plan' not in state['policy']


def test_a_readable_comparison_survives_a_later_missing_header(monkeypatch):
    never_random(monkeypatch)
    _, state = decide(prompt_canvas(selected=2), {'policy': memory(30)})
    actions, state = decide(prompt_canvas(gold=None, selected=0), state)
    assert actions == [p.pad('a')]
    assert state['_records'][-1]['observed_metric']['selection'] == 'min_loss'


def test_pending_v129_choice_migrates_even_when_every_label_is_missing(monkeypatch):
    never_random(monkeypatch)
    frame = Canvas((120, 120, 120))
    frame.text(8, 8, '5ねん8のつき100G')
    frame.hand(138, 202)                     # unlabelled discharge row
    old = {'phase': 'event', 'screen_kind': 'summer_bonus', 'policy': {
        'month': '5-8', 'gold': 100, 'summer_bonus_choice': 'bonus'}}
    actions, state = decide(frame.frame(), old)
    assert actions == [p.pad('up')]
    assert state['screen_kind'] == 'summer_bonus'
    assert state['policy']['summer_bonus_plan']['target'] == 'bonus'
    actions, state = decide(prompt_canvas(selected=1), state)
    assert actions == [p.pad('a')]


@pytest.mark.parametrize('choice,text', [('vacation', 'ひゃっほ!'), ('bonus', 'さすがは どうしさま!')])
def test_the_first_post_choice_line_can_advance_before_the_final_result(choice, text, monkeypatch):
    monkeypatch.setattr(p.random, 'choice', lambda seq: choice)
    _, state = decide(prompt_canvas(selected=0 if choice == 'vacation' else 1), {})
    response = Canvas((120, 120, 120))
    response.text(24, 80, text)
    actions, state = decide(response.frame(), state)
    assert actions == [p.pad('a')]
    assert state['screen_kind'] == 'summer_bonus_message'
    assert 'summer_bonus_plan' not in state['policy']
