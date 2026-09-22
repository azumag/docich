"""Native LLM contracts tests (#829 PR-1, mock only, no network)."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.llm import contracts as c  # noqa: E402


class TestReturnCodes(unittest.TestCase):
    def test_codes_are_frozen(self):
        self.assertEqual((c.RC_OK, c.RC_FAILED, c.RC_INVALID_SPEC), (0, 1, 2))
        self.assertEqual(c.RC_TIMEOUT, 124)
        self.assertEqual(
            (c.RC_RATE_LIMIT, c.RC_GATE_GIVEUP, c.RC_QUEUE_GIVEUP), (79, 91, 92)
        )
        self.assertEqual(
            c.FAILURE_KINDS, ("gate_giveup", "queue_giveup", "rate_limit", "failed")
        )


class TestAgentSpec(unittest.TestCase):
    def test_valid_specs(self):
        for spec in (
            "codex",
            "codex:gpt-5",
            "opencode:muse-spark-1.3-contributor-free",
            "opencode-go:deepseek-v4-flash",
            "openrouter:vendor/model",
            "amd:DeepSeek-V4-Flash",
            "local",
            "local:gemma4:12b",
        ):
            with self.subTest(spec=spec):
                self.assertTrue(c.validate_agent_spec(spec, "vercel:a"))
    def test_vercel_needs_free_agents(self):
        self.assertFalse(c.validate_agent_spec("vercel:a", ""))
        self.assertFalse(c.validate_agent_spec("vercel:a", "   "))
        self.assertTrue(c.validate_agent_spec("vercel:a", "vercel:a"))

    def test_invalid_specs(self):
        # NOTE: the legacy validator only checks known prefixes, so shell
        # metacharacters like ';' are *valid* here (the docich wrapper
        # AGENT_RE rejects them first).  Slash forms without a colon are
        # invalid, exactly like the legacy ``*`` arm.
        for spec in ("", "claude", "claude:model", "ollama", "ollama:model",
                     "foo", "opencode", "opencode:", "codex ", "local:",
                     "opencode-go/vendor/model", "opencode/vendor/model"):
            with self.subTest(spec=spec):
                self.assertFalse(c.validate_agent_spec(spec, "vercel:a"))
        self.assertTrue(c.validate_agent_spec("opencode:x;y", "vercel:a"))

    def test_minimax_deny(self):
        for spec in (
            "minimax-1", "codex:minimax-x", "opencode:minimax",
            "opencode-go:minimax", "opencode/minimax", "opencode-go/minimax",
        ):
            with self.subTest(spec=spec):
                self.assertTrue(c.is_minimax_denied(spec))
        self.assertFalse(c.is_minimax_denied("opencode:muse-spark-1.3-contributor-free"))


class TestScopes(unittest.TestCase):
    def test_scope_of(self):
        self.assertEqual(c.scope_of("COMMENT"), "comment")
        self.assertEqual(c.scope_of("RADIO:news"), "radio")
        self.assertEqual(c.scope_of("NEWS:spam_check"), "radio")
        self.assertEqual(c.scope_of("JIJI:x"), "radio")
        self.assertEqual(c.scope_of("CELEBRATION:x"), "radio")
        self.assertEqual(c.scope_of("IMPROVE:x"), "improve")
        self.assertEqual(c.scope_of("OTHER:x"), "other")

    def test_lane_of(self):
        self.assertEqual(c.lane_of("COMMENT"), "comment")
        self.assertEqual(c.lane_of("RADIO:main"), "radio")
        self.assertEqual(c.lane_of("NEWS:x"), "radio")
        self.assertEqual(c.lane_of("IMPROVE:x"), "improve")
        self.assertIsNone(c.lane_of("OTHER:x"))

    def test_failure_scope_of(self):
        self.assertEqual(c.failure_scope_of("RADIO:x:prepass"), "radio_prepass")
        self.assertEqual(c.failure_scope_of("RADIO:main"), "radio_main")
        self.assertEqual(c.failure_scope_of("COMMENT"), "default")

    def test_lane_priority(self):
        self.assertLess(c.LANE_PRIORITY["comment"], c.LANE_PRIORITY["radio"])
        self.assertLess(c.LANE_PRIORITY["radio"], c.LANE_PRIORITY["improve"])


class TestTimeouts(unittest.TestCase):
    def setUp(self):
        self.s = c.LlmSettings()

    def test_golden_defaults(self):
        self.assertEqual(c.timeout_for("COMMENT", "opencode:x", self.s), 90)
        self.assertEqual(c.timeout_for("RADIO:main", "opencode:x", self.s), 240)
        self.assertEqual(c.timeout_for("OTHER", "vercel:x", self.s), 45)
        self.assertEqual(c.timeout_for("OTHER", "opencode:x", self.s), 90)

    def test_override_wins(self):
        self.assertEqual(c.timeout_for("COMMENT", "opencode:x", self.s, 120), 120)

    def test_radio_small_override_falls_back(self):
        self.assertEqual(c.timeout_for("RADIO:main", "opencode:x", self.s, 20), 240)

    def test_from_env(self):
        s = c.LlmSettings.from_env(
            {"COMMENT_CODEX_TIMEOUT": "30", "AI_GENERATION_QUEUE_ENABLED": "0"}
        )
        self.assertEqual(s.comment_timeout, 30)
        self.assertFalse(s.queue_enabled)
        s2 = c.LlmSettings.from_env({"COMMENT_CODEX_TIMEOUT": "bogus"})
        self.assertEqual(s2.comment_timeout, 90)

    def test_replace(self):
        s = replace(self.s, queue_wait_sec=1)
        self.assertEqual(s.queue_wait_sec, 1)


if __name__ == "__main__":
    unittest.main()
