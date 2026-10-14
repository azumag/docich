"""Native FreeziBoy spelling; input ownership is distinct from battle outcome."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich.hanjuku.judgment.events import _monster_owner, _MONSTER_SKILLS
from docich.hanjuku.flows.battle_menus import monster_menu_step, MONSTER_MENU_HOLD_LIMIT
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

FIXTURES = Path(__file__).parent / 'fixtures/hanjuku'


def native(uid):
    evidence = json.loads((FIXTURES / 'g747-freeziboy-evidence.json').read_text())
    row = next(r for r in evidence['frames'] if r['uid'] == uid)
    f = read_png(FIXTURES / row['frame'])
    assert f.digest() == row['rgb_sha256'] == row['decision']['frame_sha256']
    assert row['exact_inputs']
    for receipt in row['exact_inputs']:
        assert receipt['decision_id'] == row['decision']['decision_id']
        assert receipt['frame_sha256'] == receipt['decision_frame_sha256'] == f.digest()
    return parse(f)


@pytest.mark.parametrize('uid', [8867, 8868])
def test_native_enemy_display_skills_wait_instead_of_sending_up_or_down(uid):
    s = native(uid)
    panels = {r.side: r for r in s.egg_rows if r.side}
    assert panels['enemy'].name == 'フリージボーイ'
    assert panels['enemy'].hp == 144
    skills = [r for r in s.menu_rows if 'もどれ' not in r.known]
    assert _monster_owner(skills, panels.get('ally'), panels['enemy']) == 'enemy'
    m = {}
    assert monster_menu_step(s, m) == []
    assert not m.get('monster_menu_choice')
    assert m['_records'][-1]['observed_metric']['owner'] == 'enemy'


@pytest.mark.parametrize('uid', [8867, 8868])
def test_saved_old_choice_is_discarded_and_enemy_hold_remains_bounded(uid):
    m = {'monster_menu_choice': 'skill1', 'monster_menu_cursor': 176,
         'monster_menu_choice_scope': [None, 'リーキ', 'フリージボーイ']}
    # Repeated native image is a synthetic persistence scenario, not live time.
    for _ in range(MONSTER_MENU_HOLD_LIMIT):
        m = json.loads(json.dumps(m))
        assert monster_menu_step(native(uid), m) == []
        assert not m.get('monster_menu_choice')
    # Preserve the existing finite fallback; this change does not extend holds.
    assert monster_menu_step(native(uid), m)


def owner(ally, enemy, skills=('エアコン', 'れいとう')):
    panel = lambda name: SimpleNamespace(name=name) if name is not None else None
    return _monster_owner([SimpleNamespace(known=s) for s in skills],
                          panel(ally), panel(enemy))


@pytest.mark.parametrize('name', ['フリージボーイ', 'フリージーボーイ'])
@pytest.mark.parametrize('side', ['ally', 'enemy'])
def test_exact_display_and_canonical_spellings_work_on_either_side(name, side):
    assert owner(name if side == 'ally' else 'リーキ',
                 name if side == 'enemy' else 'リーキ') == side


@pytest.mark.parametrize('ally,enemy', [
    ('フリージボーイ', 'フリージボーイ'),
    ('フリージボーイ', 'フリージーボーイ'),
    ('フリージーボーイ', 'フリージボーイ'),
])
def test_same_skills_on_both_sides_remain_ambiguous(ally, enemy):
    assert owner(ally, enemy) is None


@pytest.mark.parametrize('name', [None, '', '不明', 'フリージ', 'フリージボー?',
                                  'フリージボーイX', 'フリー ジボーイ'])
def test_unread_and_similar_names_are_not_fuzzy_aliases(name):
    assert owner('リーキ', name) is None


@pytest.mark.parametrize('skills', [(), ('もどれ',), ('???',), ('つのでつく',)])
def test_alias_name_without_matching_skill_does_not_establish_owner(skills):
    assert owner('リーキ', 'フリージボーイ', skills) is None


def test_mixed_visible_skills_remain_ambiguous_and_reference_stays_canonical():
    assert owner('ユニコーン', 'フリージボーイ', ('つのでつく', 'れいとう')) is None
    assert 'フリージボーイ' not in _MONSTER_SKILLS
    assert 'フリージーボーイ' in _MONSTER_SKILLS


@pytest.mark.parametrize('old_key', [None, [], ['ちがう技'], 'エアコン', 12])
def test_changed_or_invalid_saved_menu_starts_a_new_finite_hold(old_key):
    m = {'monster_menu_key': old_key, 'monster_menu_hold': MONSTER_MENU_HOLD_LIMIT}
    assert monster_menu_step(native(8867), m) == []
    assert m['monster_menu_hold'] == 1
