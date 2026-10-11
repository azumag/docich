import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "restart_active_soren91_agent.py"
SPEC = importlib.util.spec_from_file_location("restart_active_soren91_agent", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FakeAdapter:
    def __init__(self, alive):
        self.alive = list(alive)
        self.calls = []

    def agent_alive(self, deadline, cancel):
        self.calls.append(("alive", deadline, cancel))
        if len(self.alive) > 1:
            return self.alive.pop(0)
        return self.alive[0]

    def stop_agent(self, deadline, cancel):
        self.calls.append(("stop", deadline, cancel))

    def start_agent(self, deadline, cancel):
        self.calls.append(("start", deadline, cancel))


class Clock:
    def __init__(self):
        self.value = 100.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class RestartActiveSoren91AgentTests(unittest.TestCase):
    def test_restart_recycles_owned_agent_and_waits_for_fresh_liveness(self):
        clock = Clock()
        adapter = FakeAdapter([True, False, False, True])
        result = MODULE.restart_adapter(
            adapter,
            now=clock.now,
            sleep=clock.sleep,
            verify_seconds=2.0,
            verify_interval=0.25,
        )
        self.assertEqual(
            result,
            {"status": "completed", "previous_alive": True, "agent_alive": True},
        )
        kinds = [call[0] for call in adapter.calls]
        self.assertEqual(kinds[:3], ["alive", "stop", "start"])
        self.assertEqual(kinds.count("stop"), 1)
        self.assertEqual(kinds.count("start"), 1)

    def test_restart_also_recreates_an_already_dead_agent_window(self):
        clock = Clock()
        adapter = FakeAdapter([False, True])
        result = MODULE.restart_adapter(
            adapter,
            now=clock.now,
            sleep=clock.sleep,
            verify_seconds=1.0,
            verify_interval=0.25,
        )
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["previous_alive"])
        self.assertEqual([call[0] for call in adapter.calls[:3]], ["alive", "stop", "start"])

    def test_restart_fails_closed_when_new_agent_never_becomes_live(self):
        clock = Clock()
        adapter = FakeAdapter([True, False])
        with self.assertRaisesRegex(MODULE.RestartRefused, "agent_not_live_after_restart"):
            MODULE.restart_adapter(
                adapter,
                now=clock.now,
                sleep=clock.sleep,
                verify_seconds=1.0,
                verify_interval=0.25,
            )
        self.assertEqual([call[0] for call in adapter.calls].count("start"), 1)


if __name__ == "__main__":
    unittest.main()
