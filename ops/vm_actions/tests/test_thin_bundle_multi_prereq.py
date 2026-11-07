"""A thin deploy bundle over a merge range must not reach the gateway.

2026-11-07 20:03 JST: PR #3695's merge commit gave the thin bundle two
prerequisite refs. git words that as "The bundle requires these N refs:",
which ``gateway._bundle_verify`` (it only matches "requires this ref:") reads
as no prerequisites. The gateway then skipped seeding staging with production
HEAD and ``git fetch <bundle>`` failed, so every production deploy stopped.
The workflow briefly fell back to a full bundle (#3699) until the gateway parsed
both wordings (#3701, installed on the VM 2026-11-07); these tests now pin the
git wording and the gateway parser so a regression shows up here.
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

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


class GatewayParsesEveryPrerequisite(ThinBundlePrerequisiteWording):
    """Issue #3700: the gateway itself must report every prerequisite."""

    def prerequisites(self, base):
        from ops.vm_actions import gateway
        bundle = self.tmp / "b.bundle"
        git(self.repo, "bundle", "create", str(bundle), "HEAD", f"^{base}")
        return gateway._bundle_verify(self.repo, bundle)

    def test_a_merge_range_reports_both_boundary_commits(self):
        git(self.repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
        ok, prereqs = self.prerequisites(self.side)
        self.assertTrue(ok)
        self.assertEqual(len(prereqs), 2)
        self.assertEqual(set(prereqs), {self.side, self.base})  # `main` is inside the range

    def test_a_linear_range_still_reports_its_single_prerequisite(self):
        commit(self.repo, "next")
        ok, prereqs = self.prerequisites(self.main)
        self.assertTrue(ok)
        self.assertEqual(prereqs, [self.main])

    def test_a_full_bundle_reports_none(self):
        bundle = self.tmp / "full.bundle"
        git(self.repo, "bundle", "create", str(bundle), "HEAD")
        from ops.vm_actions import gateway
        self.assertEqual(gateway._bundle_verify(self.repo, bundle), (True, []))
