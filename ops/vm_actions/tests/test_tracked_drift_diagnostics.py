from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("vm_collect_tracked_drift", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TrackedDriftDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.collector = load_collector()
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-tracked-drift-")
        base = Path(self.tmp.name)
        self.root = base / "docich"
        self.sub_remote = base / "soviet_now"
        self._init_repo(self.sub_remote)
        (self.sub_remote / "worker.py").write_text("v1\n", encoding="utf-8")
        self._git(self.sub_remote, "add", "worker.py")
        self._git(self.sub_remote, "commit", "-m", "sub v1")
        self.sub_v1 = self._git(self.sub_remote, "rev-parse", "HEAD")

        self._init_repo(self.root)
        (self.root / "app.py").write_text("v1\n", encoding="utf-8")
        (self.root / ".gitmodules").write_text(
            '[submodule "games/soviet_now"]\n'
            "\tpath = games/soviet_now\n"
            "\turl = https://github.com/azumag/soviet_now.git\n",
            encoding="utf-8",
        )
        self._git(self.root, "add", "app.py", ".gitmodules")
        self._git(
            self.root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{self.sub_v1},games/soviet_now",
        )
        self._git(self.root, "commit", "-m", "parent v1")

        (self.root / "games").mkdir(exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", str(self.sub_remote), str(self.root / "games/soviet_now")],
            check=True,
        )
        self.sub = self.root / "games/soviet_now"
        self._git(self.sub, "checkout", "--detach", "--quiet", self.sub_v1)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _init_repo(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--quiet", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "config", "user.email", "tests@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(path), "config", "user.name", "vmops tests"], check=True)

    @staticmethod
    def _git(path: Path, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()

    def assert_only_fixed_fields(self, result):
        self.assertEqual(
            set(result),
            {
                "scan_complete",
                "parent_tracked_dirty",
                "owned_submodule_head_mismatch",
                "owned_submodule_tracked_dirty",
                "owned_submodule_missing_or_invalid",
                "unknown",
                "drift_detected",
            },
        )
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("worker.py", rendered)
        self.assertNotIn("app.py", rendered)

    def test_clean_checkout_reports_no_drift(self):
        result = self.collector._collect_tracked_drift(self.root)
        self.assert_only_fixed_fields(result)
        self.assertTrue(result["scan_complete"])
        self.assertEqual(result["drift_detected"], 0)
        self.assertEqual(result["parent_tracked_dirty"], 0)
        self.assertEqual(result["owned_submodule_head_mismatch"], 0)
        self.assertEqual(result["owned_submodule_tracked_dirty"], 0)
        self.assertEqual(result["owned_submodule_missing_or_invalid"], 0)
        self.assertEqual(result["unknown"], 0)

    def test_parent_tracked_dirty_is_fixed_category_only(self):
        (self.root / "app.py").write_text("dirty\n", encoding="utf-8")
        result = self.collector._collect_tracked_drift(self.root)
        self.assert_only_fixed_fields(result)
        self.assertEqual(result["parent_tracked_dirty"], 1)
        self.assertEqual(result["drift_detected"], 1)

    def test_owned_submodule_head_mismatch_is_counted(self):
        (self.sub_remote / "worker.py").write_text("v2\n", encoding="utf-8")
        self._git(self.sub_remote, "commit", "-am", "sub v2")
        sub_v2 = self._git(self.sub_remote, "rev-parse", "HEAD")
        self._git(self.sub, "fetch", "--quiet", str(self.sub_remote), sub_v2)
        self._git(self.sub, "checkout", "--detach", "--quiet", sub_v2)
        result = self.collector._collect_tracked_drift(self.root)
        self.assert_only_fixed_fields(result)
        self.assertEqual(result["owned_submodule_head_mismatch"], 1)
        self.assertEqual(result["drift_detected"], 1)

    def test_owned_submodule_tracked_dirty_is_counted(self):
        (self.sub / "worker.py").write_text("dirty\n", encoding="utf-8")
        result = self.collector._collect_tracked_drift(self.root)
        self.assert_only_fixed_fields(result)
        self.assertEqual(result["owned_submodule_tracked_dirty"], 1)
        self.assertEqual(result["drift_detected"], 1)

    def test_owned_submodule_missing_is_counted(self):
        shutil.rmtree(self.sub)
        result = self.collector._collect_tracked_drift(self.root)
        self.assert_only_fixed_fields(result)
        self.assertEqual(result["owned_submodule_missing_or_invalid"], 1)
        self.assertEqual(result["drift_detected"], 1)

    def test_owned_submodule_url_mismatch_is_counted(self):
        modules = self.root / ".gitmodules"
        modules.write_text(modules.read_text(encoding="utf-8").replace("https://github.com/azumag/soviet_now.git", "https://example.invalid/wrong.git"), encoding="utf-8")
        # Keep the parent tracked tree clean so the URL classification itself is exercised.
        self._git(self.root, "add", ".gitmodules")
        self._git(self.root, "commit", "-m", "wrong submodule url")
        result = self.collector._collect_tracked_drift(self.root)
        self.assert_only_fixed_fields(result)
        self.assertEqual(result["owned_submodule_missing_or_invalid"], 1)
        self.assertEqual(result["drift_detected"], 1)

    def test_scan_failure_is_unknown_not_false_clean(self):
        original = self.collector._git_text

        def failing(repo, *args):
            if Path(repo) == self.root and args[:2] == ("status", "--porcelain"):
                return None
            return original(repo, *args)

        with mock.patch.object(self.collector, "_git_text", side_effect=failing):
            result = self.collector._collect_tracked_drift(self.root)
        self.assert_only_fixed_fields(result)
        self.assertFalse(result["scan_complete"])
        self.assertGreaterEqual(result["unknown"], 1)
        self.assertEqual(result["drift_detected"], 1)


if __name__ == "__main__":
    unittest.main()
