"""Prompt guards: captured g825 pixels, exact accents and unread-body controls.

Every guard is invoked for real. Pixel controls are synthetic and are not
claimed to be additional live captures.
"""
from pathlib import Path

import pytest

from docich.hanjuku.flows import prompts as p
from docich.hanjuku.os_layer.font import TextLine, UNKNOWN, joined
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import Screen, parse
from docich.hanjuku.os_layer.readers import _options
from test_hanjuku_chart_bot import Canvas, GREEN

FIXTURES = Path(__file__).parent / 'fixtures/hanjuku'
RESPONSE = 'g825-summer-response.png'
FADE = 'g825-summer-post-a-fade.png'


def capture(name=RESPONSE):
    return parse(read_png(FIXTURES / name))


def memory(owner, *, choice_seen=True):
    return {'tick': 10, 'chapter': 1, owner + '_guard': {
        'tick': 10, 'chosen': False, 'choice_seen': choice_seen}}


def guard(owner, screen, mem):
    return getattr(p, owner + '_guard_step')(screen, mem, phase='dialogue')


def from_lines(lines, hand=None):
    return Screen(lines=lines, hand=hand, text=joined(lines), kind='text',
                  header={'year': 2, 'month': 8, 'gold': 30})


def row(y, word, x=40, *, outer=False):
    cells = tuple((x + 8 * i, ch) for i, ch in enumerate(word))
    return TextLine(y, cells + (((248, UNKNOWN),) if outer else ()))


def control_frame(*, body='むらのひと', unknown_row=False, band=None, hand=False,
                  dialogue_background=False):
    # Preserve the capture's actual outer frame and header, replace only its body.
    frame = read_png(FIXTURES / RESPONSE)
    c = Canvas()
    c.rgb = bytearray(frame.rgb)
    if dialogue_background:
        # g825's original curtain routes the public dispatcher to concert.
        # For trade/bijin public-path controls, remove only that backdrop;
        # keep the actual lower panel/outer-frame pixels from the capture.
        for y in range(135):
            for x in range(256):
                c.put(x, y, (0, 0, 0))
        c.text(48, 15, '2ねん 8のつき 30G')
    for y in range(135, 176):
        for x in range(24, 232):
            c.put(x, y, GREEN)
    if body:
        c.text(24, 151, body)
    if unknown_row:
        c.tile(24, 167, 0x0123456789ABCDEF)
    if band is not None:
        x, y, text = band
        if text == UNKNOWN:
            c.tile(x, y, 0x0123456789ABCDEF)
        else:
            c.text(x, y, text)
    if hand:
        c.hand(18, 185)
    return c.frame()


def pixel_control(**kwargs):
    return parse(control_frame(**kwargs))


def test_captured_response_outer_rows_are_not_a_choice_band():
    s = capture()
    assert s.kind == 'text' and s.hand is None
    band = [line for line in s.lines if 176 <= line.y <= 208]
    assert [(line.y, line.cells) for line in band] == [
        (191, ((248, UNKNOWN),)), (207, ((248, UNKNOWN),))]
    assert 'さすがは' in s.text
    assert p.readable_prose_screen(s) is True
    assert p._trade_choice_pair(_options(s)) is None


@pytest.mark.parametrize('owner', ['trade', 'bijin'])
def test_actual_guard_releases_captured_readable_response(owner):
    mem = memory(owner)
    assert guard(owner, capture(), mem) is None
    assert owner + '_guard' not in mem


@pytest.mark.parametrize('owner', ['trade', 'bijin'])
def test_actual_guard_holds_captured_fade(owner):
    s = capture(FADE)
    assert s.kind == 'text' and s.hand is None
    assert any(24 <= x <= 232 and ch == UNKNOWN for line in s.lines
               if 176 <= line.y <= 208 for x, ch in line.cells)
    assert p.readable_prose_screen(s) is False
    mem = memory(owner)
    assert guard(owner, s, mem) == []
    assert mem[owner + '_guard']['held'] is True


