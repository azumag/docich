"""P3 tests: agent lease fence (match, mismatch, FenceLost, loop checks)."""

import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.actions import Action  # noqa: E402
from docich.adapters import base  # noqa: E402
from docich.agent import loop  # noqa: E402
from docich.agent.fence import AgentFence, FenceLost, active_fence, check_fence, resolve_fence  # noqa: E402


LEASE_1 = "11111111-1111-1111-1111-111111111111"
LEASE_2 = "22222222-2222-2222-2222-222222222222"


def _active(generation: int = 1, game: str = "nethack", lease: str | None = None):
    from docich.naming import runtime_names

    names = runtime_names(generation)
    return {
        "game": game,
        "adapter": "cli",
        "generation": generation,
        "runtime_id": f"g{generation}-abcdef",
        "lease_id": lease or str(uuid.uuid4()),
        "game_window": names.game_window,
        "agent_window": names.agent_window,
        "adapter_session": names.adapter_session,
        "started_at": "2026-09-03T00:00:00Z",
    }


def _fence(generation: int = 1, game: str = "nethack", lease: str | None = None) -> AgentFence:
    return AgentFence(
        game=game,
        runtime_id=f"g{generation}-abcdef",
        generation=generation,
        lease_id=lease or str(uuid.uuid4()),
    )


class TestResolveFence(unittest.TestCase):
    def _state(self, *, active=None, candidate=None, previous=None):
        return {
            "schema_version": 2,
            "revision": 1,
            "phase": "probing",
            "operation": "switch",
            "request_id": "12345678-1234-5678-1234-567812345678",
            "deadline_at": None,
            "next_generation": 3,
            "active": active,
            "candidate": candidate,
            "previous": previous,
            "retiring": [],
            "last_result": None,
            "last_error": None,
            "updated_at": "2026-09-03T00:00:00Z",
        }

    def test_matching_candidate_awaits_without_fence_lost(self):
        candidate = _active(2, "robots", lease=LEASE_1)
        fence = AgentFence(
            game="robots", runtime_id="g2-abcdef", generation=2, lease_id=LEASE_1
        )
        self.assertEqual(
            resolve_fence(fence, self._state(candidate=candidate)), "await"
        )

    def test_committed_candidate_runs(self):
        active = _active(2, "robots", lease=LEASE_1)
        fence = AgentFence(
            game="robots", runtime_id="g2-abcdef", generation=2, lease_id=LEASE_1
        )
        self.assertEqual(resolve_fence(fence, self._state(active=active)), "run")

    def test_vanished_candidate_is_terminal_fence_lost(self):
        fence = AgentFence(
            game="robots", runtime_id="g2-abcdef", generation=2, lease_id=LEASE_1
        )
        with self.assertRaises(FenceLost):
            resolve_fence(fence, self._state())

    def test_replaced_candidate_is_terminal_fence_lost(self):
        fence = AgentFence(
            game="robots", runtime_id="g2-abcdef", generation=2, lease_id=LEASE_1
        )
        other = _active(3, "robots", lease=LEASE_2)
        with self.assertRaises(FenceLost):
            resolve_fence(fence, self._state(candidate=other))

    def test_rollback_previous_await_and_old_lease_lost(self):
        previous = _active(1, "nethack", lease=LEASE_1)
        new_fence = AgentFence(
            game="nethack", runtime_id="g1-abcdef", generation=1, lease_id=LEASE_1
        )
        old_fence = AgentFence(
            game="nethack", runtime_id="g1-abcdef", generation=1, lease_id=LEASE_2
        )
        state = self._state(previous=previous)
        self.assertEqual(resolve_fence(new_fence, state), "await")
        with self.assertRaises(FenceLost):
            resolve_fence(old_fence, state)


class TestLoopAwait(unittest.TestCase):
    def test_awaiting_worker_does_not_observe_or_act(self):
        adapter = FakeLoopAdapter([Action(type="text", text="x")])
        brain = FakeLoopBrain(adapter.actions)
        candidate = _active(2, "robots", lease=LEASE_1)
        fence = AgentFence(
            game="robots", runtime_id="g2-abcdef", generation=2, lease_id=LEASE_1
        )
        state = {
            "active": None,
            "candidate": candidate,
            "previous": None,
        }
        with mock.patch("docich.agent.loop.read_canonical", return_value=state), \
                mock.patch("docich.agent.loop.time.sleep") as sleep_mock:
            n = loop._run_iteration(adapter, brain, 100, fence=fence, state_dir="/tmp/x")
        self.assertEqual(n, 0)
        self.assertEqual(adapter.observe_calls, 0)
        self.assertEqual(adapter.acted, [])
        sleep_mock.assert_called_once_with(0.1)


