"""Issue #1969: a failed return must not start a second game/adventure."""
from dataclasses import replace
from pathlib import Path
import time
from unittest.mock import Mock

import pytest

from docich import config, game_switch
from docich.adapters.base import AdapterError
from docich.adapters.nethack import NethackCoordinatorAdapter
from docich.adapters.soren import SorenCoordinatorAdapter
from test_coordinator import CoordinatorTestBase
from test_nethack_adapter import _root, _spec


class TestRollbackIsolation(CoordinatorTestBase):
    def leak_candidate(self):
        self.coordinator.start("nethack")
        self.behaviors["robots"].update(
            readiness_error=game_switch.ReadinessTimeoutError("slow return"),
            cleanup_error=AdapterError("candidate still alive"),
        )
        return self.coordinator.switch("robots")

    def test_failed_candidate_blocks_previous_respawn(self):
        result = self.leak_candidate()
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.cleanup_pending)
        state = self.canonical()
        self.assertIsNone(state["active"])
        self.assertEqual(state["previous"]["game"], "nethack")
        self.assertEqual(state["retiring"][0]["game"], "robots")
        self.assertIsNone(self.factory.adapter("nethack", 3))

    def test_recovery_retries_cleanup_before_restore(self):
        failed = self.leak_candidate()
        receipt = self.store.receipts.load(failed.request_id)
        self.assertEqual(self.coordinator.recover().status, "failed")
        self.assertIsNone(self.factory.adapter("nethack", 3))
        self.behaviors["robots"].pop("cleanup_error")
        recovered = self.coordinator.recover()
        self.assertEqual(recovered.status, "rolled_back")
        self.assertEqual(self.canonical()["active"]["game"], "nethack")
        self.assertFalse(self.factory.adapter("robots", 2).runtime.alive)
        self.assertEqual(self.store.receipts.load(failed.request_id), receipt)

    def test_terminal_source_cannot_be_recreated_on_failure_or_recover(self):
        self.coordinator.start("nethack")
        old = self.factory.adapter("nethack", 1)
        old.can_restore_stopped_runtime = Mock(return_value=False)
        self.behaviors["robots"]["readiness_error"] = game_switch.ReadinessTimeoutError("slow")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "failed")
        self.assertIsNone(self.factory.adapter("nethack", 3))
        self.assertEqual(self.coordinator.recover().status, "failed")
        self.assertIsNone(self.factory.adapter("nethack", 3))
        self.assertEqual(self.canonical()["next_generation"], 3)
        self.assertEqual(old.runtime.events.count("agent_start"), 1)

    def test_unverified_restore_capability_is_not_permission(self):
        for answer in (None, "yes", 1):
            with self.subTest(answer=answer):
                # Each subcase uses an independent store/factory.
                self.tearDown()
                self.setUp()
                self.coordinator.start("nethack")
                old = self.factory.adapter("nethack", 1)
                old.can_restore_stopped_runtime = Mock(return_value=answer)
                self.behaviors["robots"]["readiness_error"] = AdapterError("slow")
                result = self.coordinator.switch("robots")
                self.assertEqual(result.status, "failed")
                self.assertIsNone(self.factory.adapter("nethack", 3))

    def test_saved_source_can_resume_normally(self):
        self.coordinator.start("nethack")
        old = self.factory.adapter("nethack", 1)
        old.can_restore_stopped_runtime = Mock(return_value=True)
        self.behaviors["robots"]["readiness_error"] = AdapterError("slow")
        self.assertEqual(self.coordinator.switch("robots").status, "rolled_back")
        self.assertIsNotNone(self.factory.adapter("nethack", 3))
        old.can_restore_stopped_runtime.assert_called_once()


