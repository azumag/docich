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
                    audio_present=True, alive=True, status="receiving", listen_ip="100.71.107.106")
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


def test_waiting_records_fifo_before_reading_late_source(setup, monkeypatch):
    s = setup
    late_source = runtime("sorengame", 8)
    s.canonical[0] = ready(runtime("hanjuku-hero", 7))
    monkeypatch.setattr(corner, "other_corner_busy", lambda *_: "owner-busy")

    @contextmanager
    def slot(*_a, **kw):
        state = json.loads(s.manager.path.read_text())
        assert state["status"] == "waiting"
        assert "previous_runtime_identity" not in state
        assert kw["requested_at"] == 1000 and kw["wait_deadline_ts"] == 8200
        assert not s.calls
        s.canonical[0] = ready(late_source)  # Existing corner finished naturally.
        yield

    monkeypatch.setattr(corner, "program_slot", slot)
    state = s.manager.run(1, wait_for_idle=True)
    assert state["status"] == "completed"
    assert s.calls[0][1]["payload"]["expected_source"] == late_source


@pytest.mark.parametrize("reason", ["stop", "expiry", "pause"])
def test_waiting_cancellation_never_dispatches_or_recovers(setup, monkeypatch, reason):
    s = setup

    @contextmanager
    def slot(*_a, **kw):
        if reason == "stop":
            s.manager.stopping = True
        elif reason == "expiry":
            s.now[0] += 120 * 60
        else:
            paused = s.g.state_dir / "corners/external-video.paused"
            paused.parent.mkdir()
            paused.write_text("user pause")
        kw["sleep"](5)
        yield

    monkeypatch.setattr(corner, "program_slot", slot)
    state = s.manager.run(1, wait_for_idle=True)
    assert state["status"] == "interrupted" and not state["recovery_required"]
    assert not s.calls and not s.stopped
    if reason == "pause":
        assert (s.g.state_dir / "corners/external-video.paused").read_text() == "user pause"


def test_waiting_renews_only_expired_owned_receiver_at_turn(setup, monkeypatch):
    s = setup
    s.prepared.update(alive=False, fresh=False, expires_at=900, status="stopped", listen_ip="100.71.107.106")
    old_id = s.prepared["receiver_id"]
    renewed_id = str(uuid.uuid4())
    observed = []
    s.manager.sleep = lambda n: s.now.__setitem__(0, s.now[0] + n)

    def prepare(_g, ip, minutes, **kw):
        observed.append((ip, minutes, kw))
        assert json.loads(s.manager.path.read_text())["status"] == "waiting"
        assert not s.calls
        s.prepared.update(receiver_id=renewed_id, alive=True, fresh=True, expires_at=5000, status="receiving")
        return s.prepared.copy()

    monkeypatch.setattr(corner, "prepare", prepare)
    state = s.manager.run(1, wait_for_idle=True)
    assert observed == [("100.71.107.106", 60, {"expected_receiver_id": old_id})]
    assert state["receiver_id"] == renewed_id


@pytest.mark.parametrize("problem", ["replacement", "audio"])
def test_receiver_replacement_or_missing_audio_at_turn_never_switches(setup, monkeypatch, problem):
    s = setup
    s.prepared["listen_ip"] = "100.71.107.106"

    def read(_g, **kw):
        if kw.get("expected") and problem == "replacement":
            raise receiver.ExternalVideoError("receiver ownership changed")
        return dict(s.prepared, audio_present=problem != "audio")

    monkeypatch.setattr(corner, "read_receiver", read)
    s.manager.sleep = lambda n: s.now.__setitem__(0, s.now[0] + n)
    with pytest.raises(receiver.ExternalVideoError):
        s.manager.run(1, wait_for_idle=True)
    state = json.loads(s.manager.path.read_text())
    assert state["status"] == "interrupted" and not state["recovery_required"]
    assert not s.calls and not s.stopped


