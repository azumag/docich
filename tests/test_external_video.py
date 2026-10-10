from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace
import uuid

import pytest

from docich import external_video_corner as corner
from docich import external_video_receiver as receiver
from docich.adapters.external_video_program import ExternalVideoProgramAdapter
from docich.game_switch import atomic_write_json


def runtime(game, generation):
    return dict(game=game, runtime_id=f"g{generation}-0123abcd", generation=generation, lease_id=str(uuid.uuid4()))


def ready(active):
    return dict(phase="ready" if active else "idle", active=active, candidate=None, previous=None, retiring=[])


@pytest.fixture
def setup(tmp_path, monkeypatch):
    g = SimpleNamespace(state_dir=tmp_path, config_path=tmp_path / "config.toml")
    now = [1000.0]
    source = runtime("sorengame", 1)
    video = runtime(corner.VIEW_NAME, 2)
    restored = runtime("sorengame", 3)
    canonical = [ready(source)]
    calls = []
    fail_restore = [False]

    def switch(target, **kw):
        rid = kw["request_id"]
        calls.append((target, kw))
        active = video if target == corner.VIEW_NAME else restored
        status = "failed" if fail_restore[0] and target != corner.VIEW_NAME else "succeeded"
        if status == "succeeded":
            canonical[0] = ready(active)
        body = dict(request_id=rid, status=status, active_runtime=active, to_game=target)
        receipt = dict(request_id=rid, status=status, target=target, generation=active["generation"], result=body)
        return SimpleNamespace(status=status, request_id=rid, receipt=receipt, cleanup_pending=False)

    manager = corner.ExternalVideoCornerManager(g, coordinator=SimpleNamespace(switch=switch),
                                              clock=lambda: now[0], sleep=lambda _n: now.__setitem__(0, now[0] + 61))
    monkeypatch.setattr(manager, "canonical", lambda: canonical[0])
    prepared = dict(receiver_id=str(uuid.uuid4()), expires_at=5000, fresh=True,
                    audio_present=True, alive=True, status="receiving")
    monkeypatch.setattr(corner, "read_receiver", lambda *_a, **_k: prepared.copy())
    monkeypatch.setattr(corner, "other_corner_busy", lambda *_: None)
    stopped = []
    monkeypatch.setattr(corner, "request_receiver_stop", lambda _g, rid: stopped.append(rid))

    @contextmanager
    def slot(*_a, **_kw):
        yield

    monkeypatch.setattr(corner, "program_slot", slot)
    return SimpleNamespace(g=g, manager=manager, now=now, canonical=canonical, calls=calls,
                           source=source, video=video, restored=restored,
                           prepared=prepared, stopped=stopped, fail_restore=fail_restore)


def test_duration_restores_exact_source_with_durable_fences(setup):
    s = setup
    state = s.manager.run(1)
    assert state["status"] == "completed"
    assert state["end_reason"] == "duration"
    assert s.calls[0][1]["payload"] == {"expected_source": s.source}
    assert s.calls[1][1]["payload"] == {"expected_source": s.video}
    assert s.calls[0][1]["request_id"] != s.calls[1][1]["request_id"]
    assert state["restored_runtime_identity"] == s.restored
    assert s.stopped == [s.prepared["receiver_id"]]


@pytest.mark.parametrize("phase", ["failed", "draining", "rolling_back"])
def test_existing_failed_or_unsettled_switch_never_starts(setup, phase):
    setup.canonical[0]["phase"] = phase
    with pytest.raises(receiver.ExternalVideoError, match="requires recovery"):
        setup.manager.run(1)
    assert not setup.calls and not setup.manager.path.exists()


def test_foreign_retiring_identity_blocks_even_ready_state(setup):
    setup.canonical[0]["retiring"] = [runtime("nethack", 9)]
    with pytest.raises(receiver.ExternalVideoError):
        setup.manager.run(1)
    assert not setup.calls


