from __future__ import annotations

import importlib.util
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "configure_comment_classifier_jev.py"


def load_module():
    spec = importlib.util.spec_from_file_location("configure_comment_classifier_jev", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


jev = load_module()


class ConfigureEnvTests(unittest.TestCase):
    def test_replaces_only_managed_assignments_and_keeps_secret_in_env_backup(self):
        with tempfile.TemporaryDirectory(prefix="jev-config-") as raw:
            root = Path(raw)
            env_file = root / ".env"
            env_file.write_text(
                "KEEP=value\n"
                "COMMENT_CLASSIFIER_BACKEND=\n"
                "export TYPESAFE_API_KEY=old\n"
                "COMMENT_CLASSIFIER_JEV_TIMEOUT_MS=999\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            backup = jev.configure_env(env_file, "TEST_KEY_123")

            current = env_file.read_text(encoding="utf-8")
            self.assertIn("KEEP=value\n", current)
            self.assertEqual(current.count("COMMENT_CLASSIFIER_BACKEND="), 1)
            self.assertEqual(current.count("TYPESAFE_API_KEY="), 1)
            self.assertIn("COMMENT_CLASSIFIER_BACKEND=jev\n", current)
            self.assertIn("COMMENT_CLASSIFIER_JEV_MODEL=jev-1.13.0\n", current)
            self.assertIn("TYPESAFE_API_KEY=TEST_KEY_123\n", current)
            self.assertEqual(backup.read_text(encoding="utf-8"),
                             "KEEP=value\n"
                             "COMMENT_CLASSIFIER_BACKEND=\n"
                             "export TYPESAFE_API_KEY=old\n"
                             "COMMENT_CLASSIFIER_JEV_TIMEOUT_MS=999\n")
            self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    def test_quotes_shell_punctuation_in_key(self):
        with tempfile.TemporaryDirectory(prefix="jev-config-") as raw:
            root = Path(raw)
            env_file = root / ".env"
            env_file.write_text("KEEP=value\n", encoding="utf-8")
            key = "value;$(true)'quoted'"
            jev.configure_env(env_file, key)
            result = subprocess.run(
                ["bash", "-c", "set -a; . \"$1\"; printf %s \"$TYPESAFE_API_KEY\"", "bash", str(env_file)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.stdout, key)

    def test_disable_removes_managed_values_and_keeps_explicit_base_backend(self):
        with tempfile.TemporaryDirectory(prefix="jev-config-") as raw:
            env_file = Path(raw) / ".env"
            env_file.write_text(
                "KEEP=value\n"
                "COMMENT_CLASSIFIER_BACKEND=jev\n"
                "COMMENT_CLASSIFIER_JEV_MODEL=jev-1.13.0\n"
                "COMMENT_CLASSIFIER_JEV_TIMEOUT_MS=1500\n"
                "COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE=0.70\n"
                "COMMENT_CLASSIFIER_JEV_LOG_ENABLED=1\n"
                "TYPESAFE_API_KEY=old\n",
                encoding="utf-8",
            )
            backup = jev.disable_env(env_file)
            current = env_file.read_text(encoding="utf-8")
            self.assertEqual(current, "KEEP=value\nCOMMENT_CLASSIFIER_BACKEND=\n")
            self.assertEqual(backup.read_text(encoding="utf-8"),
                             "KEEP=value\n"
                             "COMMENT_CLASSIFIER_BACKEND=jev\n"
                             "COMMENT_CLASSIFIER_JEV_MODEL=jev-1.13.0\n"
                             "COMMENT_CLASSIFIER_JEV_TIMEOUT_MS=1500\n"
                             "COMMENT_CLASSIFIER_JEV_MIN_CONFIDENCE=0.70\n"
                             "COMMENT_CLASSIFIER_JEV_LOG_ENABLED=1\n"
                             "TYPESAFE_API_KEY=old\n")

    def test_rejects_whitespace_key_without_touching_env(self):
        with tempfile.TemporaryDirectory(prefix="jev-config-") as raw:
            env_file = Path(raw) / ".env"
            original = "KEEP=value\n"
            env_file.write_text(original, encoding="utf-8")
            with self.assertRaises(jev.ConfigureError):
                jev.configure_env(env_file, "bad key")
            self.assertEqual(env_file.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
