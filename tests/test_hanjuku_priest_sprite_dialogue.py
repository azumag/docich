"""g739: the priest art remains through a different two-line result message."""
from pathlib import Path
from copy import deepcopy

import pytest
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse, find_hand, resume_egg_ritual_panel
from docich.hanjuku.os_layer.font import UNKNOWN, without_attached_marks, read_lines
from docich.hanjuku.flows.egg_recovery import resume_egg_ritual_step, _egg_recovery_step
from docich.hanjuku.os_layer.commands import pad
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_recovery_list_return import confirmed


def native():
    f = read_png(Path(__file__).parent / 'fixtures/hanjuku/g739-priest-recovery-dialogue.png')
    assert f.digest() == '6f279dbfa337f0dc306ad5b1b5337115b4ff47c77e477ea92db49a891b02ba76'
    return f


def scene(body):
    c = Canvas(); c.rgb = bytearray(native().rgb)
    for y in range(135, 215):
        for x in range(16, 240):
            c.put(x, y, (16, 72, 57))
    c.text(32, 151, body)
    return c


def test_original_result_dialogue_uses_the_same_priest_art_without_claiming_quantity():
    f = native()
    assert find_hand(f) == (116, 82, 137, 96)
    s = parse(f, phase='concert')
    assert s.hand is None and s.priest_ritual_dialogue and resume_egg_ritual_panel(s)
    m = {'resume_egg_ritual': {'month': '1-5', 'readings': 0}}
    assert resume_egg_ritual_step(s, m) == [pad('a')]
    assert not m.get('egg_uses') and not m.get('month_sub')
    m = confirmed(); before = deepcopy((m['egg_uses'], m['shop']))
    assert _egg_recovery_step(s, m, m['month_sub']) == [pad('a')]
    assert before == (m['egg_uses'], m['shop'])


@pytest.mark.parametrize('body', ['かいふくしました!', 'たまごがげんきになりました!'])
def test_dialogue_text_is_not_a_list_of_one_off_phrases(body):
    s = parse(scene(body).frame(), phase='concert')
    assert s.priest_ritual_scene and s.priest_ritual_dialogue and s.hand is None
    assert resume_egg_ritual_step(s, {'resume_egg_ritual': {'month':'1-5','readings':0}}) == [pad('a')]


@pytest.mark.parametrize('body', ['', '50Gかかります', 'よろしいですか?', 'うむッ!', 'いかんッ!'])
def test_blank_cost_or_confirmation_has_no_dialogue_authority(body):
    s = parse(scene(body).frame(), phase='concert')
    assert s.priest_ritual_scene and not s.priest_ritual_dialogue
    assert not resume_egg_ritual_panel(s)
    m = {'resume_egg_ritual': {'month':'1-5','readings':0}}
    assert resume_egg_ritual_step(s, m) == []
    m = confirmed()
    assert _egg_recovery_step(s, m, m['month_sub']) == []


@pytest.mark.parametrize('x,y', [(128,151), (232,151), (232,175)])
def test_unknown_message_tiles_stay_unknown_including_right_interior(x, y):
    c = scene('かいふくしました!'); c.tile(x, y, 0x0123456789ABCDEF)
    s = parse(c.frame(), phase='concert')
    assert s.priest_ritual_scene and not s.priest_ritual_dialogue
    assert not resume_egg_ritual_panel(s)
    assert resume_egg_ritual_step(s, {'resume_egg_ritual': {'month':'1-5','readings':0}}) == []


def test_ambiguous_added_hand_does_not_hide_a_selection():
    c = scene('かいふくしました!'); c.hand(24, 47)
    s = parse(c.frame(), phase='concert')
    assert s.priest_ritual_scene and not s.priest_ritual_dialogue
    assert not resume_egg_ritual_panel(s)
    assert resume_egg_ritual_step(s, {'resume_egg_ritual': {'month':'1-5','readings':0}}) == []


def test_changed_priest_art_or_other_phase_does_not_suppress_a_hand():
    c = scene('かいふくしました!'); c.put(108,80,(1,2,3))
    s = parse(c.frame(), phase='concert')
    assert not s.priest_ritual_scene and s.hand is not None
    assert not resume_egg_ritual_panel(s)
    s = parse(native(), phase='battle')
    assert not s.priest_ritual_scene and s.hand is not None


def test_only_consumed_real_accent_tiles_are_removed():
    c = Canvas(); c.text(32,151,'がぱ1')
    # A real mark over a digit is not a consumed character modifier.
    from docich.hanjuku.os_layer.glyphs import MARKS
    c.tile(48,143,next(k for k,v in MARKS.items() if v == '゛'))
    lines = without_attached_marks(c.frame(), read_lines(c.frame()), rect=(16,135,240,215))
    top = next(l for l in lines if l.y==143)
    assert top.cells == ((48,UNKNOWN),)
    assert next(l for l in lines if l.y==151).known == 'がぱ1'
