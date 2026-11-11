"""A chosen okunote candidate whose list stays open is re-sent, never awaited forever.

g1163 2026-11-11 19:10:00: A on ハダカでぶつかる left the same candidate list on
screen; the bot waited for the effect on every observation (200 waits in five
minutes) until the run ended ``screen_stalled``.
"""
from pathlib import Path

from PIL import Image

from docich.hanjuku.flows import battle_menus, okunote
from docich.hanjuku.os_layer.pixels import Frame
from docich.hanjuku.os_layer.screen import parse

FIXTURE = Path(__file__).parent / 'fixtures' / 'hanjuku' / 'g1163-okunote-candidate-list-after-a.png'


def _screen():
    im = Image.open(FIXTURE).convert('RGB')
    return parse(Frame(im.width, im.height, im.tobytes()))


def _mem():
    cur = {'ally': 'ドリアン', 'enemy': 'コマイヌ', 'ally_hp': 29, 'enemy_hp': 16,
           'start_ally_hp': 51, 'start_enemy_hp': 18, 'side': 'defense', 'castle': 'ドンドン'}
    mem = {'chapter': 9, 'battle': cur, '_records': []}
    okunote.selected(mem, cur, candidate=True)
    return mem


def test_the_production_frame_is_the_candidate_list():
    assert _screen().kind == 'okunote_menu'


def test_a_lingering_candidate_list_is_closed_and_never_stalls():
    mem, screen = _mem(), _screen()
    seen = [battle_menus.okunote_step(screen, mem) for _ in range(30)]
    wait, a, b = [{'type': 'wait', 'ms': 50}], [battle_menus.pad('a')], [battle_menus.pad('b')]
    assert seen[:okunote.CANDIDATE_MENU_WAITS] == [wait] * okunote.CANDIDATE_MENU_WAITS
    assert seen[okunote.CANDIDATE_MENU_WAITS] == b         # close first; the choice is not re-sent
    assert seen.count(a) == 1                              # one last confirm, only after the closes
    assert seen.index(a) > max(i for i, x in enumerate(seen[:seen.index(a)]) if x == b)
    run = 0
    for actions in seen:                                   # input never stops for long
        run = run + 1 if actions == wait else 0
        assert run <= okunote.CANDIDATE_MENU_WAITS
    decisions = [r['decision'] for r in mem['_records']]
    assert decisions.count('battle_okunote_candidate_close') == okunote.CANDIDATE_CLOSES
    assert decisions.count('battle_okunote_candidate_last_confirm') == 1


def test_a_stale_attempt_from_another_battle_is_dropped_within_a_bound():
    mem, screen = _mem(), _screen()
    mem['battle']['okunote_attempt']['scope'] = ['other']
    seen = [okunote.menu_pending(screen, mem, mem['battle']) for _ in range(okunote.STALE_ATTEMPT_WAITS + 1)]
    assert seen[-1] is None and 'okunote_attempt' not in mem['battle']
