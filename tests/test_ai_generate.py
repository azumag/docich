"""Native ``docich ai`` dispatch tests (#829 PR-1, mock only).

No shell, no network, no credentials, no game checkout.  Provider runs are
faked by monkeypatching the dispatch layer; validation, file contracts, and
CLI gates are exercised for real.
"""

import io
import os
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from docich import ai_generate, cli  # noqa: E402
from docich.llm import contracts as contracts_mod  # noqa: E402
from test_tts import TtsTestBase  # noqa: E402


class AiNativeTestBase(TtsTestBase):
    def _write_prompt(self, text="generate something\n"):
        prompt = self.repo_root / "prompt.txt"
        prompt.write_text(text, encoding="utf-8")
        return prompt


class TestBuildAiInvocation(AiNativeTestBase):
    def test_build_invocation(self):
        prompt = self._write_prompt()
        inv = ai_generate.build_ai_invocation(
            self.g,
            game_name="sorengame",
            label="COMMENT",
            agents="opencode:deepseek-v4-flash,qwen35e",
            prompt_file=prompt,
        )
        self.assertEqual(inv.label, "COMMENT")
        self.assertEqual(inv.agents, "opencode:deepseek-v4-flash,qwen35e")
        self.assertEqual(inv.prompt_text, "generate something\n")
        self.assertTrue(inv.state_dir.is_absolute())
        self.assertTrue(str(inv.last_agent_file).endswith("last_agent.txt"))
        self.assertTrue(str(inv.failure_kind_file).endswith("failure_kind.txt"))

    def test_label_must_start_with_comment_or_radio(self):
        prompt = self._write_prompt()
        with self.assertRaises(ai_generate.AiError):
            ai_generate.build_ai_invocation(
                self.g, game_name="sorengame", label="NEWS", agents="opencode:x",
                prompt_file=prompt,
            )

    def test_agents_require_safe_identifiers(self):
        prompt = self._write_prompt()
        with self.assertRaises(ai_generate.AiError):
            ai_generate.build_ai_invocation(
                self.g, game_name="sorengame", label="COMMENT",
                agents="opencode:x;rm -rf /", prompt_file=prompt,
            )
        with self.assertRaises(ai_generate.AiError):
            ai_generate.build_ai_invocation(
                self.g, game_name="sorengame", label="COMMENT",
                agents="", prompt_file=prompt,
            )

    def test_namespaced_models_preserve_route_and_order(self):
        prompt = self._write_prompt()
        agents = (
            "opencode-go:namespaced-model,"
            "openrouter:vendor/namespaced-model,"
            "opencode:deepseek-v4-flash"
        )
        inv = ai_generate.build_ai_invocation(
            self.g, game_name="sorengame", label="COMMENT",
            agents=agents, prompt_file=prompt,
        )
        self.assertEqual(inv.agents, agents)

    def test_namespaced_models_still_reject_shell_syntax(self):
        for agents in (
            "opencode:openrouter/vendor/namespaced-model;true",
            "opencode:$(id)/namespaced-model",
            "opencode:openrouter/vendor/namespaced-model\ntrue",
            "opencode:openrouter/vendor/namespaced-model|cat",
            "/openrouter/vendor/namespaced-model",
            "opencode:openrouter/vendor/namespaced-model,,local",
        ):
            with self.subTest(agents=agents), self.assertRaises(ai_generate.AiError):
                ai_generate._validate_agents(agents, "agents")

    def test_agent_identifier_length_boundary(self):
        valid = "openrouter/" + "x" * (128 - len("openrouter/"))
        self.assertEqual(ai_generate._validate_agents(valid, "agents"), valid)
        with self.assertRaises(ai_generate.AiError):
            ai_generate._validate_agents(valid + "x", "agents")

    def test_missing_prompt_raises(self):
        with self.assertRaises(ai_generate.AiError):
            ai_generate.build_ai_invocation(
                self.g, game_name="sorengame", label="COMMENT",
                agents="opencode:x", prompt_file=self.repo_root / "nope.txt",
            )

    def test_timeout_must_be_positive(self):
        prompt = self._write_prompt()
        with self.assertRaises(ai_generate.AiError):
            ai_generate.build_ai_invocation(
                self.g, game_name="sorengame", label="COMMENT",
                agents="opencode:x", prompt_file=prompt, timeout=0,
            )

    def test_native_invocation_has_no_shell(self):
        """The native boundary must not reference the legacy shell entry."""
        prompt = self._write_prompt()
        inv = ai_generate.build_ai_invocation(
            self.g, game_name="sorengame", label="COMMENT",
            agents="opencode:x", prompt_file=prompt,
        )
        self.assertNotIn("ai_generate_list", inv.repro())
        self.assertIn("native llm", inv.repro())
        self.assertIn("COMMENT", inv.repro())

    def test_dry_run_repro(self):
        prompt = self._write_prompt()
        rc, detail = ai_generate.run_ai(
            self.g, game_name="sorengame", label="COMMENT",
            agents="opencode:x", prompt_file=prompt, dry_run=True,
        )
        self.assertEqual(rc, 0)
        self.assertIn("native llm", detail)
        self.assertIn("COMMENT", detail)

    def test_real_run_blocked_without_allow_env(self):
        prompt = self._write_prompt()
        os.environ.pop("DOCICH_ALLOW_REAL_AI", None)
        with self.assertRaises(ai_generate.AiError) as ctx:
            ai_generate.run_ai(
                self.g, game_name="sorengame", label="COMMENT",
                agents="opencode:x", prompt_file=prompt,
            )
        self.assertIn("DOCICH_ALLOW_REAL_AI", str(ctx.exception))

    @mock.patch("docich.llm.policy.generate_list")
    def test_run_forwards_native_request(self, fake_generate):
        prompt = self._write_prompt()
        fake_generate.return_value = (0, "hello", "opencode:x", "")
        with mock.patch.dict(os.environ, {"DOCICH_ALLOW_REAL_AI": "1"}):
            rc, detail = ai_generate.run_ai(
                self.g, game_name="sorengame", label="COMMENT",
                agents="opencode:x", prompt_file=prompt,
            )
        self.assertEqual(rc, 0)
        self.assertIn("winner=opencode:x", detail)
        call = fake_generate.call_args
        self.assertEqual(call.args[2], "COMMENT")
        self.assertEqual(call.args[3], "generate something\n")
        self.assertEqual(call.args[4], ["opencode:x"])

    @mock.patch("docich.llm.policy.generate_list")
    def test_run_reports_failure_kind(self, fake_generate):
        prompt = self._write_prompt()
        fake_generate.return_value = (1, "", "", "failed")
        with mock.patch.dict(os.environ, {"DOCICH_ALLOW_REAL_AI": "1"}):
            rc, detail = ai_generate.run_ai(
                self.g, game_name="sorengame", label="COMMENT",
                agents="opencode:x", prompt_file=prompt,
            )
        self.assertEqual(rc, 1)
        self.assertIn("failure_kind=failed", detail)

    def test_settings_defaults_match_golden(self):
        settings = contracts_mod.LlmSettings()
        self.assertEqual(settings.comment_timeout, 90)
        self.assertEqual(settings.radio_timeout, 240)
        self.assertEqual(settings.codex_timeout, 300)
        self.assertEqual(settings.local_timeout, 180)
        self.assertEqual(settings.agent_backoff_sec, 600)
        self.assertEqual(settings.comment_agent_backoff_sec, 18000)
        self.assertEqual(settings.radio_agent_backoff_sec, 18000)
        self.assertEqual(settings.failure_backoff_sec, 300)
        self.assertEqual(settings.queue_stale_sec, 900)
        self.assertEqual(settings.improve_gate_wait_max_sec, 1200)