def nethack(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    root = _root(save)
    g = config.load_global(root)
    return NethackCoordinatorAdapter(g, config.load_game(g, "nethack"), _spec(root)), save


def test_ended_nethack_does_not_authorize_a_new_adventure(tmp_path):
    adapter, _ = nethack(tmp_path)
    assert adapter.can_restore_stopped_runtime(time.monotonic() + 5, None) is False


def test_saved_nethack_can_resume(tmp_path):
    adapter, save = nethack(tmp_path)
    (save / "1000docich").write_bytes(b"saved adventure")
    assert adapter.can_restore_stopped_runtime(time.monotonic() + 5, None) is True


@pytest.mark.parametrize("names", [("1000otherdocich",), ("1000docich", "1001docich")])
def test_foreign_or_ambiguous_save_cannot_restore(tmp_path, names):
    adapter, save = nethack(tmp_path)
    for name in names:
        (save / name).write_bytes(b"not uniquely ours")
    assert adapter.can_restore_stopped_runtime(time.monotonic() + 5, None) is False


def test_empty_save_does_not_restore(tmp_path):
    adapter, save = nethack(tmp_path)
    (save / "1000docich").touch()
    assert adapter.can_restore_stopped_runtime(time.monotonic() + 5, None) is False


def failed_soren_candidate(tmp_path):
    from types import SimpleNamespace
    from test_coordinator import _runtime_dict
    store = game_switch.GameSwitchStore(tmp_path / "run")
    state = store.initialize()
    rd = {**_runtime_dict(2, "sorengame"), "adapter": "soren"}
    state.update(phase="failed", candidate=rd, previous=_runtime_dict(1, "nethack"), next_generation=3)
    store.canonical.save(state)
    spec = game_switch.RuntimeSpec.from_runtime(store.state_dir, rd)
    game = SimpleNamespace(raw={"soren": {"root": str(tmp_path)}},
                           lifecycle=SimpleNamespace(boundary_timeout_s=20))
    adapter = SorenCoordinatorAdapter(SimpleNamespace(state_dir=store.state_dir), game, spec)
    adapter._retired_singleton_is_superseded = Mock(return_value=False)
    return adapter, store


def test_soren_failed_candidate_can_request_its_own_bounded_cleanup(tmp_path):
    adapter, _ = failed_soren_candidate(tmp_path)
    adapter._status = Mock(return_value={"schema": 1, "request": None, "ack": None, "resource": None})
    adapter.request_round_boundary = Mock()
    adapter._run = Mock(return_value=(0, {}))
    adapter._wait_status = Mock(return_value="stopped")
    adapter.cleanup_runtime(time.monotonic() + 10, None)
    request_id = adapter.request_round_boundary.call_args.args[0]
    assert adapter._run.call_args.args[0] == [str(adapter.control), "stop-after-boundary", request_id]
    adapter._wait_status.assert_called_once()


def test_soren_candidate_cleanup_replays_same_request_after_wait(tmp_path):
    adapter, _ = failed_soren_candidate(tmp_path)
    current = {"schema": 1, "request": None, "ack": None, "resource": None}
    adapter._status = lambda *a: current
    ids = []
    def wait(request_id, deadline, cancel):
        ids.append(request_id)
        receipt = {"schema": 1, "request_id": request_id, "game": "sorengame", "generation": 2,
                   "deadline_epoch": time.time() + 10, "deadline_at": "2026-10-09T00:00:00Z"}
        current.update(request=receipt, ack={**receipt, "status": "waiting"})
        raise game_switch.ReadinessTimeoutError("still playing")
    adapter.request_round_boundary = wait
    adapter._run = Mock(return_value=(0, {}))
    adapter._wait_status = Mock(return_value="stopped")
    with pytest.raises(game_switch.ReadinessTimeoutError):
        adapter.cleanup_runtime(time.monotonic() + 10, None)
    adapter._run.assert_not_called()
    adapter._wait_round_boundary = Mock()
    adapter.cleanup_runtime(time.monotonic() + 10, None)
    assert len(ids) == 1
    assert adapter._wait_round_boundary.call_args.args[0]["request_id"] == ids[0]
    assert adapter._run.call_args.args[0][-1] == ids[0]


@pytest.mark.parametrize("kind", ["untracked", "lease", "other_soren", "foreign_request", "unknown_status"])
def test_soren_missing_ack_never_authorizes_unowned_cleanup(tmp_path, kind):
    from test_coordinator import _runtime_dict
    adapter, store = failed_soren_candidate(tmp_path)
    state, _ = store.canonical.load()
    payload = {"schema": 1, "request": None, "ack": None, "resource": None}
    if kind == "untracked":
        state["candidate"] = None
    elif kind == "lease":
        adapter.spec = replace(adapter.spec, lease_id="foreign-lease")
    elif kind == "other_soren":
        state["previous"] = {**_runtime_dict(1, "sorengame"), "adapter": "soren"}
    elif kind == "foreign_request":
        payload.update(request={"request_id": "foreign"}, ack={"request_id": "foreign", "status": "boundary"})
    else:
        payload = {"ack": None}
    store.canonical.save(state)
    adapter._status = Mock(return_value=payload)
    adapter.request_round_boundary = Mock()
    adapter._run = Mock(return_value=(0, {}))
    with pytest.raises(AdapterError):
        adapter.cleanup_runtime(time.monotonic() + 10, None)
    adapter.request_round_boundary.assert_not_called()
    adapter._run.assert_not_called()


def test_run_finish_rejects_a_different_expedition_atomically(tmp_path):
    import datetime as dt
    from docich.nethack_run import NethackRunStore, NethackRunError
    adapter, _ = nethack(tmp_path)
    store = NethackRunStore.from_global(adapter.g)
    now = dt.datetime.now(dt.timezone.utc)
    run = store.record_started(store.prepare_start(current_is_nethack=False, now=now), now=now)
    with pytest.raises(NethackRunError, match="identity"):
        store.record_finished(now=now, nethack_still_active=False, expected_run_id="different")
    assert store.current() == run
    ended = store.record_finished(now=now, nethack_still_active=False, expected_run_id=run["run_id"])
    assert ended["status"] == "ended_unknown"
    assert ended["death_reason"] is None
    assert store.current() is None


def history_manager(tmp_path):
    import datetime as dt
    from docich.nethack_corner import NethackCornerManager
    from docich.nethack_run import NethackRunStore
    from docich.naming import runtime_directory
    from test_coordinator import _runtime_dict
    adapter, _ = nethack(tmp_path)
    store = NethackRunStore.from_global(adapter.g)
    now = dt.datetime.now(dt.timezone.utc)
    run = store.record_started(store.prepare_start(current_is_nethack=False, now=now), now=now)
    source = _runtime_dict(1, "nethack")
    canonical = {"phase": "failed", "active": None, "previous": source}
    coordinator = Mock()
    coordinator.store.canonical.load.return_value = (canonical, False)
    manager = NethackCornerManager(adapter.g, coordinator=coordinator,
                                   chat=Mock(), voice=Mock(), ensure_runtime=Mock())
    state = {"status": "failed", "run_id": run["run_id"], "switch_request_id": "restore-1",
             "last_error": "restore timeout"}
    boundary = {"schema_version": 1, "request_id": "restore-1", "player_name": "docich", "outcome": "ended",
                **{key: source[key] for key in ("game", "runtime_id", "generation")}}
    path = runtime_directory(adapter.g.state_dir, source["runtime_id"]) / "nethack_boundary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return manager, state, source, boundary, path, now


def test_failed_restore_records_ended_adventure_without_claiming_restore_success(tmp_path):
    import json
    manager, state, source, boundary, path, now = history_manager(tmp_path)
    path.write_text(json.dumps(boundary))
    manager._remember_failed_restore_terminal(state, source, now)
    assert state["status"] == "failed"
    assert state["last_error"] == "restore timeout"
    assert state["run_status"] == "ended_unknown"
    assert manager._run_store.current() is None
    assert state["run_death_reason"] is None


@pytest.mark.parametrize("change", [{"request_id": "old"}, {"generation": 99}, {"game": "robots"},
                                    {"player_name": "other"}, {"outcome": "unknown"}, {"schema_version": True}])
def test_failed_restore_ignores_stale_or_foreign_terminal_evidence(tmp_path, change):
    import json
    manager, state, source, boundary, path, now = history_manager(tmp_path)
    path.write_text(json.dumps({**boundary, **change}))
    manager._remember_failed_restore_terminal(state, source, now)
    assert manager._run_store.current()["status"] == "active"
    assert "run_status" not in state


def test_failed_restore_does_not_finish_a_live_or_new_runtime(tmp_path):
    import json
    manager, state, source, boundary, path, now = history_manager(tmp_path)
    path.write_text(json.dumps(boundary))
    canonical = manager.coordinator.store.canonical.load.return_value[0]
    canonical["active"] = source
    manager._remember_failed_restore_terminal(state, source, now)
    assert manager._run_store.current()["status"] == "active"


def test_finish_failure_keeps_original_exception_but_records_terminal_run(tmp_path):
    import json
    from unittest.mock import patch
    from docich.retro_corner import RetroCornerError
    manager, state, source, boundary, path, now = history_manager(tmp_path)
    manager.coordinator.store.canonical.load.side_effect = [
        ({"phase": "ready", "active": source}, False),
        ({"phase": "failed", "active": None, "previous": source}, False),
    ]
    path.write_text(json.dumps(boundary))
    error = RetroCornerError("exact restore failure")
    with patch("docich.retro_corner.RetroCornerManager._finish_locked", side_effect=error):
        with pytest.raises(RetroCornerError) as got:
            manager._finish_locked(state, now)
    assert got.value is error
    assert state["status"] == "failed"
    assert state["run_status"] == "ended_unknown"
    assert manager._run_store.current() is None


def test_queued_restore_does_not_announce_completion(tmp_path):
    from unittest.mock import patch
    from docich.retro_corner import CornerResult
    manager, state, source, boundary, path, now = history_manager(tmp_path)
    manager._announce_end_locked = Mock()
    with patch("docich.retro_corner.RetroCornerManager._finish_locked", return_value=CornerResult("queued")):
        result = manager._finish_locked(state, now)
    assert result.status == "queued"
    manager._announce_end_locked.assert_not_called()
    assert manager._run_store.current()["status"] == "active"


@pytest.mark.parametrize("kind", ["missing", "corrupt", "oversized", "symlink", "fifo", "wrong_corner"])
def test_untrusted_boundary_never_finishes_history(tmp_path, kind):
    import json
    import os
    manager, state, source, boundary, path, now = history_manager(tmp_path)
    if kind == "corrupt":
        path.write_text("{")
    elif kind == "oversized":
        path.write_text(json.dumps(boundary) + " " * 4096)
    elif kind == "symlink":
        target = path.parent / "other.json"
        target.write_text(json.dumps(boundary))
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "wrong_corner":
        path.write_text(json.dumps(boundary))
        state["rotation_runtime_id"] = "g9-abcdef"
    manager._remember_failed_restore_terminal(state, source, now)
    assert manager._run_store.current()["status"] == "active"
