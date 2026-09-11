from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / "ops" / "vm_actions" / "gateway.py"


def load_gateway():
    spec = importlib.util.spec_from_file_location("vm_gateway_projection_review", str(GATEWAY))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectionReviewTests(unittest.TestCase):
    """#279: diagnostics should be able to name which projected paths are
    unexpectedly mismatched, without ever exposing their bytes/hashes."""

    def setUp(self):
        self.gw = load_gateway()
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-projreview-")
        base = Path(self.tmp.name)
        self.state = base / "state"
        (self.state / "current").mkdir(parents=True)
        self.live = base / "soren"
        self.live.mkdir()
        self.sub_remote = base / "soviet_now"
        self.root = base / "docich"
        self._init_repo(self.sub_remote)

        # old_sub: two tracked files. new_sub: both changed.
        (self.sub_remote / "worker.sh").write_text("old\n", encoding="utf-8")
        (self.sub_remote / "tests_notes.txt").write_text("old-test\n", encoding="utf-8")
        self._git(self.sub_remote, "add", "worker.sh", "tests_notes.txt")
        self._git(self.sub_remote, "commit", "-m", "old")
        self.old_sub = self._git(self.sub_remote, "rev-parse", "HEAD")

        (self.sub_remote / "worker.sh").write_text("new\n", encoding="utf-8")
        (self.sub_remote / "tests_notes.txt").write_text("new-test\n", encoding="utf-8")
        self._git(self.sub_remote, "commit", "-am", "new")
        self.new_sub = self._git(self.sub_remote, "rev-parse", "HEAD")

        self._init_repo(self.root)
        (self.root / ".gitmodules").write_text(
            '[submodule "games/soviet_now"]\n'
            "\tpath = games/soviet_now\n"
            "\turl = https://github.com/azumag/soviet_now.git\n",
            encoding="utf-8",
        )
        self._git(self.root, "add", ".gitmodules")
        self._git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{self.old_sub},games/soviet_now")
        self._git(self.root, "commit", "-m", "root old gitlink")
        self.old_parent = self._git(self.root, "rev-parse", "HEAD")

        # A second root commit advancing only the gitlink to new_sub -- the
        # "candidate sha" a diagnostics call would be asked about. Root-level
        # tree entries are independent of what the submodule checkout below
        # has on disk.
        self._git(self.root, "update-index", "--cacheinfo", f"160000,{self.new_sub},games/soviet_now")
        self._git(self.root, "commit", "-m", "root new gitlink")
        self.new_parent = self._git(self.root, "rev-parse", "HEAD")

        (self.root / "games").mkdir(exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", str(self.sub_remote), str(self.root / "games/soviet_now")],
            check=True,
        )
        sub = self.root / "games/soviet_now"
        self._git(sub, "config", "user.email", "tests@example.invalid")
        self._git(sub, "config", "user.name", "vmops tests")
        self._git(sub, "checkout", "--detach", "--quiet", self.old_sub)

        (self.state / "current" / "docich.json").write_text(
            json.dumps({"mode": "git", "sha": self.old_parent}), encoding="utf-8"
        )
        self.cfg = {
            "state": str(self.state),
            "repos": {"docich": {"production": str(self.root), "mode": "git", "projections": {"games/soviet_now": str(self.live)}}},
        }

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

    def test_no_current_state_reports_a_note_not_silence(self):
        (self.state / "current" / "docich.json").unlink()
        review = self.gw._projection_review(self.cfg, "docich", self.root, self.new_parent)
        self.assertEqual(review, {"_notes": {"*": "no_current_state"}})

    def test_reports_only_mismatched_paths_by_name(self):
        # worker.sh live matches old (nothing to report); tests_notes.txt live
        # is absent entirely (the "never actually projected" shape from #279).
        (self.live / "worker.sh").write_text("old\n", encoding="utf-8")
        review = self.gw._projection_review(self.cfg, "docich", self.root, self.new_parent)
        entries = review["games/soviet_now"]
        paths = {e["path"] for e in entries}
        self.assertEqual(paths, {"tests_notes.txt"})
        entry = entries[0]
        self.assertIs(entry["live_present"], False)
        self.assertIs(entry["matches_new"], False)
        # Never leaks bytes or hashes.
        for entry in entries:
            self.assertEqual(set(entry), {"path", "live_present", "matches_new"})

    def test_live_already_matching_new_is_reported_as_matches_new(self):
        (self.live / "worker.sh").write_text("old\n", encoding="utf-8")
        (self.live / "tests_notes.txt").write_text("new-test\n", encoding="utf-8")
        review = self.gw._projection_review(self.cfg, "docich", self.root, self.new_parent)
        entries = review["games/soviet_now"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], "tests_notes.txt")
        self.assertIs(entries[0]["live_present"], True)
        self.assertIs(entries[0]["matches_new"], True)

    def test_mode_only_difference_is_reported_as_mismatched(self):
        # Content byte-identical to old, but the executable bit differs --
        # deploy_git()/_plan_projection compares the full {sha256, mode}
        # tuple, so a mode-only drift must not be silently treated as a
        # content match here either (this is the bug that produced an
        # empty result in production despite a real REASON_PROJECTION_*
        # refusal: #279 follow-up).
        (self.live / "worker.sh").write_text("old\n", encoding="utf-8")
        (self.live / "worker.sh").chmod(0o755)
        (self.live / "tests_notes.txt").write_text("old-test\n", encoding="utf-8")
        review = self.gw._projection_review(self.cfg, "docich", self.root, self.new_parent)
        entries = review["games/soviet_now"]
        paths = {e["path"] for e in entries}
        self.assertIn("worker.sh", paths)

    def test_fully_synced_reports_a_note_not_silence(self):
        # Every changed path already matches old exactly (the ordinary
        # state before any deploy attempt): nothing to report, but that
        # must be visible as a note, not indistinguishable from a bug
        # that silently found nothing.
        (self.live / "worker.sh").write_text("old\n", encoding="utf-8")
        (self.live / "tests_notes.txt").write_text("old-test\n", encoding="utf-8")
        review = self.gw._projection_review(self.cfg, "docich", self.root, self.new_parent)
        self.assertEqual(review, {"_notes": {"games/soviet_now": "no_mismatch_found"}})

    def test_no_advance_returns_empty(self):
        review = self.gw._projection_review(self.cfg, "docich", self.root, self.old_parent)
        self.assertEqual(review, {})

    def test_path_count_is_capped(self):
        # Paths must already exist at the "old" baseline (so old_meta is
        # real) and then change, with self.live left empty throughout, to
        # actually count as mismatched -- a path that is merely new (absent
        # from both old and live) is the ordinary not-yet-deployed state,
        # not a mismatch, exactly mirroring gateway._plan_projection.
        count = self.gw.PROJECTION_REVIEW_PATH_MAX + 5
        for i in range(count):
            (self.sub_remote / f"f{i}.txt").write_text("old\n", encoding="utf-8")
        self._git(self.sub_remote, "add", "-A")
        self._git(self.sub_remote, "commit", "-m", "add many (baseline)")
        base_many = self._git(self.sub_remote, "rev-parse", "HEAD")
        for i in range(count):
            (self.sub_remote / f"f{i}.txt").write_text("new\n", encoding="utf-8")
        self._git(self.sub_remote, "commit", "-am", "change many")
        many_new = self._git(self.sub_remote, "rev-parse", "HEAD")

        self._git(self.root, "update-index", "--cacheinfo", f"160000,{base_many},games/soviet_now")
        self._git(self.root, "commit", "-m", "root base_many gitlink")
        parent_base = self._git(self.root, "rev-parse", "HEAD")
        (self.state / "current" / "docich.json").write_text(
            json.dumps({"mode": "git", "sha": parent_base}), encoding="utf-8"
        )
        self._git(self.root, "update-index", "--cacheinfo", f"160000,{many_new},games/soviet_now")
        self._git(self.root, "commit", "-m", "root many_new gitlink")
        parent_many = self._git(self.root, "rev-parse", "HEAD")

        sub = self.root / "games/soviet_now"
        self._git(sub, "fetch", "--quiet", str(self.sub_remote), base_many, many_new)

        review = self.gw._projection_review(self.cfg, "docich", self.root, parent_many)
        entries = review["games/soviet_now"]
        self.assertEqual(len(entries), self.gw.PROJECTION_REVIEW_PATH_MAX + 1)
        self.assertEqual(entries[-1]["path"], "...truncated...")

    def test_diagnostics_result_merges_projection_review(self):
        # End-to-end through diagnostics_result(): the real collector must
        # still run and succeed, and the new key rides through the existing
        # sanitizer/size-cap pipeline unchanged.
        (self.live / "tmp" / "state").mkdir(parents=True)
        for rel in (
            "ops/vm_actions/collect_diagnostics.py",
            "ops/vm_actions/runtime_registry.py",
            "src/docich/runtime_backend.py",
            "src/docich/__init__.py",
        ):
            src = ROOT / rel
            if src.is_file():
                (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, self.root / rel)
        self._git(self.root, "add", "ops/vm_actions", "src")
        self._git(self.root, "commit", "-m", "add diagnostics collector")
        candidate = self._git(self.root, "rev-parse", "HEAD")

        result = self.gw.diagnostics_result(self.cfg, "docich", "production", candidate)
        self.assertEqual(result["status"], "diagnosed")
        review = result["diagnostics"].get("projection_paths_needing_review")
        self.assertIsNotNone(review)
        paths = {e["path"] for e in review["games/soviet_now"]}
        self.assertEqual(paths, {"worker.sh", "tests_notes.txt"})


if __name__ == "__main__":
    unittest.main()
