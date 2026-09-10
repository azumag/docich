import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / "ops/vm_actions/gateway.py"
SPEC = importlib.util.spec_from_file_location("vm_gateway_storage", GATEWAY)
gw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gw)


class PreviewReleaseGcTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="vmops-release-gc-"))
        self.state = self.base / "state"
        self.state.mkdir()
        self.production = self.base / "production"
        self.production.mkdir()
        self.cfg = {"state": str(self.state), "repos": {"docich": {"production": str(self.production)}}}

    def make_release(self, age_rank: int, dirty: bool = False):
        temp = self.base / f"release-{age_rank}"
        temp.mkdir()
        subprocess.run(["git", "init", "-q", temp], check=True)
        subprocess.run(["git", "-C", temp, "config", "user.email", "t@example.com"], check=True)
        subprocess.run(["git", "-C", temp, "config", "user.name", "T"], check=True)
        (temp / "app.py").write_text(f"v{age_rank}\n")
        subprocess.run(["git", "-C", temp, "add", "app.py"], check=True)
        subprocess.run(["git", "-C", temp, "commit", "-qm", f"v{age_rank}"], check=True)
        sha = subprocess.check_output(["git", "-C", temp, "rev-parse", "HEAD"], text=True).strip()
        release_root = self.state / "releases" / "docich"
        release_root.mkdir(parents=True, exist_ok=True)
        dest = release_root / sha
        temp.rename(dest)
        stamp = 1_700_000_000 + age_rank
        os.utime(dest, (stamp, stamp))
        if dirty:
            (dest / "app.py").write_text("dirty\n")
        bundle = self.state / "bundles" / "docich" / f"{sha}.bundle"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_bytes(b"bundle")
        return sha, dest, bundle

    def test_prunes_only_old_clean_releases_and_keeps_bundles(self):
        oldest_sha, oldest, oldest_bundle = self.make_release(1, dirty=True)
        third_sha, third, third_bundle = self.make_release(2)
        second_sha, second, second_bundle = self.make_release(3)
        newest_sha, newest, newest_bundle = self.make_release(4)

        removed = gw._prune_preview_releases(self.cfg, "docich", newest_sha, keep=2)

        self.assertEqual(removed, [third_sha])
        self.assertTrue(oldest.exists(), "dirty release must be preserved fail-closed")
        self.assertFalse(third.exists())
        self.assertTrue(second.exists())
        self.assertTrue(newest.exists())
        for bundle in (oldest_bundle, third_bundle, second_bundle, newest_bundle):
            self.assertTrue(bundle.exists(), "preview GC must never delete inert bundles")

    def test_protected_release_counts_toward_two_generation_retention(self):
        oldest_sha, oldest, _ = self.make_release(1)
        middle_sha, middle, _ = self.make_release(2)
        newest_sha, newest, _ = self.make_release(3)
        removed = gw._prune_preview_releases(self.cfg, "docich", oldest_sha, keep=2)
        self.assertEqual(removed, [middle_sha])
        self.assertTrue(oldest.exists())
        self.assertFalse(middle.exists())
        self.assertTrue(newest.exists())


class FilesystemStatusTests(unittest.TestCase):
    def test_reports_df_style_usage_without_paths_or_names(self):
        fake = SimpleNamespace(f_blocks=100, f_bfree=30, f_bavail=20, f_frsize=4096)
        with mock.patch.object(gw.os, "statvfs", return_value=fake):
            data = gw._filesystem_status(Path("/private/production"))
        self.assertEqual(data["total_bytes"], 409600)
        self.assertEqual(data["available_bytes"], 81920)
        self.assertEqual(data["used_percent"], 78)
        self.assertEqual(set(data), {"total_bytes", "available_bytes", "used_percent"})


class StorageMonitorWorkflowTests(unittest.TestCase):
    def test_hourly_monitor_uses_status_and_deduplicated_issue_alert(self):
        workflow = ROOT / ".github/workflows/vm-storage-monitor.yml"
        text = workflow.read_text()
        self.assertIn("cron: '17 * * * *'", text)
        self.assertIn("issues: write", text)
        self.assertIn("status docich production", text)
        self.assertIn("WARN_PERCENT: '80'", text)
        self.assertIn("CRITICAL_PERCENT: '90'", text)
        self.assertIn("VM storage pressure", text)
        self.assertNotIn("exec docich production", text)

    def test_hourly_monitor_collects_runtime_health_without_raw_diagnostics(self):
        workflow = ROOT / ".github/workflows/vm-storage-monitor.yml"
        text = workflow.read_text()
        self.assertIn("diagnostics docich production", text)
        self.assertIn("[VM runtime]", text)
        self.assertIn("counts and booleans only", text)
        self.assertIn('> "$RUNNER_TEMP/runtime-diagnostics.json"', text)
        self.assertNotIn('echo "$diagnostics_json"', text)
        self.assertNotIn("exec docich production", text)

    def test_runtime_alert_refreshes_existing_same_severity_snapshot(self):
        workflow = ROOT / ".github/workflows/vm-storage-monitor.yml"
        text = workflow.read_text()
        runtime_step = text.split("- name: Update deduplicated runtime health alert", 1)[1]
        edit = 'gh issue edit "$number" --repo "$GITHUB_REPOSITORY" --body "$body"'
        self.assertIn(edit, runtime_step)
        self.assertIn("latest sanitized snapshot", runtime_step)
        self.assertNotIn("suppressing duplicate", runtime_step)
        self.assertLess(runtime_step.index('body="$(cat <<EOF'), runtime_step.index(edit))


class ProductionStatusIntegrationTests(unittest.TestCase):
    def test_production_status_includes_sanitized_storage(self):
        base = Path(tempfile.mkdtemp(prefix="vmops-storage-status-"))
        state = base / "state"; state.mkdir()
        prod = base / "prod"; prod.mkdir()
        subprocess.run(["git", "init", "-q", prod], check=True)
        subprocess.run(["git", "-C", prod, "config", "user.email", "t@example.com"], check=True)
        subprocess.run(["git", "-C", prod, "config", "user.name", "T"], check=True)
        (prod / "app.py").write_text("ok\n")
        subprocess.run(["git", "-C", prod, "add", "app.py"], check=True)
        subprocess.run(["git", "-C", prod, "commit", "-qm", "ok"], check=True)
        sha = subprocess.check_output(["git", "-C", prod, "rev-parse", "HEAD"], text=True).strip()
        cfg = {"state": str(state), "repos": {"docich": {"production": str(prod)}}}
        gw.write_json(gw.current_file(cfg, "docich"), {"mode": "git", "sha": sha, "pending_repairs": []})
        result = gw.status_result(cfg, "docich", "production", sha)
        self.assertEqual(result["status"], "configured")
        self.assertEqual(set(result["storage"]), {"total_bytes", "available_bytes", "used_percent"})


if __name__ == "__main__":
    unittest.main()