@pytest.mark.parametrize('owner', ['trade', 'bijin'])
@pytest.mark.parametrize('control', ['hand', 'inner_unknown', 'x232_y207', 'x232_y208'])
def test_response_protective_controls_hold_actual_guards(owner, control):
    s = pixel_control(hand=control == 'hand', band={
        'hand': None, 'inner_unknown': (40, 191, UNKNOWN),
        'x232_y207': (232, 207, UNKNOWN), 'x232_y208': None}[control])
    if control == 'x232_y208':
        # This capture's pixel reader uses the y=...191,207 grid. Exercise the
        # guard's exact inclusive 208 boundary on parsed cells explicitly.
        s.lines.append(TextLine(208, ((232, UNKNOWN),)))
    if control == 'hand':
        assert s.hand is not None
    else:
        expected_x, expected_y = ((40, 191) if control == 'inner_unknown' else
                                (232, 207 if control == 'x232_y207' else 208))
        assert any(line.y == expected_y and (expected_x, UNKNOWN) in line.cells for line in s.lines)
    assert p.readable_prose_screen(s) is False
    mem = memory(owner)
    assert guard(owner, s, mem) == []
    assert mem[owner + '_guard']['held'] is True


@pytest.mark.parametrize('ys', [(176, 192), (192, 208)])
@pytest.mark.parametrize('pair', [('OK', 'NO'), ('そうする', 'やめる')])
def test_trade_choice_pair_and_guard_recognize_complete_menu(ys, pair):
    # Synthetic glyph cells at measured row spacing; the full pair permits only
    # the selected, readable refusal, including the third row at y208.
    lines = [row(y, word) for y, word in zip(ys, pair)]
    s = from_lines(lines, (18, ys[1] - 6, 38, ys[1] + 6))
    assert p._trade_choice_pair(_options(s)) == ((40, ys[0], pair[0]), (40, ys[1], pair[1]))
    mem = memory('trade')
    assert guard('trade', s, mem) == [p.pad('a')]
    assert mem['trade_guard']['chosen'] is True
    assert mem['_records'][-1]['strategy_variant'] == 'decline_general_trade'


def test_trade_unknown_on_inner_third_row_prevents_otherwise_valid_pair():
    lines = [row(176, 'OK'), row(192, 'NO'), TextLine(208, ((232, UNKNOWN),))]
    s = from_lines(lines, (18, 186, 38, 198))
    assert p._trade_choice_pair(_options(s)) == ((40, 176, 'OK'), (40, 192, 'NO'))
    mem = memory('trade')
    assert guard('trade', s, mem) == []
    assert mem['trade_guard']['held'] is True
    assert not mem['trade_guard']['chosen']


def test_trade_outer_only_separate_row_does_not_block_valid_pair():
    s = from_lines([row(176, 'OK'), row(192, 'NO'), TextLine(207, ((248, UNKNOWN),))],
                   (18, 186, 38, 198))
    mem = memory('trade')
    assert guard('trade', s, mem) == [p.pad('a')]
    assert mem['trade_guard']['chosen'] is True


def test_trade_suspect_choice_third_row_sets_choice_seen_without_hand():
    s = from_lines([row(151, 'トレード'), row(208, '???', x=216)])
    mem = memory('trade', choice_seen=False)
    assert guard('trade', s, mem) == []
    assert mem['trade_guard']['choice_seen'] is True
    assert mem['trade_guard']['held'] is True


def test_bijin_third_row_known_label_sets_choice_seen_without_hand():
    s = from_lines([row(151, 'おねがい'), row(167, 'きいてくれる?'), row(208, 'けえんな!')])
    mem = memory('bijin', choice_seen=False)
    assert guard('bijin', s, mem) == []
    assert mem['bijin_guard']['choice_seen'] is True
    assert mem['bijin_guard']['held'] is True


