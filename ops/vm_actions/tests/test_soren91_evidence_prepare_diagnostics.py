import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PREPARE = ROOT / "ops" / "vm_actions" / "soren91_evidence_prepare.py"
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
EXTRACTOR = ROOT / "ops" / "vm_actions" / "extract_soren91_evidence.py"
READER = ROOT / "ops" / "vm_actions" / "read_soren91_evidence_failure.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvidencePrepareDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="soren91-prepare-diag-")
        self.root = Path(self.tmp.name) / "soren"
        for rel in (
            "soren91/game_history",
            "soren91/tmp/summaries",
            "soren91/tmp/game_screenshots",
            "soren91/tmp/strategy_snapshots",
            "soren91/tmp/state",
        ):
            (self.root / rel).mkdir(parents=True, exist_ok=True)
        self.prepare = load(PREPARE, "soren91_evidence_prepare_diag_test")
        self.collector = load(COLLECTOR, "collect_diagnostics_prepare_diag_test")
        self.extractor = load(EXTRACTOR, "extract_soren91_prepare_diag_test")
        self.reader = load(READER, "read_soren91_prepare_diag_test")
        self.now_ms = 1_800_000_000_000

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_root_writes_only_sanitized_failure_envelope(self):
        self.assertEqual(self.prepare.prepare(2, root=self.root, now_ms=self.now_ms), 20)
        item = self.collector._collect_soren91_manual_evidence(self.root, self.now_ms + 1000)
        self.assertTrue(item["active"])
        self.assertEqual(item["games"], [0])
        self.assertEqual(item["chunkCount"], 1)

        archive = Path(self.tmp.name) / "failure.tgz"
        meta = Path(self.tmp.name) / "failure-meta.json"
        envelope = Path(self.tmp.name) / "failure-envelope.json"
        envelope.write_text(json.dumps({"diagnostics": {"soren91_manual_evidence": item}}))
        self.assertEqual(self.extractor.append_chunk(envelope, 0, archive, meta), 1)
        self.extractor.verify(archive, meta)
        self.assertEqual(self.reader.read_reason(archive), "no_recent_completed_evidence")

        with tarfile.open(archive, "r:gz") as bundle:
            self.assertEqual([member.name for member in bundle.getmembers()], ["diagnostic.json"])

    def test_reader_rejects_non_diagnostic_archive(self):
        archive = Path(self.tmp.name) / "arbitrary.tgz"
        payload = Path(self.tmp.name) / "payload.txt"
        payload.write_text("not diagnostic\n")
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(payload, arcname="payload.txt")
        self.assertEqual(self.reader.read_reason(archive), "unclassified_prepare_failure")

    def test_classifier_never_returns_raw_exception_text(self):
        secretish = "TOKEN=secret /home/private/user/path"
        reason = self.prepare.classify_failure(RuntimeError(secretish))
        self.assertEqual(reason, "unexpected_error")
        self.assertNotIn("secret", reason)
        self.assertNotIn("/home", reason)


if __name__ == "__main__":
    unittest.main()
