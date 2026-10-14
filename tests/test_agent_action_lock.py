"""Regression tests for the P3 agent action shared lock."""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.actions import Action  # noqa: E402
from docich.adapters import base  # noqa: E402
from docich.agent import loop  # noqa: E402
from docich.agent.fence import AgentFence  # noqa: E402
from docich.game_switch import GameSwitchBusyError  # noqa: E402


LEASE = "11111111-1111-1111-1111-111111111111"


def _active():
    return {
        "game": "nethack",
        "runtime_id": "g1-abcdef",
        "generation": 1,
        "lease_id": LEASE,
    }


class FakeAdapter:
    def __init__(self):
        self.observe_calls = 0
        self.acted = []

    def observe(self):
        self.observe_calls += 1
        return base.Observation(
            game="nethack",
            title="nethack",
            adapter="cli",
            ts=time.time(),
            kind="text",
            text="screen",
        )

    def act(self, action):
        self.acted.append(action)


class FakeBrain:
    def __init__(self, actions):
        self.actions = actions

    def decide(self, _obs):
        return self.actions


class TestAgentActionSharedLock(unittest.TestCase):
    def setUp(self):
        self.fence = AgentFence(
            game="nethack",
            runtime_id="g1-abcdef",
            generation=1,
            lease_id=LEASE,
        )
        self.state = {"active": _active(), "candidate": None, "previous": None}

    def test_observe_and_each_non_wait_action_use_shared_section(self):
        adapter = FakeAdapter()
        actions = [
            Action(type="text", text="x"),
            Action(type="wait", ms=10),
            Action(type="special", key="ENTER"),
        ]
        brain = FakeBrain(actions)

        def run_shared(_state_dir, fn, **_kwargs):
            return fn()

        with mock.patch("docich.agent.loop.read_canonical", return_value=self.state), \
                mock.patch("docich.agent.loop.active_fence", return_value=_active()), \
                mock.patch("docich.agent.loop.shared_section", side_effect=run_shared) as shared, \
                mock.patch("docich.agent.loop.time.sleep") as sleep_mock:
            count = loop._run_iteration(
                adapter,
                brain,
                1000,
                fence=self.fence,
                state_dir="/tmp/state",
            )

        self.assertEqual(count, 3)
        self.assertEqual(adapter.observe_calls, 1)
        self.assertEqual([a.type for a in adapter.acted], ["text", "special"])
        # observe + 2 non-wait actions. The wait itself must stay outside the lock.
        self.assertEqual(shared.call_count, 3)
        sleep_mock.assert_called_once_with(0.01)

    def test_exclusive_contention_before_action_prevents_stale_input(self):
        adapter = FakeAdapter()
        brain = FakeBrain([Action(type="text", text="x")])
        calls = 0

        def contend_after_observe(_state_dir, fn, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return fn()
            raise GameSwitchBusyError("transition in progress")

        with mock.patch("docich.agent.loop.read_canonical", return_value=self.state), \
                mock.patch("docich.agent.loop.active_fence", return_value=_active()), \
                mock.patch(
                    "docich.agent.loop.shared_section",
                    side_effect=contend_after_observe,
                ):
            with self.assertRaises(GameSwitchBusyError):
                loop._run_iteration(
                    adapter,
                    brain,
                    1000,
                    fence=self.fence,
                    state_dir="/tmp/state",
                )

        self.assertEqual(adapter.observe_calls, 1)
        self.assertEqual(adapter.acted, [])


if __name__ == "__main__":
    unittest.main()