class TestCliAi(AiNativeTestBase):
    def test_ai_parse(self):
        args = cli.build_parser().parse_args(
            ["ai", "sorengame", "--label", "COMMENT", "--agents", "opencode:x",
             "--prompt-file", "p.txt", "--dry-run"]
        )
        self.assertEqual(args.label, "COMMENT")
        self.assertTrue(args.dry_run)

    def test_ai_dry_run_prints_preview(self):
        prompt = self._write_prompt()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(
                ["--config", str(self.toml), "ai", "sorengame",
                 "--label", "COMMENT", "--agents", "opencode:x",
                 "--prompt-file", str(prompt), "--dry-run"]
            )
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("docich: ai dry-run:", out.getvalue())
        self.assertIn("native llm", out.getvalue())

    def test_ai_failure_prints_stderr(self):
        from docich import ai_generate as ai_mod

        self._write_prompt()
        with mock.patch.object(
            ai_mod, "run_ai", return_value=(1, "failure_kind=failed rc=1")
        ):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main(
                    ["--config", str(self.toml), "ai", "sorengame",
                     "--label", "COMMENT", "--agents", "opencode:x",
                     "--prompt-file", "p.txt"]
                )
        self.assertEqual(rc, 1)
        self.assertIn("エラー終了しました", out.getvalue())
        self.assertIn("failure_kind=failed", err.getvalue())


if __name__ == "__main__":
    unittest.main()