def test_program_owner_conflict_is_not_overwritten(setup, monkeypatch):
    monkeypatch.setattr(corner, "other_corner_busy", lambda *_: "owner-busy")
    with pytest.raises(receiver.ExternalVideoError, match="another program"):
        setup.manager.run(1)
    assert not setup.calls


def test_user_pause_is_preserved(setup):
    p = setup.g.state_dir / "corners/external-video.paused"
    p.parent.mkdir()
    p.write_text("owner pause")
    with pytest.raises(receiver.ExternalVideoError, match="paused"):
        setup.manager.run(1)
    assert p.read_text() == "owner pause" and not setup.calls


def test_no_audio_refuses_start_before_program_mutation(setup):
    setup.prepared["audio_present"] = False
    with pytest.raises(receiver.ExternalVideoError, match="audio"):
        setup.manager.run(1)
    assert not setup.calls and not setup.manager.path.exists()


def test_disconnect_restores_before_receiver_cleanup(setup, monkeypatch):
    def read(*_a, **kw):
        return dict(setup.prepared, fresh=bool(kw.get("fresh")))
    monkeypatch.setattr(corner, "read_receiver", read)
    state = setup.manager.run(1)
    assert state["end_reason"] == "receiver-disconnected"
    assert state["status"] == "completed"
    assert [x[0] for x in setup.calls] == [corner.VIEW_NAME, "sorengame"]


def test_restore_failure_keeps_failed_owner_and_receiver(setup):
    setup.fail_restore[0] = True
    with pytest.raises(receiver.ExternalVideoError, match="restoration"):
        setup.manager.run(1)
    state = json.loads(setup.manager.path.read_text())
    assert state["status"] == "failed" and state["recovery_required"]
    assert state["runtime_identity"] == setup.video and state["restore_request_id"]
    assert not setup.stopped


def test_foreign_operator_move_never_restores_over_new_owner(setup, monkeypatch):
    count = [0]
    def read(*_a, **_kw):
        count[0] += 1
        if count[0] > 1:
            setup.canonical[0] = ready(runtime("nethack", 8))
        return setup.prepared.copy()
    monkeypatch.setattr(corner, "read_receiver", read)
    with pytest.raises(receiver.ExternalVideoError, match="ownership changed"):
        setup.manager.run(1)
    assert len(setup.calls) == 1 and not setup.stopped


@pytest.mark.parametrize("ip", ["0.0.0.0", "127.0.0.1", "example.com", "100.71.107.106?passphrase=secret", "::1"])
def test_bind_requires_literal_tailscale_address(ip):
    with pytest.raises(receiver.ExternalVideoError):
        receiver.listen_ip(ip)


def test_static_pixels_are_fresh_but_dead_or_reused_pid_is_not(tmp_path, monkeypatch):
    g = SimpleNamespace(state_dir=tmp_path)
    rid = str(uuid.uuid4())
    root = receiver.directory(g, rid)
    root.mkdir(parents=True)
    (root / "current.png").write_bytes(b"unchanged game frame")
    state = dict(schema_version=1, receiver_id=rid, listen_ip="100.71.107.106",
                 expires_at=time.time()+100, pid=100, start_ticks=42, status="receiving")
    atomic_write_json(tmp_path / receiver.RECEIVER_FILE, state)
    monkeypatch.setattr(receiver, "worker_alive", lambda _: True)
    assert receiver.read_receiver(g, fresh=True)["fresh"]
    monkeypatch.setattr(receiver, "worker_alive", lambda _: False)
    with pytest.raises(receiver.ExternalVideoError, match="fresh decoded"):
        receiver.read_receiver(g, fresh=True)
    with pytest.raises(receiver.ExternalVideoError, match="ownership changed"):
        receiver.read_receiver(g, expected=str(uuid.uuid4()))


def test_receiver_rejects_untrusted_path_identity(tmp_path):
    with pytest.raises(receiver.ExternalVideoError):
        receiver.directory(SimpleNamespace(state_dir=tmp_path), "../../secrets")


