"""Common adapter lifecycle tests using local state and fake game processes."""
import datetime as dt
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_retro_corner import RetroCornerTestBase
from docich.retro_corner import RetroCornerError
from docich.corner_adapters import GameCornerAdapter, CornerExecutionCoordinator, CornerExecutionError
from docich.corner_catalog import Corner
from docich.corner_ownership import verify_runtime


class TestRotationGameExecution(RetroCornerTestBase):
    def test_catalog_match_target_controls_end_of_game_and_restoration(self):
        from docich.config import load_global
        from docich.corner_catalog import load_catalog
        root = Path(__file__).resolve().parents[1]
        production = load_global(root, root / "config/docich.soren-live.toml")
        catalog = {c.game: c for c in load_catalog(production)}
        for game, expected in (("nsnake", 1), ("ninvaders", 3)):
            with self.subTest(game=game):
                game_path = self.root / "config/games" / f"{game}.toml"
                game_path.write_text(f'[game]\nname="{game}"\ntitle="{game}"\nadapter="cli"\n'
                                     '[corner]\nself_play=true\n')
                adapter = GameCornerAdapter(production, catalog[game])
                self.cfg = replace(adapter.manager.config, games=[game])
                current = ["sorengame"]
                started = self.now_value
                score = self.g.state_dir / "scores" / f"{game}.jsonl"
                score.parent.mkdir(parents=True, exist_ok=True)
                score.write_text(json.dumps({"game": game, "ts": started.timestamp() - 1}) + "\n"
                                 + json.dumps({"game": "other", "ts": started.timestamp()}) + "\n")
                completed = []

                def finish_match(seconds):
                    self.now_value += dt.timedelta(seconds=seconds)
                    completed.append(1)
                    assert len(completed) <= expected, "corner waited past configured matches"
                    with score.open("a") as stream:
                        stream.write(json.dumps({"game": game, "ts": self.now_value.timestamp()}) + "\n")

                mgr, coord = self.manager(current, sleep=finish_match)
                mgr._chat = Mock()
                mgr._stream_game = Mock()
                mgr._repair_active_agent = Mock(return_value=True)
                mgr.store.canonical.load = lambda: ({"phase": "ready", "active": {
                    "game": current[0], "runtime_id": "g1-test"}}, False)
                result = mgr.run_rotation(f"match-target-{game}", game)
                assert result.status == "completed"
                assert mgr._read_state()["target_matches"] == expected
                assert len(completed) == expected
                assert coord.calls == [("switch", game), ("switch", "sorengame")]
                assert current == ["sorengame"]
                assert (self.now_value - started).total_seconds() < 60

    def execution(self):
        current = [None]
        mgr, coord = self.manager(current)
        mgr._chat = Mock()
        mgr._stream_game = Mock()
        mgr.store.canonical.load = lambda: ({
            "phase": "ready" if current[0] else "idle",
            "active": {"game": current[0], "runtime_id": "g1-test"} if current[0] else None,
        }, False)
        mgr._wait_and_finish = lambda state: mgr._finish_locked(state, mgr._local_now())
        return mgr, coord, current

    def test_persisted_request_replays_without_duplicate_start(self):
        mgr, coord, current = self.execution()
        calls = []
        def start(game, request_id=None):
            calls.append((game, request_id))
            if len(calls) == 1:
                return SimpleNamespace(status="queued")
            current[0] = game
            return SimpleNamespace(status="succeeded")
        coord.start = start
        assert mgr.run_rotation("same-request").status == "queued"
        assert mgr._read_state()["switch_request_id"] == "same-request"
        assert mgr.run_rotation("same-request").status == "completed"
        assert calls == [("robots", "same-request"), ("robots", "same-request")]
        assert mgr.run_rotation("same-request").status == "completed"
        assert len(calls) == 2
        assert coord.calls == [("stop", None)]

    def test_unknown_stop_fails_without_claiming_completion(self):
        mgr, coord, _ = self.execution()
        coord.stop = lambda **kw: SimpleNamespace(status="failed", detail="old-child-unverified")
        with pytest.raises(RetroCornerError):
            mgr.run_rotation("stop-fails")
        assert mgr._read_state()["status"] == "failed"

    def test_unowned_or_failed_state_never_starts_another_corner(self):
        mgr, coord, _ = self.execution()
        mgr._write_state({"schema_version": 1, "status": "failed", "game": "robots"})
        with pytest.raises(RetroCornerError, match="owner mismatch"):
            mgr.run_rotation("other")
        assert not coord.calls

    def test_same_game_different_generation_is_not_restored(self):
        mgr, coord, current = self.execution()
        mgr._wait_and_finish = lambda state: mgr._state_result(state)
        assert mgr.run_rotation("identity").status == "active"
        mgr.store.canonical.load = lambda: ({"phase": "ready", "active": {
            "game": "robots", "runtime_id": "g2-another-owner"}}, False)
        with pytest.raises(RuntimeError, match="generation changed"):
            mgr.run_rotation("identity")
        assert coord.calls == [("start", "robots")]

    def test_restoring_same_game_different_generation_is_not_stopped(self):
        mgr, coord, _ = self.execution()
        mgr._wait_and_finish = lambda state: mgr._state_result(state)
        assert mgr.run_rotation("restore-identity").status == "active"
        state = mgr._read_state()
        state["status"] = "restoring"
        mgr._write_state(state)
        mgr.store.canonical.load = lambda: ({"phase": "ready", "active": {
            "game": "robots", "runtime_id": "g2-another-owner"}}, False)

        with pytest.raises(RuntimeError, match="generation changed"):
            mgr._finish_locked(state, mgr._local_now())
        assert coord.calls == [("start", "robots")]


