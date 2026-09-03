"""P4 tests: switch-aware status (canonical/mirror/actual/fence split, --json)."""
import io
import json
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import cli, config  # noqa: E402
from docich.game_switch import GameSwitchStore  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.status import STATUS_SCHEMA_VERSION, collect_status  # noqa: E402
from docich.tmux import OwnershipMismatchError, PaneState  # noqa: E402


def _runtime_dict(generation, game, adapter="cli"):
    names = runtime_names(generation)
    return {
        "game": game,
        "adapter": adapter,
        "generation": generation,
        "runtime_id": f"g{generation}-abcdef",
        "lease_id": str(uuid.uuid4()),
        "game_window": names.game_window,
        "agent_window": names.agent_window,
        "adapter_session": names.adapter_session,
        "started_at": "2026-09-03T00:00:00Z",
    }


class FakeTmux:
    def __init__(self):
        self.sessions = {}
        self.windows = {}
        self.raise_on_probe = None
        self.pane_dead = False
        self.raise_on_panes = None

    def _expected(self, ownership):
        return (ownership.runtime_id, ownership.generation, ownership.role)

    def has_session(self):
        return True

    def has_window(self, name):
        return f"docich:{name}" in self.windows or name in ("display", "stream")

    def session_target_exists(self, session, strict=False):
        if self.raise_on_probe:
            raise self.raise_on_probe
        return session in self.sessions

    def window_target_exists(self, target, strict=False):
        if self.raise_on_probe:
            raise self.raise_on_probe
        return target in self.windows

    def read_session_ownership(self, session):
        from docich.tmux import OwnershipMismatchError, TmuxOwnership

        try:
            runtime_id, generation, role = self.sessions[session]
            return TmuxOwnership(runtime_id=runtime_id, generation=generation, role=role)
        except (ValueError, TypeError) as exc:
            raise OwnershipMismatchError("session ownership tagが不正です") from exc

    def pane_states_checked(self, target):
        from docich.tmux import PaneState

        if self.raise_on_panes:
            raise self.raise_on_panes
        return [PaneState(dead=self.pane_dead, pid=1234)]

    def read_window_ownership(self, target):
        from docich.tmux import OwnershipMismatchError, TmuxOwnership

        try:
            runtime_id, generation, role = self.windows[target]
            return TmuxOwnership(runtime_id=runtime_id, generation=generation, role=role)
        except (ValueError, TypeError) as exc:
            raise OwnershipMismatchError("window ownership tagが不正です") from exc


class FakeXkit:
    def __init__(self, ready=True):
        self.ready = ready

    def display_ready(self):
        return self.ready


def _boom_mirror(_path, _game):
    raise OSError("mirror boom")


class _FakeRuntime:
    def __init__(self):
        self.materialized = False
        self.alive = False
        self.cleaned = False


class _FakeStepAdapter:
    name = "cli"
    agent_enabled = False

    def __init__(self, spec, behavior, runtime):
        self.spec = spec
        self.behavior = behavior
        self.runtime = runtime

    def preflight(self, deadline, cancel):
        pass

    def materialize_runtime(self, deadline, cancel):
        self.runtime.materialized = True
        self.runtime.alive = True

    def readiness(self, deadline, cancel):
        pass

    def alive(self, deadline, cancel):
        return bool(
            self.runtime.materialized and self.runtime.alive and not self.runtime.cleaned
        )

    def cleanup_runtime(self, deadline, cancel):
        if self.behavior.get("immortal"):
            return
        if self.runtime.alive:
            self.runtime.alive = False
            self.runtime.cleaned = True

    def start_agent(self, deadline, cancel):
        pass

    def stop_agent(self, deadline, cancel):
        pass

    def _prime(self, *, alive=True):
        self.runtime.materialized = True
        self.runtime.alive = alive


class _FakeStepFactory:
    def __init__(self, behaviors):
        self.behaviors = behaviors
        self.adapters = {}

    def __call__(self, spec):
        key = (spec.game, spec.generation)
        if key not in self.adapters:
            self.adapters[key] = _FakeStepAdapter(spec, self.behaviors[spec.game], _FakeRuntime())
        else:
            self.adapters[key].spec = spec
        return self.adapters[key]

    def adapter(self, game, generation):
        return self.adapters.get((game, generation))


