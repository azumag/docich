from __future__ import annotations

import importlib.util
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "configure_jev_route.py"
CLASSIFIER_SCRIPT = ROOT / "ops" / "vm_actions" / "configure_comment_classifier_jev.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


route = load_module(SCRIPT, "configure_jev_route")
classifier = load_module(CLASSIFIER_SCRIPT, "configure_comment_classifier_jev_for_route_tests")


class GatewayStdinExecutionTests(unittest.TestCase):
    def test_reviewed_stdin_execution_can_load_classifier_sibling(self):
        result = subprocess.run(
            [sys.executable, "-", "--help"],
            input=SCRIPT.read_bytes(),
            cwd=ROOT,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertIn(b"--route", result.stdout)
        self.assertIn(b"--disable", result.stdout)


class OwnershipBoundaryTests(unittest.TestCase):
    def test_managed_keys_never_overlap_with_678s_own_script(self):
        # The two owner-only scripts must never fight over the same key.
        self.assertEqual(set(route.MANAGED_KEYS + route.RETIRED_KEYS) & set(classifier.MANAGED_KEYS), set())

    def test_managed_keys_are_exactly_the_route_keys_the_classifier_reads(self):
        self.assertEqual(set(route.MANAGED_KEYS), {"DOCICH_JEV_ROUTE", "DOCICH_JEV_VERCEL_API_KEY"})
        self.assertEqual(route.RETIRED_KEYS, ("DOCICH_SEMANTIC_BACKEND",))


class ConfigureDirectEnvTests(unittest.TestCase):
    def test_sets_route_without_touching_678_keys(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text(
                "KEEP=value\n"
                "COMMENT_CLASSIFIER_BACKEND=jev\n"
                "TYPESAFE_API_KEY=already-configured\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            backup = route.configure_direct_env(env_file)

            current = env_file.read_text(encoding="utf-8")
            self.assertIn("KEEP=value\n", current)
            self.assertIn("COMMENT_CLASSIFIER_BACKEND=jev\n", current)
            self.assertIn("TYPESAFE_API_KEY=already-configured\n", current)
            self.assertNotIn("DOCICH_SEMANTIC_BACKEND", current)
            self.assertIn("DOCICH_JEV_ROUTE=direct\n", current)
            self.assertNotIn("DOCICH_JEV_VERCEL_API_KEY", current)
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    def test_replaces_its_own_prior_assignment_and_drops_the_retired_switch(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text(
                "DOCICH_SEMANTIC_BACKEND=jev\n"
                "DOCICH_JEV_ROUTE=vercel\n"
                "DOCICH_JEV_VERCEL_API_KEY=old-vercel-key\n",
                encoding="utf-8",
            )
            route.configure_direct_env(env_file)
            current = env_file.read_text(encoding="utf-8")
            self.assertEqual(current, "DOCICH_JEV_ROUTE=direct\n")
            self.assertNotIn("old-vercel-key", current)


class ConfigureVercelEnvTests(unittest.TestCase):
    def test_writes_vercel_route_and_its_own_key(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text("KEEP=value\n", encoding="utf-8")
            route.configure_vercel_env(env_file, "TEST_VERCEL_KEY_123")
            current = env_file.read_text(encoding="utf-8")
            self.assertNotIn("DOCICH_SEMANTIC_BACKEND", current)
            self.assertIn("DOCICH_JEV_ROUTE=vercel\n", current)
            self.assertIn("DOCICH_JEV_VERCEL_API_KEY=TEST_VERCEL_KEY_123\n", current)

    def test_quotes_shell_punctuation_in_key(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text("KEEP=value\n", encoding="utf-8")
            key = "value;$(true)'quoted'"
            route.configure_vercel_env(env_file, key)
            result = subprocess.run(
                ["bash", "-c", "set -a; . \"$1\"; printf %s \"$DOCICH_JEV_VERCEL_API_KEY\"", "bash", str(env_file)],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(result.stdout, key)

    def test_rejects_invalid_key_without_touching_env(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            original = "KEEP=value\n"
            env_file.write_text(original, encoding="utf-8")
            for bad_key in ("", "bad key", "bad\nkey", "x" * 4097, "é"):
                with self.subTest(bad_key=bad_key), self.assertRaises(route.ConfigureError):
                    route.configure_vercel_env(env_file, bad_key)
                self.assertEqual(env_file.read_text(encoding="utf-8"), original)


class DisableEnvTests(unittest.TestCase):
    def test_disable_returns_to_default_route_and_removes_vercel_key(self):
        with tempfile.TemporaryDirectory(prefix="jev-route-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text(
                "KEEP=value\n"
                "COMMENT_CLASSIFIER_BACKEND=jev\n"
                "TYPESAFE_API_KEY=untouched\n"
                "DOCICH_SEMANTIC_BACKEND=jev\n"
                "DOCICH_JEV_ROUTE=vercel\n"
                "DOCICH_JEV_VERCEL_API_KEY=SECRET_TO_REMOVE\n",
                encoding="utf-8",
            )
            backup = route.disable_env(env_file)
            current = env_file.read_text(encoding="utf-8")
            self.assertEqual(current,
                             "KEEP=value\n"
                             "COMMENT_CLASSIFIER_BACKEND=jev\n"
                             "TYPESAFE_API_KEY=untouched\n")
            self.assertNotIn("SECRET_TO_REMOVE", current)
            self.assertIn("SECRET_TO_REMOVE", backup.read_text(encoding="utf-8"))

    def test_default_direct_preflight_requires_typesafe_key_only_when_jev_is_live(self):
        route._require_default_direct_route_ready({})
        route._require_default_direct_route_ready({"COMMENT_CLASSIFIER_BACKEND": "heuristic"})
        route._require_default_direct_route_ready({
            "COMMENT_CLASSIFIER_BACKEND": "jev",
            "TYPESAFE_API_KEY": "present",
        })
        with self.assertRaisesRegex(
            route.ConfigureError,
            "direct_route_requires_existing_typesafe_api_key",
        ):
            route._require_default_direct_route_ready({"COMMENT_CLASSIFIER_BACKEND": "jev"})


class VerifyJevRouteTests(unittest.TestCase):
    def test_default_route_rejects_a_worker_still_pinned_or_holding_the_vercel_key(self):
        verify = route.verify_jev_route(None)
        verify({})  # Jev disabled: no direct credential is required.
        verify({"DOCICH_JEV_ROUTE": ""})  # no error
        verify({"DOCICH_SEMANTIC_BACKEND": "jev"})  # retired, ignored
        verify({"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "present"})
        with self.assertRaises(route.ConfigureError):
            verify({"DOCICH_JEV_ROUTE": "vercel"})
        with self.assertRaises(route.ConfigureError):
            verify({"DOCICH_JEV_VERCEL_API_KEY": "present"})
        with self.assertRaisesRegex(
            route.ConfigureError,
            "direct_route_requires_existing_typesafe_api_key",
        ):
            verify({"COMMENT_CLASSIFIER_BACKEND": "jev"})

    def test_direct_requires_route_and_typesafe_key(self):
        verify = route.verify_jev_route("direct")
        verify({"DOCICH_JEV_ROUTE": "direct", "TYPESAFE_API_KEY": "present"})
        with self.assertRaises(route.ConfigureError):
            verify({"DOCICH_JEV_ROUTE": "vercel", "TYPESAFE_API_KEY": "present"})
        with self.assertRaises(route.ConfigureError):
            verify({"DOCICH_JEV_ROUTE": "direct"})

    def test_vercel_requires_its_own_key_not_typesafe_api_key(self):
        verify = route.verify_jev_route("vercel")
        verify({"DOCICH_JEV_ROUTE": "vercel", "DOCICH_JEV_VERCEL_API_KEY": "present"})
        with self.assertRaises(route.ConfigureError):
            verify({"DOCICH_JEV_ROUTE": "vercel", "TYPESAFE_API_KEY": "present"})


class RefactoredClassifierVerificationTests(unittest.TestCase):
    """Regression coverage for the extract-method refactor in #678's script.

    This logic previously lived inline inside restart_chat_worker and had no
    direct unit coverage at all (only the real-process restart, which no
    test here exercises); extracting it as _verify_comment_classifier_jev
    makes it directly testable without a real PID.
    """

    def test_enable_requires_backend_key_and_model(self):
        verify = classifier._verify_comment_classifier_jev(True)
        verify({"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "k",
                "COMMENT_CLASSIFIER_JEV_MODEL": classifier.MODEL})
        with self.assertRaises(classifier.ConfigureError):
            verify({"COMMENT_CLASSIFIER_BACKEND": "legacy"})
        with self.assertRaises(classifier.ConfigureError):
            verify({"COMMENT_CLASSIFIER_BACKEND": "jev"})
        with self.assertRaises(classifier.ConfigureError):
            verify({"COMMENT_CLASSIFIER_BACKEND": "jev", "TYPESAFE_API_KEY": "k",
                    "COMMENT_CLASSIFIER_JEV_MODEL": "jev-9.9.9"})

    def test_disable_requires_backend_and_key_gone(self):
        verify = classifier._verify_comment_classifier_jev(False)
        verify({})
        with self.assertRaises(classifier.ConfigureError):
            verify({"COMMENT_CLASSIFIER_BACKEND": "jev"})
        with self.assertRaises(classifier.ConfigureError):
            verify({"TYPESAFE_API_KEY": "still-here"})


if __name__ == "__main__":
    unittest.main()
