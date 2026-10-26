import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "storage_reclaim_staged.sh"


class StagedTests(unittest.TestCase):
    def run_helper(self, *args, apply=False, stage=None):
        env = dict(os.environ)
        env["APPLY"] = "1" if apply else "0"
        argv = ["bash", str(HELPER)]
        if stage:
            argv += ["--stage", stage]
        argv += list(args)
        return subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60, check=False)

    def test_dry_run_reports_all_stages(self):
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        stages = ["journal_vacuum", "apt_clean", "snap_disabled", "snapd_cache", "docker_prune", "logrotate_install", "logrotate_run"]
        for s in stages:
            self.assertIn("stage=" + s, r.stdout)

    def test_single_stage(self):
        r = self.run_helper(stage="journal_vacuum")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("stage=journal_vacuum", r.stdout)

    def test_payload_size(self):
        preamble = b"APPLY=0\n"
        payload = preamble + HELPER.read_bytes()
        self.assertLessEqual(len(payload), 16384)
        self.assertLessEqual(len(payload), 16000)


if __name__ == "__main__":
    unittest.main()
