"""Tests for the agent loop's single-cycle wiring (observe -> decide -> act).

Deliberately does NOT call loop.run_agent() itself, since that function is an
intentional `while True` supervised forever-loop (architecture.md SS6) -- only
the extracted _run_iteration() helper (one observe/decide/act cycle) is
exercised here.
"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.actions import Action  # noqa: E402
from docich.adapters import base  # noqa: E402
from docich.agent import loop  # noqa: E402


class FakeAdapter:
    def __init__(self):
        self.observe_calls = 0
        self.acted: list[Action] = []

    def observe(self) -> base.Observation:
        self.observe_calls += 1
        return base.Observation(
            game="g", title="g", adapter="cli", ts=time.time(), kind="text", text="hi"
        )

    def act(self, action: Action) -> None:
        self.acted.append(action)


class FakeBrain:
    def __init__(self, actions_to_return: list[Action]):
        self.actions_to_return = actions_to_return
        self.seen_obs: list[base.Observation] = []

    def decide(self, obs: base.Observation) -> list[Action]:
        self.seen_obs.append(obs)
        return self.actions_to_return


class TestRunIteration(unittest.TestCase):
    def test_observe_result_is_passed_to_brain_decide(self):
        adapter = FakeAdapter()
        brain = FakeBrain([])
        loop._run_iteration(adapter, brain, 1000)
        self.assertEqual(adapter.observe_calls, 1)
        self.assertEqual(len(brain.seen_obs), 1)
        self.assertEqual(brain.seen_obs[0].adapter, "cli")

    def test_wait_action_sleeps_and_is_not_forwarded_to_adapter_act(self):
        adapter = FakeAdapter()
        brain = FakeBrain([Action(type="wait", ms=250)])
        with mock.patch("docich.agent.loop.time.sleep") as sleep_mock:
            n = loop._run_iteration(adapter, brain, 1000)
        self.assertEqual(n, 1)
        self.assertEqual(adapter.acted, [])
        sleep_mock.assert_called_once_with(0.25)

    def test_non_wait_action_is_forwarded_to_adapter_act(self):
        adapter = FakeAdapter()
        act = Action(type="text", text="j")
        brain = FakeBrain([act])
        n = loop._run_iteration(adapter, brain, 1000)
        self.assertEqual(n, 1)
        self.assertEqual(adapter.acted, [act])

    def test_multiple_actions_are_all_processed_in_order(self):
        adapter = FakeAdapter()
        acts = [
            Action(type="text", text="a"),
            Action(type="wait", ms=1),
            Action(type="special", key="Escape"),
        ]
        brain = FakeBrain(acts)
        with mock.patch("docich.agent.loop.time.sleep"):
            n = loop._run_iteration(adapter, brain, 1000)
        self.assertEqual(n, 3)
        self.assertEqual(adapter.acted, [acts[0], acts[2]])  # wait は adapter.act に渡さない

    def test_empty_action_list_returns_zero(self):
        adapter = FakeAdapter()
        brain = FakeBrain([])
        n = loop._run_iteration(adapter, brain, 1000)
        self.assertEqual(n, 0)
        self.assertEqual(adapter.acted, [])

    def test_adapter_act_exception_propagates_to_caller(self):
        # run_agent 側で catch する契約なので、ここでは伝播することだけを確認する。
        class RaisingAdapter(FakeAdapter):
            def act(self, action):
                raise RuntimeError("boom")

        adapter = RaisingAdapter()
        brain = FakeBrain([Action(type="text", text="x")])
        with self.assertRaises(RuntimeError):
            loop._run_iteration(adapter, brain, 1000)


if __name__ == "__main__":
    unittest.main()
