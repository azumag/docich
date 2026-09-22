import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import ai_generate  # noqa: E402
import docich.llm.dispatch as dispatch_module  # noqa: E402
from docich.llm.backoff import model_backoff_seconds  # noqa: E402
from docich.llm.contracts import DispatchRequest, ProviderResult  # noqa: E402
from docich.llm.dispatch import Dispatcher  # noqa: E402
from docich.llm.policy import parse_agents  # noqa: E402


class TestNativeAiGenerate(unittest.TestCase):
    def _prompt(self, root: Path) -> Path:
        prompt = root / "prompt.txt"
        prompt.write_text("generate something\n", encoding="utf-8")
        return prompt

    def test_dry_run_is_game_independent_and_does_not_call_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = self._prompt(Path(tmp))
            with mock.patch("docich.llm.dispatch.call_agent") as call_agent:
                rc, detail = ai_generate.run_ai(
                    None,
                    game_name=None,
                    label="COMMENT:test",
                    agents="opencode:deepseek-v4-flash,local",
                    prompt_file=prompt,
                    dry_run=True,
                )
            self.assertEqual(rc, 0)
            self.assertIn("backend=native", detail)
            self.assertIn("COMMENT:test", detail)
            self.assertIn("opencode/deepseek-v4-flash", detail)
            call_agent.assert_not_called()

    def test_real_run_remains_explicitly_gated(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = self._prompt(Path(tmp))
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("DOCICH_ALLOW_REAL_AI", None)
                with self.assertRaises(ai_generate.AiError) as ctx:
                    ai_generate.run_ai(
                        None,
                        label="COMMENT",
                        agents="codex",
                        prompt_file=prompt,
                    )
            self.assertIn("DOCICH_ALLOW_REAL_AI", str(ctx.exception))

    def test_run_prompt_remains_explicitly_gated(self):
        with mock.patch("docich.llm.dispatch.call_agent") as call_agent:
            with self.assertRaises(ai_generate.AiError) as ctx:
                ai_generate.run_prompt(
                    None,
                    label="COMMENT:test",
                    agents="codex",
                    prompt_text="private prompt",
                    env={},
                )
        self.assertIn("DOCICH_ALLOW_REAL_AI", str(ctx.exception))
        call_agent.assert_not_called()

    def test_input_policy_rejects_unsafe_or_retired_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = self._prompt(Path(tmp))
            for label, agents in (
                ("NEWS", "codex"),
                ("COMMENT", "opencode:x;true"),
                ("COMMENT", "minimax"),
                ("COMMENT", "opencode:x,,local"),
            ):
                with self.subTest(label=label, agents=agents), self.assertRaises(ai_generate.AiError):
                    ai_generate.run_ai(
                        None,
                        label=label,
                        agents=agents,
                        prompt_file=prompt,
                        dry_run=True,
                    )

    def test_dispatch_falls_back_and_writes_only_sanitized_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "DOCICH_LLM_STATE_DIR": str(root / "state"),
                "AI_BACKOFF_DIR": str(root / "backoff"),
                "AI_FAIL_STREAK_DIR": str(root / "streak"),
                "DOCICH_LLM_STATS_DIR": str(root / "stats"),
                "DOCICH_LLM_TELEMETRY": "1",
            }
            calls = []

            def provider(spec, request, timeout, provider_env):
                calls.append((spec.raw, request.label, timeout))
                if spec.raw == "codex:first":
                    return ProviderResult(1, failure_kind="provider_failed")
                return ProviderResult(0, output="answer")

            request = DispatchRequest(
                label="COMMENT:test",
                prompt="private prompt must not be persisted",
                agents=parse_agents("codex:first,local", env),
            )
            last_agent = root / "last_agent"
            failure_kind = root / "failure_kind"
            result = Dispatcher(env=env, provider_caller=provider).dispatch(
                request,
                last_agent_file=last_agent,
                failure_kind_file=failure_kind,
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.output, "answer")
            self.assertEqual(result.last_agent, "local")
            self.assertEqual([item[0] for item in calls], ["codex:first", "local"])
            self.assertEqual(last_agent.read_text(encoding="utf-8").strip(), "local")
            self.assertEqual(failure_kind.read_text(encoding="utf-8"), "")
            telemetry = (root / "stats").glob("*.jsonl")
            rows = "\n".join(path.read_text(encoding="utf-8") for path in telemetry)
            self.assertNotIn("private prompt", rows)
            self.assertNotIn("answer", rows)

    def test_rate_limit_backoff_skips_the_same_agent_on_the_next_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "DOCICH_LLM_STATE_DIR": str(root / "state"),
                "AI_BACKOFF_DIR": str(root / "backoff"),
                "AI_FAIL_STREAK_DIR": str(root / "streak"),
                "DOCICH_LLM_TELEMETRY": "0",
                "AI_BACKOFF_SEC_ITEMS": "first:30",
            }
            request = DispatchRequest(
                label="COMMENT:test",
                prompt="prompt",
                agents=parse_agents("codex:first,local", env),
            )
            first_calls = []

            def first_provider(spec, request, timeout, provider_env):
                first_calls.append(spec.raw)
                if spec.raw == "codex:first":
                    return ProviderResult(79, failure_kind="rate_limit")
                return ProviderResult(0, output="fallback")

            first = Dispatcher(env=env, provider_caller=first_provider).dispatch(request)
            self.assertEqual(first.returncode, 0)
            self.assertEqual(first.last_agent, "local")
            self.assertEqual(first_calls, ["codex:first", "local"])

            second_calls = []

            def second_provider(spec, request, timeout, provider_env):
                second_calls.append(spec.raw)
                return ProviderResult(0, output="second")

            second = Dispatcher(env=env, provider_caller=second_provider).dispatch(request)
            self.assertEqual(second.returncode, 0)
            self.assertEqual(second.last_agent, "local")
            self.assertEqual(second_calls, ["local"])

    def test_muse_free_backoff_uses_model_suffix_and_utc_reset(self):
        env = {
            "AI_BACKOFF_SEC_ITEMS": (
                "muse-spark-1.3-contributor-free:86400 "
                "muse-spark-1.3-contributor:86400"
            )
        }
        free = parse_agents("opencode:muse-spark-1.3-contributor-free", env)[0]
        paid = parse_agents("opencode-go:muse-spark-1.3-contributor", env)[0]
        self.assertEqual(model_backoff_seconds(free, "RADIO", env, now=1788807600), 18000)
        self.assertEqual(model_backoff_seconds(paid, "RADIO", env, now=1788807600), 86400)

        shorter = dict(env)
        shorter["AI_BACKOFF_SEC_ITEMS"] = "muse-spark-1.3-contributor-free:300"
        self.assertEqual(model_backoff_seconds(free, "RADIO", shorter, now=1788807600), 300)

    def test_validator_rejection_falls_back_without_model_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "DOCICH_LLM_STATE_DIR": str(root / "state"),
                "AI_BACKOFF_DIR": str(root / "backoff"),
                "AI_FAIL_STREAK_DIR": str(root / "streak"),
                "DOCICH_LLM_TELEMETRY": "0",
            }
            calls = []

            def provider(spec, request, timeout, provider_env):
                calls.append(spec.raw)
                return ProviderResult(0, output="bad" if spec.raw == "codex:first" else "good")

            request = DispatchRequest(
                label="RADIO:test",
                prompt="prompt",
                agents=parse_agents("codex:first,local", env),
                validator=lambda output: output == "good",
            )
            result = Dispatcher(env=env, provider_caller=provider).dispatch(request)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.output, "good")
            self.assertEqual(calls, ["codex:first", "local"])
            self.assertFalse((root / "backoff" / "codex:first").exists())

    def test_dispatch_reclamps_provider_timeout_after_lock_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "DOCICH_LLM_STATE_DIR": str(root / "state"),
                "AI_BACKOFF_DIR": str(root / "backoff"),
                "AI_FAIL_STREAK_DIR": str(root / "streak"),
                "DOCICH_LLM_TELEMETRY": "0",
                "AI_GENERATION_QUEUE_ENABLED": "1",
                "OPENCODE_RUN_LOCK_ENABLED": "1",
            }
            clock = [100.0]
            observed_timeouts = []

            class DelayingLock:
                def __init__(self, *args, **kwargs):
                    pass

                def acquire(self, *, deadline=None):
                    clock[0] += 0.3

                def release(self):
                    pass

            def provider(spec, request, timeout, provider_env):
                observed_timeouts.append(timeout)
                return ProviderResult(0, output="ok")

            request = DispatchRequest(
                label="COMMENT:test",
                prompt="prompt",
                agents=parse_agents("opencode:foo", env),
                timeout_sec=10,
            )
            with (
                mock.patch.object(dispatch_module, "FileLock", DelayingLock),
                mock.patch.object(dispatch_module.time, "monotonic", side_effect=lambda: clock[0]),
            ):
                result = Dispatcher(env=env, provider_caller=provider).dispatch(
                    request,
                    overall_timeout_sec=1,
                )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(len(observed_timeouts), 1)
            self.assertAlmostEqual(observed_timeouts[0], 0.4, places=6)

    def test_dispatch_does_not_start_provider_after_lock_wait_exhausts_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "DOCICH_LLM_STATE_DIR": str(root / "state"),
                "AI_BACKOFF_DIR": str(root / "backoff"),
                "AI_FAIL_STREAK_DIR": str(root / "streak"),
                "DOCICH_LLM_TELEMETRY": "0",
                "AI_GENERATION_QUEUE_ENABLED": "1",
                "OPENCODE_RUN_LOCK_ENABLED": "1",
            }
            clock = [100.0]
            calls = []

            class DelayingLock:
                def __init__(self, *args, **kwargs):
                    pass

                def acquire(self, *, deadline=None):
                    clock[0] += 0.6

                def release(self):
                    pass

            def provider(spec, request, timeout, provider_env):
                calls.append(timeout)
                return ProviderResult(0, output="must not run")

            request = DispatchRequest(
                label="COMMENT:test",
                prompt="prompt",
                agents=parse_agents("opencode:foo", env),
                timeout_sec=10,
            )
            with (
                mock.patch.object(dispatch_module, "FileLock", DelayingLock),
                mock.patch.object(dispatch_module.time, "monotonic", side_effect=lambda: clock[0]),
            ):
                result = Dispatcher(env=env, provider_caller=provider).dispatch(
                    request,
                    overall_timeout_sec=1,
                )

            self.assertEqual(result.returncode, 124)
            self.assertEqual(result.failure_kind, "timeout")
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
