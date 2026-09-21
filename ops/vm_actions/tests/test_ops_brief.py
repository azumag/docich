import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("brief_gateway_test", ROOT / "ops/vm_actions/gateway.py")
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)
brief = gw.ops_brief
SOURCE = "## 2026-09-22 — 最新の修正 Issue #123\nprivate body\n## 2026-09-21 — 配信復旧\n".encode()


class BriefGenerationTests(unittest.TestCase):
    def test_deterministic_public_projection_omits_body(self):
        data = brief.build(SOURCE)
        self.assertEqual(data, brief.build(SOURCE))
        self.assertNotIn(b"private body", data)
        self.assertEqual(brief.validate(data)["topics"], ["最新の修正", "配信復旧"])
        self.assertEqual(brief.validate(data)["source_sha256"], brief.digest(SOURCE))
        self.assertIn("- 最新の修正\n".encode(), brief.render(data))

    def test_body_only_change_invalidates_source_provenance(self):
        old, new = brief.build(SOURCE), brief.build(SOURCE + b"more private detail\n")
        self.assertNotEqual(old, new)
        self.assertEqual(brief.render(old), brief.render(new))

    def test_bounded_topics_and_fenced_headings(self):
        source = "```md\n## fake\n```\n## " + "長" * 100 + "\n## second\n## third\n## fourth\n"
        topics = brief.validate(brief.build(source.encode()))["topics"]
        self.assertEqual(len(topics), 3)
        self.assertEqual(len(topics[0]), 70)
        self.assertEqual(topics[1:], ["second", "third"])

    def test_empty_invalid_and_tampered_artifacts_fail_closed(self):
        for source in (b"", b"no headings", b"\xff", b"x" * (brief.MAX_SOURCE + 1)):
            with self.subTest(source_size=len(source)), self.assertRaises((ValueError, UnicodeError)):
                brief.build(source)
        value = brief.validate(brief.build(SOURCE))
        for mutation in ({"topics": ["stale"]}, {"source": "games/soviet_now/handoff.md"},
                         {"schema": 2}, {"topics": ["line\ninjection"]}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                brief.validate(brief.encode({**value, **mutation}))

    def test_cli_detects_stale_missing_source_and_never_prints_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, artifact = root / "handoff.md", root / "brief.json"
            source.write_bytes(SOURCE)
            base = [sys.executable, str(ROOT / "ops/vm_actions/ops_brief.py")]

            def run(op):
                result = subprocess.run(base + [op, "--handoff", str(source), "--artifact", str(artifact)],
                                        capture_output=True, text=True)
                self.assertNotIn("private body", result.stdout + result.stderr)
                self.assertNotIn("最新の修正", result.stdout + result.stderr)
                return result.returncode

            self.assertEqual(run("build"), 0)
            self.assertEqual(run("check-source"), 0)
            source.write_bytes(SOURCE + b"private changed\n")
            self.assertNotEqual(run("check-source"), 0)
            source.unlink()
            self.assertNotEqual(run("check-source"), 0)
            self.assertEqual(run("check-artifact"), 0)

    def test_repository_artifact_and_workflow_gate(self):
        brief.validate((ROOT / brief.ARTIFACT).read_bytes())
        workflow = (ROOT / ".github/workflows/vm-operations.yml").read_text()
        self.assertLess(workflow.index("Require parent operations brief gateway capability"),
                        workflow.index("Upload candidate to VM staging"))
        self.assertIn(brief.CAPABILITY, workflow)
        self.assertIn("check-artifact --artifact candidate/" + brief.ARTIFACT, workflow)
        self.assertIn("/handoff.md", (ROOT / ".gitignore").read_text())
        self.assertIn('"$source_dir/ops_brief.py"', (ROOT / "ops/vm_actions/install_vm_gateway.sh").read_text())


class BriefDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o022))
        temp = tempfile.TemporaryDirectory(prefix="vmops-brief-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root, self.sub, self.live = (self.base / name for name in ("parent", "sub", "live"))
        for path in (self.root, self.sub):
            path.mkdir()
            gw.git(path, "init", "-q")
            gw.git(path, "config", "user.name", "Test")
            gw.git(path, "config", "user.email", "test@example.invalid")
        (self.sub / "prompts").mkdir()
        self.legacy = b"# legacy\n- reviewed previous topic\n"
        (self.sub / brief.DESTINATION).write_bytes(self.legacy)
        (self.sub / "code.txt").write_text("before\n")
        self.sub_sha = self.commit(self.sub)
        gw.git(self.root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(self.sub), brief.PROJECTION)
        self.baseline = self.commit(self.root)
        self.live.mkdir()
        (self.live / "prompts").mkdir()
        (self.live / brief.DESTINATION).write_bytes(self.legacy)
        (self.live / "code.txt").write_text("before\n")
        (self.live / "handoff.md").write_text("## stale VM handoff must never be read\n")
        self.cfg = {"state": str(self.base / "state"), "repos": {"docich": {
            "production": str(self.root), "mode": "git", "projections": {brief.PROJECTION: str(self.live)}}}}
        self.state_path = gw.current_file(self.cfg, "docich")
        gw.write_json(self.state_path, {"mode": "git", "sha": self.baseline})
        patcher = mock.patch.object(gw, "OWNED_SUBMODULES", {brief.PROJECTION: str(self.sub)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def commit(self, root):
        gw.git(root, "add", ".")
        gw.git(root, "commit", "-qm", "test")
        return gw.git(root, "rev-parse", "HEAD")

    def candidate(self, source=SOURCE, sub_change=False, remove=False):
        old = gw.git(self.root, "rev-parse", "HEAD")
        artifact = self.root / brief.ARTIFACT
        if remove:
            artifact.unlink()
        else:
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(brief.build(source))
        if sub_change:
            (self.sub / brief.DESTINATION).write_bytes(b"# stale submodule\n- wrong topic\n")
            (self.sub / "code.txt").write_text("after\n")
            new_sub = self.commit(self.sub)
            gw.git(self.root / brief.PROJECTION, "fetch", "-q", str(self.sub), new_sub)
            gw.git(self.root / brief.PROJECTION, "checkout", "-q", "--detach", new_sub)
        new = self.commit(self.root)
        bundle = gw.bundle_file(self.cfg, "docich", new)
        bundle.parent.mkdir(parents=True, exist_ok=True)
        gw.git(self.root, "bundle", "create", str(bundle), "HEAD")
        gw.git(self.root, "reset", "--hard", old)
        gw.sync_owned_submodules(self.root)
        return new

    def deploy(self, sha):
        return gw.deploy_git(self.cfg, "docich", sha)

    def test_parent_only_change_updates_live_and_provenance_without_submodule_commit(self):
        sha = self.candidate()
        self.assertEqual(self.deploy(sha)["status"], "deployed")
        self.assertEqual(gw.submodule_gitlink(self.root, brief.PROJECTION), self.sub_sha)
        self.assertEqual((self.live / brief.DESTINATION).read_bytes(), brief.render(brief.build(SOURCE)))
        self.assertEqual(gw.read_json(self.state_path)["ops_brief_source_sha256"], brief.digest(SOURCE))
        self.assertEqual(gw.status_result(self.cfg, "docich", "production", sha)["status"], "configured")
        self.assertIn("stale VM handoff", (self.live / "handoff.md").read_text())

    def test_submodule_brief_changes_cannot_overwrite_parent_projection(self):
        self.deploy(self.candidate(sub_change=True))
        self.assertEqual((self.live / "code.txt").read_text(), "after\n")
        self.assertEqual((self.live / brief.DESTINATION).read_bytes(), brief.render(brief.build(SOURCE)))

    def test_subsequent_parent_only_change_updates_brief(self):
        self.deploy(self.candidate())
        source = b"## next topic\n"
        self.deploy(self.candidate(source))
        self.assertEqual((self.live / brief.DESTINATION).read_bytes(), brief.render(brief.build(source)))
        self.assertEqual(gw.submodule_gitlink(self.root, brief.PROJECTION), self.sub_sha)

    def test_body_only_change_updates_provenance_and_same_commit_is_retryable(self):
        self.deploy(self.candidate())
        source = SOURCE + b"private body changed\n"
        sha = self.candidate(source)
        self.deploy(sha)
        self.deploy(sha)
        self.assertEqual(gw.read_json(self.state_path)["ops_brief_source_sha256"], brief.digest(source))
        self.assertEqual((self.live / brief.DESTINATION).read_bytes(), brief.render(brief.build(SOURCE)))

    def test_diagnostics_health_is_fixed_vocabulary_and_excludes_legacy_copy(self):
        self.assertEqual(gw._ops_brief_health(self.cfg, "docich", self.baseline), "unmanaged")
        sha = self.candidate(sub_change=True)
        self.deploy(sha)
        self.assertEqual(gw._ops_brief_health(self.cfg, "docich", sha), "matched")
        review = gw._projection_review(self.cfg, "docich", self.root, sha)
        self.assertNotIn(brief.DESTINATION, json.dumps(review))
        (self.live / brief.DESTINATION).write_bytes(self.legacy)
        self.assertEqual(gw._ops_brief_health(self.cfg, "docich", sha), "drift")
        self.assertEqual(gw._ops_brief_health(self.cfg, "docich", None), "unknown")

    def test_malformed_candidate_and_missing_mapping_refuse_deploy(self):
        artifact = self.root / brief.ARTIFACT
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"{}")
        bad_sha = self.commit(self.root)
        gw.git(self.root, "reset", "--hard", self.baseline)
        with self.assertRaises(ValueError):
            gw._plan_projections(self.cfg, "docich", self.root, self.baseline, bad_sha)
        sha = self.candidate()
        self.cfg["repos"]["docich"]["projections"] = {}
        with self.assertRaisesRegex(ValueError, "managed projection missing"):
            self.deploy(sha)

    def test_diagnostics_reports_stale_projection_as_warn_without_content(self):
        collector = self.root / "ops/vm_actions/collect_diagnostics.py"
        collector.parent.mkdir(parents=True)
        collector.write_text('print(\'{"status":"ok"}\')\n')
        self.baseline = self.commit(self.root)
        gw.write_json(self.state_path, {"mode": "git", "sha": self.baseline})
        sha = self.candidate()
        self.deploy(sha)
        healthy = gw.diagnostics_result(self.cfg, "docich", "production", sha)["diagnostics"]
        self.assertEqual(healthy["ops_brief_projection"], {"status": "matched"})
        (self.live / brief.DESTINATION).write_bytes(b"PRIVATE_RUNTIME_TEXT")
        stale = gw.diagnostics_result(self.cfg, "docich", "production", sha)["diagnostics"]
        self.assertEqual(stale["status"], "warn")
        self.assertEqual(stale["ops_brief_projection"], {"status": "drift"})
        for hidden in ("PRIVATE_RUNTIME_TEXT", "private body", brief.digest(SOURCE), "最新の修正"):
            self.assertNotIn(hidden, json.dumps(stale, ensure_ascii=False))

    def test_concurrent_edit_during_failure_is_preserved(self):
        sha = self.candidate()
        original = gw.write_json

        def fail_final(path, value):
            if value.get("sha") == sha and "deployment_intent" not in value:
                (self.live / brief.DESTINATION).write_bytes(b"concurrent operator edit")
                raise OSError("simulated failure")
            original(path, value)

        with mock.patch.object(gw, "write_json", side_effect=fail_final):
            with self.assertRaisesRegex(ValueError, "rollback incomplete"):
                self.deploy(sha)
        self.assertEqual((self.live / brief.DESTINATION).read_bytes(), b"concurrent operator edit")
        self.assertTrue(gw.read_json(self.state_path)["deployment_intent"]["recovery_required"])

    def test_unknown_legacy_drift_is_preserved_and_deploy_rolls_back(self):
        sha = self.candidate()
        (self.live / brief.DESTINATION).write_bytes(b"unknown operator edit")
        with self.assertRaisesRegex(ValueError, "projection drift"):
            self.deploy(sha)
        self.assertEqual(gw.git(self.root, "rev-parse", "HEAD"), self.baseline)
        self.assertEqual((self.live / brief.DESTINATION).read_bytes(), b"unknown operator edit")

    def test_missing_live_and_stale_live_never_report_success(self):
        sha = self.candidate()
        self.deploy(sha)
        for content in (self.legacy, None):
            if content is None:
                (self.live / brief.DESTINATION).unlink()
            else:
                (self.live / brief.DESTINATION).write_bytes(content)
            self.assertEqual(gw.status_result(self.cfg, "docich", "production", sha)["status"], "drift")
            with self.assertRaisesRegex(ValueError, "drift"):
                self.deploy(sha)

    def test_state_commit_failure_rolls_back_projection_and_parent(self):
        sha = self.candidate()
        original = gw.write_json

        def fail_final(path, value):
            if value.get("sha") == sha and "deployment_intent" not in value:
                raise OSError("simulated final state failure")
            original(path, value)

        with mock.patch.object(gw, "write_json", side_effect=fail_final):
            with self.assertRaises(OSError):
                self.deploy(sha)
        self.assertEqual(gw.read_json(self.state_path)["sha"], self.baseline)
        self.assertEqual((self.live / brief.DESTINATION).read_bytes(), self.legacy)

    def test_removing_parent_artifact_cannot_reactivate_stale_submodule_brief(self):
        self.deploy(self.candidate())
        sha = self.candidate(remove=True)
        with self.assertRaisesRegex(ValueError, "artifact removal refused"):
            self.deploy(sha)

    def test_bootstrap_and_rebaseline_do_not_adopt_stale_brief(self):
        sha = self.candidate()
        gw.git(self.root, "reset", "--hard", sha)
        with self.assertRaisesRegex(ValueError, "ops brief projection drift"):
            gw.rebaseline_git(self.cfg, "docich", sha)
        self.state_path.unlink()
        with self.assertRaisesRegex(ValueError, "ops brief projection drift"):
            gw.bootstrap_git(self.cfg, "docich", sha)

    def test_symlink_destination_is_rejected(self):
        sha = self.candidate()
        output = self.live / brief.DESTINATION
        output.unlink()
        output.symlink_to(self.live / "handoff.md")
        with self.assertRaises(ValueError):
            self.deploy(sha)
        self.assertTrue(output.is_symlink())


if __name__ == "__main__":
    unittest.main()