def _prime_runtime(store, factory, runtime):
    from docich.game_switch import RuntimeSpec

    factory(
        RuntimeSpec.from_runtime(store.state_dir, runtime)
    )._prime(alive=True)


def _coordinator(store, behaviors=None, **kwargs):
    from docich import game_switch

    factory = _FakeStepFactory(behaviors or {"nethack": {}, "robots": {}})
    kwargs.setdefault("quiesce_verify_timeout_s", 0.3)
    kwargs.setdefault("poll_interval_s", 0.01)
    return game_switch.GameSwitchCoordinator(store, factory, **kwargs)


class StatusTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.g = config.load_global(self.root)
        self.tmux = FakeTmux()
        self.xkit = FakeXkit()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _collect(self):
        return collect_status(self.g, tmux=self.tmux, xkit=self.xkit)

    def _mirror(self, value=None):
        from docich.state import State

        if value is None:
            return State(self.g).current_game()
        State(self.g).set_current_game(value)

    def _save_idle_retiring(self):
        from docich.game_switch import GameSwitchStore

        store = GameSwitchStore(self.g.state_dir)
        state, _ = store.canonical.load()
        state.update(
            {
                "phase": "idle",
                "active": None,
                "candidate": None,
                "previous": None,
                "retiring": [_runtime_dict(1, "nethack")],
                "next_generation": 2,
                "last_result": {
                    "request_id": str(uuid.uuid4()),
                    "operation": "stop",
                    "status": "succeeded",
                    "from_game": "nethack",
                    "to_game": None,
                    "generation": 1,
                },
            }
        )
        store.canonical.save(state)
        return store

    def _canonical(self, store):
        state, _ = store.canonical.load()
        return state

    def _save_ready(self, active, **extra):
        store = GameSwitchStore(self.g.state_dir)
        state, _ = store.canonical.load()
        state.update(
            {
                "phase": "ready",
                "active": active,
                "next_generation": (active["generation"] + 1) if active else 1,
                **extra,
            }
        )
        return store.canonical.save(state)

    def _track(self, runtime, role_game="game", role_agent="agent"):
        self.tmux.windows[f"docich:{runtime['game_window']}"] = (
            runtime["runtime_id"], runtime["generation"], role_game,
        )
        self.tmux.windows[f"docich:{runtime['agent_window']}"] = (
            runtime["runtime_id"], runtime["generation"], role_agent,
        )
        self.tmux.sessions[runtime["adapter_session"]] = (
            runtime["runtime_id"], runtime["generation"], "adapter",
        )


class TestIdleStatus(StatusTestBase):
    def test_no_canonical_reports_absent_layers(self):
        data = self._collect()
        self.assertEqual(data["schema_version"], STATUS_SCHEMA_VERSION)
        self.assertFalse(data["canonical"]["present"])
        self.assertFalse(data["canonical"]["corrupt"])
        self.assertIsNone(data["mirror"]["game"])
        self.assertIsNone(data["mirror"]["matches_canonical"])
        self.assertIsNone(data["actual"]["active"])
        self.assertIsNone(data["actual"]["candidate"])
        self.assertIsNone(data["agent_fence"]["tuple"])
        self.assertFalse(data["agent_fence"]["agent_window_present"])
        self.assertFalse(data["cleanup_pending"])


