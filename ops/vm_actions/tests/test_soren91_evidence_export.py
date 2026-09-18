import base64
import importlib.util
import json
import os
import subprocess
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "soren91_evidence_export.py"
EXTRACTOR = ROOT / "ops" / "vm_actions" / "extract_soren91_evidence.py"
AUTH = ROOT / "ops" / "vm_actions" / "authorize_soren91_evidence_export.py"
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
WORKFLOW = ROOT / ".github" / "workflows" / "soren91-evidence-export.yml"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvidenceBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="soren91-evidence-")
        self.root = Path(self.tmp.name) / "soren"
        self.runtime = self.root / "soren91"
        for rel in (
            "game_history",
            "tmp/summaries",
            "tmp/game_screenshots",
            "tmp/strategy_snapshots",
            "tmp/state",
        ):
            (self.runtime / rel).mkdir(parents=True, exist_ok=True)
        self.mod = load(HELPER, "soren91_evidence_export_test")
        self.now_ms = 1_800_000_000_000

    def tearDown(self):
        self.tmp.cleanup()

    def add_game(self, game: int, *, age_minutes: int = 5, screenshot_turns=(3, 11)):
        token = f"{game:04d}"
        history = self.runtime / "game_history" / f"game_{token}.jsonl"
        history.write_text(
            "\n".join(
                json.dumps(
                    {
                        "turn": turn,
                        "state": {"confidence": 0.9},
                        "decision": {"x": 0.1 * turn, "diagnostics": {"risk": 0}},
                    }
                )
                for turn in range(1, 15)
            )
            + "\n"
        )
        summary = self.runtime / "tmp" / "summaries" / f"game_{token}.json"
        summary.write_text(json.dumps({"game": game, "rank": 42, "turns": 14}) + "\n")
        mtime = (self.now_ms - age_minutes * 60_000) / 1000
        os.utime(summary, (mtime, mtime))
        (self.runtime / "tmp" / "strategy_snapshots" / f"game_{token}_strategy.mjs").write_text(
            "export const marker = true;\n"
        )
        shot_dir = self.runtime / "tmp" / "game_screenshots" / f"game_{token}"
        shot_dir.mkdir(parents=True, exist_ok=True)
        for turn in screenshot_turns:
            (shot_dir / f"turn_{turn}.png").write_bytes(b"PNG" + bytes([turn % 256]))
        return summary

    @staticmethod
    def fake_transcode(src: Path, dst: Path):
        dst.write_bytes(b"JPEG:" + src.name.encode("ascii"))

    def test_prepare_selects_recent_completed_games_and_fixed_evidence_only(self):
        self.add_game(100, age_minutes=70)
        self.add_game(101, age_minutes=20)
        self.add_game(102, age_minutes=10)
        # Incomplete newer summary must not enter the export.
        incomplete = self.runtime / "tmp" / "summaries" / "game_0103.json"
        incomplete.write_text('{"game":103}\n')
        os.utime(incomplete, ((self.now_ms - 60_000) / 1000,) * 2)
        # Unrelated files must never be swept into the archive.
        (self.runtime / "tmp" / "state" / "secret.env").write_text("TOKEN=do-not-export\n")
        (self.runtime / "tmp" / "state" / "soren91_loop_metrics.json").write_text(
            '{"profileStatus":"ok","samples":[]}\n'
        )

        state = self.mod.prepare_export(
            self.root, game_count=2, now_ms=self.now_ms, transcode=self.fake_transcode
        )
        self.assertEqual(state["games"], [102, 101])
        self.assertGreater(state["bundleBytes"], 0)
        self.assertLessEqual(state["bundleBytes"], self.mod.MAX_BUNDLE_BYTES)
        self.assertGreaterEqual(state["chunkCount"], 1)

        bundle = self.runtime / "tmp" / "state" / self.mod.BUNDLE_NAME
        with tarfile.open(bundle, "r:gz") as archive:
            names = set(archive.getnames())
        self.assertIn("manifest.json", names)
        self.assertIn("game_0102/history.jsonl", names)
        self.assertIn("game_0102/summary.json", names)
        self.assertIn("game_0102/strategy.mjs", names)
        self.assertIn("game_0102/screenshots/turn_3.jpg", names)
        self.assertIn("telemetry/soren91_loop_metrics.json", names)
        self.assertNotIn("game_0100/history.jsonl", names)
        self.assertTrue(all("secret.env" not in name for name in names))
        self.assertTrue(all(not name.endswith(".png") for name in names))

    def test_old_or_incomplete_evidence_is_not_exported(self):
        self.add_game(10, age_minutes=24 * 60 + 1)
        with self.assertRaises(self.mod.EvidenceError):
            self.mod.prepare_export(
                self.root, game_count=1, now_ms=self.now_ms, transcode=self.fake_transcode
            )

    def test_noncanonical_unpadded_alias_is_not_exported(self):
        # Production completion paths are exactly game_XXXX.*. A stray
        # unpadded alias must not be interpreted as a valid completed game.
        history = self.runtime / "game_history" / "game_7.jsonl"
        summary = self.runtime / "tmp" / "summaries" / "game_7.json"
        history.write_text('{"turn":1}\n')
        summary.write_text('{"gameNumber":7}\n')
        mtime = (self.now_ms - 60_000) / 1000
        os.utime(summary, (mtime, mtime))
        with self.assertRaises(self.mod.EvidenceError):
            self.mod.prepare_export(
                self.root, game_count=1, now_ms=self.now_ms, transcode=self.fake_transcode
            )

    def test_symlinked_required_evidence_is_rejected(self):
        self.add_game(20)
        history = self.runtime / "game_history" / "game_0020.jsonl"
        target = self.runtime / "game_history" / "real.jsonl"
        target.write_text(history.read_text())
        history.unlink()
        history.symlink_to(target.name)
        with self.assertRaises(self.mod.EvidenceError):
            self.mod.prepare_export(
                self.root, game_count=1, now_ms=self.now_ms, transcode=self.fake_transcode
            )

    def test_select_is_bounded_and_clear_removes_only_export_state(self):
        self.add_game(30)
        state = self.mod.prepare_export(
            self.root, game_count=1, now_ms=self.now_ms, transcode=self.fake_transcode
        )
        chosen = self.mod.select_chunk(
            state["chunkCount"] - 1, self.root, now_ms=self.now_ms + 1000
        )
        self.assertEqual(chosen["currentChunk"], state["chunkCount"] - 1)
        with self.assertRaises(self.mod.EvidenceError):
            self.mod.select_chunk(state["chunkCount"], self.root, now_ms=self.now_ms + 1000)
        keep = self.runtime / "tmp" / "state" / "keep.json"
        keep.write_text("{}\n")
        self.mod.clear_export(self.root)
        self.assertTrue(keep.exists())
        self.assertFalse((self.runtime / "tmp" / "state" / self.mod.STATE_NAME).exists())
        self.assertFalse((self.runtime / "tmp" / "state" / self.mod.BUNDLE_NAME).exists())


class DiagnosticsChunkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="soren91-diagnostic-export-")
        self.root = Path(self.tmp.name) / "soren"
        state = self.root / "soren91" / "tmp" / "state"
        state.mkdir(parents=True)
        self.now_ms = 1_800_000_000_000
        self.bundle = b"x" * 30000
        self.digest = __import__("hashlib").sha256(self.bundle).hexdigest()
        (state / "soren91_manual_evidence_export.tar.gz").write_bytes(self.bundle)
        (state / "soren91_manual_evidence_export.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "createdAtMs": self.now_ms,
                    "expiresAtMs": self.now_ms + 10 * 60 * 1000,
                    "bundleBytes": len(self.bundle),
                    "bundleSha256": self.digest,
                    "chunkBytes": 24 * 1024,
                    "chunkCount": 2,
                    "currentChunk": 0,
                    "games": [9],
                }
            )
            + "\n"
        )
        self.collector = load(COLLECTOR, "collect_diagnostics_export_test")
        self.extractor = load(EXTRACTOR, "extract_soren91_evidence_test")

    def tearDown(self):
        self.tmp.cleanup()

    def test_collector_returns_bounded_split_base64_without_writing(self):
        state = self.root / "soren91" / "tmp" / "state" / "soren91_manual_evidence_export.json"
        before = state.read_bytes()
        item = self.collector._collect_soren91_manual_evidence(self.root, self.now_ms + 1000)
        self.assertTrue(item["active"])
        self.assertEqual(item["chunkIndex"], 0)
        self.assertLessEqual(len(item["parts"]), 100)
        self.assertTrue(all(len(part) <= 480 for part in item["parts"]))
        decoded = base64.b64decode("".join(item["parts"]), validate=True)
        self.assertEqual(decoded, self.bundle[: 24 * 1024])
        self.assertEqual(state.read_bytes(), before)

    def test_expired_export_is_inactive(self):
        item = self.collector._collect_soren91_manual_evidence(
            self.root, self.now_ms + 10 * 60 * 1000 + 1
        )
        self.assertEqual(item, {"active": False})

    def test_extractor_reassembles_and_verifies_two_chunks(self):
        out = Path(self.tmp.name) / "out.tgz"
        meta = Path(self.tmp.name) / "meta.json"
        envelope = Path(self.tmp.name) / "envelope.json"
        first = self.collector._collect_soren91_manual_evidence(self.root, self.now_ms + 1000)
        envelope.write_text(json.dumps({"diagnostics": {"soren91_manual_evidence": first}}))
        self.assertEqual(self.extractor.append_chunk(envelope, 0, out, meta), 2)

        state_path = self.root / "soren91" / "tmp" / "state" / "soren91_manual_evidence_export.json"
        state = json.loads(state_path.read_text())
        state["currentChunk"] = 1
        state_path.write_text(json.dumps(state) + "\n")
        second = self.collector._collect_soren91_manual_evidence(self.root, self.now_ms + 1000)
        envelope.write_text(json.dumps({"diagnostics": {"soren91_manual_evidence": second}}))
        self.assertEqual(self.extractor.append_chunk(envelope, 1, out, meta), 2)
        self.extractor.verify(out, meta)
        self.assertEqual(out.read_bytes(), self.bundle)