def test_waiting_reservation_blocks_later_program_then_cleans_queue(setup, monkeypatch):
    from docich import corner_boundary as boundary
    s = setup
    root = s.g.state_dir / "soren/tmp/state"
    root.mkdir(parents=True)
    owner = s.g.state_dir / "retro_corner.json"
    atomic_write_json(owner, {"status": "active"})
    atomic_write_json(root / boundary.REGISTRY_FILE, {"owner_state": str(owner)})
    monkeypatch.setattr(boundary, "resolve_soren_root", lambda _g: root.parent.parent)
    monkeypatch.setattr(boundary.time, "time", s.manager.clock)
    monkeypatch.setattr(corner, "program_slot", boundary.program_slot)
    s.prepared["listen_ip"] = "100.71.107.106"
    slept = []

    def sleep(seconds):
        if not slept:
            assert not s.calls
            assert boundary.other_corner_busy(s.g, owner) == "queued:external_video_corner"
            atomic_write_json(owner, {"status": "completed"})
            s.canonical[0] = ready(runtime("sorengame", 9))
        slept.append(seconds)
        s.now[0] += 61

    s.manager.sleep = sleep
    assert s.manager.run(1, wait_for_idle=True)["status"] == "completed"
    entry = json.loads((root / boundary.QUEUE_DIR / "external_video_corner.json").read_text())
    assert entry["status"] == "done"
    assert s.calls[0][1]["payload"]["expected_source"]["generation"] == 9


def test_prepare_renewal_fence_rejects_replaced_reservation(tmp_path, monkeypatch):
    g = SimpleNamespace(state_dir=tmp_path)
    state = dict(schema_version=1, receiver_id=str(uuid.uuid4()), listen_ip="100.71.107.106",
                 expires_at=time.time()-100, status="stopped")
    atomic_write_json(tmp_path / receiver.RECEIVER_FILE, state)
    monkeypatch.setattr(receiver, "worker_alive", lambda _state: False)
    with pytest.raises(receiver.ExternalVideoError, match="ownership changed"):
        receiver.prepare(g, "100.71.107.106", expected_receiver_id=str(uuid.uuid4()))
    assert json.loads((tmp_path / receiver.RECEIVER_FILE).read_text()) == state


def test_queued_stop_cancels_shared_fifo_entry(setup, monkeypatch):
    from docich import corner_boundary as boundary
    s = setup
    root = s.g.state_dir / "soren/tmp/state"
    root.mkdir(parents=True)
    owner = s.g.state_dir / "retro_corner.json"
    atomic_write_json(owner, {"status": "active"})
    atomic_write_json(root / boundary.REGISTRY_FILE, {"owner_state": str(owner)})
    monkeypatch.setattr(boundary, "resolve_soren_root", lambda _g: root.parent.parent)
    monkeypatch.setattr(boundary.time, "time", s.manager.clock)
    monkeypatch.setattr(corner, "program_slot", boundary.program_slot)

    def sleep(_seconds):
        s.manager.stopping = True
        s.now[0] += 1

    s.manager.sleep = sleep
    assert s.manager.run(1, wait_for_idle=True)["status"] == "interrupted"
    entry = json.loads((root / boundary.QUEUE_DIR / "external_video_corner.json").read_text())
    assert entry["status"] == "cancelled"
    assert json.loads(owner.read_text())["status"] == "active"
    assert not s.calls


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
    setup.manager.sleep = lambda n: setup.now.__setitem__(0, setup.now[0] + 10)
    state = setup.manager.run(1)
    assert state["end_reason"] == "receiver-disconnected"
    assert state["status"] == "completed"
    assert [x[0] for x in setup.calls] == [corner.VIEW_NAME, "sorengame"]


def test_brief_stale_frame_does_not_end_the_corner(setup, monkeypatch):
    seen = []

    def read(*_a, **kw):
        # stale for 20 s (< grace), then fresh again until the duration ends
        stale = 1000 <= setup.now[0] < 1020 + 10 and bool(seen.append(1) or True)
        return dict(setup.prepared, fresh=bool(kw.get("fresh")) or not stale)

    monkeypatch.setattr(corner, "read_receiver", read)
    setup.manager.sleep = lambda n: setup.now.__setitem__(0, setup.now[0] + 10)
    state = setup.manager.run(1)
    assert seen and state["end_reason"] == "duration"


