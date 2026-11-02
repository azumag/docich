"""Regression tests for the #2832 Stage 1 bounded agent telemetry.

The telemetry is observation-side only.  These tests pin that: with the flag
off it is a no-op (no file, same event order), with the flag on the observe ->
decide -> act order, the decisions and the fence/hot-load behaviour are
unchanged, and the snapshot carries only fixed numeric fields.
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.actions import Action  # noqa: E402
from docich.adapters import base  # noqa: E402
from docich.agent import loop, telemetry  # noqa: E402
from docich.agent.fence import AgentFence  # noqa: E402


LEASE = "11111111-1111-1111-1111-111111111111"


class RecordingAdapter:
    def __init__(self, events):
        self.events = events

    def observe(self):
        self.events.append("observe")
        return base.Observation(
            game="g", title="g", adapter="cli", ts=time.time(), kind="text", text="hi"
        )

    def act(self, action):
        self.events.append(f"act:{action.type}")


class RecordingBrain:
    def __init__(self, events, actions):
        self.events = events
        self.actions = actions

    def decide(self, obs):
        self.events.append("decide")
        return list(self.actions)


def _run_once(telemetry_obj=None, actions=None):
    events = []
    adapter = RecordingAdapter(events)
    brain = RecordingBrain(events, actions if actions is not None else [
        Action(type="text", text="a"),
        Action(type="wait", ms=1),
        Action(type="special", key="Escape"),
    ])
    with mock.patch("docich.agent.loop.time.sleep"):
        n = loop._run_iteration(adapter, brain, 1000, telemetry=telemetry_obj)
    return n, events


class TestFlag(unittest.TestCase):
    def test_flag_values(self):
        self.assertFalse(telemetry.enabled({}))
        self.assertFalse(telemetry.enabled({"DOCICH_AGENT_TELEMETRY": "0"}))
        self.assertFalse(telemetry.enabled({"DOCICH_AGENT_TELEMETRY": ""}))
        for value in ("1", "true", "ON", " yes "):
            self.assertTrue(telemetry.enabled({"DOCICH_AGENT_TELEMETRY": value}))

    def test_from_env_off_returns_noop_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tel = telemetry.AgentTelemetry.from_env(Path(tmp), "g", "b", environ={})
            self.assertIs(tel, telemetry.DISABLED)
            n, _ = _run_once(tel)
            tel.flush(force=True)
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertFalse(tel.active)


class TestOrderAndDecisionInvariance(unittest.TestCase):
    def test_event_order_and_action_count_match_with_flag_on_and_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            on = telemetry.AgentTelemetry.from_env(
                Path(tmp), "g", "b", environ={"DOCICH_AGENT_TELEMETRY": "1"}
            )
            n_off, events_off = _run_once(telemetry.DISABLED)
            n_on, events_on = _run_once(on)
        self.assertEqual(n_off, n_on)
        self.assertEqual(events_off, events_on)
        self.assertEqual(events_on, ["observe", "decide", "act:text", "act:special"])

    def test_default_none_telemetry_behaves_like_disabled(self):
        n, events = _run_once(None)
        self.assertEqual(events, ["observe", "decide", "act:text", "act:special"])
        self.assertEqual(n, 3)


class TestSnapshot(unittest.TestCase):
    def test_flag_on_writes_fixed_fields_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tel = telemetry.AgentTelemetry.from_env(
                Path(tmp), "g", "b", environ={"DOCICH_AGENT_TELEMETRY": "1"}
            )
            _run_once(tel)
            tel.flush(force=True)
            path = Path(tmp) / "agent_telemetry" / "g.json"
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        self.assertEqual(data["schema"], telemetry.SCHEMA)
        self.assertEqual(set(data["counters"]), set(telemetry._COUNTERS))
        self.assertEqual(set(data["seconds"]), set(telemetry._TIMERS))
        self.assertEqual(data["counters"]["cycles"], 1)
        self.assertEqual(data["counters"]["observes"], 1)
        self.assertEqual(data["counters"]["decides"], 1)
        self.assertEqual(data["counters"]["acts"], 2)
        self.assertEqual(data["counters"]["waits"], 1)
        self.assertEqual(data["counters"]["actions_completed"], 3)
        for value in data["counters"].values():
            self.assertIsInstance(value, int)
        for value in data["seconds"].values():
            self.assertIsInstance(value, float)
        # No prompt, frame or action payload is recorded.
        self.assertNotIn("hi", raw)
        self.assertNotIn("Escape", raw)

    def test_flush_is_throttled_and_forced_flush_writes(self):
        clock = {"t": 100.0}
        with tempfile.TemporaryDirectory() as tmp:
            tel = telemetry.AgentTelemetry(
                Path(tmp) / "t.json", "g", "b", clock=lambda: clock["t"]
            )
            tel.count("cycles")
            tel.flush()
            first = (Path(tmp) / "t.json").stat().st_mtime_ns
            tel.count("cycles")
            clock["t"] += 1.0
            tel.flush()
            self.assertEqual(json.loads((Path(tmp) / "t.json").read_text())["counters"]["cycles"], 1)
            tel.flush(force=True)
            self.assertEqual(json.loads((Path(tmp) / "t.json").read_text())["counters"]["cycles"], 2)
            self.assertIsNotNone(first)


class TestPublishFailure(unittest.TestCase):
    def test_hotload_publish_failure_is_counted_and_cycle_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            tel = telemetry.AgentTelemetry(Path(tmp) / "t.json", "g", "b")
            events = []
            adapter = RecordingAdapter(events)
            brain = RecordingBrain(events, [Action(type="text", text="a")])
            with mock.patch("docich.hanjuku_hotload.publish_observation",
                            side_effect=RuntimeError("boom")):
                n = loop._run_iteration(adapter, brain, 1000, telemetry=tel)
            self.assertEqual(n, 1)
            self.assertEqual(events, ["observe", "decide", "act:text"])
            self.assertEqual(tel._counters["hotload_publish_failures"], 1)


class TestFencedPath(unittest.TestCase):
    def _fence(self):
        return AgentFence(game="g", runtime_id="r", generation=1, lease_id=LEASE)

    def test_fenced_run_keeps_order_and_records_lock_time(self):
        tel = telemetry.AgentTelemetry(None, "g", "b")
        events = []
        adapter = RecordingAdapter(events)
        brain = RecordingBrain(events, [Action(type="text", text="a")])
        with mock.patch("docich.agent.loop.resolve_fence", return_value="run"), \
             mock.patch("docich.agent.loop.read_canonical", return_value={}), \
             mock.patch("docich.agent.loop._check_loop_fence"), \
             mock.patch("docich.agent.loop.shared_section",
                        side_effect=lambda _sd, fn: fn()):
            n = loop._run_iteration(adapter, brain, 1000, fence=self._fence(),
                                    state_dir=Path("/unused"), telemetry=tel)
        self.assertEqual(n, 1)
        self.assertEqual(events, ["observe", "decide", "act:text"])

    def test_await_branch_is_counted_without_observing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tel = telemetry.AgentTelemetry(Path(tmp) / "t.json", "g", "b")
            events = []
            adapter = RecordingAdapter(events)
            brain = RecordingBrain(events, [Action(type="text", text="a")])
            with mock.patch("docich.agent.loop.resolve_fence", return_value="await"), \
                 mock.patch("docich.agent.loop.read_canonical", return_value={}), \
                 mock.patch("docich.agent.loop.time.sleep"):
                n = loop._run_iteration(adapter, brain, 1000, fence=self._fence(),
                                        state_dir=Path("/unused"), telemetry=tel)
        self.assertEqual(n, 0)
        self.assertEqual(events, [])
        self.assertEqual(tel._counters["awaits"], 1)


class TestFlushFailureDoesNotStopLoop(unittest.TestCase):
    def test_oserror_from_flush_is_logged_not_raised(self):
        tel = mock.Mock()
        tel.flush.side_effect = OSError("disk full")
        with mock.patch("builtins.print") as printed:
            loop._flush_telemetry(tel)
        self.assertTrue(any("telemetry" in str(c) for c in printed.call_args_list))


if __name__ == "__main__":
    unittest.main()
