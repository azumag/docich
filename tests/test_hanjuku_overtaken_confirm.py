"""A text confirm is not sent once the newest capture shows a command screen.

g1075-1d5f65e9 2026-10-29 JST 18:27:47: the bot decided A on a dialogue frame
at 47.3, the corner captured ラビットサタン's settled skill menu at 47.6, and
the A arrived at 47.8. It confirmed デスのカマ, the top row, outside the
skill-stall count, so デスのカマ ran 3 times before ラビットキック instead of 2.
"""
import importlib.util
import shutil
from pathlib import Path

from docich.hanjuku.os_layer.pixels import read_png

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'hanjuku'
DIALOGUE = FIXTURES / 'g1075-omedetori-kyuu-dialogue.png'     # phase 'dialogue', kind 'text'
MENU = FIXTURES / 'g1075-omedetori-menu-riesling.png'          # phase 'event', settled skill menu
SLIDING = FIXTURES / 'g1075-omedetori-menu-sliding-up.png'     # phase 'event', box still drawing

spec = importlib.util.spec_from_file_location('hanjuku_overtaken_entry', ROOT / 'brains/hanjuku/bot.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
A = [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]


def latest(tmp_path, source):
    path = tmp_path / 'latest.png'
    shutil.copyfile(source, path)
    return path


def test_a_dialogue_a_overtaken_by_the_skill_menu_is_withheld(tmp_path):
    fresh = entry.overtaken_confirm(latest(tmp_path, MENU), read_png(DIALOGUE).resized(),
                                    {'screen_kind': 'text'}, A)
    assert fresh is not None and fresh.digest() == read_png(MENU).resized().digest()


def test_the_same_frame_or_another_dialogue_still_gets_its_a(tmp_path):
    frame = read_png(DIALOGUE).resized()
    assert entry.overtaken_confirm(latest(tmp_path, DIALOGUE), frame, {'screen_kind': 'text'}, A) is None


def test_a_frame_already_in_the_event_phase_is_not_second_guessed(tmp_path):
    # The decision itself was made on a command-phase frame: its owner already read it.
    frame = read_png(SLIDING).resized()
    assert entry.overtaken_confirm(latest(tmp_path, MENU), frame, {'screen_kind': 'unknown'}, A) is None


def test_only_a_lone_generic_confirm_on_text_or_unknown_is_checked(tmp_path):
    frame = read_png(DIALOGUE).resized()
    path = latest(tmp_path, MENU)
    assert entry.overtaken_confirm(path, frame, {'screen_kind': 'monster_menu'}, A) is None
    assert entry.overtaken_confirm(path, frame, {'screen_kind': 'text'},
                                   [{'type': 'pad', 'buttons': ['b'], 'hold_ms': 100}]) is None
    assert entry.overtaken_confirm(path, frame, {'screen_kind': 'text'}, A + A) is None


def test_an_unreadable_capture_never_drops_the_input(tmp_path):
    broken = tmp_path / 'latest.png'
    broken.write_bytes(b'not a png')
    assert entry.overtaken_confirm(broken, read_png(DIALOGUE).resized(), {'screen_kind': 'text'}, A) is None


def run_bot(tmp_path, monkeypatch, captures):
    import io
    import json
    import sys

    identity = {'game': 'hanjuku-hero', 'runtime_id': 'g9-0123abcd', 'generation': 9, 'lease_id': 'lease-9'}
    runtime = tmp_path / 'run' / 'runtimes' / identity['runtime_id']
    runtime.mkdir(parents=True)
    module_spec = importlib.util.spec_from_file_location('hanjuku_overtaken_main', ROOT / 'brains/hanjuku/bot.py')
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    module.ROOT = tmp_path
    reads = iter(captures)
    monkeypatch.setattr(module, 'read_png', lambda _: read_png(next(reads)))
    observation = {'game': 'hanjuku-hero', 'screenshot': 'latest.png',
                   'meta': {'runtime_dir': str(runtime), 'hanjuku': identity}}
    output = io.StringIO()
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(observation)))
    monkeypatch.setattr(sys, 'stdout', output)
    assert module.main() == 0
    decisions = [json.loads(line) for line in (runtime / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    return json.loads(output.getvalue())['actions'], decisions


def test_the_bot_sends_nothing_when_the_menu_overtook_its_dialogue_frame(tmp_path, monkeypatch):
    actions, decisions = run_bot(tmp_path, monkeypatch, [DIALOGUE, MENU])
    assert actions == []
    held = [d for d in decisions if d.get('decision') == 'confirm_overtaken']
    assert len(held) == 1 and held[0]['observed_metric']['decided_phase'] == 'dialogue'


def test_the_bot_still_acknowledges_a_dialogue_nothing_overtook(tmp_path, monkeypatch):
    actions, decisions = run_bot(tmp_path, monkeypatch, [DIALOGUE, DIALOGUE])
    assert actions == A
    assert not [d for d in decisions if d.get('decision') == 'confirm_overtaken']
