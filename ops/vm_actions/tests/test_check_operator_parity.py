"""Contracts for the operator parity gate (docich#2681).

The gate replaces the "production is exactly current protected main" check:
operators may run while production sits at its deployed reviewed baseline
(``status == "configured"``) as long as that baseline is the current main SHA
or a locally proven ancestor of it. Everything else fails closed.
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ops.vm_actions import check_operator_parity as parity


def git(repo: Path, *argv: str, check: bool = True):
    return subprocess.run(
        ["git", "-C", str(repo), *argv],
        check=check,
        capture_output=True,
        text=True,
    )


def make_repo(path: Path, commits: int) -> list:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "T")
    shas = []
    for index in range(commits):
        (path / "f.txt").write_text(f"{index}\n")
        git(path, "add", "f.txt")
        git(path, "commit", "-qm", f"c{index}")
        shas.append(git(path, "rev-parse", "HEAD").stdout.strip())
    return shas


class ParityGateTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="parity-gw-"))
        self.src = self.base / "src"
        self.shas = make_repo(self.src, commits=4)
        self.main_sha = self.shas[-1]
        self.control = self.base / "control"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{self.src}", str(self.control)],
            check=True,
        )

    def run_gate(self, status, expected, control=None):
        if not isinstance(status, str):
            status = json.dumps(status)
        argv = [
            "check_operator_parity.py",
            status,
            expected,
            str(control if control is not None else self.control),
        ]
        out = io.StringIO()
        code = 0
        old_argv = sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(out):
                parity.main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        finally:
            sys.argv = old_argv
        return code, out.getvalue()

    def test_exact_main_match_is_allowed(self):
        status = {"status": "configured", "sha": self.main_sha}
        code, out = self.run_gate(status, self.main_sha)
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "")

    def test_configured_ancestor_baseline_is_allowed(self):
        # Production one commit behind main at a reviewed ancestor: the
        # 2026-10-23 incident state that must not block operators anymore.
        status = {"status": "configured", "sha": self.shas[-2]}
        code, out = self.run_gate(status, self.main_sha)
        self.assertEqual(code, 0, out)

    def test_drift_status_still_refuses(self):
        status = {"status": "drift", "sha": self.shas[-2]}
        code, out = self.run_gate(status, self.main_sha)
        self.assertEqual(code, 1)
        self.assertIn("recorded reviewed baseline", out)

    def test_recovery_status_still_refuses(self):
        status = {"status": "recovery_required", "sha": self.main_sha}
        code, out = self.run_gate(status, self.main_sha)
        self.assertEqual(code, 1)

    def test_invalid_status_payload_refuses(self):
        code, out = self.run_gate("not-json", self.main_sha)
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", out)

    def test_invalid_deployed_sha_refuses(self):
        status = {"status": "configured", "sha": "zz" * 20}
        code, out = self.run_gate(status, self.main_sha)
        self.assertEqual(code, 1)
        self.assertIn("valid deployed sha", out)

    def test_non_ancestor_baseline_refuses(self):
        # A commit that never reached main (the 2026-10-23 out-of-band commit)
        # is not fetchable from main history and must fail closed.
        side = self.base / "side"
        side.mkdir()
        subprocess.run(["git", "clone", "-q", str(self.src), str(side)], check=True)
        # CI runners do not provide a global git identity; set it explicitly.
        git(side, "config", "user.email", "t@example.com")
        git(side, "config", "user.name", "T")
        git(side, "checkout", "-q", "-b", "side", self.shas[0])
        (side / "f.txt").write_text("side\n")
        git(side, "add", "f.txt")
        git(side, "commit", "-qm", "local-only")
        side_sha = git(side, "rev-parse", "HEAD").stdout.strip()
        status = {"status": "configured", "sha": side_sha}
        code, out = self.run_gate(status, self.main_sha)
        self.assertEqual(code, 1)
        self.assertIn("ancestor of current main", out)

    def test_baseline_beyond_depth_window_refuses(self):
        # The ancestor proof is bounded by PARITY_DEPTH commits of history; a
        # baseline deeper behind main fails closed instead of being trusted.
        deep_src = self.base / "deep-src"
        deep_shas = make_repo(deep_src, commits=parity.PARITY_DEPTH + 10)
        deep_main = deep_shas[-1]
        control = self.base / "deep-control"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{deep_src}", str(control)],
            check=True,
        )
        status = {"status": "configured", "sha": deep_shas[2]}
        code, out = self.run_gate(status, deep_main, control=control)
        self.assertEqual(code, 1)
        self.assertIn("ancestor of current main", out)

    def test_history_fetch_failure_fails_closed(self):
        git(self.control, "remote", "set-url", "origin", "file:///nonexistent/repo.git")
        status = {"status": "configured", "sha": self.shas[-2]}
        code, out = self.run_gate(status, self.main_sha)
        self.assertEqual(code, 1)
        self.assertIn("main history fetch failed", out)

    def test_invalid_expected_sha_is_a_usage_error(self):
        code, out = self.run_gate({"status": "configured", "sha": self.main_sha}, "nothex")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
