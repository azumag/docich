"""Acknowledge the measured field encounter without inventing castle entry."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse, Screen, classify_text

F = Path(__file__).parent / 'fixtures' / 'hanjuku' / 'g706-field-encounter.png'


def test_native_encounter_is_acknowledged_with_no_castle_or_actor_binding():
    frame = read_png(F);screen = parse(frame)
    assert screen.kind == 'field_encounter_started'
    state = {'policy': {'chapter': 2, 'name': {'done': True}}}
    actions, state = decide(frame, state)
    assert actions == [p.pad('a')]
    record = state['_records'][-1]
    assert record['decision'] == 'field_encounter_observed'
    assert record['general'] == p.NAME and record['castle'] is None
    assert not state['policy'].get('attack') and not state['policy'].get('captured')


@pytest.mark.parametrize('general', ['どうし', 'シェーブル', 'ゼウス'])
def test_complete_known_general_encounter_is_safe_to_advance(general):
    text = general + 'しょうぐんがてきとそうぐうしました!!'
    screen = Screen([], None, text, kind='field_encounter_started')
    assert classify_text(screen) == 'field_encounter_started'
    mem = {'chapter': 2}
    assert p.message_step(screen, mem) == [p.pad('a')]
    assert not mem.get('attack')


@pytest.mark.parametrize('text', ['どうししょうぐんがてきとそうぐうしました!',
    'どうししょうぐんがてきとそうぐうしました!!うむッ!いかんッ!',
    '�しょうぐんがてきとそうぐうしました!!'])
def test_partial_unknown_and_appended_confirmation_are_not_measured_encounters(text):
    assert classify_text(Screen([], None, text)) != 'field_encounter_started'


def test_unknown_general_name_cannot_supply_a_new_input():
    text = '未確認しょうぐんがてきとそうぐうしました!!'
    assert p.message_step(Screen([], None, text, kind='field_encounter_started'), {}) == []