class TestReadyStatus(StatusTestBase):
    def test_matched_mirror_and_actual(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        from docich.state import State

        State(self.g).set_current_game("nethack")
        self._track(active)
        data = self._collect()
        self.assertEqual(data["canonical"]["phase"], "ready")
        self.assertTrue(data["mirror"]["matches_canonical"])
        self.assertEqual(data["actual"]["active"]["game_window"]["ownership"], "matched")
        self.assertEqual(data["actual"]["active"]["agent_window"]["ownership"], "matched")
        self.assertEqual(data["actual"]["active"]["adapter_session"]["ownership"], "matched")
        self.assertEqual(data["agent_fence"]["tuple"]["lease_id"], active["lease_id"])
        self.assertTrue(data["agent_fence"]["agent_window_present"])
        self.assertFalse(data["cleanup_pending"])

    def test_mirror_mismatch_is_reported(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        from docich.state import State

        State(self.g).set_current_game("robots")
        data = self._collect()
        self.assertFalse(data["mirror"]["matches_canonical"])
        self.assertEqual(data["mirror"]["game"], "robots")

    def test_missing_actual_windows(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        data = self._collect()
        self.assertFalse(data["actual"]["active"]["game_window"]["exists"])
        self.assertEqual(data["actual"]["active"]["game_window"]["ownership"], "absent")
        self.assertFalse(data["agent_fence"]["agent_window_present"])

    def test_ownership_mismatch_is_reported(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        self.tmux.windows[f"docich:{active['game_window']}"] = ("g1-zzzzzz", 1, "game")
        data = self._collect()
        self.assertEqual(data["actual"]["active"]["game_window"]["ownership"], "mismatched")

    def test_unreadable_probe_is_reported_not_guessed(self):
        from docich.tmux import TmuxError

        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        self.tmux.raise_on_probe = TmuxError("socket error")
        data = self._collect()
        self.assertIsNone(data["actual"]["active"]["game_window"]["exists"])
        self.assertEqual(data["actual"]["active"]["game_window"]["ownership"], "unreadable")

    def test_retiring_means_cleanup_pending(self):
        active = _runtime_dict(2, "robots")
        old = _runtime_dict(1, "nethack")
        self._save_ready(active, retiring=[old])
        data = self._collect()
        self.assertTrue(data["cleanup_pending"])

    def test_candidate_actual_is_reported(self):
        active = _runtime_dict(1, "nethack")
        candidate = _runtime_dict(2, "robots")
        self._save_ready(active)
        store = GameSwitchStore(self.g.state_dir)
        state, _ = store.canonical.load()
        state.update(
            {
                "phase": "probing",
                "operation": "switch",
                "request_id": str(uuid.uuid4()),
                "candidate": candidate,
                "next_generation": 3,
            }
        )
        store.canonical.save(state)
        self._track(candidate)
        data = self._collect()
        self.assertEqual(data["actual"]["candidate"]["game"], "robots")
        self.assertEqual(data["actual"]["candidate"]["game_window"]["ownership"], "matched")

    def test_pane_dead_is_reported_not_alive(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        self._track(active)
        self.tmux.pane_dead = True
        data = self._collect()
        self.assertEqual(data["actual"]["active"]["game_window"]["panes"], "dead")
        self.assertEqual(data["actual"]["active"]["game_window"]["ownership"], "matched")

    def test_pane_probe_error_is_unreadable(self):
        from docich.tmux import TmuxError

        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        self._track(active)
        self.tmux.raise_on_panes = TmuxError("pane error")
        data = self._collect()
        self.assertEqual(data["actual"]["active"]["game_window"]["panes"], "unreadable")
        self.assertEqual(data["actual"]["active"]["game_window"]["ownership"], "matched")

    def test_agent_window_present_is_null_when_unreadable(self):
        from docich.tmux import TmuxError

        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        self.tmux.raise_on_probe = TmuxError("socket error")
        data = self._collect()
        self.assertIsNone(data["agent_fence"]["agent_window_present"])
        self.assertEqual(data["actual"]["active"]["agent_window"]["ownership"], "unreadable")

    def test_agent_window_present_true_false(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        self._track(active)
        data = self._collect()
        self.assertTrue(data["agent_fence"]["agent_window_present"])
        self.tmux.windows.clear()
        self.tmux.sessions.clear()
        data = self._collect()
        self.assertFalse(data["agent_fence"]["agent_window_present"])

    def test_retiring_actual_reports_ownership_and_panes(self):
        active = _runtime_dict(2, "robots")
        old = _runtime_dict(1, "nethack")
        self._save_ready(active, retiring=[old])
        self._track(active)
        self._track(old)
        data = self._collect()
        self.assertTrue(data["cleanup_pending"])
        self.assertEqual(len(data["actual"]["retiring"]), 1)
        probed = data["actual"]["retiring"][0]
        self.assertEqual(probed["game"], "nethack")
        self.assertEqual(probed["game_window"]["ownership"], "matched")
        self.assertEqual(probed["game_window"]["panes"], "alive")

    def test_previous_actual_is_reported(self):
        active = _runtime_dict(1, "nethack")
        previous = _runtime_dict(2, "robots")
        store = GameSwitchStore(self.g.state_dir)
        state, _ = store.canonical.load()
        state.update(
            {
                "phase": "rolling_back",
                "operation": "switch",
                "request_id": str(uuid.uuid4()),
                "active": None,
                "previous": previous,
                "next_generation": 3,
            }
        )
        store.canonical.save(state)
        self._track(previous)
        data = self._collect()
        self.assertIsNone(data["actual"]["active"])
        self.assertEqual(data["actual"]["previous"]["game"], "robots")
        self.assertEqual(data["actual"]["previous"]["game_window"]["ownership"], "matched")

    def test_retiring_probe_error_stays_unreadable(self):
        from docich.tmux import TmuxError

        active = _runtime_dict(2, "robots")
        old = _runtime_dict(1, "nethack")
        self._save_ready(active, retiring=[old])
        self.tmux.raise_on_probe = TmuxError("socket error")
        data = self._collect()
        self.assertTrue(data["cleanup_pending"])
        probed = data["actual"]["retiring"][0]
        self.assertIsNone(probed["game_window"]["exists"])
        self.assertEqual(probed["game_window"]["ownership"], "unreadable")
        self.assertEqual(probed["game_window"]["panes"], "unreadable")

    def test_non_cli_session_is_not_applicable(self):
        active = _runtime_dict(1, "hanjuku-hero", adapter="retroarch")
        self._save_ready(active)
        data = self._collect()
        self.assertFalse(data["actual"]["active"]["adapter_session"].get("applicable", True))


class TestCorruptStatus(StatusTestBase):
    def test_corrupt_canonical_is_reported_not_raised(self):
        (self.g.state_dir).mkdir(parents=True, exist_ok=True)
        (self.g.state_dir / "game_switch.json").write_text("{broken", encoding="utf-8")
        data = self._collect()
        self.assertTrue(data["canonical"]["present"])
        self.assertTrue(data["canonical"]["corrupt"])
        self.assertTrue(data["canonical"]["error"])
        self.assertIsNone(data["actual"]["active"])


class TestStatusCli(StatusTestBase):
    def test_json_output_is_stable_schema(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        out = io.StringIO()
        with mock.patch("docich.cli.Tmux", return_value=self.tmux), mock.patch(
            "docich.cli.XKit", return_value=self.xkit
        ):
            with redirect_stdout(out):
                rc = cli.cmd_status(self.g, json_output=True)
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["schema_version"], STATUS_SCHEMA_VERSION)
        for key in (
            "session_alive", "windows", "display_ready", "canonical", "mirror",
            "actual", "agent_fence", "cleanup_pending", "stream",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["canonical"]["phase"], "ready")

    def test_human_output_shows_switch_section(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        from docich.state import State

        State(self.g).set_current_game("nethack")
        out = io.StringIO()
        with mock.patch("docich.cli.Tmux", return_value=self.tmux), mock.patch(
            "docich.cli.XKit", return_value=self.xkit
        ):
            with redirect_stdout(out):
                rc = cli.cmd_status(self.g)
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("switch:", text)
        self.assertIn("canonical: phase=ready", text)
        self.assertIn("mirror[current_game]: nethack (一致)", text)
        self.assertIn("agent_fence:", text)
        self.assertIn("cleanup_pending:", text)

    def test_human_shows_unknown_and_pane_dead(self):
        from docich.tmux import TmuxError

        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        self._track(active)
        self.tmux.pane_dead = True
        out = io.StringIO()
        with mock.patch("docich.cli.Tmux", return_value=self.tmux), mock.patch(
            "docich.cli.XKit", return_value=self.xkit
        ):
            with redirect_stdout(out):
                rc = cli.cmd_status(self.g)
        self.assertEqual(rc, 0)
        self.assertIn("停止中 (pane dead)", out.getvalue())

        self.tmux.raise_on_probe = TmuxError("socket error")
        out = io.StringIO()
        with mock.patch("docich.cli.Tmux", return_value=self.tmux), mock.patch(
            "docich.cli.XKit", return_value=self.xkit
        ):
            with redirect_stdout(out):
                rc = cli.cmd_status(self.g)
        self.assertEqual(rc, 0)
        self.assertIn("agent_window=不明", out.getvalue())

    def test_human_shows_previous_retiring_and_presence(self):
        active = _runtime_dict(2, "robots")
        old = _runtime_dict(1, "nethack")
        self._save_ready(active, retiring=[old])
        from docich.state import State

        State(self.g).set_current_game("robots")
        self._track(active)
        self._track(old)
        self.tmux.pane_dead = True
        out = io.StringIO()
        with mock.patch("docich.cli.Tmux", return_value=self.tmux), mock.patch(
            "docich.cli.XKit", return_value=self.xkit
        ):
            with redirect_stdout(out):
                rc = cli.cmd_status(self.g)
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("previous: (なし)", text)
        self.assertIn("retiring: nethack", text)
        # present=true + pane dead は「存在 (pane dead)」で矛盾しない
        self.assertIn("agent_window=存在 (pane dead)", text)

    def test_status_json_flag_parses(self):
        args = cli.build_parser().parse_args(["status", "--json"])
        self.assertEqual(args.command, "status")
        self.assertTrue(args.json)

    def test_status_without_flag_defaults_to_human(self):
        args = cli.build_parser().parse_args(["status"])
        self.assertFalse(args.json)


class TestMirrorRepair(StatusTestBase):
    def _mark_alive(self, coord, game, generation):
        state, _ = coord.store.canonical.load()
        for key in ("active", "candidate", "previous"):
            runtime = state.get(key)
            if isinstance(runtime, dict) and runtime.get("game") == game and runtime.get("generation") == generation:
                _prime_runtime(coord.store, coord.adapter_factory, runtime)
        for runtime in state.get("retiring") or []:
            if isinstance(runtime, dict) and runtime.get("game") == game and runtime.get("generation") == generation:
                _prime_runtime(coord.store, coord.adapter_factory, runtime)

    def test_recover_repairs_stale_mirror(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        # stale mirror pointing elsewhere is repaired from canonical.
        self._mirror("robots")
        from docich.game_switch import GameSwitchStore

        store = GameSwitchStore(self.g.state_dir)
        coord = _coordinator(store)
        self._mark_alive(coord, "nethack", 1)
        result = coord.recover()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self._mirror(), "nethack")

    def test_recover_repairs_missing_mirror(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        from docich.game_switch import GameSwitchStore

        store = GameSwitchStore(self.g.state_dir)
        coord = _coordinator(store)
        self._mark_alive(coord, "nethack", 1)
        result = coord.recover()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self._mirror(), "nethack")

    def test_recover_clears_mirror_when_idle(self):
        self._mirror("nethack")
        from docich.game_switch import GameSwitchStore

        store = GameSwitchStore(self.g.state_dir)
        coord = _coordinator(store)
        result = coord.recover()
        self.assertEqual(result.status, "succeeded")
        self.assertIsNone(self._mirror())

    def test_repair_mirror_failure_is_warning_only(self):
        active = _runtime_dict(1, "nethack")
        self._save_ready(active)
        from docich.game_switch import GameSwitchStore

        store = GameSwitchStore(self.g.state_dir)
        coord = _coordinator(store, mirror_writer=_boom_mirror)
        self._mark_alive(coord, "nethack", 1)
        result = coord.recover()
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(any("mirror" in w for w in result.warnings))


class TestIdleRetiringRecover(StatusTestBase):
    def test_idle_retiring_is_retried_on_recover(self):
        from docich.game_switch import RuntimeSpec

        store = self._save_idle_retiring()
        coord = _coordinator(store, behaviors={"nethack": {}})
        state, _ = store.canonical.load()
        _prime_runtime(store, coord.adapter_factory, state["retiring"][0])
        result = coord.recover()
        self.assertEqual(result.status, "succeeded")
        self.assertFalse(result.cleanup_pending)
        self.assertEqual(self._canonical(store)["retiring"], [])

    def test_idle_retiring_survives_failed_retry(self):
        from docich.game_switch import RuntimeSpec

        from docich.game_switch import RuntimeSpec

        store = self._save_idle_retiring()
        coord = _coordinator(store, behaviors={"nethack": {"immortal": True, "agent_enabled": False}})
        state, _ = store.canonical.load()
        _prime_runtime(store, coord.adapter_factory, state["retiring"][0])
        result = coord.recover()
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(result.cleanup_pending)
        self.assertEqual(len(self._canonical(store)["retiring"]), 1)


class TestLegacyCommand(StatusTestBase):
    def test_legacy_json_lists_footprint(self):
        self._mirror("nethack")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.cmd_status_legacy(self.g, json_output=True)
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["footprint"], ["current_game"])

    def test_legacy_human_reports_none(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.cmd_status_legacy(self.g)
        self.assertEqual(rc, 0)
        self.assertIn("(なし)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