def test_duration_ceiling_is_a_day_not_three_hours(setup):
    with pytest.raises(receiver.ExternalVideoError, match="1-1440"):
        setup.manager.run(1441)
    assert setup.manager.run(300)["status"] == "completed"


def test_operator_end_flag_restores_and_speaks_closing_once(setup, monkeypatch):
    spoken = []
    import docich.external_video_closing as closing
    monkeypatch.setattr(closing, "speak_closing", lambda g, minutes: spoken.append(minutes) or "spoken:fallback")

    def sleep(_n):
        state = json.loads(setup.manager.path.read_text())
        if state["status"] == "active" and not (setup.g.state_dir / corner.STOP_FILE).exists():
            corner.request_end(setup.g, state, corner.OPERATOR_END)
        setup.now[0] += 5

    setup.manager.sleep = sleep
    state = setup.manager.run(180)
    assert state["end_reason"] == corner.OPERATOR_END
    assert [x[0] for x in setup.calls] == [corner.VIEW_NAME, "sorengame"]
    assert spoken == [1] or len(spoken) == 1
    assert state["closing"]["status"] == "spoken:fallback"


def test_manual_stop_does_not_speak_closing(setup, monkeypatch):
    import docich.external_video_closing as closing
    monkeypatch.setattr(closing, "speak_closing", lambda *a: pytest.fail("closing on manual stop"))

    def sleep(_n):
        state = json.loads(setup.manager.path.read_text())
        if state["status"] == "active":
            corner.request_end(setup.g, state, "manual")
        setup.now[0] += 5

    setup.manager.sleep = sleep
    state = setup.manager.run(180)
    assert state["end_reason"] == "manual" and "closing" not in state


def test_end_request_rejects_unknown_reason_and_idle_state(setup):
    with pytest.raises(receiver.ExternalVideoError):
        corner.request_end(setup.g, {"status": "active", "start_request_id": "x"}, "bogus")
    with pytest.raises(receiver.ExternalVideoError):
        corner.request_end(setup.g, {"status": "completed", "start_request_id": "x"}, corner.OPERATOR_END)


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
    atomic_write_json(tmp_path / corner.STATE_FILE, {"status": "waiting", "listen_ip": "100.71.107.106"})
    monkeypatch.setattr(module, "_external_video_process_alive", lambda _: False)
    data = module._collect_external_video(tmp_path, 1000)
    text = json.dumps(data)
    assert "never-output-me" not in text and "100.71" not in text and str(rid) not in text
    assert data["receiver_readable"] and data["receiver_alive"] is False
    assert data["corner_status"] == "waiting"


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


def test_canonical_waits_out_a_busy_switch_lock_instead_of_crashing_the_runner(setup, monkeypatch):
    from docich.game_switch import GameSwitchBusyError
    from contextlib import contextmanager
    calls = []

    @contextmanager
    def lock(*, exclusive, blocking=False):
        calls.append(1)
        if len(calls) < 4:
            raise GameSwitchBusyError("busy")
        yield

    monkeypatch.setattr(setup.manager.store, "lock", lock)
    real = type(setup.manager).canonical
    monkeypatch.setattr(setup.manager.store.canonical, "load", lambda: ({"phase": "ready"}, None))
    assert real(setup.manager) == {"phase": "ready"}
    assert len(calls) == 4


def test_canonical_still_raises_when_the_lock_never_frees(setup, monkeypatch):
    from docich.game_switch import GameSwitchBusyError
    from contextlib import contextmanager

    @contextmanager
    def lock(*, exclusive, blocking=False):
        raise GameSwitchBusyError("busy")
        yield

    monkeypatch.setattr(setup.manager.store, "lock", lock)
    with pytest.raises(GameSwitchBusyError):
        type(setup.manager).canonical(setup.manager, patience_s=2)


def test_default_coordinator_carries_the_stream_category_hook(tmp_path, monkeypatch):
    import docich.stream_category as sc
    marker = object()
    monkeypatch.setattr(sc, "commit_hook", lambda g: marker)
    g = SimpleNamespace(state_dir=tmp_path, config_path=tmp_path / "c.toml")
    manager = corner.ExternalVideoCornerManager(g)
    assert manager.coordinator.post_commit is marker