def test_media_pipeline_really_decodes_video_and_measures_audio(tmp_path):
    binary = shutil.which("ffmpeg")
    if not binary:
        pytest.skip("ffmpeg is not installed")
    source = tmp_path / "sample.ts"
    subprocess.run([binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=96x54:rate=10", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                    "-t", "2", "-c:v", "mpeg2video", "-c:a", "mp2", "-y", str(source)], check=True, timeout=20)
    g = SimpleNamespace(state_dir=tmp_path)
    state = dict(receiver_id=str(uuid.uuid4()), listen_ip="100.71.107.106")
    root = receiver.directory(g, state["receiver_id"])
    root.mkdir(parents=True)
    command = receiver.ffmpeg_command(g, state)
    command[0] = binary
    command[command.index("-i") + 1] = str(source)
    # Verify the actual bounded pipeline without any live socket/sender.
    command[-1] = str(tmp_path / "relay.ts")
    subprocess.run(command, check=True, capture_output=True, timeout=20)
    assert (root / "current.png").read_bytes().startswith(b"\x89PNG")
    assert "lavfi.astats.Overall.RMS_level=" in (root / "audio-levels.log").read_text()


def test_diagnostics_cannot_emit_receiver_secrets_or_address(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / "ops/vm_actions/collect_diagnostics.py"
    spec = importlib.util.spec_from_file_location("external_video_diagnostics_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rid = str(uuid.uuid4())
    atomic_write_json(tmp_path / receiver.RECEIVER_FILE,
                      dict(schema_version=1, receiver_id=rid, listen_ip="100.71.107.106", expires_at=2000,
                           status="receiving", token="never-output-me", pid=4321, start_ticks=1, audio_present=True))
    monkeypatch.setattr(module, "_external_video_process_alive", lambda _: False)
    data = module._collect_external_video(tmp_path, 1000)
    text = json.dumps(data)
    assert "never-output-me" not in text and "100.71" not in text and str(rid) not in text
    assert data["receiver_readable"] and data["receiver_alive"] is False


def test_failed_start_is_settled_only_by_its_own_confirmed_recovery(setup):
    s = setup
    rid = str(uuid.uuid4())
    state = dict(start_request_id=rid, previous_runtime_identity=s.source, status="starting")
    body = dict(request_id=rid, status="failed", error_code="rollback_failed")
    receipt = dict(request_id=rid, target=corner.VIEW_NAME, status="failed", result=body)
    s.manager.coordinator.switch = lambda *_a, **_kw: SimpleNamespace(
        status="failed", receipt=receipt, request_id=rid, cleanup_pending=False)
    s.canonical[0] = ready(s.restored)
    s.canonical[0]["last_result"] = dict(request_id=str(uuid.uuid4()), status="rolled_back",
        from_game=s.source["game"], restored_generation=s.restored["generation"])
    with pytest.raises(receiver.ExternalVideoError):
        s.manager._dispatch(state)
    s.canonical[0]["last_result"]["request_id"] = rid
    assert s.manager._dispatch(state)
    assert state["status"] == "interrupted"


def test_diagnostics_freshness_requires_live_worker_and_recent_frame(tmp_path, monkeypatch):
    import os
    path = Path(__file__).parents[1] / "ops/vm_actions/collect_diagnostics.py"
    spec = importlib.util.spec_from_file_location("external_video_fresh_projection", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rid = str(uuid.uuid4())
    state = dict(schema_version=1, receiver_id=rid, expires_at=2000,
                 status="receiving", pid=4321, start_ticks=1, audio_present=True)
    atomic_write_json(tmp_path / receiver.RECEIVER_FILE, state)
    frame = tmp_path / "external-video" / rid / "current.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"same pixels")
    os.utime(frame, (998, 998))
    monkeypatch.setattr(module, "_external_video_process_alive", lambda _: True)
    assert module._collect_external_video(tmp_path, 1000)["frame_fresh"]
    assert not module._collect_external_video(tmp_path, 1010)["frame_fresh"]
    monkeypatch.setattr(module, "_external_video_process_alive", lambda _: False)
    assert not module._collect_external_video(tmp_path, 1000)["frame_fresh"]
