import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import ai_generate  # noqa: E402
from test_tts import TtsTestBase  # noqa: E402


class AiTestBase(TtsTestBase):
    def _write_ai(self, game="sorengame"):
        self._write_game(game)
        (self.submodule / "eloop_lib.sh").write_text(
            "#!/usr/bin/env bash\n",
            encoding="utf-8",
        )
        lib = self.submodule / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        (lib / "ai_generate.sh").write_text(
            "#!/usr/bin/env bash\n",
            encoding="utf-8",
        )
        prompt = self.repo_root / "prompt.txt"
        prompt.write_text("generate something\n", encoding="utf-8")
        return prompt


class TestBuildAiInvocation(AiTestBase):
    def test_build_invocation(self):
        prompt = self._write_ai()
        inv = ai_generate.build_ai_invocation(
            self.g,
            game_name="sorengame",
            label="COMMENT",
            agents="opencode:deepseek-v4-flash,qwen35e",
            prompt_file=prompt,
        )
        self.assertEqual(inv.cwd, self.submodule.resolve())
        self.assertEqual(inv.fn_name, "ai_generate_list")
        self.assertEqual(inv.argv[0], "bash")
        self.assertEqual(inv.argv[3], "ai_generate_list")
        self.assertEqual(inv.argv[4], "COMMENT")
        self.assertEqual(inv.argv[6], "opencode:deepseek-v4-flash,qwen35e")
        self.assertEqual(inv.env["DOCICH_CC_ENABLED"], "0")
        self.assertTrue(inv.env["AI_BACKOFF_DIR"].endswith("ai_backoff"))

    def test_label_must_start_with_comment_or_radio(self):
        prompt = self._write_ai()
        with self.assertRaises(ai_generate.AiError):
            ai_generate.build_ai_invocation(
                self.g, game_name="sorengame", label="NEWS", agents="opencode:x",
                prompt_file=prompt,
            )

    def test_agents_require_safe_identifiers(self):
        prompt = self._write_ai()
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
        prompt = self._write_ai()
        agents = (
            "opencode-go:union-alpha,"
            "openrouter:stealth/union-alpha,"
            "opencode:deepseek-v4-flash"
        )
        inv = ai_generate.build_ai_invocation(
            self.g, game_name="sorengame", label="COMMENT",
            agents=agents, prompt_file=prompt,
        )
        self.assertEqual(inv.agents, agents)
        self.assertEqual(inv.argv[6], agents)

    def test_namespaced_models_cross_bash_wrapper_unchanged(self):
        prompt = self._write_ai()
        (self.submodule / "eloop_lib.sh").write_text(
            'ai_generate_list() { printf "%s" "$3" > "$6"; }\n',
            encoding="utf-8",
        )
        agents = "opencode-go/union-alpha,openrouter:stealth/union-alpha,local"
        winner = self.repo_root / "received-agents.txt"
        inv = ai_generate.build_ai_invocation(
            self.g, game_name="sorengame", label="COMMENT",
            agents=f" {agents} ", prompt_file=prompt, last_agent_file=winner,
        )
        result = ai_generate.run(inv.argv, cwd=str(inv.cwd), env_extra=inv.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(winner.read_text(), agents)

    def test_namespaced_models_still_reject_shell_syntax(self):
        for agents in (
            "opencode:openrouter/stealth/union-alpha;true",
            "opencode:$(id)/union-alpha",
            "opencode:openrouter/stealth/union-alpha\ntrue",
            "opencode:openrouter/stealth/union-alpha|cat",
            "/openrouter/stealth/union-alpha",
            "opencode:openrouter/stealth/union-alpha,,local",
        ):
            with self.subTest(agents=agents), self.assertRaises(ai_generate.AiError):
                ai_generate._validate_agents(agents, "agents")

    def test_agent_identifier_length_boundary(self):
        valid = "openrouter/" + "x" * (128 - len("openrouter/"))
        self.assertEqual(ai_generate._validate_agents(valid, "agents"), valid)
        with self.assertRaises(ai_generate.AiError):
            ai_generate._validate_agents(valid + "x", "agents")

    def test_missing_prompt_raises(self):
        self._write_ai()
        with self.assertRaises(ai_generate.AiError):
            ai_generate.build_ai_invocation(
                self.g, game_name="sorengame", label="COMMENT",
                agents="opencode:x", prompt_file=self.repo_root / "nope.txt",
            )

    def test_timeout_must_be_positive(self):
        prompt = self._write_ai()
        with self.assertRaises(ai_generate.AiError):
            ai_generate.build_ai_invocation(
                self.g, game_name="sorengame", label="COMMENT",
                agents="opencode:x", prompt_file=prompt, timeout=0,
            )

    def test_positional_slots_stay_aligned_without_timeout(self):
        prompt = self._write_ai()
        inv = ai_generate.build_ai_invocation(
            self.g, game_name="sorengame", label="COMMENT",
            agents="opencode:x", prompt_file=prompt,
        )
        self.assertEqual(inv.argv[4], "COMMENT")
        self.assertEqual(inv.argv[6], "opencode:x")
        self.assertEqual(inv.argv[7], "")  # timeout slot kept empty
        self.assertEqual(inv.argv[8], "")  # validator omitted
        self.assertTrue(inv.argv[9].endswith("last_agent.txt"))
        self.assertTrue(inv.argv[10].endswith("failure_kind.txt"))

    def test_positional_slots_with_timeout(self):
        prompt = self._write_ai()
        inv = ai_generate.build_ai_invocation(
            self.g, game_name="sorengame", label="COMMENT",
            agents="opencode:x", prompt_file=prompt, timeout=120,
        )
        self.assertEqual(inv.argv[7], "120")
        self.assertEqual(inv.argv[8], "")
        self.assertTrue(inv.argv[9].endswith("last_agent.txt"))
        self.assertTrue(inv.argv[10].endswith("failure_kind.txt"))

    def test_dry_run_repro(self):
        prompt = self._write_ai()
        rc, detail = ai_generate.run_ai(
            self.g, game_name="sorengame", label="COMMENT",
            agents="opencode:x", prompt_file=prompt, dry_run=True,
        )
        self.assertEqual(rc, 0)
        self.assertIn("function=ai_generate_list", detail)
        self.assertIn("COMMENT", detail)

    def test_real_run_blocked_without_allow_env(self):
        prompt = self._write_ai()
        os.environ.pop("DOCICH_ALLOW_REAL_AI", None)
        with self.assertRaises(ai_generate.AiError) as ctx:
            ai_generate.run_ai(
                self.g, game_name="sorengame", label="COMMENT",
                agents="opencode:x", prompt_file=prompt,
            )
        self.assertIn("DOCICH_ALLOW_REAL_AI", str(ctx.exception))

    @mock.patch("docich.ai_generate.run")
    def test_run_forwards_invocation(self, mock_run):
        prompt = self._write_ai()
        mock_run.return_value = mock.Mock(returncode=0, stderr="")
        os.environ["DOCICH_ALLOW_REAL_AI"] = "1"
        rc, detail = ai_generate.run_ai(
            self.g, game_name="sorengame", label="COMMENT",
            agents="opencode:x", prompt_file=prompt,
        )
        self.assertEqual(rc, 0)
        call = mock_run.call_args[0][0]
        self.assertEqual(call[3], "ai_generate_list")
        self.assertTrue(mock_run.call_args.kwargs["capture"])