def test_improvement_terminal_evidence_is_required(tmp_path):
    corner = Corner("snake", "game", "nsnake")
    adapter = GameCornerAdapter.__new__(GameCornerAdapter)
    adapter.g = SimpleNamespace(state_dir=tmp_path)
    adapter.corner = corner
    adapter.observations = lambda: [{"completed_at": 100, "improve_job": {"spawned": True}}]
    assert adapter.resources_released() is False
    _, path = adapter.improvement_paths()
    path.write_text(json.dumps({"status": "running", "started_at": 101}))
    assert adapter.resources_released() is False
    path.write_text(json.dumps({"status": "kept", "started_at": 99}))
    assert adapter.resources_released() is False
    path.write_text(json.dumps({"status": "kept", "started_at": 101}))
    assert adapter.resources_released() is True


def test_failed_improvement_releases_only_with_fresh_terminal_evidence(tmp_path):
    corner = Corner("snake", "game", "nsnake")
    adapter = GameCornerAdapter.__new__(GameCornerAdapter)
    adapter.g = SimpleNamespace(state_dir=tmp_path)
    adapter.corner = corner
    adapter.observations = lambda: [{"completed_at": 100, "improve_job": {"spawned": True}}]
    _, path = adapter.improvement_paths()
    path.write_text(json.dumps({"status": "failed", "started_at": 101, "completed_at": 102}))
    assert adapter.resources_released() is True
    path.write_text(json.dumps({"status": "failed", "started_at": 99, "completed_at": 102}))
    assert adapter.resources_released() is False
    path.write_text(json.dumps({"status": "failed", "started_at": 101}))
    assert adapter.resources_released() is False
    path.write_text(json.dumps({"status": "failed", "started_at": 101, "completed_at": 100}))
    assert adapter.resources_released() is False
    path.write_text(json.dumps({"status": "failed", "started_at": 101, "completed_at": 102,
                                "recovery_required": True}))
    assert adapter.resources_released() is False
    path.write_text(json.dumps({"status": "running", "started_at": 101}))
    assert adapter.resources_released() is False


@pytest.mark.parametrize("configured", [None, 7])
def test_game_adapter_inherits_global_match_target_when_omitted(tmp_path, configured):
    from docich.config import load_global
    path = tmp_path / "config.toml"
    path.write_text('[retro_corner]\ngames=["nsnake"]\n' + (
        f'target_matches={configured}\n' if configured is not None else ''
    ))
    adapter = GameCornerAdapter(load_global(tmp_path, path), Corner("nsnake", "game", "nsnake"))
    assert adapter.manager.config.target_matches == (configured or 3)


def test_runtime_environment_uses_persisted_target_for_resumed_request(tmp_path, monkeypatch):
    from docich.config import load_global

    path = tmp_path / "config.toml"
    path.write_text('[retro_corner]\ngames=["nsnake"]\ntarget_matches=3\n')
    g = load_global(tmp_path, path)
    adapter = GameCornerAdapter(g, Corner("nsnake", "game", "nsnake", target_matches=3))
    adapter.state_path.parent.mkdir(parents=True, exist_ok=True)
    adapter.state_path.write_text(json.dumps({
        "status": "starting", "rotation_request_id": "req-1", "target_matches": 1,
    }))
    monkeypatch.delenv("DOCICH_TARGET_MATCHES", raising=False)
    monkeypatch.delenv("NSNAKE_MAX_MATCHES", raising=False)
    with adapter.runtime_environment({"request_id": "req-1"}):
        assert os.environ["DOCICH_TARGET_MATCHES"] == "1"
        assert os.environ["NSNAKE_MAX_MATCHES"] == "1"
    assert "DOCICH_TARGET_MATCHES" not in os.environ
    assert "NSNAKE_MAX_MATCHES" not in os.environ

    adapter.target_matches = 1
    adapter.state_path.write_text(json.dumps({
        "status": "active", "rotation_request_id": "req-2", "target_matches": 3,
    }))
    with adapter.runtime_environment({"request_id": "req-2"}):
        assert os.environ["DOCICH_TARGET_MATCHES"] == "3"
        assert os.environ["NSNAKE_MAX_MATCHES"] == "3"


