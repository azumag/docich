"""Native LLM provider tests (#829 PR-1, mock only, no network).

Provider binaries are faked with tiny shell scripts on a private PATH.
The local-LLM HTTP call is faked by stubbing the urllib opener.
"""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.llm import contracts as c  # noqa: E402
from docich.llm.providers import base as b  # noqa: E402
from docich.llm.providers import codex as codex_prov  # noqa: E402
from docich.llm.providers import local_llm as local_prov  # noqa: E402
from docich.llm.providers import opencode as opencode_prov  # noqa: E402


def _write_script(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class ProviderTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="docich-llm-prov-"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.settings = c.LlmSettings(
            codex_bin="codex",
            opencode_bin="opencode",
            opencode_abort_retry=False,
            rotation_gate_enabled=False,
        )
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.bin) + os.pathsep + self._old_path

    def tearDown(self):
        os.environ["PATH"] = self._old_path

    def _fake(self, name: str, body: str) -> Path:
        path = self.bin / name
        _write_script(path, body)
        return path


class TestModelMapping(unittest.TestCase):
    def test_opencode_mapping(self):
        cases = {
            "openrouter:vendor/m": "openrouter/vendor/m",
            "vercel:poolside/m": "vercel/poolside/m",
            "amd:DeepSeek-V4-Flash": "amd-token-factory/DeepSeek-V4-Flash",
            "opencode-go/m": "opencode-go/m",
            "opencode-go:m": "opencode-go/m",
            "opencode/m": "opencode/m",
            "opencode:m": "opencode/m",
        }
        for agent, expected in cases.items():
            with self.subTest(agent=agent):
                self.assertEqual(opencode_prov.model_from_agent(agent), expected)

    def test_agent_args(self):
        self.assertEqual(
            opencode_prov.agent_args("vercel:x", "RADIO:news"),
            ["--agent", "soren-lite"],
        )
        self.assertEqual(
            opencode_prov.agent_args("amd:x", "RADIO:y:prepass"),
            ["--agent", "soren-research"],
        )
        self.assertEqual(opencode_prov.agent_args("opencode:x", "RADIO:news"), [])
        self.assertEqual(opencode_prov.agent_args("vercel:x", "COMMENT"), ["--agent", "soren-lite"])


class TestTextClassifiers(unittest.TestCase):
    def test_rate_limit_text(self):
        for text in ("429 Too Many Requests", "Rate limit exceeded",
                     "quota exhausted", "RESOURCE_EXHAUSTED", "usage limit hit"):
            with self.subTest(text=text):
                self.assertTrue(b.rate_limit_detected(text))
        self.assertFalse(b.rate_limit_detected("hello world"))

    def test_provider_error_text(self):
        self.assertTrue(b.provider_error_detected("invalid bearer token"))
        self.assertTrue(b.provider_error_detected("model not found"))
        self.assertFalse(b.provider_error_detected("hello world"))

    def test_clean_text_strips_reasoning(self):
        self.assertEqual(b.clean_text("<think>hmm</think>answer"), "answer")
        self.assertEqual(b.clean_text("a<analysis>x</analysis>b"), "ab")
        self.assertEqual(b.clean_text("\x1b[31mhi\x1b[0m"), "hi")


class TestCodexProvider(ProviderTestBase):
    def test_argv_shape_and_model(self):
        capture = self.tmp / "argv.txt"
        self._fake(
            "codex",
            'out=""; prev=""; for a in "$@"; do'
            ' if [ "$prev" = "-o" ]; then out="$a"; fi; prev="$a"; done;'
            ' printf "hello-codex" > "$out";'
            ' printf "%s\\n" "$@" >> "' + str(capture) + '"\n',
        )
        result = codex_prov.run("codex:gpt-5", "do things", 30, self.settings)
        self.assertEqual(result.rc, 0)
        self.assertEqual(result.stdout, "hello-codex")
        self.assertEqual(result.resolved_model, "gpt-5")
        argv = capture.read_text(encoding="utf-8").splitlines()
        self.assertEqual(argv[:4], ["exec", "--skip-git-repo-check", "-m", "gpt-5"])
        self.assertIn("do things", argv)

    def test_default_model(self):
        self._fake("codex", 'out=""; prev=""; for a in "$@"; do'
                  ' if [ "$prev" = "-o" ]; then out="$a"; fi; prev="$a"; done;'
                  ' printf "hi" > "$out"\n')
        result = codex_prov.run("codex", "prompt", 30, self.settings)
        self.assertEqual(result.rc, 0)
        self.assertEqual(result.resolved_model, "amd-token-factory-deepseek-v4-flash")

    def test_missing_binary(self):
        settings = c.LlmSettings(codex_bin="/nonexistent/codex-xyz")
        # Hermetic: the provider falls back to "codex" on PATH, so hide the
        # real binary — otherwise this test would spawn a real CLI (rc 124).
        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            result = codex_prov.run("codex", "prompt", 5, settings)
        self.assertEqual(result.rc, 1)

    def test_timeout_kills(self):
        self._fake("codex", "sleep 30\n")
        result = codex_prov.run("codex", "prompt", 1, self.settings)
        self.assertEqual(result.rc, 124)
        self.assertTrue(result.timed_out)

    def test_rate_limit_output_maps_to_79(self):
        self._fake("codex", 'out=""; prev=""; for a in "$@"; do'
                  ' if [ "$prev" = "-o" ]; then out="$a"; fi; prev="$a"; done;'
                  ' printf "Error: rate limit exceeded" > "$out"\n')
        result = codex_prov.run("codex", "prompt", 10, self.settings)
        self.assertEqual(result.rc, 79)


