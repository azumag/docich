import importlib.util
import json
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]


def collector():
    spec = importlib.util.spec_from_file_location('bgm_diag', ROOT / 'ops/vm_actions/collect_diagnostics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._systemctl_system = lambda *a, **kw: (0, 'ActiveState=active\nMainPID=100')
    module._pulse_listing = lambda *a: ('', None)
    module._bgm_owned_player = lambda pid, worker: pid == 101 and worker == 100
    return module


def snapshot(root, **changes):
    path = root / 'tmp/state/bgm_worker.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(schema=1, observed_at=99, worker_pid=100, player_pid=101,
                state='running', reason='eligible', player_exits=2, last_exit=7)
    data.update(changes)
    path.write_text(json.dumps(data))
    return path


def stream(index=1, pid=101, *, identity=True):
    body = (f'Sink Input #{index}\n\tSink: 3\n\tMute: no\n\tCorked: no\n'
            '\tVolume: front-left: 16384 / 25% / -36.12 dB\n'
            '\tProperties:\n\t\tapplication.name = "ffplay"\n'
            f'\t\tapplication.process.id = "{pid}"\n')
    if identity:
        body += ('\t\tapplication.id = "docich.corner-bgm"\n'
                 '\t\tmodule-stream-restore.id = "docich-corner-bgm"\n')
    return body


def test_current_state_and_only_owned_identity_stream_are_reported(tmp_path):
    module = collector()
    snapshot(tmp_path)
    # A TTS ffplay and a forged identity with an unrelated parent are separate.
    listing = stream() + stream(2, 900, identity=False) + stream(3, 900)
    module._pulse_listing = lambda *a: ('3\tsoren_null\n' if a[-1] == 'sinks' else listing, None)
    out = module._collect_corner_bgm(tmp_path, 100)
    assert out['telemetry'] == 'current' and out['state'] == 'running'
    assert out['player_present'] is True and out['player_exits'] == 2
    assert out['last_exit'] == 7 and out['reason'] == 'eligible'
    assert out['identified_streams'] == 2 and out['owned_streams'] == 1
    assert out['streams'] == [dict(mute=False, corked=False, volume_percent=[25], on_target_sink=True)]
    assert 'pid' not in json.dumps(out) and 'application' not in json.dumps(out)


def test_stale_or_previous_worker_does_not_report_live_playback(tmp_path):
    module = collector()
    for changes in ({'observed_at': 1}, {'worker_pid': 99}):
        snapshot(tmp_path, **changes)
        out = module._collect_corner_bgm(tmp_path, 100)
        assert out['telemetry'] == 'stale'
        assert out['state'] == 'unknown' and out['player_present'] is None


def test_unknown_and_private_snapshot_content_is_not_exported(tmp_path):
    module = collector()
    snapshot(tmp_path, reason='PRIVATE_ERROR', state='PRIVATE_ERROR',
             player_exits=True, last_exit=999, secret='PRIVATE_VALUE')
    out = module._collect_corner_bgm(tmp_path, 100)
    assert out['state'] == 'unknown' and out['reason'] == 'unknown'
    assert out['player_exits'] is None and out['last_exit'] is None
    assert 'PRIVATE' not in json.dumps(out)


def test_unreadable_snapshot_and_pulse_are_not_healthy_empty(tmp_path):
    module = collector()
    path = snapshot(tmp_path)
    for raw in ('[1]', 'not-json', 'x' * 4097):
        path.write_text(raw)
        module._pulse_listing = lambda *a: (None, 'pactl_failed')
        out = module._collect_corner_bgm(tmp_path, 100)
        assert out['telemetry'] == 'unreadable'
        assert out['pulse_readable'] is False and out['owned_streams'] is None
    path.unlink()
    assert module._collect_corner_bgm(tmp_path, 100)['telemetry'] == 'missing'
    target = tmp_path / 'private'
    target.write_text('{}')
    path.symlink_to(target)
    assert module._collect_corner_bgm(tmp_path, 100)['telemetry'] == 'unreadable'


def test_player_requires_live_tagged_ffplay_with_service_parent(tmp_path):
    spec = importlib.util.spec_from_file_location('owner_diag', ROOT / 'ops/vm_actions/collect_diagnostics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    proc = tmp_path / '101'
    proc.mkdir()
    (proc / 'comm').write_text('ffplay\n')
    (proc / 'cmdline').write_bytes(b'ffplay\0-window_title\0soren-bgm-loop\0')
    (proc / 'stat').write_text('101 (ffplay) S 100 0 0')
    assert module._bgm_owned_player(101, 100, tmp_path)
    assert not module._bgm_owned_player(101, 99, tmp_path)
    (proc / 'stat').write_text('101 (ffplay) Z 100 0 0')
    assert not module._bgm_owned_player(101, 100, tmp_path)


def test_bgm_basics_survive_generic_pulse_budget_truncation(tmp_path):
    module = collector()
    snapshot(tmp_path)
    bgm = module._collect_corner_bgm(tmp_path, 100)
    payload = dict(corner_bgm=bgm, pulse_sink_inputs={'streams': [{'mute': False}]},
                   nethack_history={'daily': {'records': []}, 'completed_runs': {'records': []}},
                   ai={'recent_events': [], 'anomalous_components': {}}, workers={'details': {}},
                   soren91_drop_profile={'profileStatus': 'unavailable'})
    with mock.patch.object(module, 'MAX_JSON_BYTES', 1):
        module._diagnostics_budget(payload)
    assert payload['pulse_sink_inputs']['output_omitted'] is True
    assert payload['corner_bgm'] == bgm


def test_duplicate_owned_streams_remain_counted_but_rows_are_bounded(tmp_path):
    module = collector()
    listing = ''.join(stream(i) for i in range(5))
    module._pulse_listing = lambda *a: ('3\tPRIVATE_SINK\n' if a[-1] == 'sinks' else listing, None)
    out = module._collect_corner_bgm(tmp_path, 100)
    assert out['owned_streams'] == 5 and len(out['streams']) == 2
    assert all(s['on_target_sink'] is False for s in out['streams'])
    assert 'PRIVATE' not in json.dumps(out)