@pytest.mark.parametrize("adapter_name", ["paper", "nethack"])
def test_adapters_without_game_match_target_have_no_runtime_environment_side_effect(adapter_name):
    from docich.corner_adapters import NethackCornerAdapter, PaperCornerAdapter

    adapter_cls = {"paper": PaperCornerAdapter, "nethack": NethackCornerAdapter}[adapter_name]
    adapter = adapter_cls.__new__(adapter_cls)
    with adapter.runtime_environment({"request_id": "no-target"}):
        pass


def test_game_coordinator_unsafe_phase_never_calls_adapter(tmp_path):
    from docich.game_switch import GameSwitchStore
    store = GameSwitchStore(tmp_path)
    store.canonical.load = lambda: ({"phase": "failed"}, False)
    adapter = SimpleNamespace(manager=SimpleNamespace(store=store), run=Mock())
    coordinator = CornerExecutionCoordinator(SimpleNamespace(), clock=lambda: 100, sleep=lambda _: None)
    with pytest.raises(CornerExecutionError):
        coordinator.execute(adapter, {"selected_at": 100})
    adapter.run.assert_not_called()


def test_meriken_env_file_is_scoped_to_adapter_execution(tmp_path, monkeypatch):
    from docich.corner_adapters import MerikenCornerAdapter

    env_file = tmp_path / "soren91.env"
    env_file.write_text(
        "SOREN91_MACOS_AGENT_BASE_URL='http://100.64.0.2:8787'\n"
        "SOREN91_LOCAL_AGENT_TOKEN=secret-token\n"
        "export SOREN91_OCI_TAILSCALE_IP=100.64.0.3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCICH_SOREN91_ENV_FILE", str(env_file))
    for key in MerikenCornerAdapter.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    adapter = MerikenCornerAdapter.__new__(MerikenCornerAdapter)
    with adapter.runtime_environment():
        assert os.environ["SOREN91_MACOS_AGENT_BASE_URL"] == "http://100.64.0.2:8787"
        assert os.environ["SOREN91_LOCAL_AGENT_TOKEN"] == "secret-token"
        assert os.environ["SOREN91_OCI_TAILSCALE_IP"] == "100.64.0.3"
    for key in MerikenCornerAdapter.ENV_KEYS:
        assert key not in os.environ


def test_paper_rotation_replays_same_request_and_remains_non_live(tmp_path, monkeypatch):
    from test_paper_corner import setup, manager, FakeResult
    g = setup(tmp_path)
    now = [100.0]
    mgr = manager(g, clock=lambda: now[0], overlay=lambda *a, **k: None,
                  speech=lambda *a, **k: None, stream_paper=lambda: None)
    mgr._ensure_fallback_script = lambda state: None
    mgr._next_narration_item = lambda state: ("exhausted", None)
    mgr._wait_for_speech = lambda state: True
    mgr._refresh_paper_flag = lambda state: None
    mgr._clear_paper_flag = lambda: None
    mgr._spawn_improve_once = lambda state: None
    current = [None]
    calls = []
    def start(game, request_id=None):
        calls.append(request_id)
        if len(calls) == 1:
            return FakeResult("queued")
        current[0] = game
        return FakeResult()
    mgr.coordinator.start = start
    mgr.coordinator.stop = lambda **kw: FakeResult()
    mgr._active_game = lambda: current[0]
    mgr.store.canonical.load = lambda: ({"phase": "ready", "active": {
        "game": current[0], "runtime_id": "g1-paper"}}, False)
    assert mgr.run_rotation("paper-request") == "queued"
    assert mgr._read_state()["live_eligible"] is False
    assert mgr.run_rotation("paper-request") == "completed"
    assert calls == ["paper-request", "paper-request"]
    assert mgr.run_rotation("paper-request") == "completed"
    assert len(calls) == 2