class AuthorizationAndWorkflowTests(unittest.TestCase):
    def base_env(self):
        return {
            "GITHUB_REPOSITORY": "azumag/docich",
            "GITHUB_REPOSITORY_ID": "1327276249",
            "GITHUB_REPOSITORY_OWNER": "azumag",
            "GITHUB_REPOSITORY_OWNER_ID": "9018513",
            "GITHUB_ACTOR": "azumag",
            "GITHUB_ACTOR_ID": "9018513",
            "GITHUB_TRIGGERING_ACTOR": "azumag",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_PROTECTED": "true",
            "GITHUB_DEFAULT_BRANCH": "main",
            "GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/soren91-evidence-export.yml@refs/heads/main",
            "GITHUB_SHA": "a" * 40,
        }

    def run_auth(self, updates):
        env = os.environ.copy()
        env.update(self.base_env())
        env.update(updates)
        return subprocess.run(["python3", str(AUTH)], env=env, capture_output=True, text=True)

    def test_dispatch_requires_confirmation_and_bounded_game_count(self):
        ok = self.run_auth(
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "INPUT_CONFIRM": "production",
                "INPUT_GAMES": "3",
            }
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = self.run_auth(
            {
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "INPUT_CONFIRM": "production",
                "INPUT_GAMES": "4",
            }
        )
        self.assertNotEqual(bad.returncode, 0)

    def test_issue_command_is_exact_owner_only_issue_414(self):
        fields = {
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_ACTION": "created",
            "ISSUE_NUMBER": "414",
            "COMMENT_BODY": "/soren91-evidence-export",
            "COMMENT_AUTHOR": "azumag",
            "COMMENT_AUTHOR_ID": "9018513",
        }
        ok = self.run_auth(fields)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        wrong = dict(fields)
        wrong["ISSUE_NUMBER"] = "413"
        self.assertNotEqual(self.run_auth(wrong).returncode, 0)

    def test_workflow_keeps_transfer_bounded_and_does_not_log_payload(self):
        text = WORKFLOW.read_text()
        self.assertIn("github.event.issue.number == 414", text)
        self.assertIn("github.event.comment.body == '/soren91-evidence-export'", text)
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", text)
        self.assertIn("retention-days: 1", text)
        self.assertIn("soren91_evidence_prepare.py", text)
        self.assertIn("soren91_evidence_export.py select", text)
        self.assertIn("soren91_evidence_export.py clear", text)
        self.assertIn('"diagnostics docich production $SHA"', text)
        self.assertNotIn('echo "$diagnostics_json"', text)
        self.assertNotIn('cat "$envelope"', text)
        self.assertNotIn("scp ", text)
        self.assertNotIn("rsync ", text)
        self.assertNotIn("ssh-keyscan", text)
        self.assertNotIn("github.event.comment.body }} |", text)


if __name__ == "__main__":
    unittest.main()