@pytest.mark.parametrize('owner', ['trade', 'bijin'])
def test_measured_menu_pixel_controls_keep_decline_action(owner):
    if owner == 'trade':
        from test_hanjuku_trade_guard import trade_pixels
        s = parse(trade_pixels(selected=1))
    else:
        from test_hanjuku_bijin_guard import bijin_pixels
        s = parse(bijin_pixels(selected=1))
    assert s.kind == 'text' and s.hand is not None
    mem = memory(owner)
    assert guard(owner, s, mem) == [p.pad('a')]
    assert mem[owner + '_guard']['chosen'] is True
    assert 'decline' in mem['_records'][-1]['strategy_variant']


@pytest.mark.parametrize('owner', ['trade', 'bijin'])
def test_partial_known_prose_row_keeps_guard(owner):
    s = from_lines([row(151, 'むらの' + UNKNOWN), TextLine(191, ((248, UNKNOWN),))])
    assert p.readable_prose_screen(s) is False
    mem = memory(owner)
    assert guard(owner, s, mem) == []
    assert mem[owner + '_guard']['held'] is True


@pytest.mark.parametrize('owner', ['trade', 'bijin'])
def test_all_unknown_body_has_no_release_evidence(owner):
    s = pixel_control(body='', unknown_row=True)
    assert p.readable_prose_screen(s) is False
    mem = memory(owner)
    assert guard(owner, s, mem) == []
    assert mem[owner + '_guard']['held'] is True


@pytest.mark.parametrize('owner', ['trade', 'bijin'])
def test_known_body_plus_unread_body_row_must_keep_guard(owner):
    # Contract probe: y167 was a text baseline in the original capture; this
    # replaces it with an unread inner glyph, while y151 remains readable.
    # It is NOT a claim that this exact damaged frame occurred in the live run.
    s = pixel_control(unknown_row=True)
    assert any(line.y == 151 and line.known for line in s.lines)
    assert any(line.y == 167 and (24, UNKNOWN) in line.cells for line in s.lines)
    mem = memory(owner)
    result = guard(owner, s, mem)
    assert result == [], (p.readable_prose_screen(s), result, mem)
    assert mem[owner + '_guard']['held'] is True


@pytest.mark.parametrize('owner', ['trade', 'bijin'])
def test_public_decision_path_must_not_send_a_on_mixed_unread_body(owner):
    from docich.hanjuku_bot import classify, decide
    frame = control_frame(unknown_row=True, dialogue_background=True)
    assert classify(frame) == 'dialogue'
    actions, state = decide(frame, {'policy': memory(owner)})
    assert actions == [], (actions, state.get('screen_kind'))
    assert state['policy'][owner + '_guard']['held'] is True


def test_same_line_outer_unknown_should_not_block_complete_inner_trade_menu():
    # Only the inner glyph cells can make a complete menu unreadable.
    s = from_lines([row(176, 'OK', outer=True), row(192, 'NO', outer=True)],
                   (18, 186, 38, 198))
    assert p._trade_choice_pair(_options(s)) is not None
    mem = memory('trade')
    assert guard('trade', s, mem) == [p.pad('a')]


def test_captured_accents_are_pixel_attested_without_removing_raw_cells():
    s = capture()
    assert s.prose_attached_marks == frozenset({(48, 143), (72, 143), (56, 159)})
    for x, y in s.prose_attached_marks:
        assert any(line.y == y and (x, UNKNOWN) in line.cells for line in s.lines)
    assert (248, 191) not in s.prose_attached_marks
    assert p.readable_prose_screen(s) is True


@pytest.mark.parametrize('owner', ['trade', 'bijin'])
@pytest.mark.parametrize('damage', ['none', 'digit_mark', 'isolated_mark', 'unknown_above_voiced',
                                   'unread_on_accent_row'])
