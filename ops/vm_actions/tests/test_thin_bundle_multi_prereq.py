"""A thin deploy bundle over a merge range must not reach the gateway.

2026-11-07 20:03 JST: PR #3695's merge commit gave the thin bundle two
prerequisite refs. git words that as "The bundle requires these N refs:",
which ``gateway._bundle_verify`` (it only matches "requires this ref:") reads
as no prerequisites. The gateway then skipped seeding staging with production
HEAD and ``git fetch <bundle>`` failed, so every production deploy stopped.
The workflow now falls back to a full bundle on the plural wording.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-operations.yml"
GUARD = re.compile(r"The bundle requires these [0-9]+ refs")


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=check)


def commit(repo, name):
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-qm", name)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


class ThinBundlePrerequisiteWording(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="thin-bundle-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "r"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        self.base = commit(self.repo, "base")
        git(self.repo, "checkout", "-q", "-b", "side")
        self.side = commit(self.repo, "side")
        git(self.repo, "checkout", "-q", "main")
        self.main = commit(self.repo, "main")

    def verify_text(self, base):
        bundle = self.tmp / "b.bundle"
        git(self.repo, "bundle", "create", str(bundle), "HEAD", f"^{base}")
        out = git(self.repo, "bundle", "verify", str(bundle), check=False)
        return out.stdout + out.stderr

    def test_a_merge_range_with_two_boundary_commits_uses_the_plural_wording(self):
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
        # The range main..merge has boundary commits `main` and `side`'s base.
        text = self.verify_text(self.side)
        self.assertRegex(text, GUARD)

    def test_a_linear_range_keeps_the_single_prerequisite_wording(self):
        commit(self.repo, "next")
        text = self.verify_text(self.main)
        self.assertNotRegex(text, GUARD)
        self.assertIn("requires this ref", text)

    def test_workflow_falls_back_to_a_full_bundle_on_the_plural_wording(self):
        text = WORKFLOW.read_text()
        step = text[text.index("name: Create deployment bundle"):text.index("name: Upload candidate to VM staging")]
        self.assertIn("The bundle requires these [0-9]+ refs", step)
        thin = step.index('bundle create "$RUNNER_TEMP/vm-payload" HEAD "^$base"')
        guard = step.index("The bundle requires these [0-9]+ refs")
        full = step.index('bundle create "$RUNNER_TEMP/vm-payload" HEAD\n', guard)
        self.assertLess(thin, guard)
        self.assertLess(guard, full)


if __name__ == "__main__":
    unittest.main()
