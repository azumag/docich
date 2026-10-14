import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "summarize_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("summarize_runtime_timeout_components", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeTimeoutComponentSummaryTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_timeout_buckets_are_cross_tabulated_by_fixed_component_only(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {},
            "ai": {
                "recent_events": [
                    {
                        "event": "fail",
                        "component": "RADIO:secret-show:prepass",
                        "provider": "private-provider-a",
                        "model": "private-model-a",
                        "rc": "124",
                        "error_preview": "provider timeout after 20s private-provider-a",
                    },
                    {
                        "event": "fail",
                        "component": "RADIO:secret-show:main",
                        "provider": "private-provider-b",
                        "model": "private-model-b",
                        "rc": "124",
                        "error_preview": "provider timed out after 45 seconds private-provider-b",
                    },
                    {
                        "event": "fail",
                        "component": "COMMENT:secret-channel",
                        "provider": "private-provider-c",
                        "model": "private-model-c",
                        "rc": "124",
                        "error_preview": "provider timeout without duration private-provider-c",
                    },
                ]
            },
            "improvement": {},
        }

        _, summary = self.mod.summarize(data)

        self.assertIn("ai_recent_timeout_exact_20s=1", summary)
        self.assertIn("ai_recent_timeout_other_known=1", summary)
        self.assertIn("ai_recent_timeout_unknown=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_origin_local_budget=0", summary)
        self.assertIn("ai_recent_timeout_exact_20s_origin_upstream_or_cli=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_origin_unknown=0", summary)
        self.assertIn("ai_recent_timeout_exact_20s_backend_other=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_component_radio_prepass=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_component_radio_main=0", summary)
        self.assertIn("ai_recent_timeout_other_known_component_radio_main=1", summary)
        self.assertIn("ai_recent_timeout_unknown_component_comment=1", summary)
        self.assertNotIn("secret-show", summary)
        self.assertNotIn("secret-channel", summary)
        self.assertNotIn("private-provider", summary)
        self.assertNotIn("private-model", summary)
        self.assertNotIn("45 seconds", summary)

    def test_canonical_local_timeout_preview_is_classified_without_leaking_preview(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {},
            "ai": {
                "recent_events": [
                    {
                        "event": "fail",
                        "component": "RADIO:secret-show:main",
                        "provider": "private-provider",
                        "model": "private-model",
                        "rc": "1",
                        "error_preview": "timeout after 20s",
                    }
                ]
            },
            "improvement": {},
        }

        _, summary = self.mod.summarize(data)

        self.assertIn("ai_recent_timeout_exact_20s=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_origin_local_budget=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_origin_upstream_or_cli=0", summary)
        self.assertIn("ai_recent_timeout_exact_20s_origin_unknown=0", summary)
        self.assertIn("ai_recent_timeout_exact_20s_backend_other=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_component_radio_main=1", summary)
        self.assertNotIn("secret-show", summary)
        self.assertNotIn("private-provider", summary)
        self.assertNotIn("private-model", summary)
        self.assertNotIn("timeout after 20s", summary)

    def test_exact_20s_backend_is_collapsed_to_fixed_family_without_model_leak(self):
        providers = (
            ("local", "local"),
            ("codex", "codex"),
            ("opencode-go", "opencode"),
            ("vercel", "vercel"),
            ("amd", "amd"),
            ("secret-provider", "other"),
        )
        events = []
        for index, (provider, _family) in enumerate(providers):
            events.append(
                {
                    "event": "fail",
                    "component": f"RADIO:{index}:main",
                    "provider": provider,
                    "model": f"private-model-{index}",
                    "rc": "1",
                    "error_preview": "timeout after 20s",
                }
            )
        data = {
            "status": "warn",
            "workers": {},
            "queues": {},
            "ai": {"recent_events": events},
            "improvement": {},
        }

        _, summary = self.mod.summarize(data)

        for _provider, family in providers:
            self.assertIn(f"ai_recent_timeout_exact_20s_backend_{family}=1", summary)
        self.assertNotIn("secret-provider", summary)
        for index in range(len(providers)):
            self.assertNotIn(f"private-model-{index}", summary)

    def test_news_spam_check_timeout_is_not_counted_as_radio_main(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {},
            "ai": {
                "recent_events": [
                    {
                        "event": "fail",
                        "component": "NEWS:spam_check",
                        "provider": "private-provider",
                        "model": "private-model",
                        "rc": "124",
                        "error_preview": "provider timeout after 20s private-provider",
                    }
                ]
            },
            "improvement": {},
        }

        _, summary = self.mod.summarize(data)

        self.assertIn("ai_recent_timeout_exact_20s=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_origin_upstream_or_cli=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_backend_other=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_component_news_spam_check=1", summary)
        self.assertIn("ai_recent_timeout_exact_20s_component_radio_main=0", summary)
        self.assertIn("ai_recent_fail_component_news_spam_check=1", summary)
        self.assertNotIn("NEWS:spam_check", summary)
        self.assertNotIn("private-provider", summary)
        self.assertNotIn("private-model", summary)

    def test_missing_recent_events_emit_zero_for_every_fixed_cross_bucket(self):
        data = {
            "status": "ok",
            "workers": {},
            "queues": {},
            "ai": {},
            "improvement": {},
        }

        _, summary = self.mod.summarize(data)

        for origin in self.mod.TIMEOUT_ORIGINS:
            self.assertIn(f"ai_recent_timeout_exact_20s_origin_{origin}=0", summary)
        for family in self.mod.BACKEND_FAMILIES:
            self.assertIn(f"ai_recent_timeout_exact_20s_backend_{family}=0", summary)
        for bucket in self.mod.TIMEOUT_BUCKETS:
            for component in self.mod.COMPONENTS:
                self.assertIn(f"ai_recent_timeout_{bucket}_component_{component}=0", summary)


if __name__ == "__main__":
    unittest.main()