class TestOpencodeProvider(ProviderTestBase):
    def test_argv_shape_and_mapping(self):
        capture = self.tmp / "argv.txt"
        self._fake(
            "opencode",
            'printf "hello-opencode"; printf "%s\\n" "$@" >> "' + str(capture) + '"\n',
        )
        result = opencode_prov.run(
            "openrouter:vendor/m", "prompt text", 30, self.settings,
            label="COMMENT", state_dir=self.state,
        )
        self.assertEqual(result.rc, 0)
        self.assertEqual(result.stdout, "hello-opencode")
        self.assertEqual(result.resolved_model, "openrouter/vendor/m")
        argv = capture.read_text(encoding="utf-8").splitlines()
        self.assertEqual(argv[0], "run")
        self.assertIn("--model", argv)
        self.assertIn("openrouter/vendor/m", argv)
        self.assertIn("prompt text", argv)

    def test_vercel_gets_agent_args(self):
        capture = self.tmp / "argv.txt"
        self._fake(
            "opencode",
            'printf "ok"; printf "%s\\n" "$@" >> "' + str(capture) + '"\n',
        )
        result = opencode_prov.run(
            "vercel:pool/m", "p", 30, self.settings,
            label="RADIO:news", state_dir=self.state,
        )
        self.assertEqual(result.rc, 0)
        argv = capture.read_text(encoding="utf-8").split()
        self.assertIn("--agent", argv)
        self.assertIn("soren-lite", argv)

    def test_rate_limit_output_maps_to_79(self):
        self._fake("opencode", 'printf "429 Too Many Requests"\n')
        result = opencode_prov.run(
            "opencode:x", "p", 10, self.settings, state_dir=self.state,
        )
        self.assertEqual(result.rc, 79)

    def test_abort_retry(self):
        flag = self.tmp / "calls.txt"
        self._fake(
            "opencode",
            'printf "x" >> "' + str(flag) + '"; printf "boom"; exit 1\n',
        )
        settings = c.LlmSettings(
            opencode_bin="opencode", opencode_abort_retry=True,
            opencode_abort_retry_wait_sec=0, rotation_gate_enabled=False,
        )
        result = opencode_prov.run(
            "opencode:x", "p", 10, settings, state_dir=self.state,
        )
        self.assertEqual(result.rc, 1)
        self.assertEqual(flag.read_text(encoding="utf-8"), "xx")

    def test_no_retry_for_vercel(self):
        flag = self.tmp / "calls.txt"
        self._fake(
            "opencode",
            'printf "x" >> "' + str(flag) + '"; printf "boom"; exit 1\n',
        )
        settings = c.LlmSettings(
            opencode_bin="opencode", opencode_abort_retry=True,
            opencode_abort_retry_wait_sec=0, rotation_gate_enabled=False,
        )
        result = opencode_prov.run(
            "vercel:pool/m", "p", 10, settings, state_dir=self.state,
        )
        self.assertEqual(result.rc, 1)
        self.assertEqual(flag.read_text(encoding="utf-8"), "x")

    def test_xdg_env_scoped_to_child(self):
        seen = self.tmp / "env.txt"
        self._fake(
            "opencode",
            'printf "XDG_STATE_HOME=$XDG_STATE_HOME\\n" >> "' + str(seen) + '"; printf ok\n',
        )
        result = opencode_prov.run(
            "opencode:x", "p", 10, self.settings, state_dir=self.state,
        )
        self.assertEqual(result.rc, 0)
        line = seen.read_text(encoding="utf-8").strip()
        child_home = line.split("=", 1)[1]
        self.assertIn("xdg_state", child_home)
        # The parent environment is never mutated by the fixed setup.
        self.assertNotEqual(os.environ.get("XDG_STATE_HOME"), child_home)


class TestLocalProvider(unittest.TestCase):
    def test_chat_completions_parsing(self):
        payload = b'{"choices": [{"message": {"content": "local answer"}}]}'

        class FakeResponse:
            def read(self, *args):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeOpener:
            def open(self, request, timeout=None):
                self.request = request
                self.timeout = timeout
                return FakeResponse()

        opener = FakeOpener()
        with mock.patch("urllib.request.build_opener", return_value=opener):
            from docich.llm.providers import openai_compatible as oc

            result = oc.post_chat_completions(
                "http://127.0.0.1:9", "gemma4:12b", "hello", 5,
                extra_body={"temperature": 0.7, "num_predict": 1600},
            )
        self.assertEqual(result.rc, 0)
        self.assertEqual(result.stdout, "local answer")
        self.assertTrue(opener.request.full_url.endswith("/v1/chat/completions"))

    def test_model_from_agent(self):
        settings = c.LlmSettings()
        self.assertEqual(local_prov.model_from_agent("local:foo", settings), "foo")
        self.assertEqual(
            local_prov.model_from_agent("local", settings), settings.local_model
        )


if __name__ == "__main__":
    unittest.main()
