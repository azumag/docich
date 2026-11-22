"""A bot with no chapter opens the castle status and reads the chapter from the castle name (v646)."""
import json
from pathlib import Path

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import deploy
from docich.hanjuku.flows.prompts import observe_events
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.strategy.campaign import observe_chapter_castle
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png

from test_hanjuku_house import Canvas

FIXTURES = Path(__file__).parent / 'fixtures/hanjuku'


def menu(cursor='しゅつげき'):
    """The castle menu with the hand on one of its two rows (x=24, y=31/47)."""
    c = Canvas()
    c.text(24, 31, 'しゅつげき')
    c.text(24, 47, 'ステータス')
    c.hand(2, 25 if cursor == 'しゅつげき' else 41)
    screen = parse(c.frame())
    assert screen.kind == 'castle_menu'
    return screen


def status_panel(name):
    """The native 城ステータス panel keeps the しゅつげき/ステータス menu drawn."""
    c = Canvas()
    c.text(24, 31, 'しゅつげき')
    c.text(24, 47, 'ステータス')
    c.text(120, 31, name)
    c.text(120 + 8 * len(name), 31, 'じょう')
    c.text(120, 47, 'しゅうにゅう')
    c.text(176, 47, '22')
    c.text(200, 47, 'G')
    c.text(120, 63, 'レベル')
    c.text(176, 63, '1')
    c.text(120, 79, 'しょうぐん')
    c.text(176, 79, '2')
    c.text(200, 79, 'めい')
    c.text(120, 95, 'ゼウス')
    screen = parse(c.frame())
    assert screen.kind == 'castle_menu'
    return screen


def lost_chapter(**fields):
    """A running bot that lost its chapter (g1163 after the chapter 8 game over)."""
    mem = {'tick': 5, 'month': '8-3', 'resume_chapter_pending': True}
    mem.update(fields)
    return mem


def test_cursor_on_sortie_moves_down_without_counting_an_opening():
    mem = lost_chapter()
    assert deploy.chapterless_status_step(menu('しゅつげき'), mem) == [pad('down')]
    assert mem['chapter_probe']['opens'] == 0


def test_cursor_on_status_presses_a_and_counts_the_opening():
    mem = lost_chapter()
    assert deploy.chapterless_status_step(menu('ステータス'), mem) == [pad('a')]
    assert mem['chapter_probe'] == {'month': '8-3', 'opens': 1, 'seen': []}


def test_hiragana_home_castle_of_chapter_8_fixes_the_chapter():
    assert [n for n, names in chart.CASTLE_NAMES.items() if 'あるまむーん' in names] == [8]
    mem = lost_chapter()
    observe_events(status_panel('あるまむーん'), mem)
    assert mem['chapter'] == 8
    assert 'resume_chapter_pending' not in mem


def test_okazaki_also_fixes_chapter_8():
    mem = lost_chapter()
    observe_events(status_panel('おかざき'), mem)
    assert mem['chapter'] == 8


def test_chapter_probe_is_dropped_once_the_chapter_is_known():
    mem = lost_chapter()
    deploy.chapterless_status_step(menu('ステータス'), mem)
    assert 'chapter_probe' in mem
    observe_events(status_panel('あるまむーん'), mem)
    assert 'chapter_probe' not in mem


def test_katakana_home_castle_stays_ambiguous_and_backs_out():
    mem = lost_chapter()
    screen = status_panel('アルマムーン')
    observe_events(screen, mem)
    assert not mem.get('chapter') and mem['resume_chapter_pending'] is True
    assert deploy.chapterless_status_step(screen, mem) == [pad('b')]
    assert mem['chapter_probe']['seen'] == ['アルマムーン']


def test_ambiguous_panel_is_recorded_with_the_castle_name():
    mem = lost_chapter()
    deploy.chapterless_status_step(status_panel('アルマムーン'), mem)
    record = next(r for r in mem['_records'] if r['decision'] == 'chapter_probe_ambiguous')
    assert record['observed_metric']['castle'] == 'アルマムーン'
    assert '複数の章' in record['reason']


def test_openings_stop_at_the_limit_and_return_after_the_month_changes():
    mem = lost_chapter()
    for _ in range(deploy.CHAPTER_PROBE_OPEN_LIMIT):
        assert deploy.chapterless_status_step(menu('ステータス'), mem) == [pad('a')]
    assert deploy.chapterless_status_step(menu('ステータス'), mem) == []
    assert deploy.chapterless_status_step(menu('しゅつげき'), mem) == []
    mem['month'] = '8-4'
    assert deploy.chapterless_status_step(menu('ステータス'), mem) == [pad('a')]
    assert mem['chapter_probe'] == {'month': '8-4', 'opens': 1, 'seen': []}


def test_known_chapter_does_not_use_the_probe():
    mem = {'tick': 5, 'chapter': 3, 'chapter_probe': {'month': None, 'opens': 1, 'seen': []}}
    assert deploy.chapterless_status_step(menu('ステータス'), mem) is None
    assert 'chapter_probe' not in mem


def test_bare_scratch_dict_never_fixes_a_chapter_from_a_castle_name():
    mem = {}
    observe_chapter_castle(mem, 'あるまむーん')
    assert not mem.get('chapter')


def test_orchestrator_routes_a_chapterless_castle_menu_to_the_probe_and_a_known_chapter_to_deploy(monkeypatch):
    calls = []
    monkeypatch.setattr(deploy, 'chapterless_status_step', lambda s, m: calls.append('probe') or [])
    monkeypatch.setattr(deploy, 'deploy_step', lambda s, m: calls.append('deploy') or [])
    frame = read_png(FIXTURES / 'g706-resume-castle-menu.png')
    decide(frame, {'policy': {'resume_chapter_pending': True}})
    decide(frame, {'policy': {'chapter': 3}})
    assert calls == ['probe', 'deploy']


def test_decide_opens_status_and_fixes_the_chapter_from_real_frames():
    """g706 castle menu -> status selected -> スペランザ status panel (chapter 2 only)."""
    state = {'policy': {'name': {'done': True}, 'resume_chapter_pending': True}}
    for name, expected in (('g706-resume-castle-menu.png', [pad('down')]),
                           ('g706-resume-status-selected.png', [pad('a')])):
        actions, state = decide(read_png(FIXTURES / name), json.loads(json.dumps(state)))
        assert actions == expected, name
        assert not state['policy'].get('chapter')
    assert state['policy']['chapter_probe']['opens'] == 1
    _, state = decide(read_png(FIXTURES / 'g706-resume-castle-status.png'), json.loads(json.dumps(state)))
    mem = state['policy']
    assert mem['chapter'] == 2 and 'resume_chapter_pending' not in mem
    assert 'chapter_probe' not in mem
