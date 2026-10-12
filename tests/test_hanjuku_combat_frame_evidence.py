"""Combat observations must retain pixels before the result moves to the map."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku_pixels import Frame, read_png
from docich.hanjuku_screen import parse

IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g713-58fb2ff1',
            'generation': 713, 'lease_id': 'test-lease'}


@pytest.fixture
def bot(monkeypatch):
    from docich import hanjuku_scene
    monkeypatch.setattr(hanjuku_scene, 'observe', lambda *args, **kwargs: None)
    path = Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py'
    spec = importlib.util.spec_from_file_location('combat_evidence_bot', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def persist(bot, root, source, kind, step=611, actions=None):
    runtime = root / 'runtimes' / IDENTITY['runtime_id']
    runtime.mkdir(parents=True, exist_ok=True)
    state = {'step': step, 'screen_kind': kind, 'policy': {'chapter': 2}}
    bot.persist(runtime, state, [], {'hanjuku': IDENTITY},
                actions=actions or [], frame_sha256=source.digest(), frame=source)
    record = json.loads((runtime / 'hanjuku_decisions.jsonl').read_text().splitlines()[-1])
    return runtime, record


def test_real_battle_panel_is_kept_without_a_result_or_card_flow(bot, tmp_path):
    source = read_png(Path(__file__).parent / 'fixtures/hanjuku/g692-venus-artemis-followup-panel.png')
    assert parse(source).kind == 'battle'
    runtime, record = persist(bot, tmp_path, source, 'battle')
    assert record['snapshot'] is not None
    assert read_png(runtime / 'hanjuku_frames' / record['snapshot']).digest() == source.digest()
    assert record['decision_id'] == 'g713-58fb2ff1:713:611'
    assert record['frame_sha256'] == source.digest()
    assert record['planned_actions'] == []
    assert record['dispatch_status'] == 'planned_not_yet_sent'
    assert not (runtime / 'hanjuku_events.jsonl').exists()


@pytest.mark.parametrize('kind', ['egg_battle_menu', 'monster_menu'])
def test_summoned_combat_choice_keeps_frame_and_planned_input(bot, tmp_path, kind):
    source = Frame(256, 224, bytes((40, 90, 110)) * (256 * 224))
    actions = [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    runtime, record = persist(bot, tmp_path, source, kind, actions=actions)
    assert record['snapshot'] is not None
    assert read_png(runtime / 'hanjuku_frames' / record['snapshot']).digest() == record['frame_sha256']
    assert record['planned_actions'] == actions
    assert record['dispatch_status'] == 'planned_not_yet_sent'
    assert not (runtime / 'hanjuku_events.jsonl').exists()


def test_combat_capture_keeps_the_existing_ring_and_does_not_relabel_old_pixels(bot, tmp_path):
    old = Frame(256, 224, bytes((40, 90, 110)) * (256 * 224))
    new = Frame(256, 224, bytes((90, 40, 110)) * (256 * 224))
    runtime, prior = persist(bot, tmp_path, old, 'battle', step=0)
    _, current = persist(bot, tmp_path, new, 'battle', step=120)
    assert prior['snapshot'] == current['snapshot'] == 'decision-000.png'
    assert len(list((runtime / 'hanjuku_frames').glob('decision-*.png'))) == 1
    assert read_png(runtime / 'hanjuku_frames' / current['snapshot']).digest() == current['frame_sha256']
    assert prior['frame_sha256'] != current['frame_sha256']


def test_map_observation_still_does_not_fill_the_combat_ring(bot, tmp_path):
    source = Frame(256, 224, bytes((40, 90, 110)) * (256 * 224))
    runtime, record = persist(bot, tmp_path, source, 'map')
    assert record['snapshot'] is None
    assert not (runtime / 'hanjuku_frames').exists()
