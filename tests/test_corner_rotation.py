"""Offline contract tests; all execution and clocks injected, no paid/runtime IO."""
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.config import load_global
from docich.corner_catalog import Corner, load_catalog, CornerCatalogError
from docich.corner_rotation import CornerRotationManager, RotationError, DAY


class Adapter:
    def __init__(self, g, corner):
        self.corner = corner
        self.available = True
        self.states = []

    def eligible(self):
        return self.available

    def observations(self):
        return self.states


class Executor:
    def __init__(self):
        self.calls = []
        self.result = "completed"

    def execute(self, adapter, request):
        self.calls.append(dict(request))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[corner_rotation]\nenabled=true\n[paths]\nstate_dir="run"\n')
    g = load_global(tmp_path, path)
    clock = [1000000.0]
    catalog = [Corner("retro", "game", "nsnake"), Corner("paper", "paper", "paper-view"),
               Corner("meriken", "meriken", "soren91")]
    executor = Executor()
    def make(items=None):
        return CornerRotationManager(g, clock=lambda: clock[0], seed="test-seed", catalog=catalog if items is None else items,
                                     adapter_factory=Adapter, executor=executor)
    return g, clock, catalog, executor, make


def state(manager):
    return json.loads(manager.path.read_text())


def test_flat_live_catalog_and_financial_boundary():
    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    catalog = load_catalog(g)
    assert {c.id for c in catalog} == {"ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console",
                                     "nethack", "hanjuku-hero", "paper", "meriken"}
    assert all(c.live_eligible is False for c in catalog)
    assert not next(c for c in catalog if c.id == "hanjuku-hero").enabled
    assert not any(c.id == "retro" for c in catalog)


def test_real_adapters_derive_live_eligible_count(tmp_path, monkeypatch):
    from docich.retro_corner import RetroCornerManager
    g = replace(load_global(ROOT, ROOT / "config/docich.soren-live.toml"), state_dir=tmp_path)
    monkeypatch.setattr(RetroCornerManager, "_executable_exists", staticmethod(lambda _: True))
    manager = CornerRotationManager(g)
    eligible, excluded = manager._eligible()
    assert len(eligible) == 8
    assert {"paper", "meriken", "nsnake", "nethack"} <= set(eligible)
    assert excluded == {"hanjuku-hero": "disabled-or-paused"}


def test_adapter_config_disables_paper_and_meriken_from_effective_n(tmp_path, monkeypatch):
    from docich.paper_corner_fast import FastPaperCornerManager
    from docich.retro_corner import RetroCornerManager

    monkeypatch.setattr(
        RetroCornerManager,
        "_executable_exists",
        staticmethod(lambda _name: True),
    )
    monkeypatch.setattr(FastPaperCornerManager, "_require_outputs", lambda _self: None)

    config = replace(
        load_global(ROOT, ROOT / "config/docich.soren-live.toml"),
        state_dir=tmp_path,
    )
    manager = CornerRotationManager(config)

    paper = manager.adapters["paper"]
    meriken = manager.adapters["meriken"]
    paper.manager.enabled = False
    meriken.manager.config = replace(meriken.manager.config, enabled=False)

    eligible, excluded = manager._eligible()

    assert "paper" not in eligible
    assert "meriken" not in eligible
    assert excluded["paper"] == "adapter-unavailable"
    assert excluded["meriken"] == "adapter-unavailable"


