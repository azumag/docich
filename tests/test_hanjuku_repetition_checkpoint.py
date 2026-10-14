"""An observed loop uses the same saved-stop/cleanup contract as static stasis."""
import time

import pytest

from test_retroarch_safe_boundary import adapter
from test_hanjuku_saved_stop import emulator
from test_hanjuku_repetition_run import induce_loop
from docich import hanjuku_run
from docich.adapters.base import AdapterError
from docich.adapters.retroarch import ManualSaveTimeout
from docich.retroarch_boundary import (BOUNDARY_FILE, MANUAL_SAVE_FILE,
                                      read_record, require_input_open)


@pytest.mark.parametrize('paused', [False, True])
def test_loop_is_saved_once_and_checkpoint_survives_cleanup(adapter, monkeypatch, paused):
    calls = emulator(adapter, monkeypatch, paused=paused)
    (adapter.spec.runtime_dir / MANUAL_SAVE_FILE).unlink()
    state = induce_loop(adapter.spec.runtime_dir, monkeypatch,
                        identity=hanjuku_run.runtime_identity(adapter.spec))
    adapter.request_round_boundary('loop-stop', time.monotonic() + 2, None)
    boundary = read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)
    assert boundary['status'] == 'reached' and boundary['outcome'] == 'screen_stalled'
    assert boundary['checkpoint'] == 'hanjuku.state'
    assert boundary['frame_sha256'] == state['frame_sha256']
    assert calls.count('SAVE_STATE') == 1
    assert calls.count('PAUSE_TOGGLE') == (0 if paused else 1)
    with pytest.raises(AdapterError, match='holds input'):
        require_input_open(adapter.spec.runtime_dir)
    adapter.request_round_boundary('loop-stop', time.monotonic() + 1, None)
    assert calls.count('SAVE_STATE') == 1
    monkeypatch.setattr('docich.agent.fence.read_canonical',
                        lambda _: {'active': {'runtime_id': adapter.spec.runtime_id}})
    adapter.cleanup_runtime(time.monotonic() + 1, None)
    assert (adapter.spec.runtime_dir / 'states/hanjuku.state').read_bytes() == b'completed save'
    adapter._require_safe_cleanup(time.monotonic() + 1, None)


def test_loop_save_failure_does_not_force_shutdown(adapter, monkeypatch):
    calls = emulator(adapter, monkeypatch, save=False)
    (adapter.spec.runtime_dir / MANUAL_SAVE_FILE).unlink()
    induce_loop(adapter.spec.runtime_dir, monkeypatch,
                identity=hanjuku_run.runtime_identity(adapter.spec))
    monkeypatch.setattr('docich.adapters.retroarch.MANUAL_SAVE_TIMEOUT_S', .05)
    with pytest.raises(ManualSaveTimeout):
        adapter.request_round_boundary('loop-stop', time.monotonic() + 1, None)
    assert read_record(adapter.spec.runtime_dir / BOUNDARY_FILE)['status'] == 'waiting'
    assert not any(call[0].startswith('kill') for call in adapter.tmux.calls)
    assert calls.count('SAVE_STATE') == 1 and calls.count('PAUSE_TOGGLE') == 2
