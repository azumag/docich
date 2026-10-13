"""Ended g741 native parent menus; no emulator or production input."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import Frame, read_png
from docich.hanjuku_screen import Screen, parse, resume_battle_menu_panel
from docich.hanjuku.flows.battle_menus import orphan_battle_menu_step

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g741-defa2f9a',
            'generation': 741, 'lease_id': 'f7d03327-28e1-417f-a011-e7a7fdf5cdb0'}


def native(uid):
    return read_png(ROOT / f'g741-orphan-menu-{uid}.png').resized()


def screen(kind, **kwargs):
    return Screen([], None, '', kind=kind, **kwargs)


@pytest.mark.parametrize('uid', [8819, 8820])
def test_native_gray_menu_has_no_actor_or_hp_and_only_cancels(uid):
    frame = native(uid)
    metadata = json.loads((ROOT / 'g741-orphan-menu-evidence.json').read_text())
    proof = next(e for e in metadata['images'] if e['decision_id'].endswith(f':{uid}'))
    assert frame.digest() == proof['rgb_sha256']
    assert proof['planned_actions'] == [] and proof['matching_input_receipts'] == 0
    parsed = parse(frame)
    assert parsed.kind == 'battle_menu' and parsed.hidden_battle_commands
    assert parsed.menu_cursor == 176 and parsed.battle is None
    assert resume_battle_menu_panel(parsed)
    actions, result = decide(frame, {'policy': {'chapter': 2, 'month': '2-3',
                    'name': {'done': True}, 'battle': None}}, run_identity=IDENTITY)
    assert actions[0]['buttons'] == ['b'] and len(actions) == 1
    assert result['policy'].get('battle') is None
    assert result['_records'][-1]['decision'] == 'battle_menu_context_recheck'


def test_animated_parent_cannot_refill_budget_or_borrow_generic_a():
    a, b = native(8819), native(8820)
    # A synthetic third animation image changes no command glyph/cursor.
    rgb = bytearray(a.rgb); rgb[:3] = b'\x00\x00\x00'
    c = Frame(a.width, a.height, bytes(rgb))
    assert len({a.digest(), b.digest(), c.digest()}) == 3
    state = {'policy': {'chapter': 2, 'month': '2-3', 'name': {'done': True}}}
    inputs = []; records = []
    for n in range(45):
        actions, state = decide((a, b, c)[n % 3], json.loads(json.dumps(state)),
                                run_identity=IDENTITY)
        inputs += [action['buttons'] for action in actions if action.get('type') == 'pad']
        records += [record['decision'] for record in state['_records']]
    assert inputs == [['b'], ['b'], ['b']]
    assert records.count('battle_menu_recheck_unconfirmed') == 1
    assert 'no_input_fallback' not in records
    assert state['policy']['orphan_battle_menu']['attempts'] == 3


def test_fades_and_irreversible_lists_keep_close_budget_without_input():
    parent = parse(native(8819))
    mem = {'_run_identity': IDENTITY, '_records': []}
    assert orphan_battle_menu_step(parent, mem)[0]['buttons'] == ['b']
    for kind in ('unknown', 'text', 'battle_menu_pending', 'okunote_menu',
                 'monster_menu', 'egg_choice_menu', 'egg_battle_menu'):
        assert orphan_battle_menu_step(screen(kind), mem) == []
        assert mem['orphan_battle_menu']['attempts'] == 1
    assert orphan_battle_menu_step(parent, mem)[0]['buttons'] == ['b']
    assert mem['orphan_battle_menu']['attempts'] == 2


@pytest.mark.parametrize('bad', [None, True, -1, 4, '1'])
def test_corrupt_persisted_budget_does_not_get_more_cancels(bad):
    mem = {'_run_identity': IDENTITY, '_records': [],
           'orphan_battle_menu': {'identity': IDENTITY, 'attempts': bad}}
    assert orphan_battle_menu_step(parse(native(8819)), mem) == []
    assert mem['orphan_battle_menu']['attempts'] == 3


def test_foreign_receipt_does_not_get_more_cancels():
    other = {**IDENTITY, 'lease_id': 'other'}
    mem = {'_run_identity': IDENTITY, '_records': [],
           'orphan_battle_menu': {'identity': other, 'attempts': 0}}
    assert orphan_battle_menu_step(parse(native(8819)), mem) == []


def test_real_new_foreground_releases_owner_without_inventing_a_chapter():
    mem = {'_run_identity': IDENTITY, '_records': [], 'resume_chapter_pending': True,
           'resume_battle_menu': {'readings': 0}}
    assert orphan_battle_menu_step(parse(native(8819)), mem)[0]['buttons'] == ['b']
    assert orphan_battle_menu_step(screen('battle'), mem) is None
    assert 'resume_battle_menu' not in mem and 'orphan_battle_menu' not in mem
    assert 'chapter' not in mem and mem['resume_chapter_pending'] is True


def test_bound_battle_retains_existing_menu_strategy():
    battle = {'ally': 'ゼウス', 'ally_hp': 85, 'enemy': 'クミン', 'enemy_hp': 27}
    mem = {'_records': [], 'battle': deepcopy(battle),
           'orphan_battle_menu': {'identity': IDENTITY, 'attempts': 3}}
    assert orphan_battle_menu_step(parse(native(8819)), mem) is None
    assert mem['battle'] == battle


@pytest.mark.parametrize('kind', ['unknown', 'text', 'okunote_menu', 'egg_battle_menu'])
def test_unowned_other_foreground_is_not_claimed(kind):
    mem = {'_records': []}
    assert orphan_battle_menu_step(screen(kind), mem) is None
    assert 'orphan_battle_menu' not in mem


@pytest.mark.parametrize('change', [{'kind': 'okunote_menu'}, {'menu_cursor': None},
                                  {'menu_cursor': True}, {'hidden_battle_commands': False},
                                  {'header': {'chapter': 2}}])
def test_resume_cue_requires_the_measured_disabled_parent(change):
    parsed = parse(native(8819))
    for key, value in change.items():
        setattr(parsed, key, value)
    assert not resume_battle_menu_panel(parsed)
