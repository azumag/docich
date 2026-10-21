"""g684 13:05: the 花いちもんめ lead-in was plain text, its choice was not a
measured yes/no, A accepted and ヴィーナス (82) was traded for シェーブル (27)."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

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


def _offer(mem, name):
    policy.trade_guard_step(text('トレードをしようじゃないか'), mem)
    policy.trade_guard_step(text(f'そちらの{name}しょうぐんとわがぐんのイキのいいのとではどーだ?'), mem)


def test_a_weak_named_general_is_traded_on_the_default_row():
    # Owner 2026-10-11: 弱い将軍なら受ける (シェーブル 27/4 in gcgx).
    mem = {'tick': 10, 'chapter': 1}
    _offer(mem, 'シェーブル')
    assert mem['trade_guard']['accept'] is True
    assert policy.trade_guard_step(choice(['OK', 'NO'], 1), mem) == [policy.pad('up')]
    assert policy.trade_guard_step(choice(['OK', 'NO'], 0), mem) == [policy.pad('a')]
    assert mem['_records'][-1]['strategy_variant'] == 'accept_weak_general_trade'


def test_strong_chart_hero_and_unknown_generals_are_refused():
    for name, rule in (('ヴィーナス', 'chart_general'), ('ゼウス', 'strong'),
                       ('どうし', 'hero'), ('ココット', 'chart_general'), ('だれか', 'unknown')):
        accept, evidence = policy.trade_accept_weak({'chapter': 1}, name)
        assert accept is False and evidence['rule'] == rule, name
    mem = {'tick': 10, 'chapter': 1}
    _offer(mem, 'ゼウス')
    assert policy.trade_guard_step(choice(['OK', 'NO'], 0), mem) == [policy.pad('down')]
    assert policy.trade_guard_step(choice(['OK', 'NO'], 1), mem) == [policy.pad('a')]
    assert mem['_records'][-1]['strategy_variant'] == 'decline_general_trade'


def test_measured_yes_no_accepts_only_a_weak_general():
    from docich.hanjuku_font import TextLine as T
    def yn(name, on):
        lines = [T(183, tuple((184 + 8 * j, ch) for j, ch in enumerate('うむッ!'))),
                 T(199, tuple((184 + 8 * j, ch) for j, ch in enumerate('いかんッ!')))]
        y = 183 if on == 'yes' else 199
        return Screen(lines=lines, hand=(162, y - 6, 182, y + 6),
                      text=f'そちらの{name}しょうぐんとイキのいいのとではどーだ?うむッ!いかんッ!', kind='yes_no')
    mem = {'chapter': 1}
    assert policy.yes_no_step(yn('アルファルファ', 'yes'), mem) == [policy.pad('a')]
    assert mem['_records'][-1]['strategy_variant'] == 'accept_weak_general_trade'
    mem = {'chapter': 1}
    assert policy.yes_no_step(yn('ゼウス', 'yes'), mem) == [policy.pad('down')]


def trade_pixels(*, question=True, rows=('そうする', 'やめる'), selected=0,
                 hand=True, x=(40, 40), y=(183, 199), cursor=None, unread=False,
                 unread_suffix=False, unread_extra=False):
    from test_hanjuku_chart_bot import Canvas, GREEN
    # A bottom dialogue panel, without the all-green Canvas's shop background.
    canvas = Canvas((0, 0, 0))
    canvas.rgb[144 * 256 * 3:] = bytes(GREEN) * (256 * 80)
    if question:
        canvas.text(24, 151, 'そちらのゼウスしょうぐんと')
        canvas.text(24, 167, 'イキのいいのとではどーだ?')
    for i, word in enumerate(rows):
        canvas.text(x[i], y[i], word)
    if unread:
        canvas.tile(x[1], y[1], 0x0123456789ABCDEF)
    if unread_suffix:
        canvas.tile(x[1] + 8 * len(rows[1]), y[1], 0x0123456789ABCDEF)
    if unread_extra:
        canvas.tile(120, 207, 0x0123456789ABCDEF)
    if hand:
        hx, hy = cursor if cursor is not None else (x[selected] - 22, y[selected] - 6)
        canvas.hand(hx, hy)
    return canvas.frame()


def trade_state(name='ゼウス'):
    mem = {'tick': 10, 'chapter': 1}
    _offer(mem, name)
    mem.pop('_records', None)
    return {'policy': mem}


def decide_trade(frame, state=None):
    from docich.hanjuku_bot import classify, decide
    from docich.hanjuku_screen import parse
    screen = parse(frame)
    assert classify(frame) == 'dialogue' and screen.kind == 'text'
    return decide(frame, trade_state() if state is None else state)


def test_question_lines_are_not_choices_in_the_public_pixel_decision_path():
    actions, state = decide_trade(trade_pixels())
    assert actions == [policy.pad('down')]
    assert state['policy']['trade_guard']['accept'] is False
    assert not state['policy']['trade_guard']['chosen']
    assert not any(row.get('strategy_variant') == 'decline_general_trade'
                   for row in state['_records'])
    actions, state = decide_trade(trade_pixels(selected=1), state)
    assert actions == [policy.pad('a')]
    record = state['_records'][-1]
    assert record['choice'] == 'やめる'
    assert record['observed_metric']['rows'] == [183, 199]


@pytest.mark.parametrize('question', [False, True])
def test_missing_hand_holds_a_readable_trade_choice_instead_of_legacy_a(question):
    frame = trade_pixels(question=question, hand=False)
    actions, state = decide_trade(frame)
    assert actions == []
    assert not state['policy']['trade_guard']['chosen']
    assert any(row['decision'] == 'situation_held' for row in state['_records'])
    actions, state = decide_trade(trade_pixels(question=question), state)
    assert actions == [policy.pad('down')]


def test_a_choice_only_frame_without_a_trade_guard_keeps_legacy_behavior():
    actions, state = decide_trade(trade_pixels(question=False, hand=False),
                                 {'policy': {'chapter': 1}})
    assert actions == [policy.pad('a')]
    assert 'trade_guard' not in state['policy']


@pytest.mark.parametrize('name,accept', [
    ('シェーブル', True), ('ゼウス', False), ('どうし', False),
    ('ヴィーナス', False), ('だれか', False),
])
@pytest.mark.parametrize('selected', [0, 1])
@pytest.mark.parametrize('rows', [('そうする', 'やめる'), ('やめる', 'そうする')])
def test_verified_pixel_choices_preserve_strength_and_named_general_rules(name, accept, selected, rows):
    actions, state = decide_trade(trade_pixels(question=False, selected=selected, rows=rows), trade_state(name))
    target = rows.index('そうする' if accept else 'やめる')
    assert actions == [policy.pad('a' if selected == target else 'down' if target > selected else 'up')]
    assert state['policy']['trade_guard']['accept'] is accept


@pytest.mark.parametrize('options', [
    {'rows': ('そうする',), 'hand': False},
    {'rows': ('わからない', 'やめる')},
    {'rows': ('わからない', 'やめる'), 'hand': False},
    {'rows': ('わからない', 'ほりゅう'), 'hand': False},
    {'rows': ('そうする', 'そうする')},
    {'x': (40, 48)},
    {'y': (183, 207)},
    {'rows': ('そうする', 'やめる', 'ほりゅう'), 'x': (40, 40, 40), 'y': (176, 192, 207)},
    {'rows': ('そうする', 'やめる', 'ほりゅう'), 'x': (40, 40, 120), 'y': (176, 192, 207)},
    {'cursor': (18, 145)},
    {'cursor': (98, 177)},
    {'cursor': (0, 177)},
    {'unread': True},
    {'unread': True, 'hand': False},
    {'unread_suffix': True},
    {'unread_extra': True},
])
def test_unknown_incomplete_or_misaligned_pixel_choices_never_confirm(options):
    actions, state = decide_trade(trade_pixels(**options), trade_state('シェーブル'))
    assert actions == []
    assert not state['policy']['trade_guard']['chosen']
    assert not any(row.get('strategy_variant') in ('decline_general_trade', 'accept_weak_general_trade')
                   for row in state['_records'])


def test_missing_choice_pixels_after_a_seen_choice_do_not_release_to_plain_dialogue():
    _, state = decide_trade(trade_pixels())
    actions, state = decide_trade(trade_pixels(rows=(), hand=False), state)
    assert actions == [] and not state['policy']['trade_guard']['chosen']


def test_readable_trade_dialogue_without_choices_still_advances():
    from test_hanjuku_chart_bot import Canvas, GREEN
    canvas = Canvas((0, 0, 0))
    canvas.rgb[144 * 256 * 3:] = bytes(GREEN) * (256 * 80)
    canvas.text(24, 183, 'トレードをしようじゃないか')
    actions, state = decide_trade(canvas.frame(), {'policy': {'chapter': 1}})
    assert actions == [policy.pad('a')]
    assert not state['policy']['trade_guard']['chosen']
    actions, state = decide_trade(trade_pixels(rows=(), hand=False), state)
    assert actions == [policy.pad('a')]


def test_trade_guard_expiry_keeps_its_observation_boundary():
    mem = trade_state()['policy']
    mem['tick'] = 10 + policy.TRADE_GUARD_OBSERVATIONS
    assert policy.trade_guard_step(choice(['OK', 'NO'], 0), mem) == [policy.pad('down')]
    mem = trade_state()['policy']  # a lead-in that has never seen its choice
    mem['tick'] = 11 + policy.TRADE_GUARD_OBSERVATIONS
    assert policy.trade_guard_step(choice(['OK', 'NO'], 0), mem) is None
    assert 'trade_guard' not in mem


@pytest.mark.parametrize('options', [
    {'hand': False}, {'unread': True},
    {'question': False, 'hand': False},
    {'question': False, 'unread': True, 'hand': False},
    {'question': False, 'rows': ('そうする',), 'hand': False},
])
def test_json_restored_trade_hold_survives_observations_100_and_101(options):
    import json
    from docich import hanjuku_bot as bot, hanjuku_screen as screen_module
    from hanjuku_frame_read_cache import reuse_frame_reads
    from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, decide
    from docich.hanjuku_screen import parse
    frame = trade_pixels(**options)
    assert parse(frame).kind == 'text'
    state = trade_state()
    # Reuse only immutable frame reads, as the public no-input gate contracts
    # do. Every policy/FSM decision and JSON persistence round trip still runs.
    with reuse_frame_reads(bot, screen_module):
        for observation in range(1, NO_INPUT_HOLD_MAX + 11):
            state = json.loads(json.dumps(state, ensure_ascii=False))
            actions, state = decide(frame, state)
            assert actions == []
            assert state['no_input_streak'] == observation
            assert state['policy']['trade_guard']['held'] is True
            assert not state['policy']['trade_guard']['chosen']
            assert not any(row['decision'] == 'no_input_fallback' for row in state['_records'])


def test_an_expired_lead_in_without_any_seen_choice_releases_normally():
    frame = trade_pixels(question=False, hand=False)
    state = trade_state()
    state['policy']['tick'] = 10 + policy.TRADE_GUARD_OBSERVATIONS
    actions, state = decide_trade(frame, state)
    assert actions == [policy.pad('a')]
    assert 'trade_guard' not in state['policy']


def test_a_readable_different_prose_screen_releases_the_previous_trade_choice():
    import json
    from test_hanjuku_chart_bot import Canvas, GREEN
    _, state = decide_trade(trade_pixels(question=False, hand=False))
    canvas = Canvas((0, 0, 0))
    canvas.rgb[144 * 256 * 3:] = bytes(GREEN) * (256 * 80)
    canvas.text(24, 151, 'おはなしがつづく')
    actions, state = decide_trade(canvas.frame(), json.loads(json.dumps(state)))
    assert actions == [policy.pad('a')]
    assert 'trade_guard' not in state['policy']


@pytest.mark.parametrize('tiles, prose_x', [
    (((24, 151),), 32),      # the prose lost its leading cell
    (((88, 151),), 24),      # the prose lost its trailing cell
])
def test_a_prose_body_that_lost_an_edge_cell_keeps_the_trade_hold(tiles, prose_x):
    # The release boundary above is the same one bijin_guard copies, so it has
    # the same hole: `joined()` strips UNKNOWN from both ends of every row and
    # `UNKNOWN not in flat` therefore released a partly read page (PR #2325
    # review, P1). Read the raw cells of the message band instead.
    import json
    from test_hanjuku_chart_bot import Canvas, GREEN
    from docich.hanjuku_font import UNKNOWN
    from docich.hanjuku_screen import parse
    _, state = decide_trade(trade_pixels(question=False, hand=False))
    canvas = Canvas((0, 0, 0))
    canvas.rgb[144 * 256 * 3:] = bytes(GREEN) * (256 * 80)
    canvas.text(prose_x, 151, 'おはなしがつづく')
    for x, y in tiles:
        canvas.tile(x, y, 0x0123456789ABCDEF)
    frame = canvas.frame()
    screen = parse(frame)
    assert screen.kind == 'text'
    assert UNKNOWN not in screen.text          # the aggregate hides the gap
    actions, state = decide_trade(frame, json.loads(json.dumps(state)))
    assert actions == []
    assert state['policy']['trade_guard']['held'] is True
    assert not any(row['decision'] == 'no_input_fallback' for row in state['_records'])


def test_a_new_monthly_owner_clears_a_previous_trade_hold():
    from docich.hanjuku_screen import parse
    _, state = decide_trade(trade_pixels(hand=False))
    mem = state['policy']
    mem['month_sub'] = {'kind': 'recruit'}
    assert policy.trade_guard_step(parse(trade_pixels(hand=False)), mem, phase='dialogue') is None
    assert 'trade_guard' not in mem


def test_a_completed_trade_releases_its_guard():
    _, state = decide_trade(trade_pixels(question=False, selected=1))
    assert state['policy']['trade_guard']['chosen'] is True
    actions, state = decide_trade(trade_pixels(rows=(), hand=False), state)
    assert actions == [policy.pad('a')]
    assert 'trade_guard' not in state['policy']


def test_trade_hold_releases_ownership_when_a_measured_confirmation_returns():
    import json
    from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, decide
    from docich.hanjuku_screen import parse
    from test_hanjuku_trade_refusal import confirmation
    _, state = decide_trade(trade_pixels(hand=False))
    state = json.loads(json.dumps(state))
    frame = confirmation(cursor='missing')
    assert parse(frame).kind == 'yes_no'
    state['no_input_frames'] = [frame.digest()]
    state['no_input_streak'] = NO_INPUT_HOLD_MAX
    actions, state = decide(frame, state)
    assert actions == [policy.pad('b')]
    assert not state['policy']['trade_guard'].get('held')
    assert any(row['decision'] == 'no_input_fallback' for row in state['_records'])


def test_trade_hold_cannot_follow_the_month_menu_foreground():
    import json
    from docich.hanjuku_bot import decide
    from docich.hanjuku_screen import parse
    from test_hanjuku_chart_bot import month_canvas
    _, state = decide_trade(trade_pixels(hand=False))
    state = json.loads(json.dumps(state))
    frame = month_canvas(100)
    assert parse(frame).kind == 'month_menu'
    actions, state = decide(frame, state)
    assert 'trade_guard' not in state['policy']


def test_unowned_unknown_transition_keeps_its_legacy_no_input_fallback():
    from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, decide
    from test_hanjuku_chart_bot import Canvas
    frame = Canvas((0, 0, 0)).frame()
    state = {'policy': {}, 'no_input_frames': [frame.digest()],
             'no_input_streak': NO_INPUT_HOLD_MAX}
    actions, state = decide(frame, state)
    assert state['phase'] == 'transition' and actions == [policy.pad('a')]
    assert any(row['decision'] == 'no_input_fallback' for row in state['_records'])


@pytest.mark.parametrize('phase,expected', [('field', 'select'), ('transition', 'a')])
def test_a_trade_hold_cannot_exempt_a_later_field_or_transition(phase, expected):
    import json
    from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, classify, decide
    from docich.hanjuku_screen import parse
    from test_hanjuku_no_input_gate import FIELD, DARK
    _, state = decide_trade(trade_pixels(hand=False))
    state = json.loads(json.dumps(state))
    frame = FIELD if phase == 'field' else DARK
    assert classify(frame) == phase and parse(frame).kind == 'unknown'
    state['no_input_frames'] = [frame.digest()]
    state['no_input_streak'] = NO_INPUT_HOLD_MAX
    actions, state = decide(frame, state)
    assert actions == [policy.pad(expected)]
    assert 'trade_guard' not in state['policy']
    assert any(row['decision'] == 'no_input_fallback' for row in state['_records'])
    actions, state = decide_trade(trade_pixels(question=False, hand=False),
                                 json.loads(json.dumps(state)))
    assert actions == [policy.pad('a')]
    assert 'trade_guard' not in state['policy']


def test_persisted_trade_hold_cannot_exempt_another_owners_unknown_screen(monkeypatch):
    import json
    from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, decide
    from test_hanjuku_chart_bot import Canvas
    _, state = decide_trade(trade_pixels(hand=False))
    state = json.loads(json.dumps(state))
    frame = Canvas((0, 0, 0)).frame()
    state['no_input_frames'] = [frame.digest()]
    state['no_input_streak'] = NO_INPUT_HOLD_MAX
    # The higher-priority owner prevents trade_guard_step from running.
    monkeypatch.setattr(policy, 'defender_egg_pending_step', lambda _screen, _mem: [])
    actions, state = decide(frame, state)
    assert state['phase'] == 'transition'
    assert actions == [policy.pad('a')]
    assert any(row['decision'] == 'no_input_fallback' for row in state['_records'])


@pytest.mark.parametrize('phase', ['field', 'transition'])
def test_trade_guard_is_released_before_a_real_pending_command_owner(phase):
    import json
    from docich.hanjuku_bot import classify, decide
    from docich.hanjuku_screen import parse
    from test_hanjuku_no_input_gate import FIELD, DARK
    _, state = decide_trade(trade_pixels(hand=False))
    state = json.loads(json.dumps(state))
    state['policy']['battle'] = {'command_open_pending': {'owner': 'retreat', 'waits': 0}}
    control = json.loads(json.dumps(state))
    control['policy'].pop('trade_guard')
    other_frame = FIELD if phase == 'field' else DARK
    blank_dialogue = trade_pixels(question=False, rows=(), hand=False)
    assert classify(other_frame) == phase and parse(other_frame).kind == 'unknown'
    assert classify(blank_dialogue) == 'dialogue' and parse(blank_dialogue).kind == 'unknown'
    # The real higher-priority owner consumes its two waits without dispatching
    # trade_guard_step. A prior exchange must already be gone on that field/fade.
    for frame, expected in ((other_frame, [{'type': 'wait', 'ms': 50}]),
                            (blank_dialogue, [{'type': 'wait', 'ms': 50}]),
                            (blank_dialogue, [policy.pad('a')])):
        actions, state = decide(frame, json.loads(json.dumps(state)))
        control_actions, control = decide(frame, json.loads(json.dumps(control)))
        assert actions == control_actions == expected
        assert 'trade_guard' not in state['policy']
    assert 'command_open_pending' not in state['policy']['battle']