def test_only_exact_consumed_accents_can_be_ignored_by_actual_guards(owner, damage):
    from test_hanjuku_chart_bot import MARK
    frame = control_frame(body='がぱ1')
    c = Canvas()
    c.rgb = bytearray(frame.rgb)
    if damage == 'digit_mark':
        c.tile(40, 143, MARK['゛'])
    elif damage == 'isolated_mark':
        c.tile(88, 143, MARK['゛'])
    elif damage == 'unknown_above_voiced':
        # A known voiced glyph below is insufficient: the upper bitmap itself
        # must be an exact consumed mark, never an arbitrary unread tile.
        for y in range(143, 151):
            for x in range(24, 32):
                c.put(x, y, GREEN)
        c.tile(24, 143, 0x0123456789ABCDEF)
    elif damage == 'unread_on_accent_row':
        c.tile(88, 159, 0x0123456789ABCDEF)
    s = parse(c.frame())
    assert s.kind == 'text' and s.hand is None
    mem = memory(owner)
    if damage == 'none':
        assert s.prose_attached_marks == frozenset({(24, 143), (32, 143)})
        assert p.readable_prose_screen(s) is True
        assert guard(owner, s, mem) is None
        assert owner + '_guard' not in mem
    else:
        assert p.readable_prose_screen(s) is False
        assert guard(owner, s, mem) == []
        assert mem[owner + '_guard']['held'] is True


@pytest.mark.parametrize('owner', ['trade', 'bijin'])
def test_raw_screen_without_pixel_attestation_does_not_guess_an_accent(owner):
    s = from_lines([TextLine(143, ((40, UNKNOWN),)), row(151, 'がぱ')])
    assert not s.prose_attached_marks
    assert p.readable_prose_screen(s) is False
    mem = memory(owner)
    assert guard(owner, s, mem) == []
    assert mem[owner + '_guard']['held'] is True


@pytest.mark.parametrize('control', ['inner_unknown', 'partial_label', 'third_row', 'no_hand', 'accept_row'])
def test_same_line_outer_unknown_never_relaxes_trade_selection_safety(control):
    lines = [row(176, 'OK', outer=True), row(192, 'NO', outer=True)]
    hand = (18, 186, 38, 198)
    if control == 'inner_unknown':
        lines[1] = TextLine(192, ((40, 'N'), (48, 'O'), (232, UNKNOWN), (248, UNKNOWN)))
    elif control == 'partial_label':
        lines[1] = TextLine(192, ((40, 'N'), (48, UNKNOWN), (248, UNKNOWN)))
    elif control == 'third_row':
        lines.append(TextLine(208, ((232, UNKNOWN),)))
    elif control == 'no_hand':
        hand = None
    elif control == 'accept_row':
        hand = (18, 170, 38, 182)
    s = from_lines(lines, hand)
    mem = memory('trade')
    actions = guard('trade', s, mem)
    if control == 'accept_row':
        assert actions == [p.pad('down')]
    else:
        assert actions == []
        assert mem['trade_guard']['held'] is True
    assert mem['trade_guard']['chosen'] is False


def test_same_line_outer_tiles_allow_the_readable_refusal_in_pixel_guard_path():
    from test_hanjuku_trade_guard import trade_pixels
    frame = trade_pixels(selected=1)
    c = Canvas()
    c.rgb = bytearray(frame.rgb)
    for y in (183, 199):
        c.tile(248, y, 0x0123456789ABCDEF)
    s = parse(c.frame())
    assert s.kind == 'text' and s.hand is not None
    for y in (183, 199):
        assert any(line.y == y and (248, UNKNOWN) in line.cells and line.known for line in s.lines)
    mem = memory('trade')
    assert guard('trade', s, mem) == [p.pad('a')]
    assert mem['trade_guard']['chosen'] is True
    assert mem['_records'][-1]['strategy_variant'] == 'decline_general_trade'
