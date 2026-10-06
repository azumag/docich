"""Monthly foreground menus and hot-loaded navigation preserve input ownership.

These are synthetic compositions of the existing glyph and hand measurements.
They reproduce the background/foreground ambiguity without claiming that an
unexported live frame has a particular foreground panel.
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_house as house, hanjuku_policy as policy
from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, classify, decide
from docich.hanjuku_screen import find_hand, parse
from test_hanjuku_chart_bot import Canvas, GREEN, MONTH_GRID


def monthly_canvas(on='しょうにん'):
    """Native monthly rectangles, including a readable 2-11 / 59G header."""
    canvas = Canvas((50, 30, 10))
    for x0, y0, x1, y1 in ((48, 40, 210, 104), (18, 176, 230, 206)):
        for y in range(y0, y1):
            for x in range(x0, x1):
                canvas.put(x, y, GREEN)
    canvas.text(48, 15, '2ねん11のつき59G')
    for label, (x, y) in MONTH_GRID.items():
        canvas.text(x, y, label)
    if on is not None:
        x, y = MONTH_GRID[on]
        canvas.hand(x - 22, y - 6)
    return canvas


def confirmation_canvas(on='うむッ!', *, foreground_hand=True):
    canvas = monthly_canvas()
    canvas.text(24, 167, 'よろしいですかな?')
    canvas.text(184, 183, 'うむッ!')
    canvas.text(184, 199, 'いかんッ!')
    if foreground_hand:
        canvas.hand(162, 177 if on == 'うむッ!' else 193)
    return canvas


def monthly_memory(phase=None):
    mem = {'chapter': 1, 'month': '2-11', 'gold': 59, 'orders': {}, 'picked': [],
           'stats': {'battles': 183, 'wins': 183}}
    if phase is not None:
        mem['house'] = {'phase': phase, 'chapter': 1, 'month_scan': True,
                        'age': 0, 'total': 0, 'seen': [], 'pending': []}
    return mem


@pytest.mark.parametrize('choice,y', [('うむッ!', 177), ('いかんッ!', 193)])
def test_month_confirmation_uses_foreground_hand_with_background_hand_visible(choice, y):
    frame = confirmation_canvas(choice).frame()
    assert find_hand(frame) is None                 # two real hand-sized components
    screen = parse(frame)
    assert screen.kind == 'month_menu'              # retains monthly payment guards
    assert screen.hand == (162, y, 179, y + 12)
    assert screen.selected == choice
    assert not policy.month_menu_ready(screen)


def test_missing_foreground_hand_cannot_reuse_the_background_selection():
    frame = confirmation_canvas(foreground_hand=False).frame()
    assert find_hand(frame) == (26, 41, 43, 53)
    screen = parse(frame)
    assert screen.kind == 'month_menu'
    assert screen.hand is None and screen.selected is None
    assert not policy.month_menu_ready(screen)
    actions, state = decide(frame, {'policy': monthly_memory()})
    assert policy.pad('a') not in actions
    assert not state['policy'].get('house')
    assert not state['policy'].get('month_sub')


def test_two_foreground_hands_remain_ambiguous_instead_of_picking_one():
    canvas = confirmation_canvas()
    canvas.hand(162, 193)
    screen = parse(canvas.frame())
    assert screen.kind == 'month_menu'
    assert screen.hand is None and screen.selected is None
    assert not policy.month_menu_ready(screen)


def test_one_confirmation_word_does_not_invent_a_foreground_pair():
    canvas = monthly_canvas()
    canvas.text(184, 183, 'うむッ!')
    screen = parse(canvas.frame())
    assert screen.kind == 'month_menu'
    assert screen.hand == (26, 41, 43, 53)
    assert screen.selected == 'しょうにん'


@pytest.mark.parametrize('house_phase', [None, 'month_open', 'open_roster'])
def test_unowned_month_confirmation_is_declined_without_payment_or_survey_takeover(house_phase):
    state = {'policy': monthly_memory(house_phase)}
    actions, state = decide(confirmation_canvas().frame(), state)
    assert actions == [policy.pad('down')]
    assert not state['policy'].get('month_exit')
    assert not state['policy'].get('month_sub')
    actions, state = decide(confirmation_canvas('いかんッ!').frame(), state)
    assert actions == [policy.pad('a')]
    confirmed = [row for row in state['_records'] if row['decision'] == 'month_confirm']
    assert len(confirmed) == 1 and confirmed[0]['choice'] == 'いかんッ!'
    assert state['policy']['gold'] == 59
    assert tuple(state['policy']['stats'][key] for key in ('battles', 'wins')) == (183, 183)
    assert state['policy']['shop'].get('bought', []) == []
    assert not state['policy'].get('month_sub')
    if house_phase is None:
        assert not state['policy'].get('house')
    else:
        assert state['policy']['house']['phase'] == house_phase


def test_owned_month_exit_still_confirms_the_visible_yes_choice():
    mem = monthly_memory()
    mem['month_exit'] = True
    actions, state = decide(confirmation_canvas().frame(), {'policy': mem})
    assert actions == [policy.pad('a')]
    assert state['policy']['month_exit'] is False
    assert any(row['decision'] == 'month_confirm' and row['choice'] == 'うむッ!'
               for row in state['_records'])


@pytest.mark.parametrize('kind,button,decision', [
    ('egg', 'b', 'egg_recover_skip'),
    ('chikujou', 'down', 'chikujou_declined'),
])
def test_paid_foreground_keeps_its_own_budget_handler(kind, button, decision):
    canvas = monthly_canvas()
    canvas.text(24, 151, '50Gかかりますが')
    canvas.text(184, 183, 'うむッ!')
    canvas.text(184, 199, 'いかんッ!')
    canvas.hand(162, 177)
    mem = monthly_memory()
    mem['chapter'] = 2
    mem['recruit_month_scan_attempts'] = {'scope': [2, '2-11'], 'count': 2}
    policy._plan(mem, {'year': 2, 'month': 11, 'gold': 59})
    mem['month_sub'] = {'kind': kind, 'gold_before': 59, 'presses': 0,
                        'key': '2-11', 'left_menu': True, 'full_selected': False,
                        'chikujou_version': 2}
    actions, state = decide(canvas.frame(), {'policy': mem, 'screen_kind': 'month_menu'})
    assert actions == [policy.pad(button)]
    records = state['_records']
    assert any(row['decision'] == decision for row in records)
    assert not any(row['decision'] in {'month_confirm', 'egg_recover_confirm', 'chikujou_confirm'}
                   for row in records)
    sub = state['policy']['month_sub']
    assert not sub.get('confirm_sent') and sub.get('stage') != 'recovering'
    assert sub.get('quoted_cost') is None
    assert state['policy']['gold'] == 59


def test_paid_egg_return_without_hand_is_not_treated_as_a_foreground_ritual():
    frame = monthly_canvas(on=None).frame()
    mem = monthly_memory()
    mem['month_sub'] = {'kind': 'egg', 'gold_before': 109, 'quoted_cost': 50,
                        'full_selected': True, 'stage': 'recovering',
                        'left_menu': True, 'key': '2-11', 'presses': 10}
    actions, state = decide(frame, {'policy': mem, 'screen_kind': 'month_menu'})
    assert actions == []
    assert state['policy']['gold'] == 59
    assert 'month_sub' not in state['policy']
    receipt = next(row for row in state['_records'] if row['decision'] == 'egg_recover')
    assert receipt['observed_metric']['gold_before'] == 109
    assert receipt['observed_metric']['gold_after'] == 59
    assert not any(row['decision'] == 'egg_recover_confirm' for row in state['_records'])


@pytest.mark.parametrize('phase', ['month_open', 'open_roster'])
def test_quantity_foreground_is_not_ready_for_monthly_roster_navigation(phase):
    canvas = monthly_canvas()
    canvas.text(24, 183, 'じゅうじキーでへいしのかずを')
    frame = canvas.frame()
    screen = parse(frame)
    assert screen.kind == 'month_menu' and screen.hand is not None
    assert not policy.month_menu_ready(screen)
    mem = monthly_memory(phase)
    assert house.step(screen, mem, frame) is None
    assert mem['house']['phase'] == phase


def test_information_menu_is_foreground_even_when_monthly_labels_remain():
    canvas = monthly_canvas(on=None)
    canvas.text(48, 127, 'しょうぐん')
    canvas.text(48, 143, 'ステータス')
    canvas.text(48, 159, 'システム')
    canvas.hand(26, 121)
    frame = canvas.frame()
    screen = parse(frame)
    assert 'しょうにん' in screen.text and 'おしまい' in screen.text
    assert screen.kind == 'main_menu'
    assert screen.selected == 'しょうぐん'
    actions, state = decide(frame, {'policy': monthly_memory('open_roster')})
    assert actions == [policy.pad('a')]
    assert state['policy']['house']['phase'] == 'roster'


def test_stuck_monthly_roster_dismisses_with_b_without_claiming_roster_opened():
    frame = monthly_canvas().frame()
    state = {'policy': monthly_memory('month_open')}
    plans = []
    for _ in range(policy.MENU_NAV_PRESS_LIMIT + 4):
        actions, state = decide(frame, state)
        plans.append(actions)
        assert state['policy']['house']['phase'] == 'month_open'
    assert plans[0] == [policy.pad('down')]
    assert plans.count([policy.pad('b')]) == 1
    assert all(policy.pad('a') not in actions for actions in plans)
    assert tuple(state['policy']['stats'][key] for key in ('battles', 'wins')) == (183, 183)


@pytest.mark.parametrize('phase', ['month_open', 'open_roster'])
def test_hotload_reobserves_legacy_exhausted_house_navigation(phase):
    mem = monthly_memory(phase)
    legacy = {'direction': 'down', 'distance': 48, 'presses': 120}
    mem['house_nav'] = dict(legacy)
    mem['month_nav'] = dict(legacy)
    state = {'screen_kind': 'month_menu', 'phase': 'month_menu', 'phase_step': 200,
             'policy': mem}
    actions, state = decide(monthly_canvas().frame(), state)
    assert actions == [policy.pad('down')]
    assert state['policy']['house']['phase'] == 'month_open'
    assert state['policy']['gold'] == 59 and state['policy']['month'] == '2-11'
    assert tuple(state['policy']['stats'][key] for key in ('battles', 'wins')) == (183, 183)


def test_measured_information_menu_target_still_opens_normally():
    frame = monthly_canvas('メインメニュー').frame()
    actions, state = decide(frame, {'policy': monthly_memory('month_open')})
    assert actions == [policy.pad('a')]
    assert state['policy']['house']['phase'] == 'open_roster'
    assert not any(row['decision'] == 'menu_nav_stuck' for row in state['_records'])


@pytest.mark.parametrize('change', ['label', 'screen_kind', 'month', 'chapter'])
def test_exhausted_navigation_does_not_poison_a_new_menu_context(change):
    screen = parse(monthly_canvas().frame())
    mem = monthly_memory()
    for _ in range(policy.MENU_NAV_PRESS_LIMIT + 3):
        policy.guarded_menu_to(screen, mem, 'へいしほじゅう', key='month_nav')
    label = 'へいしほじゅう'
    if change == 'label':
        label = 'メインメニュー'  # same down direction but farther away
    elif change == 'screen_kind':
        screen.kind = 'text'
    elif change == 'month':
        mem['month'] = '2-12'
    else:
        mem['chapter'] = 2
    assert policy.guarded_menu_to(screen, mem, label, key='month_nav') == policy.pad('down')


def test_no_input_fallback_cannot_confirm_an_unread_monthly_selection():
    frame = monthly_canvas(on=None).frame()
    assert classify(frame) == 'month_menu' and parse(frame).kind == 'month_menu'
    mem = monthly_memory()
    mem['recruit_month_scan_attempts'] = {'scope': [1, '2-11'], 'count': 2}
    state = {'policy': mem, 'phase': 'month_menu', 'phase_step': 200,
             'screen_kind': 'month_menu', 'no_input_streak': NO_INPUT_HOLD_MAX,
             'no_input_frames': [frame.digest()]}
    actions, state = decide(frame, state)
    assert actions == [policy.pad('b')]
    assert any(row['decision'] == 'no_input_fallback' for row in state['_records'])
    assert not state['policy'].get('month_exit')
    assert not state['policy'].get('month_sub')
    assert state['policy']['gold'] == 59


def test_unowned_unread_monthly_screen_releases_the_scan_after_its_budget():
    frame = monthly_canvas(on=None).frame()
    screen = parse(frame)
    assert screen.kind == 'month_menu' and screen.hand is None
    mem = monthly_memory('month_open')
    for _ in range(house.STEP_LIMIT):
        assert house.step(screen, mem, frame) is None
    assert mem['house']['phase'] == 'close'
    assert mem['recruit_hold']['status'] == 'observation_failed'
    assert house.step(screen, mem, frame) is None
    assert 'house' not in mem
    assert any(row['decision'] == 'house_deferred' for row in mem['_records'])
    assert not any(row['decision'] == 'house_month_scan_complete' for row in mem['_records'])
    assert mem['gold'] == 59 and mem['month'] == '2-11'


def test_owned_monthly_transaction_pauses_the_unread_roster_scan_budget():
    frame = monthly_canvas(on=None).frame()
    screen = parse(frame)
    mem = monthly_memory('open_roster')
    mem['house'].update(age=2, total=9)
    mem['month_sub'] = {'kind': 'egg', 'key': '2-11', 'gold_before': 59,
                        'presses': 0, 'left_menu': True}
    for _ in range(house.STEP_LIMIT + 1):
        assert house.step(screen, mem, frame) is None
    assert mem['house']['phase'] == 'open_roster'
    assert (mem['house']['age'], mem['house']['total']) == (2, 9)
    assert mem['month_sub']['kind'] == 'egg'
    assert not any(row['decision'] in {'house_deferred', 'house_month_scan_complete'}
                   for row in mem.get('_records', []))
