import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest
from docich.twica_config import load_common_config
from docich.twica_state import (OwnerControl, TransitionError, alive, component, consumers,
    diagnostics, fresh, heartbeat, identity, lease, owner, retired, set_owner, write_json)
from docich.twica_overlay import SnapshotPublisher, private_directory
from docich.twica_stream import OwnedReader


def ready(tmp_path):
    root = private_directory(tmp_path / 'state')
    heartbeat(root, 'compositor', state='running', frames_sent=3)
    heartbeat(root, 'renderer', state='standby', browser_active=False,
              generation=owner(root)['generation'])
    return root


def consumer(root, **kwargs):
    path = private_directory(root / 'consumers') / f'{uuid4().hex}.json'
    write_json(path, {'protocol': 1, **identity(), 'updated_ns': time.monotonic_ns(),
                      'generation': owner(root)['generation'], 'state': 'legacy', 'frames': 1,
                      **kwargs})
    return path


def test_default_legacy_and_corrupt_state_does_not_revive_legacy(tmp_path):
    root = private_directory(tmp_path / 'state')
    assert owner(root)['mode'] == 'legacy'
    write_json(root / 'owner.json', {'protocol': 2, 'mode': 'legacy', 'generation': 'x'})
    assert owner(root)['mode'] == 'blocked'
    (root / 'owner.json').unlink()
    target = tmp_path / 'target'
    target.write_text('{}')
    (root / 'owner.json').symlink_to(target)
    assert owner(root)['mode'] == 'blocked'


@pytest.mark.parametrize('missing,reason', [('compositor', 'compositor_not_ready'),
                                           ('renderer', 'renderer_not_ready')])
def test_cutover_requires_actual_runtime(tmp_path, missing, reason):
    root = ready(tmp_path)
    (root / f'{missing}.json').unlink()
    with pytest.raises(TransitionError, match=reason):
        OwnerControl(root, inventory=lambda ports: True).activate()
    assert owner(root)['mode'] == 'legacy'


def test_old_generation_proxy_blocks_before_mutation(tmp_path):
    root = ready(tmp_path)
    with pytest.raises(TransitionError, match='legacy_clients_not_upgraded'):
        OwnerControl(root, inventory=lambda ports: False).activate()
    assert owner(root)['mode'] == 'legacy'


def test_unacknowledged_live_client_blocks_and_safely_rolls_back(tmp_path):
    root = ready(tmp_path)
    consumer(root)
    with pytest.raises(TransitionError, match='legacy_retirement_timeout'):
        OwnerControl(root, inventory=lambda ports: True, timeout=.12).activate()
    assert owner(root)['mode'] == 'legacy'


def test_cutover_waits_for_frame_removal_generation_not_css_hiding(tmp_path):
    root = ready(tmp_path)
    path = consumer(root)
    observations = []
    stop = threading.Event()
    def host():
        while not stop.wait(.01):
            current = owner(root)
            observations.append(current['mode'])
            if current['mode'] == 'draining':
                write_json(path, {'protocol': 1, **identity(), 'updated_ns': time.monotonic_ns(),
                                 'generation': current['generation'], 'state': 'retired', 'frames': 0})
                return
    thread = threading.Thread(target=host)
    thread.start()
    try:
        assert OwnerControl(root, inventory=lambda ports: True, timeout=1).activate() == {
            'mode': 'common', 'changed': True}
    finally:
        stop.set(); thread.join()
    assert 'draining' in observations
    assert owner(root)['mode'] == 'common'


def test_pid_reuse_stale_live_record_and_generation_proof(tmp_path):
    root = ready(tmp_path)
    path = consumer(root, birth='not-current')
    assert consumers(root) == []
    record = {'protocol': 1, **identity(), 'updated_ns': 0,
              'generation': 'a' * 32, 'state': 'retired', 'frames': 0}
    write_json(path, record)
    assert not retired(root, 'a' * 32)
    record['updated_ns'] = time.monotonic_ns()
    write_json(path, record)
    assert not retired(root, 'b' * 32)
    assert retired(root, 'a' * 32)
    record['frames'] = 1
    write_json(path, record)
    assert not retired(root, 'a' * 32)


def test_rollback_does_not_revive_legacy_without_browser_closed_ack(tmp_path):
    root = ready(tmp_path)
    set_owner(root, 'common')
    heartbeat(root, 'renderer', state='active', browser_active=True)
    with pytest.raises(TransitionError, match='renderer_retirement_timeout'):
        OwnerControl(root, timeout=.12).rollback()
    assert owner(root)['mode'] == 'draining'


def test_rollback_waits_for_common_browser_exit_and_matching_generation(tmp_path):
    root = ready(tmp_path)
    set_owner(root, 'common')
    heartbeat(root, 'renderer', state='active', browser_active=True)
    stop = threading.Event()
    def renderer():
        while not stop.wait(.01):
            current = owner(root)
            if current['mode'] == 'draining':
                heartbeat(root, 'renderer', state='standby', browser_active=False,
                          generation=current['generation'])
                return
    t = threading.Thread(target=renderer); t.start()
    try:
        assert OwnerControl(root, timeout=1).rollback()['mode'] == 'legacy'
    finally:
        stop.set(); t.join()


def test_atomic_control_lease_is_single_owner(tmp_path):
    root = ready(tmp_path)
    with lease(root, 'control'):
        with pytest.raises(BlockingIOError):
            with lease(root, 'control'):
                pytest.fail('duplicate operation owner')
    with lease(root, 'control'):
        pass


def test_pipeline_cannot_show_a_late_snapshot_after_rollback(tmp_path):
    cfg = load_common_config(tmp_path, {'DOCICH_TWICA_STATE_DIR': str(tmp_path/'state'),
                            'DOCICH_TWICA_FRAME_DIR': str(tmp_path/'frames')})
    private_directory(cfg.state)
    with SnapshotPublisher(cfg.frames, 1, 1) as pub:
        pub.publish(b'\xff\xff\xff\xff', time.monotonic_ns())
        reader = OwnedReader(cfg, 1, 1)
        assert reader.read() == bytes(4)
        set_owner(cfg.state, 'common')
        assert reader.read() == b'\xff\xff\xff\xff'
        set_owner(cfg.state, 'draining')
        assert reader.read() == bytes(4)
        set_owner(cfg.state, 'legacy')
        assert reader.read() == bytes(4)


def test_diagnostics_is_a_fixed_projection(tmp_path):
    root = ready(tmp_path)
    heartbeat(root, 'renderer', state='standby', browser_active=False,
              url='NEVER_PUBLISH_ME', token='NEVER_PUBLISH_ME')
    data = diagnostics(root)
    assert 'NEVER_PUBLISH_ME' not in json.dumps(data)
    assert data['renderer_fresh'] is True
    assert set(data) == {'protocol', 'owner', 'renderer_fresh', 'renderer_active',
        'compositor_fresh', 'compositor_running', 'legacy_consumers', 'unready_consumers', 'frame_state'}


@pytest.mark.parametrize('env', [
    {'DOCICH_TWICA_COMMON_ENABLED': 'perhaps'},
    {'DOCICH_TWICA_STATE_DIR': 'relative'},
    {'DOCICH_TWICA_FRAME_DIR': 'relative'},
    {'DOCICH_TWICA_FPS': '0'}, {'SOREN_DIRECT_TWICA_PROXY_PORT': '65536'},
    {'DOCICH_TWICA_AUDIO_SINK': 'sink; command'},
])
def test_invalid_config_is_rejected(tmp_path, env):
    with pytest.raises(ValueError):
        load_common_config(tmp_path, env)
