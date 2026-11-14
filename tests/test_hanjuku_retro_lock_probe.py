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
