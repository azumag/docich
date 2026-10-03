"""Turning off fixed narration also stops the durable recap retry entry."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_narration as narration


@pytest.mark.parametrize('pending', [False, True])
def test_disabled_retry_never_revives_prior_fixed_terminal_candidate(tmp_path, monkeypatch, pending):
    runtime = tmp_path / 'runtimes/g1-abc123'
    runtime.mkdir(parents=True)
    identity = {'game': 'hanjuku-hero', 'runtime_id': runtime.name, 'generation': 1, 'lease_id': 'lease-1'}
    item = {**identity, 'seq': 1, 'at': 1000, 'key': 'game_over_recap',
            'terminal_recap': True, 'text': '古い固定振り返りです。'}
    (runtime / 'hanjuku_commentary.jsonl').write_text(json.dumps(item) + '\n')
    if pending:
        (runtime / narration.STATE).write_text(json.dumps({narration.TERMINAL_DELIVERIES_KEY: {
            narration._terminal_delivery_key(identity): 'pending'}}))
    monkeypatch.setattr('docich.config.load_game', lambda *a: SimpleNamespace(raw={
        'hanjuku': {'narration': {'enabled': False}}}))
    monkeypatch.setattr('docich.webui._enqueue_audio_text', lambda *a, **k: pytest.fail('fixed speech resumed'))
    assert narration.retry_pending_terminal_deliveries(SimpleNamespace(state_dir=tmp_path)) is True
    # No queue or unrelated output blocker is created by the disable gate.
    assert not (runtime / 'hanjuku_narration.jsonl').exists()


def test_unreadable_config_keeps_fixed_retries_silent(tmp_path, monkeypatch):
    monkeypatch.setattr('docich.config.load_game', lambda *a: (_ for _ in ()).throw(OSError('unavailable')))
    monkeypatch.setattr(narration, '_terminal_candidates', lambda *a: pytest.fail('must stop before retry scan'))
    assert narration.retry_pending_terminal_deliveries(SimpleNamespace(state_dir=tmp_path)) is True