def test_manual_meriken_start_scopes_runtime_environment(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import os
    from docich import corner_rotation

    config = replace(
        load_global(ROOT, ROOT / "config/docich.soren-live.toml"),
        state_dir=tmp_path,
    )
    env_file = tmp_path / "soren91.env"
    env_file.write_text(
        "SOREN91_MACOS_AGENT_BASE_URL=http://100.64.0.2:8787\n"
        "SOREN91_LOCAL_AGENT_TOKEN=manual-test-token\n"
        "SOREN91_OCI_TAILSCALE_IP=100.64.0.3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCICH_SOREN91_ENV_FILE", str(env_file))
    for key in (
        "SOREN91_MACOS_AGENT_BASE_URL",
        "SOREN91_LOCAL_AGENT_TOKEN",
        "SOREN91_OCI_TAILSCALE_IP",
    ):
        monkeypatch.delenv(key, raising=False)

    captured = {}

    class Executor:
        def execute(self, adapter, request):
            scope_factory = getattr(adapter, "runtime_environment", None)
            scope = scope_factory() if callable(scope_factory) else nullcontext()
            with scope:
                captured["inside"] = os.environ.get("SOREN91_LOCAL_AGENT_TOKEN")
            captured["after"] = os.environ.get("SOREN91_LOCAL_AGENT_TOKEN")
            return "completed"

    original_manager = corner_rotation.CornerRotationManager
    holder = {}

    def build_manager(g):
        if "manager" not in holder:
            holder["manager"] = original_manager(
                g,
                clock=lambda: 1000000.0,
                seed="manual-meriken-env",
                executor=Executor(),
            )
            holder["manager"]._eligible = lambda: (["meriken"], {})
        return holder["manager"]

    monkeypatch.setattr(corner_rotation, "CornerRotationManager", build_manager)
    manager = holder.setdefault(
        "owner", build_manager(config).adapters["meriken"].manager
    )

    result = corner_rotation.run_manual(config, manager, ["soren91"])

    assert result.status == "completed"
    assert captured == {"inside": "manual-test-token", "after": None}


def test_nethack_legacy_state_is_visible_to_unified_rotation(tmp_path, monkeypatch):
    from docich.retro_corner import RetroCornerManager

    monkeypatch.setattr(RetroCornerManager, "_executable_exists", staticmethod(lambda _: True))
    config = replace(
        load_global(ROOT, ROOT / "config/docich.soren-live.toml"),
        state_dir=tmp_path,
    )
    manager = CornerRotationManager(config, seed="nethack-state")
    assert manager.adapters["nethack"].state_path.name == "nethack_corner.json"

    (tmp_path / "nethack_corner.json").write_text(
        json.dumps({"status": "active", "started_at": 100.0}),
        encoding="utf-8",
    )

    result = manager.tick()

    assert result["reason"] == "other-corner-needs-finish-or-recovery"


def test_removed_corner_keeps_improvement_release_gate(setup):
    _, clock, catalog, executor, make = setup
    manager = make()
    manager.tick()
    (manager.g.state_dir / "retro_corner.json").write_text(
        json.dumps({
            "status": "completed",
            "game": "nsnake",
            "completed_at": clock[0],
            "improve_job": {"spawned": True},
        }),
        encoding="utf-8",
    )
    clock[0] += DAY
    remaining = [corner for corner in catalog if corner.id != "retro"]

    result = make(remaining).tick()

    assert result["reason"] == "other-corner-needs-finish-or-recovery"
    assert len(executor.calls) == 1


def test_initial_migration_observes_removed_legacy_corner(setup):
    g, clock, catalog, executor, make = setup
    state_dir = Path(g.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "retro_corner.json").write_text(json.dumps({
        "schema_version": 1,
        "status": "completed",
        "game": "nsnake",
        "completed_at": clock[0],
        "improve_job": {"spawned": True},
    }))
    remaining = [corner for corner in catalog if corner.id != "retro"]

    result = make(remaining).tick()

    assert result["reason"] == "other-corner-needs-finish-or-recovery"
    assert not executor.calls


@pytest.mark.parametrize("entry", [
    'id="paper",adapter="paper",game="paper-view",live_eligible=true',
    'id="paper",adapter="paper",game="paper-view",enabled="yes"',
    'id="x",adapter="unknown",game="x"',
])
def test_catalog_rejects_unsafe_or_unknown_entries(tmp_path, entry):
    path = tmp_path / "config.toml"
    path.write_text('[corner_rotation]\nenabled=true\ncorners=[{' + entry + '}]')
    with pytest.raises(CornerCatalogError):
        load_catalog(load_global(tmp_path, path))


def test_pause_marker_changes_N_without_forgetting_history(setup):
    g, clock, _, executor, make = setup
    make().tick()
    selected = executor.calls[0]["corner"]
    paused = g.state_dir / "corners" / f"{selected}.paused"
    paused.parent.mkdir()
    paused.touch()
    clock[0] += DAY / 2
    manager = make()
    manager.tick()
    assert state(manager)["interval_seconds"] == DAY / 2
    paused.unlink()
    assert any(r["corner"] == selected for r in state(make())["history"])


def test_interval_rolling_exclusion_restart_and_no_registration_bias(setup):
    _, clock, catalog, executor, make = setup
    for index in range(3):
        manager = make(list(reversed(catalog)) if index % 2 else catalog)
        assert manager.tick()["status"] == "ready"
        saved = state(manager)
        assert saved["interval_seconds"] == DAY / 3
        assert saved["next_due_at"] == clock[0] + DAY / 3
        clock[0] += DAY / 3
    assert len({call["corner"] for call in executor.calls}) == 3
    # Exactly 24h after first run is eligible again.
    make().tick()
    assert executor.calls[3]["corner"] == executor.calls[0]["corner"]


def test_disabled_paused_unavailable_do_not_inflate_denominator(setup):
    _, _, catalog, executor, make = setup
    manager = make([replace(catalog[0], enabled=False), replace(catalog[1], paused=True), catalog[2]])
    assert manager.tick()["corner"] == "meriken"
    assert state(manager)["interval_seconds"] == DAY
    assert len(executor.calls) == 1


def test_unavailable_and_empty_candidates_wait_without_duplicate(setup):
    _, clock, _, executor, make = setup
    manager = make()
    for adapter in manager.adapters.values():
        adapter.available = False
    assert manager.tick()["reason"] == "no-enabled-corner"
    assert not executor.calls
    saved = state(manager)
    saved["history"] = [dict(corner=c, at=clock[0], source="manual") for c in manager.adapters]
    manager.save(saved)
    assert make().tick()["reason"] == "all-corners-cooling-down"


def test_queue_replay_preserves_identity_across_restart_and_catalog_change(setup):
    _, clock, catalog, executor, make = setup
    executor.result = "queued"
    manager = make()
    assert manager.tick()["status"] == "waiting"
    first = executor.calls[0]
    clock[0] += 60
    # Disabling after dispatch must still allow this execution's safe restore.
    manager = make([replace(c, enabled=False) for c in catalog])
    manager.adapters[first["corner"]].states = [{"status": "starting", "rotation_request_id": first["request_id"]}]
    executor.result = "completed"
    assert manager.tick()["status"] == "ready"
    assert executor.calls[1]["request_id"] == first["request_id"]
    assert executor.calls[1]["corner"] == first["corner"]
    assert state(manager)["history"][-1]["at"] == clock[0]


def test_slot_miss_coalesces_and_does_not_catch_up_in_burst(setup):
    _, clock, _, executor, make = setup
    make().tick()
    clock[0] += 20 * 3600
    manager = make()
    manager.tick()
    assert len(executor.calls) == 2
    assert state(manager)["next_due_at"] == clock[0] + DAY / 3
    assert manager.tick()["reason"] == "not-due"


def test_clock_regression_and_large_forward_jump_quarantine(setup):
    _, clock, _, executor, make = setup
    make().tick()
    clock[0] -= 1
    assert make().tick()["reason"] == "clock-regressed"
    clock[0] += 2 * DAY
    assert make().tick()["reason"] == "clock-gap-quarantine"
    clock[0] += DAY - 1
    assert make().tick()["reason"] == "clock-gap-quarantine"
    clock[0] += 1
    assert make().tick()["status"] == "ready"
    assert len(executor.calls) == 2


def test_manual_running_blocks_and_completed_usage_imports_cooldown(setup):
    _, clock, _, executor, make = setup
    manager = make()
    manager.adapters["paper"].states = [{"status": "active", "started_at": clock[0]}]
    assert manager.tick()["reason"] == "other-corner-needs-finish-or-recovery"
    manager.adapters["paper"].states[0].update(status="completed", completed_at=clock[0])
    manager.tick()
    assert executor.calls[0]["corner"] != "paper"


@pytest.mark.parametrize("status", ["failed", "restoring", "recovery_required", "starting"])
def test_unowned_execution_blocks_even_if_file_lock_is_free(setup, status):
    _, clock, _, executor, make = setup
    manager = make()
    manager.adapters["meriken"].states = [{"status": status, "started_at": clock[0]}]
    assert manager.tick()["status"] == "waiting"
    assert not executor.calls


def test_failure_does_not_drop_reserved_request_or_expose_exception(setup):
    _, _, _, executor, make = setup
    executor.result = RuntimeError("sensitive provider output")
    manager = make()
    with pytest.raises(RuntimeError):
        manager.tick()
    assert state(manager)["pending"]["request_id"] == executor.calls[0]["request_id"]
    assert "sensitive" not in manager.path.read_text()
    executor.result = "completed"
    assert make().tick()["status"] == "recovery_required"
    assert len(executor.calls) == 1


def test_running_state_without_request_owner_fails_closed(setup):
    _, clock, _, executor, make = setup
    manager = make()
    saved = manager.load(clock[0])
    saved["status"] = "running"
    manager.save(saved)

    with pytest.raises(RotationError, match="missing request owner"):
        manager.tick()
    assert not executor.calls


def test_corrupt_state_never_becomes_fresh_schedule(setup):
    _, _, _, executor, make = setup
    manager = make()
    manager.path.parent.mkdir(parents=True)
    manager.path.write_text('{"schema_version":99}')
    with pytest.raises(RotationError):
        manager.tick()
    assert not executor.calls


def test_missing_ledger_with_existing_rotation_execution_is_not_migrated(setup):
    _, clock, _, executor, make = setup
    manager = make()
    manager.adapters["paper"].states = [{"status": "completed", "rotation_request_id": "existing"}]
    with pytest.raises(RotationError, match="ledger missing"):
        manager.tick()
    assert not executor.calls


def test_migrate_history_and_reject_pending_legacy(setup):
    g, clock, _, executor, make = setup
    g.state_dir.mkdir(parents=True)
    legacy = g.state_dir / "retro_corner.json"
    history = [{"game": "nsnake", "selected_at": clock[0]}]
    legacy.write_text(json.dumps({"schema_version": 1, "status": "completed", "rotation": {"selection_history": history}}))
    manager = make()
    manager.tick()
    assert executor.calls[0]["corner"] != "retro"
    assert any(r["corner"] == "retro" for r in state(manager)["history"])


def test_seeded_order_is_independent_of_catalog_order(setup):
    _, clock, catalog, executor, make = setup
    manager = make()
    one = manager.load(clock[0])
    manager.tick()
    first = executor.calls[0]["corner"]
    manager.save(one)
    make(list(reversed(catalog))).tick()
    assert executor.calls[1]["corner"] == first


def test_single_flight_does_not_start_second_adapter(setup):
    _, _, _, executor, make = setup
    manager = make()
    with manager.locked():
        assert make().tick()["reason"] == "already-running"
    assert not executor.calls


def test_disabled_reserved_but_not_started_corner_stays_queued(setup):
    _, _, catalog, executor, make = setup
    executor.result = "queued"  # program lock wait, before adapter state exists
    make().tick()
    assert make([replace(c, enabled=False) for c in catalog]).tick()["reason"] == "selected-corner-disabled-or-paused"
    assert len(executor.calls) == 1


def test_late_actual_start_reanchors_next_slot(setup):
    _, clock, _, executor, make = setup
    executor.result = "queued"
    make().tick()
    first = executor.calls[0]
    clock[0] += 3600
    manager = make()
    manager.adapters[first["corner"]].states = [{"status": "active", "rotation_request_id": first["request_id"],
                                                "started_at": clock[0]}]
    executor.result = "completed"
    manager.tick()
    assert state(manager)["next_due_at"] == clock[0] + DAY / 3


def test_manual_queue_replay_uses_same_durable_request(setup, monkeypatch):
    from docich import corner_rotation
    g, _, _, executor, make = setup
    monkeypatch.setattr(corner_rotation, "CornerRotationManager", lambda _: make())
    manager = SimpleNamespace(path=g.state_dir / "paper_corner_manual.json")
    executor.result = "queued"
    assert corner_rotation.run_manual(g, manager, ["paper-view"]) == "queued"
    first = executor.calls[0]["request_id"]
    assert make().tick()["reason"] == "manual-request-needs-resume-or-recovery"
    executor.result = "completed"
    assert corner_rotation.run_manual(g, manager, ["paper-view"]) == "completed"
    assert executor.calls[1]["request_id"] == first
    assert state(make()).get("manual_pending") is None


def test_stop_reaches_owner_when_scheduler_lock_is_busy(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from docich import corner_rotation

    class BusyRotation:
        @contextmanager
        def locked(self):
            yield False

    monkeypatch.setattr(corner_rotation, "CornerRotationManager", lambda _g: BusyRotation())
    manager = SimpleNamespace(path=tmp_path / "paper_corner.json")
    manager.path.write_text(json.dumps({"status": "active"}))
    assert corner_rotation.stop_manual(
        SimpleNamespace(state_dir=tmp_path),
        manager,
        lambda: "stopped",
        busy_callback=lambda: "owner-stopped",
    ) == "owner-stopped"


def test_stop_queues_durable_request_while_owner_is_starting(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from docich import corner_rotation

    class BusyRotation:
        @contextmanager
        def locked(self):
            yield False

    monkeypatch.setattr(corner_rotation, "CornerRotationManager", lambda _g: BusyRotation())
    manager = SimpleNamespace(path=tmp_path / "paper_corner.json")
    manager.path.write_text(json.dumps({"status": "starting"}))
    g = SimpleNamespace(state_dir=tmp_path)
    assert corner_rotation.stop_manual(
        g,
        manager,
        lambda: "stopped",
        busy_callback=lambda: "owner-stopped",
    ) == "queued"
    marker = tmp_path / "corner-stop-requests" / manager.path.name
    marker_state = json.loads(marker.read_text())
    assert marker_state["state_file"] == manager.path.name
    assert isinstance(marker_state["requested_at"], (int, float))
