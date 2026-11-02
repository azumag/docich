from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "storage_reclaim.sh"


class TmpWorktreeReclaimContractTests(unittest.TestCase):
    def test_known_stale_tmp_worktrees_are_exact_allowlist_entries(self):
        text = HELPER.read_text(encoding="utf-8")
        for name in ("soviet-2489-fix", "persist-recon", "wt1029"):
            self.assertIn(f'"$sys_tmp/{name}"', text)
        self.assertIn("stale_min_age_days=7", text)
        self.assertIn('refs="$(stale_clone_refs "$path")"', text)
        self.assertIn("referenced by $refs running process(es)", text)

    def test_gateway_payload_keeps_headroom(self):
        preamble = b"APPLY=0\nVOICEVOX_ARCHIVE=0\nAIVIS_ENGINE=0\n"
        payload = preamble + HELPER.read_bytes()
        self.assertLessEqual(len(payload), 16000)


if __name__ == "__main__":
    unittest.main()
