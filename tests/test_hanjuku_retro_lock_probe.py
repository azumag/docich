"""Read-only diagnostics must be able to say whether the retro lock is held.

g1163 2026-11-14: `retro_corner.status=restoring` stayed for hours with no
`last_error_code`. The timer-safe restore driver runs under
`locks/retro-corner.lock`; if another process keeps that lock, the driver's
error is swallowed by the rotation and nothing is recorded. A shared
non-blocking probe tells a held lock from a missing one without writing or
waiting.
"""

import fcntl
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'retro_lock_diag', ROOT / 'ops' / 'vm_actions' / 'collect_diagnostics.py')
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


def test_a_missing_lock_is_reported_without_creating_it(tmp_path):
    assert diag._probe_retro_lock(tmp_path) == 'missing'
    assert not (tmp_path / 'locks' / 'retro-corner.lock').exists()


def test_a_free_lock_is_reported_free(tmp_path):
    lock = tmp_path / 'locks' / 'retro-corner.lock'
    lock.parent.mkdir()
    lock.write_text('', encoding='utf-8')
    assert diag._probe_retro_lock(tmp_path) == 'free'


def test_a_held_lock_is_reported_held(tmp_path):
    lock = tmp_path / 'locks' / 'retro-corner.lock'
    lock.parent.mkdir()
    lock.write_text('', encoding='utf-8')
    handle = lock.open('a+', encoding='utf-8')
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert diag._probe_retro_lock(tmp_path) == 'held'
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    assert diag._probe_retro_lock(tmp_path) == 'free'


def test_an_unreadable_lock_is_not_reported_free(tmp_path):
    lock = tmp_path / 'locks' / 'retro-corner.lock'
    lock.parent.mkdir()
    lock.write_text('', encoding='utf-8')
    os.chmod(lock, 0)
    try:
        if os.access(lock, os.R_OK):
            return  # root can read it; no portable assertion here
        assert diag._probe_retro_lock(tmp_path) == 'unreadable'
    finally:
        os.chmod(lock, 0o600)


def test_a_stubbed_retro_entry_keeps_the_recovery_fields():
    entry = {
        "present": True, "readable": True, "status": "restoring",
        "game": "hanjuku-hero", "last_error_code": None,
        "restoring_settle_code": "restoring-finish-pending",
        "lock_state": "held", "body": "x" * 200,
    }
    stub = diag._corner_stub(entry)
    assert stub["lock_state"] == "held"
    assert stub["restoring_settle_code"] == "restoring-finish-pending"
    assert stub["output_omitted"] == "hard_ceiling"
    assert "body" not in stub


def _write_receipt(directory, request_id, **fields):
    import json
    directory.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": 1, "request_id": request_id, "operation": "switch",
        "target": "hanjuku-hero", "payload_hash": "x" * 64, "generation": 63,
        "runtime_id": "g63-0123abcd", "runtime_dir": "/x", "game_window": "w",
        "agent_window": "a", "adapter_session": "s", "status": "accepted",
        "result": None, "created_at": "2026-11-10T08:52:00+00:00",
        "updated_at": "2026-11-10T08:52:00+00:00", **fields,
    }
    (directory / f"{request_id}.json").write_text(json.dumps(receipt), encoding='utf-8')


def test_pending_switch_receipts_are_visible_without_identities(tmp_path):
    directory = tmp_path / 'game-switch' / 'requests'
    _write_receipt(directory, 'f287f8ee-0cac-4e69-94ce-4af518451782')
    _write_receipt(directory, 'aaaaaaaa-0000-4000-8000-000000000001',
                   status='succeeded', result={})
    _write_receipt(directory, 'bbbbbbbb-0000-4000-8000-000000000002', status='queued')
    out = diag._collect_game_switch_fifo(tmp_path, 1794304123.0)
    assert out['pending_count'] == 1
    row = out['pending'][0]
    assert row['status'] == 'accepted' and row['operation'] == 'switch'
    assert row['target'] == 'hanjuku-hero' and row['generation'] == 63
    assert row['age_sec'] > 0
    text = str(out)
    assert 'f287f8ee' not in text and 'payload_hash' not in text


def test_a_terminal_only_store_reports_no_pending(tmp_path):
    directory = tmp_path / 'game-switch' / 'requests'
    _write_receipt(directory, 'aaaaaaaa-0000-4000-8000-000000000001',
                   status='failed', result={'error_code': 'x'})
    out = diag._collect_game_switch_fifo(tmp_path, 1794304123.0)
    assert out['pending_count'] == 0 and out['pending'] == []