class TestFenceCheck(unittest.TestCase):
    def test_exact_match_passes(self):
        active = _active(lease=LEASE_1)
        check_fence(_fence(lease=LEASE_1), active)

    def test_mismatched_fields_raise_fence_lost(self):
        active = _active(lease=LEASE_1)
        for bad in (
            _fence(game="other", lease=LEASE_1),
            _fence(generation=2, lease=LEASE_1),
            _fence(lease=LEASE_2),
            AgentFence(game="nethack", runtime_id="g1-zzzzzz", generation=1, lease_id=LEASE_1),
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(FenceLost):
                    check_fence(bad, active)

    def test_idle_active_raises_fence_lost(self):
        with self.assertRaises(FenceLost):
            check_fence(_fence(lease=LEASE_1), None)

    def test_fence_lost_carries_terminal_code(self):
        try:
            check_fence(_fence(lease="55555555-5555-5555-5555-555555555555"), _active(lease="66666666-6666-6666-6666-666666666666"))
            self.fail("FenceLost が投げられるべきです")
        except FenceLost as exc:
            self.assertEqual(exc.code, 75)
            self.assertNotIsInstance(exc, Exception)


class TestActiveFence(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmpdir.name) / "run"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, phase, active):
        from docich.game_switch import GameSwitchStore

        store = GameSwitchStore(self.state_dir)
        state, _ = store.canonical.load()
        state.update({"phase": phase, "active": active, "next_generation": 2})
        store.canonical.save(state)

    def test_returns_active_dict(self):
        active = _active(lease=LEASE_1)
        self._write("ready", active)
        self.assertEqual(active_fence(self.state_dir)["lease_id"], LEASE_1)

    def test_idle_returns_none(self):
        self._write("idle", None)
        self.assertIsNone(active_fence(self.state_dir))

    def test_corrupt_state_propagates(self):
        from docich.game_switch import StateCorruptError

        (self.state_dir).mkdir(parents=True, exist_ok=True)
        (self.state_dir / "game_switch.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(StateCorruptError):
            active_fence(self.state_dir)


class FakeLoopAdapter:
    def __init__(self, actions):
        self.actions = actions
        self.observe_calls = 0
        self.acted = []

    def observe(self):
        self.observe_calls += 1
        return base.Observation(game="g", title="g", adapter="cli", ts=time.time(), kind="text", text="t")

    def act(self, action):
        self.acted.append(action)


class FakeLoopBrain:
    def __init__(self, actions):
        self.actions = actions

    def decide(self, obs):
        return self.actions


class TestLoopFence(unittest.TestCase):
    def test_no_fence_means_no_checks(self):
        adapter = FakeLoopAdapter([Action(type="text", text="x")])
        brain = FakeLoopBrain(adapter.actions)
        n = loop._run_iteration(adapter, brain, 1000)
        self.assertEqual(n, 1)
        self.assertEqual(len(adapter.acted), 1)

    def test_fence_checked_before_observe(self):
        adapter = FakeLoopAdapter([])
        brain = FakeLoopBrain([])
        with mock.patch("docich.agent.loop.active_fence", return_value=None):
            with self.assertRaises(FenceLost):
                loop._run_iteration(
                    adapter, brain, 1000,
                    fence=_fence(lease=LEASE_1), state_dir="/tmp/x",
                )
        self.assertEqual(adapter.observe_calls, 0)

    def test_fence_checked_after_decide(self):
        adapter = FakeLoopAdapter([])
        brain = FakeLoopBrain([Action(type="text", text="x")])
        active = _active(lease=LEASE_1)
        fence = _fence(lease=LEASE_1)
        # pre-observe と post-decide は一致するが、action 前の3回目で不一致
        with mock.patch(
            "docich.agent.loop.read_canonical", return_value={"active": active}
        ), mock.patch(
            "docich.agent.loop.active_fence",
            side_effect=[active, active, _active(lease=LEASE_2)],
        ):
            with self.assertRaises(FenceLost):
                loop._run_iteration(adapter, brain, 1000, fence=fence, state_dir="/tmp/x")
        # observe までは実行されるが、act には到達しない
        self.assertEqual(adapter.observe_calls, 1)
        self.assertEqual(adapter.acted, [])

    def test_fence_lost_propagates_as_terminal_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            fence = _fence(lease=LEASE_1)
            games = Path(tmp) / "config" / "games"
            games.mkdir(parents=True)
            (games / "nethack.toml").write_text(
                '[game]\nname = "nethack"\nadapter = "cli"\n\n'
                "[cli]\ncommand = \"true\"\n\n"
                '[agent]\nenabled = true\nbrain = "command"\ncommand = "true"\n',
                encoding="utf-8",
            )
            with mock.patch("docich.agent.loop.make_adapter") as make_mock, \
                    mock.patch("docich.agent.loop.brains.build_brain") as brain_mock:
                make_mock.return_value = FakeLoopAdapter([])
                brain_mock.return_value = FakeLoopBrain([])
                import docich.config as config_mod

                g = config_mod.load_global(Path(tmp))
                with mock.patch("docich.agent.loop.active_fence", return_value=None):
                    with self.assertRaises(FenceLost) as ctx:
                        loop.run_agent(
                            g, "nethack",
                            runtime_id="g1-abcdef", generation=1, lease_id=LEASE_1,
                        )
            self.assertEqual(ctx.exception.code, 75)

    def test_run_agent_rejects_partial_fence_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            games = Path(tmp) / "config" / "games"
            games.mkdir(parents=True)
            (games / "nethack.toml").write_text(
                '[game]\nname = "nethack"\nadapter = "cli"\n\n'
                "[cli]\ncommand = \"true\"\n",
                encoding="utf-8",
            )
            import docich.config as config_mod

            g = config_mod.load_global(Path(tmp))
            with self.assertRaises(ValueError):
                loop.run_agent(g, "nethack", runtime_id="g1-abcdef")


class TestAdapterSideCheck(unittest.TestCase):
    def _ctx(self, fence=None):
        with tempfile.TemporaryDirectory() as tmp:
            import docich.config as config_mod
            import docich.state as state_mod

            root = Path(tmp)
            g = config_mod.load_global(root)
            game = config_mod.GameConfig(
                name="nethack", title="t", adapter="cli", raw={"cli": {"command": "x"}},
                agent=config_mod.GameAgentConfig(), path=root,
            )
            tmux = mock.Mock()
            tmux.capture_pane.return_value = "screen"
            tmux.send_keys.return_value = None
            ctx = base.AdapterContext(g=g, game=game, state=state_mod.State(g), tmux=tmux, xkit=None, fence=fence)
            return ctx, tmux

    def test_unfenced_context_performs_no_check(self):
        import docich.adapters.cli_game as cli_game_mod

        ctx, tmux = self._ctx()
        adapter = cli_game_mod.CliGameAdapter(ctx)
        adapter.observe()
        tmux.capture_pane.assert_called_once()

    def test_fenced_act_mismatch_raises_fence_lost(self):
        import docich.adapters.cli_game as cli_game_mod

        ctx, tmux = self._ctx(fence=_fence(lease="33333333-3333-3333-3333-333333333333"))
        adapter = cli_game_mod.CliGameAdapter(ctx)
        with mock.patch("docich.agent.fence.active_fence", return_value=_active(lease="44444444-4444-4444-4444-444444444444")):
            with self.assertRaises(FenceLost):
                adapter.act(Action(type="text", text="x"))
        tmux.send_keys.assert_not_called()

    def test_fenced_act_match_proceeds(self):
        import docich.adapters.cli_game as cli_game_mod

        ctx, tmux = self._ctx(fence=_fence(lease=LEASE_1))
        adapter = cli_game_mod.CliGameAdapter(ctx)
        with mock.patch("docich.agent.fence.active_fence", return_value=_active(lease=LEASE_1)):
            adapter.act(Action(type="text", text="x"))
        tmux.send_keys.assert_called_once()


if __name__ == "__main__":
    unittest.main()