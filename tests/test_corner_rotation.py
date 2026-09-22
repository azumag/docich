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
from docich.corner_rotation import CornerRotationManager, RotationError, DAY, ERROR_KINDS


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
    assert next(c for c in catalog if c.id == "hanjuku-hero").enabled
    assert not any(c.id == "retro" for c in catalog)
    assert next(c for c in catalog if c.id == "nsnake").target_matches == 1
    assert all(c.target_matches is None for c in catalog if c.id != "nsnake")


def test_production_profile_marks_common_rotation_enabled_for_all_nine_corners():
    from docich.corner_catalog import rotation_config

    g = load_global(ROOT, ROOT / "config/docich.soren-live.toml")
    raw = rotation_config(g)
    assert raw["enabled"] is True
    catalog = load_catalog(g)
    assert len(catalog) == 9
    paper = next(c for c in catalog if c.id == "paper")
    assert paper.enabled is True
    assert paper.live_eligible is False
    assert {c.id for c in catalog} >= {
        "ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console",
        "hanjuku-hero", "nethack", "paper", "meriken",
    }


def test_common_rotation_tick_ignores_the_legacy_start_hour(tmp_path, monkeypatch):
    """Enabled common rotation owns selection; the legacy daily start_hour and
    mode must not make the legacy entry tick a no-op outside that hour."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    from docich.retro_corner import RetroCornerManager, load_retro_corner_config

    path = tmp_path / "config.toml"
    path.write_text(
        '[paths]\nstate_dir="run"\n'
        '[retro_corner]\nenabled=true\nmode="daily"\nstart_hour=19\n'
        'duration_minutes=20\ntimezone="Asia/Tokyo"\ngames=["nsnake"]\n'
        '[corner_rotation]\nenabled=true\n'
        'corners=[{id="nsnake",adapter="game",game="nsnake"}]\n'
    )
    g = load_global(tmp_path, path)
    calls = []

    class FakeRotation:
        def __init__(self, _g):
            pass

        def tick(self):
            calls.append(True)
            return {"status": "ready"}

    monkeypatch.setattr("docich.corner_rotation.CornerRotationManager", FakeRotation)
    manager = RetroCornerManager(
        g,
        config=load_retro_corner_config(g),
        coordinator=object(),
        now=lambda: dt.datetime(2026, 9, 22, 3, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        sleep=lambda seconds: None,
        active_game_reader=lambda: None,
        ensure_runtime=lambda: None,
    )
    result = manager.tick()
    assert calls == [True]
    assert result.status == "ready"


@pytest.mark.parametrize("value", ["0", "101", "-1", "true", '"1"', "1.5", "[]"])
def test_catalog_rejects_invalid_match_targets(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text('[corner_rotation]\ncorners=[{id="nsnake",adapter="game",game="nsnake",target_matches=' + value + '}]')
    with pytest.raises(CornerCatalogError, match="target_matches"):
        load_catalog(load_global(tmp_path, path))


@pytest.mark.parametrize("adapter,game", [("paper", "paper-view"), ("meriken", "soren91"), ("nethack", "nethack")])
def test_non_match_adapters_reject_match_targets(tmp_path, adapter, game):
    path = tmp_path / "config.toml"
    path.write_text(f'[corner_rotation]\ncorners=[{{id="{game}",adapter="{adapter}",game="{game}",target_matches=1}}]')
    with pytest.raises(CornerCatalogError, match="target_matches"):
        load_catalog(load_global(tmp_path, path))


def test_match_targets_do_not_weight_eligible_count_or_interval(setup):
    _, _, catalog, _, make = setup
    manager = make([replace(catalog[0], target_matches=100), *catalog[1:]])
    assert manager.tick()["status"] == "ready"
    assert len(state(manager)["eligible"]) == 3
    assert state(manager)["interval_seconds"] == DAY / 3
    manager = make([replace(catalog[0], target_matches=1), *catalog[1:]])
    assert manager.tick()["reason"] == "not-due"
    assert state(manager)["interval_seconds"] == DAY / 3


def test_real_adapters_derive_live_eligible_count(tmp_path, monkeypatch):
    from docich.retro_corner import RetroCornerManager
    g = replace(load_global(ROOT, ROOT / "config/docich.soren-live.toml"), state_dir=tmp_path)
    monkeypatch.setattr(RetroCornerManager, "_executable_exists", staticmethod(lambda _: True))
    monkeypatch.setattr("docich.adapters.retroarch.resolve_rom", lambda *_: ROOT / "vm-only.sfc")
    monkeypatch.setattr("docich.adapters.retroarch.resolve_core", lambda *_: "/vm-only/core.so")
    manager = CornerRotationManager(g)
    eligible, excluded = manager._eligible()
    assert len(eligible) == 9
    assert {"paper", "meriken", "nsnake", "nethack", "hanjuku-hero"} <= set(eligible)
    assert excluded == {}


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


def test_terminal_failed_manual_paper_state_does_not_pin_rotation(tmp_path, monkeypatch):
    from docich import corner_terminal
    from docich.corner_adapters import RetiredCornerObserver

    config = load_global(tmp_path)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    (config.state_dir / "paper_corner_manual.json").write_text(json.dumps({
        "status": "failed",
        "game": None,
        "previous_game": "sorengame",
        "completed_at": 100.0,
        "last_error": "restore failed after the view was already gone",
    }))

    class Canonical:
        def load(self):
            return ({"phase": "ready", "active": {"game": "sorengame"}}, False)

    class Store:
        canonical = Canonical()

    monkeypatch.setattr(corner_terminal, "GameSwitchStore", lambda _path: Store())
    observer = RetiredCornerObserver(
        config,
        {"id": "retired-paper", "adapter": "paper", "game": "paper-view"},
    )

    [state] = list(observer.observations())
    assert state["status"] == "completed"

    manager = CornerRotationManager(
        config,
        catalog=[Corner("retro", "game", "nsnake")],
        adapter_factory=Adapter,
        executor=Executor(),
    )
    observed = manager.load(1000.0)
    observed["known_corners"] = {
        "retired-paper": {"id": "retired-paper", "adapter": "paper", "game": "paper-view"}
    }
    assert manager._observe(observed, 1000.0) is False


@pytest.mark.parametrize("retired", [False, True])
@pytest.mark.parametrize("game_field", [{}, {"game": None}, {"game": "paper-view"}])
@pytest.mark.parametrize("case", [
    "restored", "mismatch", "draining", "failed", "missing", "corrupt",
    "recovery-required", "error-code", "no-completion", "no-previous",
    "idle-restored", "idle-missing",
])
def test_paper_terminal_contract_through_rotation_tick(tmp_path, monkeypatch, retired, game_field, case):
    from docich import corner_terminal
    from docich.corner_adapters import PaperCornerAdapter

    path = tmp_path / "config.toml"
    path.write_text('[corner_rotation]\nenabled=true\n[paths]\nstate_dir="run"\n')
    g = load_global(tmp_path, path)
    g.state_dir.mkdir(parents=True)
    paper_state = dict(status="failed", previous_game="sorengame", completed_at=100,
                       recovery_required=False, **game_field)
    canonical = {"phase": "ready", "active": {"game": "sorengame"}}
    if case == "mismatch":
        canonical["active"]["game"] = "nsnake"
    elif case in {"draining", "failed"}:
        canonical["phase"] = case
    elif case == "recovery-required":
        paper_state["recovery_required"] = True
    elif case == "error-code":
        paper_state["last_error_code"] = "recovery_required"
    elif case == "no-completion":
        paper_state.pop("completed_at")
    elif case == "no-previous":
        paper_state.pop("previous_game")
        canonical = {"phase": "idle", "active": None}
    elif case in {"idle-restored", "idle-missing"}:
        paper_state["previous_game"] = None
        canonical = {"phase": "idle", "active": None}
    state_path = g.state_dir / "paper_corner_manual.json"
    original = json.dumps(paper_state)
    state_path.write_text(original)

    def load():
        if case == "corrupt":
            raise ValueError("unreadable canonical")
        return canonical, case in {"missing", "idle-missing"}

    monkeypatch.setattr(corner_terminal, "GameSwitchStore", lambda _: SimpleNamespace(canonical=SimpleNamespace(load=load)))
    paper = PaperCornerAdapter.__new__(PaperCornerAdapter)
    paper.g = g
    paper.corner = Corner("paper", "paper", "paper-view")
    paper.manager = SimpleNamespace(path=g.state_dir / "paper_corner.json")
    paper.eligible = lambda: True
    catalog = [Corner("nsnake", "game", "nsnake")]
    if not retired:
        catalog.append(paper.corner)
    executor = Executor()
    manager = CornerRotationManager(
        g, clock=lambda: 1000, catalog=catalog, executor=executor,
        adapter_factory=lambda g, c: paper if c.adapter == "paper" else Adapter(g, c),
    )
    result = manager.tick()
    if case in {"restored", "idle-restored"}:
        assert result["status"] == "ready"
        assert executor.calls[0]["corner"] == "nsnake"
    else:
        assert result["reason"] == "other-corner-needs-finish-or-recovery"
        assert not executor.calls
    assert state_path.read_text() == original


def test_unstable_failed_manual_paper_state_still_blocks_rotation(tmp_path, monkeypatch):
    from docich import corner_terminal
    from docich.corner_adapters import RetiredCornerObserver

    config = load_global(tmp_path)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    (config.state_dir / "paper_corner_manual.json").write_text(json.dumps({
        "status": "failed",
        "game": None,
        "previous_game": "sorengame",
        "completed_at": 100.0,
    }))

    class Canonical:
        def load(self):
            return ({"phase": "draining", "active": {"game": "sorengame"}}, False)

    class Store:
        canonical = Canonical()

    monkeypatch.setattr(corner_terminal, "GameSwitchStore", lambda _path: Store())
    observer = RetiredCornerObserver(
        config,
        {"id": "retired-paper", "adapter": "paper", "game": "paper-view"},
    )

    [state] = list(observer.observations())
    assert state["status"] == "failed"

    manager = CornerRotationManager(
        config,
        catalog=[Corner("retro", "game", "nsnake")],
        adapter_factory=Adapter,
        executor=Executor(),
    )
    observed = manager.load(1000.0)
    observed["known_corners"] = {
        "retired-paper": {"id": "retired-paper", "adapter": "paper", "game": "paper-view"}
    }
    assert manager._observe(observed, 1000.0) is True


def test_recovered_paper_owner_allows_real_coordinator_slot_dispatch(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import time
    from docich import corner_boundary, corner_terminal
    from docich.corner_adapters import CornerExecutionCoordinator, PaperCornerAdapter

    path = tmp_path / "config.toml"
    path.write_text('[corner_rotation]\nenabled=true\n[paths]\nstate_dir="run"\n')
    g = load_global(tmp_path, path)
    g.state_dir.mkdir(parents=True)
    paper_path = g.state_dir / "paper_corner_manual.json"
    original = json.dumps({"status": "failed", "game": None, "previous_game": "sorengame",
                           "completed_at": time.time() - 10, "recovery_required": False})
    paper_path.write_text(original)
    registry = tmp_path / "tmp/state" / corner_boundary.REGISTRY_FILE
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"owner_state": str(paper_path)}))
    store = SimpleNamespace(
        canonical=SimpleNamespace(load=lambda: ({"phase": "ready", "active": {"game": "sorengame"}}, False)),
        lock=lambda **_: nullcontext(),
    )
    monkeypatch.setattr(corner_terminal, "GameSwitchStore", lambda _: store)
    monkeypatch.setattr(corner_boundary, "resolve_soren_root", lambda _: tmp_path)
    calls = []
    paper = PaperCornerAdapter.__new__(PaperCornerAdapter)
    paper.g, paper.corner = g, Corner("paper", "paper", "paper-view")
    paper.manager = SimpleNamespace(path=g.state_dir / "paper_corner.json")
    paper.eligible = lambda: True
    game = Adapter(g, Corner("nsnake", "game", "nsnake", target_matches=1))
    game.manager = SimpleNamespace(store=store)
    game.state_path = g.state_dir / "retro_corner.json"
    game.run = lambda request: calls.append(request) or "completed"
    executor = CornerExecutionCoordinator(g, clock=time.time, sleep=lambda _: None, wait_seconds=-1)
    manager = CornerRotationManager(g, catalog=[game.corner, paper.corner], executor=executor,
                                    adapter_factory=lambda _, c: paper if c.adapter == "paper" else game)
    assert manager.tick()["status"] == "ready"
    assert len(calls) == 1
    assert calls[0]["corner"] == "nsnake"
    assert json.loads(registry.read_text())["owner_state"] == str(game.state_path)
    assert paper_path.read_text() == original


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


def test_removed_corner_releases_after_its_own_terminal_improvement_failure(setup):
    _, clock, catalog, executor, make = setup
    manager = make()
    manager.tick()
    state_dir = manager.g.state_dir
    (state_dir / "retro_corner.json").write_text(
        json.dumps({
            "status": "completed",
            "game": "nsnake",
            "completed_at": clock[0],
            "improve_job": {"spawned": True},
        }),
        encoding="utf-8",
    )
    (state_dir / "corner_improve_nsnake.json").write_text(
        json.dumps({"status": "failed", "started_at": clock[0] + 1, "completed_at": clock[0] + 2}),
        encoding="utf-8",
    )
    clock[0] += DAY
    remaining = [corner for corner in catalog if corner.id != "retro"]

    result = make(remaining).tick()

    assert result["status"] == "ready"
    assert len(executor.calls) == 2


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


def test_all_cooling_corners_reanchor_next_due_to_earliest_expiry(setup):
    g, clock, catalog, executor, make = setup
    manager = make()
    now = clock[0]
    manager.save({
        "schema_version": 1,
        "seed": "test-seed",
        "slot": 4,
        "last_seen_at": now - 1,
        "last_slot_at": now - 2 * DAY,
        "next_due_at": now - 60,
        "interval_seconds": DAY / len(catalog),
        "history": [
            {"corner": corner.id, "at": now - 3600, "source": "test"}
            for corner in catalog
        ],
        "pending": None,
        "status": "waiting",
        "known_corners": {},
    })

    result = manager.tick()

    assert result["reason"] == "all-corners-cooling-down"
    assert state(manager)["next_due_at"] == now - 3600 + DAY
    assert not executor.calls


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


def _policy_config(root, *, mode=None, cooldown_hours=None):
    root.mkdir(parents=True, exist_ok=True)
    lines = ["[corner_rotation]", "enabled=true"]
    if mode is not None:
        lines.append(f'schedule_mode="{mode}"')
    if cooldown_hours is not None:
        lines.append(f"cooldown_hours={cooldown_hours}")
    path = root / "config.toml"
    path.write_text("\n".join(lines) + '\n[paths]\nstate_dir="run"\n')
    return load_global(root, path)


def _policy_manager(g, clock, executor):
    catalog = [Corner("retro", "game", "nsnake"), Corner("paper", "paper", "paper-view"),
               Corner("meriken", "meriken", "soren91")]
    return CornerRotationManager(g, clock=lambda: clock[0], seed="queue-seed",
                                 catalog=catalog, adapter_factory=Adapter, executor=executor)


def test_queue_mode_dispatches_back_to_back_without_interval(tmp_path):
    g = _policy_config(tmp_path, mode="queue")
    clock = [1000000.0]
    executor = Executor()
    manager = _policy_manager(g, clock, executor)

    picks = [manager.tick()["corner"] for _ in range(3)]

    assert len(executor.calls) == 3
    assert len(set(picks)) == 3
    assert state(manager)["interval_seconds"] is None
    assert state(manager)["next_due_at"] == clock[0]


def test_queue_mode_uses_configured_cooldown_and_reanchors_when_empty(tmp_path):
    g = _policy_config(tmp_path, mode="queue", cooldown_hours=1)
    clock = [1000000.0]
    executor = Executor()
    manager = _policy_manager(g, clock, executor)

    picks = [manager.tick()["corner"] for _ in range(3)]
    blocked = manager.tick()

    assert blocked["reason"] == "all-corners-cooling-down"
    assert state(manager)["next_due_at"] == clock[0] + 3600

    clock[0] += 3601
    again = manager.tick()
    assert again["corner"] in picks
    assert len(executor.calls) == 4


@pytest.mark.parametrize("body", [
    'schedule_mode="bogus"',
    "cooldown_hours=0",
    "cooldown_hours=-1",
    'cooldown_hours="24"',
    "cooldown_hours=true",
])
def test_invalid_dispatch_policy_is_rejected(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(f'[corner_rotation]\nenabled=true\n{body}\n[paths]\nstate_dir="run"\n')
    with pytest.raises(CornerCatalogError):
        load_catalog(load_global(tmp_path, path))


def test_queue_mode_does_not_wait_for_improvement_jobs(tmp_path):
    from docich.corner_adapters import RetiredCornerObserver

    record = {"id": "nsnake", "adapter": "game", "game": "nsnake"}

    def observer_for(mode):
        root = tmp_path / mode
        g = _policy_config(root, mode=mode)
        state_dir = g.state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "retro_corner.json").write_text(json.dumps({
            "status": "completed", "game": "nsnake",
            "started_at": 1.0, "completed_at": 2.0,
            "improve_job": {"spawned": True, "date": "2026-09-10"},
        }), encoding="utf-8")
        return RetiredCornerObserver(g, record)

    # interval mode keeps the fail-closed improvement wait
    assert observer_for("interval").resources_released() is False
    # queue mode fires the next corner without waiting for the improvement job
    assert observer_for("queue").resources_released() is True


# --- operator-gated latch recovery (#986) ----------------------------------


def _latch(manager, executor, clock, error=None):
    """Drive tick() into the durable recovery_required latch."""
    executor.result = error if error is not None else RuntimeError("sensitive provider output")
    with pytest.raises(RuntimeError):
        manager.tick()
    executor.result = "completed"
    return state(manager)


def test_latch_records_a_fixed_error_kind_and_never_the_exception_text(setup):
    _, clock, _, executor, make = setup
    manager = make()
    latched = _latch(manager, executor, clock)
    assert latched["status"] == "recovery_required"
    assert latched["reason"] == "execution-or-state-unverified"
    assert latched["error_kind"] in ERROR_KINDS
    assert latched["error_kind"] == "unexpected"
    assert "sensitive" not in manager.path.read_text()


@pytest.mark.parametrize("error,expected", [
    (RuntimeError("provider output"), "unexpected"),
    ("execution-error-fixture", "execution-error"),
])
def test_latch_classifies_failures_into_fixed_kinds(setup, error, expected):
    from docich.corner_adapters import CornerExecutionError

    _, clock, _, executor, make = setup
    if isinstance(error, str):
        error = CornerExecutionError(error)
    manager = make()
    executor.result = error
    with pytest.raises(RuntimeError):
        manager.tick()
    latched = state(manager)
    assert latched["status"] == "recovery_required"
    assert latched["error_kind"] == expected
    assert latched["error_kind"] in ERROR_KINDS
    assert str(error) not in manager.path.read_text()


def test_recover_resumes_the_same_reservation_and_never_duplicates(setup):
    _, clock, _, executor, make = setup
    manager = make()
    latched = _latch(manager, executor, clock)
    request_id = latched["pending"]["request_id"]
    corner_id = latched["pending"]["corner"]
    history = [dict(row) for row in latched["history"]]
    calls = len(executor.calls)

    outcome = make().recover()
    assert outcome["status"] == "waiting"
    assert outcome["reason"] == "execution-pending"
    assert outcome["corner"] == corner_id

    resumed = state(make())
    assert resumed["status"] == "waiting"
    assert resumed["pending"]["request_id"] == request_id
    assert resumed["pending"]["phase"] == "dispatched"
    # request identity, history and cooldown are untouched by recovery
    assert resumed["history"] == history
    assert len(executor.calls) == calls

    result = make().tick()
    assert result == {"status": "ready", "corner": corner_id, "result": "completed"}
    final = state(make())
    assert final["status"] == "ready"
    assert final["pending"] is None
    assert len(executor.calls) == calls + 1
    assert executor.calls[-1]["request_id"] == request_id


def test_recover_commits_an_ended_reservation_without_relaunching(setup):
    _, clock, _, executor, make = setup
    manager = make()
    latched = _latch(manager, executor, clock)
    request_id = latched["pending"]["request_id"]
    corner_id = latched["pending"]["corner"]
    clock[0] += 60

    resumed = make()
    resumed.adapters[corner_id].states = [{
        "status": "completed",
        "rotation_request_id": request_id,
        "started_at": 1000010.0,
        "completed_at": 1000020.0,
    }]
    calls = len(executor.calls)
    outcome = resumed.recover()

    assert outcome == {"status": "ready", "corner": corner_id,
                       "result": "completed", "recovered": True}
    final = state(resumed)
    assert final["status"] == "ready"
    assert final["pending"] is None
    assert final["last_result"]["request_id"] == request_id
    assert final["last_slot_at"] == 1000020.0
    assert any(row["corner"] == corner_id and row["source"] == "completion"
               for row in final["history"])
    # no duplicate corner launch: the adapter already ended this request
    assert len(executor.calls) == calls


def test_recover_never_launches_while_the_pending_corner_is_still_running(setup):
    _, clock, _, executor, make = setup
    manager = make()
    latched = _latch(manager, executor, clock)
    request_id = latched["pending"]["request_id"]
    corner_id = latched["pending"]["corner"]

    busy = make()
    busy.adapters[corner_id].states = [{
        "status": "active",
        "rotation_request_id": request_id,
        "started_at": clock[0] - 5,
    }]
    calls = len(executor.calls)
    with pytest.raises(RotationError, match="corner-level recovery"):
        busy.recover()
    refused = state(busy)
    assert refused["status"] == "recovery_required"
    # a refusal never overwrites the classification of the original latch
    assert refused["error_kind"] == "unexpected"
    assert len(executor.calls) == calls


def test_recover_waits_while_another_corner_holds_the_program_slot(setup):
    _, clock, _, executor, make = setup
    manager = make()
    latched = _latch(manager, executor, clock)
    request_id = latched["pending"]["request_id"]
    corner_id = latched["pending"]["corner"]

    waiting = make()
    other = next(c.id for c in waiting.catalog if c.id != corner_id)
    waiting.adapters[other].states = [{"status": "active", "started_at": clock[0] - 5}]
    calls = len(executor.calls)
    outcome = waiting.recover()

    assert outcome == {"status": "waiting",
                       "reason": "other-corner-needs-finish-or-recovery"}
    kept = state(waiting)
    assert kept["pending"]["request_id"] == request_id
    assert kept["status"] == "waiting"
    assert len(executor.calls) == calls


def test_recover_requires_a_latched_state(setup):
    _, _, _, _, make = setup
    with pytest.raises(RotationError, match="not latched"):
        make().recover()


def test_recover_refuses_a_manual_reservation(setup):
    import uuid as uuid_mod

    _, clock, _, executor, make = setup
    manager = make()
    latched = _latch(manager, executor, clock)
    latched["manual_pending"] = {
        "corner": "paper", "state_file": "paper_corner.json",
        "request_id": str(uuid_mod.uuid4()), "selected_at": clock[0],
    }
    manager.path.write_text(json.dumps(latched))
    with pytest.raises(RotationError, match="manual reservation"):
        make().recover()
    assert state(make())["status"] == "recovery_required"


def test_recover_retries_a_latch_that_has_no_reservation(setup):
    _, clock, _, executor, make = setup
    manager = make()
    assert manager.tick()["status"] == "ready"
    raw = state(manager)
    raw.update(status="recovery_required", reason="execution-or-state-unverified",
               error_kind="unexpected")
    manager.path.write_text(json.dumps(raw))

    assert make().recover() == {"status": "waiting", "reason": "recovery-retry"}
    assert state(make())["status"] == "waiting"


def test_recover_cli_refuses_with_a_fixed_message(tmp_path, capsys):
    from docich import corner_rotation

    state_dir = tmp_path / "run"
    state_dir.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        '[corner_rotation]\nenabled=true\n'
        f'[paths]\nstate_dir="{state_dir}"\n'
    )
    code = corner_rotation.main(["--config", str(config), "recover"])
    captured = capsys.readouterr()
    assert code == 2
    assert "corner rotation recovery refused: rotation state is not latched" in captured.err


def _latch_manual(make, executor, clock, corner="paper"):
    """Turn a latched automatic state into a latched manual reservation."""
    import uuid as uuid_mod

    manager = make()
    latched = _latch(manager, executor, clock)
    latched["pending"] = None
    latched["manual_pending"] = {
        "corner": corner, "state_file": f"{corner}_corner_manual.json",
        "request_id": str(uuid_mod.uuid4()), "selected_at": clock[0],
    }
    manager.path.write_text(json.dumps(latched))
    return manager, state(manager)["manual_pending"]["request_id"]


def test_recover_commits_a_finished_manual_reservation(setup):
    _, clock, _, executor, make = setup
    manager, request_id = _latch_manual(make, executor, clock)
    clock[0] += 60
    manager.adapters["paper"].states = [{
        "status": "completed",
        "rotation_request_id": request_id,
        "started_at": 1000010.0,
        "completed_at": 1000020.0,
    }]
    calls = len(executor.calls)

    outcome = manager.recover()
    assert outcome == {"status": "ready", "corner": "paper",
                       "result": "completed", "recovered": True}
    final = state(manager)
    assert final["status"] == "ready"
    assert final["manual_pending"] is None
    assert final["last_result"]["request_id"] == request_id
    assert any(row["corner"] == "paper" and row["source"] == "manual-completion"
               for row in final["history"])
    assert len(executor.calls) == calls


def test_recover_returns_a_started_less_manual_reservation_to_the_operator(setup):
    _, clock, _, executor, make = setup
    manager, _ = _latch_manual(make, executor, clock)
    calls = len(executor.calls)

    outcome = manager.recover()
    assert outcome["status"] == "waiting"
    assert outcome["reason"] == "manual-request-needs-resume-or-recovery"
    kept = state(manager)
    assert kept["status"] == "waiting"
    # the operator's slot is kept, not deleted
    assert kept["manual_pending"] is not None
    assert kept["pending"] is None
    # and the automatic path stays parked until that manual start resumes it
    assert make().tick()["reason"] == "manual-request-needs-resume-or-recovery"
    assert state(make())["manual_pending"] is not None
    assert len(executor.calls) == calls


def test_recover_refuses_a_running_manual_reservation(setup):
    _, clock, _, executor, make = setup
    manager, request_id = _latch_manual(make, executor, clock)
    manager.adapters["paper"].states = [{
        "status": "active",
        "rotation_request_id": request_id,
        "started_at": clock[0] - 5,
    }]
    with pytest.raises(RotationError, match="corner-level recovery"):
        manager.recover()
    assert state(manager)["status"] == "recovery_required"


def test_recover_refuses_whenever_any_own_observation_is_busy(setup):
    _, clock, _, executor, make = setup
    manager = make()
    latched = _latch(manager, executor, clock)
    request_id = latched["pending"]["request_id"]
    corner_id = latched["pending"]["corner"]
    # a terminal row first, a busy row second: ordering must not decide safety
    resumed = make()
    resumed.adapters[corner_id].states = [
        {"status": "completed", "rotation_request_id": request_id,
         "completed_at": clock[0] + 10},
        {"status": "active", "rotation_request_id": request_id,
         "started_at": clock[0] + 5},
    ]
    calls = len(executor.calls)
    with pytest.raises(RotationError, match="corner-level recovery"):
        resumed.recover()
    assert state(resumed)["status"] == "recovery_required"
    assert len(executor.calls) == calls


def test_recover_cli_fails_when_the_latch_remains(tmp_path, capsys):
    from docich import corner_rotation

    state_dir = tmp_path / "run"
    state_dir.mkdir()
    config = tmp_path / "disabled.toml"
    config.write_text(
        '[corner_rotation]\nenabled=false\n'
        f'[paths]\nstate_dir="{state_dir}"\n'
    )
    (state_dir / "corner_rotation.json").write_text(json.dumps({
        "schema_version": 1, "seed": "s", "slot": 3, "history": [],
        "pending": None, "status": "recovery_required",
        "reason": "execution-or-state-unverified",
        "next_due_at": 0.0, "last_seen_at": 0.0,
    }))
    code = corner_rotation.main(["--config", str(config), "recover"])
    captured = capsys.readouterr()
    assert code == 4
    assert "latch remains after recovery" in captured.err


def test_recover_rejects_clock_regression_before_touching_any_timestamp(setup):
    _, clock, _, executor, make = setup
    manager = make()
    latched = _latch(manager, executor, clock)
    # no reservation at all: the branch that refreshes last_seen_at
    latched["pending"] = None
    latched["last_seen_at"] = clock[0] + DAY / 2
    manager.path.write_text(json.dumps(latched))

    before = state(manager)
    with pytest.raises(RotationError, match="clock regressed"):
        make().recover()
    after = state(manager)
    assert after["status"] == "recovery_required"
    assert after["last_seen_at"] == before["last_seen_at"]


def _cli_env(tmp_path, *, enabled, body=None):
    from docich import corner_rotation

    state_dir = tmp_path / "run"
    state_dir.mkdir(exist_ok=True)
    config = tmp_path / "config.toml"
    config.write_text(
        f'[corner_rotation]\nenabled={"true" if enabled else "false"}\n'
        f'[paths]\nstate_dir="{state_dir}"\n'
    )
    if body is not None:
        (state_dir / "corner_rotation.json").write_text(body)
    return corner_rotation, config, state_dir


def _latched_ledger_text():
    return json.dumps({
        "schema_version": 1, "seed": "s", "slot": 3, "history": [],
        "pending": None, "status": "recovery_required",
        "reason": "execution-or-state-unverified",
        "next_due_at": 0.0, "last_seen_at": 0.0,
    })


def test_recover_cli_cannot_claim_success_from_an_unreadable_ledger(tmp_path, capsys):
    corner_rotation, config, state_dir = _cli_env(
        tmp_path, enabled=False, body="{ not json")
    code = corner_rotation.main(["--config", str(config), "recover"])
    captured = capsys.readouterr()
    assert code == 4
    assert "latch remains after recovery" in captured.err
    assert '"latch_resolved":false' in captured.out.replace(" ", "")
    assert (state_dir / "corner_rotation.json").read_text() == "{ not json"


def test_recover_cli_fails_while_the_scheduler_lock_is_busy(tmp_path, capsys):
    import fcntl

    corner_rotation, config, state_dir = _cli_env(
        tmp_path, enabled=True, body=_latched_ledger_text())
    lock_dir = state_dir / "locks"
    lock_dir.mkdir()
    handle = (lock_dir / "corner-rotation.lock").open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        code = corner_rotation.main(["--config", str(config), "recover"])
        captured = capsys.readouterr()
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
    assert code == 4
    assert "already-running" in captured.out
    assert "latch remains after recovery" in captured.err
    # the unit is never restarted on this path: the script stops on non-zero
    assert json.loads((state_dir / "corner_rotation.json").read_text())["status"] == "recovery_required"


def test_recover_cli_reports_a_resolved_latch(tmp_path, capsys):
    corner_rotation, config, _ = _cli_env(
        tmp_path, enabled=True, body=_latched_ledger_text().replace(
            '"recovery_required"', '"waiting"').replace(
            '"execution-or-state-unverified"', '"not-due"'))
    code = corner_rotation.main(["--config", str(config), "recover"])
    captured = capsys.readouterr()
    assert code == 2  # not latched: refused before any claim of success
    assert "not latched" in captured.err


@pytest.mark.parametrize("corner_id", ["ninvaders", "nethack", "meriken"])
def test_rotation_target_override_validation_matches_the_manager_signature(
        tmp_path, monkeypatch, corner_id):
    """A common rotation dispatch always passes the selected game (#986).

    ``RetroCornerManager._begin_locked`` calls ``_validate_games([target])``
    whenever a target override is present. A fixed single-game manager whose
    override accepted no argument therefore raised a TypeError before writing
    any corner state, which latched the entire rotation.
    """
    from docich.retro_corner import RetroCornerManager

    g = replace(load_global(ROOT, ROOT / "config/docich.soren-live.toml"),
                state_dir=tmp_path)
    monkeypatch.setattr(RetroCornerManager, "_executable_exists",
                        staticmethod(lambda _: True))
    monkeypatch.setattr("docich.adapters.retroarch.resolve_rom",
                        lambda *_: ROOT / "vm-only.sfc")
    monkeypatch.setattr("docich.adapters.retroarch.resolve_core",
                        lambda *_: "/vm-only/core.so")
    catalog = {c.id: c for c in load_catalog(g)}
    corner = catalog[corner_id]
    manager = CornerRotationManager(g)
    adapter = manager.adapters[corner_id]
    assert isinstance(adapter.manager, RetroCornerManager)
    # the exact call _begin_locked makes for this dispatch
    adapter.manager._validate_games([corner.game])


def test_latch_classifies_corner_side_errors_as_execution_error(setup):
    from docich.retro_corner import RetroCornerError

    _, clock, _, executor, make = setup
    manager = make()
    executor.result = RetroCornerError("retro corner対象は登録済みですが無効です: nethack")
    with pytest.raises(RetroCornerError):
        manager.tick()
    latched = state(manager)
    assert latched["status"] == "recovery_required"
    assert latched["error_kind"] == "execution-error"
