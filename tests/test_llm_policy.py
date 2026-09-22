"""Native LLM chain policy tests (#829 PR-1, mock only, no network).

The provider layer is stubbed at ``dispatch_one``; queue/backoff/gate,
file contracts, telemetry schema, and redaction are exercised for real.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.llm import budget as budget_mod  # noqa: E402
from docich.llm import contracts as c  # noqa: E402
from docich.llm import policy as policy_mod  # noqa: E402


def _settings(**over):
    base = c.LlmSettings(queue_wait_sec=1)
    return replace(base, **over)


class PolicyTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="docich-llm-policy-"))
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.settings = _settings()
        self.last_agent = self.tmp / "last_agent.txt"
        self.failure_kind = self.tmp / "failure_kind.txt"

    def _run(self, agents, **kwargs):
        params = dict(
            settings=self.settings,
            state_dir=self.state,
            label="COMMENT",
            prompt_text="hello",
            agents=agents,
            last_agent_file=self.last_agent,
            failure_kind_file=self.failure_kind,
        )
        params.update(kwargs)
        return policy_mod.generate_list(**params)

    def _stats(self):
        lines = []
        for path in (self.state / "ai_stats").glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                lines.append(json.loads(line))
        return lines

    def _patch_dispatch(self, effects):
        """Stub dispatch_one with a rc/text/resolved/backoff sequence."""
        calls = []

        def fake(settings, state, label, agent, prompt, **kwargs):
            calls.append(agent)
            return effects[min(len(calls) - 1, len(effects) - 1)]

        patcher = mock.patch.object(
            policy_mod.dispatch_mod, "dispatch_one", side_effect=fake
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls


class TestWinnerPath(PolicyTestBase):
    def test_first_winner(self):
        calls = self._patch_dispatch([(0, "answer", "m", True)])
        rc, text, winner, kind = self._run(["opencode:a", "opencode:b"])
        self.assertEqual((rc, text, winner, kind), (0, "answer", "opencode:a", ""))
        self.assertEqual(calls, ["opencode:a"])
        self.assertEqual(self.last_agent.read_text(encoding="utf-8"), "opencode:a\n")
        self.assertEqual(self.failure_kind.read_text(encoding="utf-8"), "")
        events = {line["event"] for line in self._stats()}
        self.assertIn("winner", events)
        self.assertIn("chain_summary", events)

    def test_fallback_to_second(self):
        self._patch_dispatch([(1, "", "m1", True), (0, "second", "m2", True)])
        rc, text, winner, kind = self._run(["opencode:a", "opencode:b"])
        self.assertEqual((rc, text, winner), (0, "second", "opencode:b"))
        # The failed first candidate records scoped failure backoff.
        self.assertGreater(
            len(list((self.state / "ai_failure_backoff" / "default").glob("*"))), 0
        )

    def test_invalid_spec_skipped_silently(self):
        calls = self._patch_dispatch([(0, "answer", "m", True)])
        rc, text, winner, kind = self._run(["claude", "opencode:a"])
        self.assertEqual((rc, winner), (0, "opencode:a"))
        self.assertEqual(calls, ["opencode:a"])
        self.assertEqual(self.failure_kind.read_text(encoding="utf-8"), "")

    def test_empty_output_takes_no_backoff(self):
        self._patch_dispatch([(1, "", "m", False)])
        rc, text, winner, kind = self._run(["opencode:a"])
        self.assertEqual((rc, kind), (1, "failed"))
        self.assertEqual(
            list((self.state / "ai_failure_backoff").rglob("*")), []
        )


class TestRateLimit(PolicyTestBase):
    def test_rate_limit_kind_and_backoff(self):
        self._patch_dispatch([(79, "", "m", True)])
        rc, text, winner, kind = self._run(["opencode:a"])
        self.assertEqual((rc, kind), (1, "rate_limit"))
        self.assertEqual(self.failure_kind.read_text(encoding="utf-8"), "rate_limit\n")
        self.assertTrue(list((self.state / "ai_backoff").glob("*")))

    def test_vercel_family_trips_on_two_distinct(self):
        # vercel:* specs are invalid without VERCEL_FREE_AGENTS; enable it
        # so the chain actually attempts both candidates.
        self.settings = _settings(vercel_free_agents="vercel:a,vercel:b,vercel:c")
        self._patch_dispatch([(79, "", "m", True), (79, "", "m", True)])
        rc, text, winner, kind = self._run(
            ["vercel:a", "vercel:b"], timeout=None,
        )
        self.assertEqual(kind, "rate_limit")
        from docich.llm.backoff import FamilyBreaker

        self.assertGreater(FamilyBreaker(self.state, self.settings).check(), 0)
        # A third vercel agent is now suppressed without a model call.
        calls = self._patch_dispatch([(0, "answer", "m", True)])
        rc, text, winner, kind = self._run(["vercel:c"])
        self.assertEqual((rc, kind), (1, "rate_limit"))
        self.assertEqual(calls, [])

    def test_all_skipped_on_rate_backoff(self):
        self._patch_dispatch([(79, "", "m", True)])
        self._run(["opencode:a"])
        calls = self._patch_dispatch([(0, "answer", "m", True)])
        rc, text, winner, kind = self._run(["opencode:a"])
        self.assertEqual((rc, kind), (1, "rate_limit"))
        self.assertEqual(calls, [])


class TestGiveUps(PolicyTestBase):
    def test_queue_giveup(self):
        from docich.llm.queue import LaneQueue

        lanes = LaneQueue(self.state, self.settings)
        acquired, _ = lanes.acquire("RADIO:main", owner_pid=os.getpid())
        self.assertTrue(acquired)
        try:
            calls = self._patch_dispatch([(0, "answer", "m", True)])
            rc, text, winner, kind = self._run(
                ["opencode:a"], label="RADIO:main", max_queue_wait=1,
            )
            self.assertEqual((rc, kind), (92, "queue_giveup"))
            self.assertEqual(
                self.failure_kind.read_text(encoding="utf-8"), "queue_giveup\n"
            )
            self.assertEqual(calls, [])
        finally:
            lanes.release("RADIO:main")

    def test_gate_giveup_with_short_cap(self):
        improve = self.tmp / "improve_state.json"
        improve.write_text(
            json.dumps(
                {"status": "running", "pid": os.getpid(), "started_at": time.time()}
            ),
            encoding="utf-8",
        )
        self.settings = _settings(improve_gate_wait_max_sec=1)
        calls = self._patch_dispatch([(0, "answer", "m", True)])
        rc, text, winner, kind = self._run(
            ["opencode:a"], label="RADIO:main", improve_state_path=improve,
        )
        self.assertEqual((rc, kind), (91, "gate_giveup"))
        self.assertEqual(
            self.failure_kind.read_text(encoding="utf-8"), "gate_giveup\n"
        )
        self.assertEqual(calls, [])
        # No model call happened: no backoff or streak state.
        self.assertEqual(list((self.state / "ai_backoff").glob("*")), [])
        self.assertEqual(list((self.state / "ai_fail_streak").rglob("*")), [])

    def test_budget_exhausted_is_empty_success(self):
        calls = self._patch_dispatch([(0, "answer", "m", True)])
        past = budget_mod.ChainBudget.from_deadline(time.monotonic() - 1)
        rc, text, winner, kind = self._run(["opencode:a"], chain_budget=past)
        self.assertEqual((rc, text, kind), (0, "", ""))
        self.assertEqual(calls, [])
        events = {line["event"] for line in self._stats()}
        self.assertIn("budget_exhausted", events)


class TestTelemetryRedaction(PolicyTestBase):
    def test_prompt_never_hits_stats(self):
        self._patch_dispatch([(0, "answer", "m", True)])
        secret_prompt = "SECRET-PROMPT-MARKER-12345"
        self._run(["opencode:a"], prompt_text=secret_prompt)
        blob = ""
        for path in (self.state / "ai_stats").glob("*.jsonl"):
            blob += path.read_text(encoding="utf-8")
        self.assertNotIn(secret_prompt, blob)
        summary = [
            line for line in self._stats() if line["event"] == "chain_summary"
        ]
        self.assertEqual(len(summary), 1)
        self.assertRegex(summary[0]["error"], r"vrl=\d+;vda=\d+;nfs=[01];term=winner")


if __name__ == "__main__":
    unittest.main()
