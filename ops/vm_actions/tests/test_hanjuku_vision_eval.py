"""Offline vision metrics and real #1302/parser boundary regressions."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile
import zlib

OPS = Path(__file__).resolve().parents[1]
ROOT = OPS.parents[1]
spec = importlib.util.spec_from_file_location("hanjuku_vision_eval_under_test",
                                             OPS / "evaluate_hanjuku_vision.py")
vision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vision)
RUN = "g12-12345678"
NAME = "hanjuku_frames/decision-001.png"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def png(rgb=None):
    rgb = bytes(256 * 224 * 3) if rgb is None else rgb
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    raw = b"".join(b"\0" + rgb[y*768:(y+1)*768] for y in range(224))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 224, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def unit_bundle():
    """A metrics-only fixture; real archive validation has its own tests below."""
    image = png()
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(NAME, image)
    raw = data.getvalue()
    manifest = {"identity": {"runtime_id": RUN}, "files": {
        NAME: {"export_file_sha256": digest(image), "rgb_sha256": digest(bytes(256*224*3))}}}
    return raw, manifest


def labels_for(raw, manifest, expected):
    meta = manifest["files"][NAME]
    return {"schema": 1, "runtime_id": RUN, "archive_sha256": digest(raw),
            "split": "holdout", "samples": [
                {"snapshot": NAME, "file_sha256": meta["export_file_sha256"],
                 "rgb_sha256": meta["rgb_sha256"], "expected": expected}]}


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.manifest = unit_bundle()
        self.verify = patch.object(vision, "_verified", return_value=self.manifest).start()
        self.hashes = patch.object(vision, "_source_hashes", return_value={"parser": "a"*64}).start()
        self.addCleanup(patch.stopall)

    def evaluate(self, expected, actual):
        observed = dict.fromkeys(vision.FIELDS)
        observed.update(actual)
        with patch.object(vision, "observe", return_value=observed):
            return vision.evaluate(self.raw, RUN, labels_for(self.raw, self.manifest, expected))

    def test_outcome_categories(self):
        cases = [(32, 32, "correct_present"), (None, None, "correct_absent"),
                 (32, None, "abstained"), (32, 2, "wrong_value"),
                 (None, 2, "false_present"), (0, 0, "correct_present"),
                 (1, True, "wrong_value"), ([1, 2], (1, 2), "wrong_value")]
        for expected, actual, outcome in cases:
            with self.subTest(expected=expected, actual=actual):
                self.assertEqual(vision.outcome(expected, actual), outcome)

    def test_wrong_hp_is_not_abstention(self):
        result = self.evaluate({"enemy_hp": 32}, {"enemy_hp": 2})
        counts = result["by_field"]["enemy_hp"]
        self.assertEqual(counts["wrong_value"], 1)
        self.assertEqual(counts["abstained"], 0)
        self.assertEqual(counts["accuracy_when_decided"], 0)
        self.assertEqual(counts["present_coverage"], 1)

    def test_unknown_hp_is_not_wrong_lower_hp(self):
        counts = self.evaluate({"enemy_hp": 32}, {})["aggregate"]
        self.assertEqual(counts["abstained"], 1)
        self.assertEqual(counts["wrong"], 0)
        self.assertEqual(counts["present_coverage"], 0)
        self.assertIsNone(counts["accuracy_when_decided"])

    def test_true_absence_and_false_presence_are_distinct(self):
        result = self.evaluate({"hand": None, "menu_cursor": None}, {"menu_cursor": 176})
        self.assertEqual(result["aggregate"]["correct_absent"], 1)
        self.assertEqual(result["aggregate"]["false_present"], 1)
        self.assertIsNone(result["aggregate"]["present_coverage"])

    def test_zero_hp_is_a_present_value(self):
        self.assertEqual(self.evaluate({"enemy_hp": 0}, {"enemy_hp": 0})[
            "aggregate"]["correct_present"], 1)

    def test_unlabelled_fields_are_not_in_denominator(self):
        result = self.evaluate({"enemy_hp": 32}, {"enemy_hp": 32, "ally_hp": 999})
        self.assertEqual(result["aggregate"]["labeled"], 1)
        self.assertEqual(result["by_field"]["ally_hp"]["labeled"], 0)
        self.assertEqual(set(result["samples"][0]["fields"]), {"enemy_hp"})

    def test_empty_labels_cannot_claim_perfect_accuracy(self):
        observe = Mock(side_effect=AssertionError("must not parse unlabelled frames"))
        with patch.object(vision, "observe", observe):
            result = vision.evaluate(self.raw, RUN, labels_for(self.raw, self.manifest, {}))
        self.assertEqual(result["status"], "no_ground_truth")
        self.assertEqual(result["labeled_samples"], 0)
        self.assertEqual(result["unlabeled_samples"], 1)
        self.assertIsNone(result["aggregate"]["accuracy_when_decided"])
        self.assertIsNone(result["aggregate"]["decision_coverage"])
        observe.assert_not_called()

    def test_empty_dataset_is_explicit(self):
        labels = labels_for(self.raw, self.manifest, {})
        labels["samples"] = []
        result = vision.evaluate(self.raw, RUN, labels)
        self.assertEqual(result["selected_samples"], 0)
        self.assertEqual(result["status"], "no_ground_truth")

    def test_prepare_never_generates_ground_truth(self):
        result = vision.prepare(self.raw, RUN, "calibration")
        self.assertEqual(result["samples"][0]["expected"], {})
        self.assertEqual(result["archive_sha256"], digest(self.raw))
        self.assertEqual(result["split"], "calibration")

    def test_template_deduplicates_identical_rgb_ring_slots(self):
        self.manifest["files"]["hanjuku_frames/decision-002.png"] = self.manifest["files"][NAME]
        self.assertEqual(len(vision.prepare(self.raw, RUN, "holdout")["samples"]), 1)

    def test_archive_is_not_claimed_as_authenticated_full_history(self):
        result = self.evaluate({"enemy_hp": 32}, {"enemy_hp": 32})
        self.assertFalse(result["source"]["history_complete"])
        self.assertEqual(result["source"]["github_provenance"], "must_be_verified_separately")
        self.assertEqual(result["parser_source_sha256"], {"parser": "a"*64})

    def test_source_change_during_evaluation_rejected(self):
        self.hashes.side_effect = [{"parser": "a"*64}, {"parser": "b"*64}]
        with self.assertRaisesRegex(vision.EvaluationError, "parser_source_changed"):
            self.evaluate({"enemy_hp": 32}, {"enemy_hp": 32})

    def test_malformed_observer_rejected(self):
        with patch.object(vision, "observe", return_value={}):
            with self.assertRaisesRegex(vision.EvaluationError, "invalid_observation"):
                vision.evaluate(self.raw, RUN, labels_for(self.raw, self.manifest, {"enemy_hp": 32}))

    def test_metrics_totals_and_rates(self):
        counts = dict.fromkeys(vision.COUNTS, 0)
        counts.update(labeled=5, expected_present=3, correct_present=1, correct_absent=1,
                      wrong_value=1, false_present=1, abstained=1)
        result = vision.metrics(counts)
        self.assertEqual(result["correct"], 2)
        self.assertEqual(result["wrong"], 2)
        self.assertEqual(result["accuracy_when_decided"], .5)
        self.assertEqual(result["decision_coverage"], .8)
        self.assertEqual(result["present_coverage"], 2/3)
        self.assertEqual(result["wrong_rate"], .4)


class LabelValidationTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.manifest = unit_bundle()
        self.labels = labels_for(self.raw, self.manifest, {"enemy_hp": 32})

    def check(self, labels=None):
        return vision.validate_labels(self.labels if labels is None else labels, self.raw, self.manifest)

    def test_valid_partial_and_absence_labels(self):
        self.labels["samples"][0]["expected"] = {"enemy_hp": 0, "hand": None, "kind": "battle",
                                                  "cursor": [0, 0], "marker": [-2, -2]}
        self.check()

    def test_bad_top_level_types(self):
        for value in (None, [], True, "labels"):
            with self.subTest(value=value), self.assertRaises(vision.EvaluationError):
                vision.validate_labels(value, self.raw, self.manifest)

    def test_bad_schema(self):
        for value in (True, 2, "1", None):
            self.labels["schema"] = value
            with self.subTest(value=value), self.assertRaises(vision.EvaluationError):
                self.check()

    def test_archive_and_runtime_binding(self):
        for key, value in (("runtime_id", "g13-12345678"), ("archive_sha256", "b"*64)):
            labels = copy.deepcopy(self.labels)
            labels[key] = value
            with self.subTest(key=key), self.assertRaises(vision.EvaluationError):
                self.check(labels)

    def test_unknown_keys_rejected(self):
        self.labels["extra"] = "not accepted"
        with self.assertRaises(vision.EvaluationError):
            self.check()

    def test_invalid_split(self):
        for value in ("train", [], None, True):
            self.labels["split"] = value
            with self.subTest(value=value), self.assertRaises(vision.EvaluationError):
                self.check()

    def test_missing_or_traversal_snapshot(self):
        for value in ("../frame.png", "/tmp/frame.png", "hanjuku_run.json", None):
            self.labels["samples"][0]["snapshot"] = value
            with self.subTest(value=value), self.assertRaises(vision.EvaluationError):
                self.check()

    def test_file_and_rgb_sha_domains_cannot_be_swapped(self):
        sample = self.labels["samples"][0]
        sample["file_sha256"], sample["rgb_sha256"] = sample["rgb_sha256"], sample["file_sha256"]
        with self.assertRaisesRegex(vision.EvaluationError, "snapshot_hash_mismatch"):
            self.check()

    def test_stale_ring_slot_rejected(self):
        self.manifest["files"][NAME]["rgb_sha256"] = "f"*64
        with self.assertRaisesRegex(vision.EvaluationError, "snapshot_hash_mismatch"):
            self.check()

    def test_duplicate_name_rejected(self):
        self.labels["samples"].append(copy.deepcopy(self.labels["samples"][0]))
        with self.assertRaisesRegex(vision.EvaluationError, "duplicate_sample"):
            self.check()

    def test_same_rgb_under_another_name_rejected(self):
        alias = "hanjuku_frames/frame-001.png"
        self.manifest["files"][alias] = self.manifest["files"][NAME]
        item = copy.deepcopy(self.labels["samples"][0])
        item["snapshot"] = alias
        self.labels["samples"].append(item)
        with self.assertRaisesRegex(vision.EvaluationError, "duplicate_sample"):
            self.check()

    def test_too_many_samples_rejected(self):
        self.labels["samples"] *= vision.MAX_SAMPLES + 1
        with self.assertRaisesRegex(vision.EvaluationError, "invalid_labels"):
            self.check()

    def test_malformed_expected_fields(self):
        cases = [{"enemy_hp": True}, {"enemy_hp": "32"}, {"enemy_hp": -1},
                 {"enemy_hp": 1000}, {"enemy_hp": float("nan")}, {"typo": 1},
                 {"kind": "unknown"}, {"kind": None}, {"ally": "\ufffd"},
                 {"hand": [2, 1, 0, 3]}, {"hand": [0, 0, 256, 2]},
                 {"menu_cursor": 224}, {"cursor": [0, True]}, {"marker": [0]},
                 {"selected": "bad\nlabel"}, {"enemy": ""}]
        for expected in cases:
            self.labels["samples"][0]["expected"] = expected
            with self.subTest(expected=expected), self.assertRaises(vision.EvaluationError):
                self.check()

    def test_all_labels_validated_before_any_observation(self):
        self.labels["samples"].append({"bad": "label"})
        observer = Mock()
        with patch.object(vision, "_verified", return_value=self.manifest), patch.object(vision, "observe", observer):
            with self.assertRaises(vision.EvaluationError):
                vision.evaluate(self.raw, RUN, self.labels)
        observer.assert_not_called()


class ArchiveAndParserIntegrationTests(unittest.TestCase):
    """Uses real receiver, evidence producer and parser; no mocks of their logic."""
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(OPS))
        sys.path.insert(0, str(ROOT / "src"))
        import hanjuku_evidence
        cls.evidence = hanjuku_evidence

    def bundle(self, image=None):
        image = png() if image is None else image
        run = {"schema": 1, "game": "hanjuku-hero", "runtime_id": RUN,
               "generation": 12, "lease_id": "synthetic-evaluation-test",
               "frame_sha256": "0"*64, "terminal_reason": "screen_stalled",
               "unchanged_seconds": 300}
        identity = self.evidence.terminal_identity(run, RUN)
        raw = self.evidence.build_archive(identity, {
            "hanjuku_run.json": self.evidence._dump(run), NAME: image}, [])
        return raw, vision._verified(raw, RUN)

    def battle_image(self, *, occluded=False):
        from docich.hanjuku_glyphs import GLYPHS
        glyphs = {value: code for code, value in GLYPHS.items()}
        rgb = bytearray(256 * 224 * 3)
        def pixel(x, y, value):
            i = (y * 256 + x) * 3
            rgb[i:i+3] = bytes(value)
        for y in range(160, 200):
            for x in range(256):
                pixel(x, y, (240, 240, 240))
        for x, text in ((24, "ア"), (152, "イ"), (96, "32"), (224, "40")):
            for offset, char in enumerate(text):
                mask = glyphs[char]
                for y in range(8):
                    for dx in range(8):
                        if mask & (1 << (63 - y*8 - dx)):
                            pixel(x + offset*8 + dx, 176+y, (0, 0, 0))
        if occluded:
            pixel(96, 176, (255, 0, 0))
        return png(bytes(rgb))

    def test_real_receiver_template_and_blank_frame(self):
        raw, manifest = self.bundle()
        result = vision.evaluate(raw, RUN, labels_for(raw, manifest, {"hand": None, "enemy_hp": None}))
        self.assertEqual(result["aggregate"]["correct_absent"], 2)
        self.assertTrue(result["parser_source_sha256"])
        self.assertEqual(vision.prepare(raw, RUN, "holdout")["samples"][0]["expected"], {})

    def test_real_parser_reads_hp_exactly(self):
        raw, manifest = self.bundle(self.battle_image())
        expected = {"kind": "battle", "enemy": "ア", "ally": "イ", "enemy_hp": 32, "ally_hp": 40}
        result = vision.evaluate(raw, RUN, labels_for(raw, manifest, expected))
        self.assertEqual(result["aggregate"]["correct_present"], 5, result["samples"])

    def test_occluded_32_is_not_invented_as_2(self):
        raw, manifest = self.bundle(self.battle_image(occluded=True))
        result = vision.evaluate(raw, RUN, labels_for(raw, manifest, {"enemy_hp": 32}))
        self.assertEqual(result["aggregate"]["abstained"], 1, result["samples"])
        self.assertEqual(result["aggregate"]["wrong"], 0)

    def test_no_decide_or_subprocess(self):
        from docich import hanjuku_bot
        raw, manifest = self.bundle()
        with patch.object(hanjuku_bot, "decide", side_effect=AssertionError("game input")), \
                patch("subprocess.run", side_effect=AssertionError("external process")):
            vision.evaluate(raw, RUN, labels_for(raw, manifest, {"hand": None}))

    def test_wrong_runtime_rejected(self):
        raw, _ = self.bundle()
        with self.assertRaisesRegex(vision.EvaluationError, "unexpected_runtime"):
            vision.prepare(raw, "g13-12345678", "holdout")

    def test_archive_tampering_rejected(self):
        raw, _ = self.bundle()
        changed = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(changed, "w") as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == NAME:
                    data = png(bytes([1]) * (256*224*3))
                target.writestr(info, data)
        with self.assertRaises(self.evidence.EvidenceError):
            vision.prepare(changed.getvalue(), RUN, "holdout")

    def test_nonterminal_archive_rejected(self):
        raw, _ = self.bundle()
        changed = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(changed, "w") as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "hanjuku_run.json":
                    run = json.loads(data)
                    run["terminal_reason"] = None
                    data = self.evidence._dump(run)
                target.writestr(info, data)
        with self.assertRaises(self.evidence.EvidenceError):
            vision.prepare(changed.getvalue(), RUN, "holdout")

    def test_png_ancillary_chunks_rejected(self):
        image = png()
        ancillary = b"tEXt" + b"private=not-exportable"
        chunk = struct.pack(">I", len(ancillary)-4) + ancillary + struct.pack(">I", zlib.crc32(ancillary))
        with self.assertRaises(self.evidence.EvidenceError):
            vision._frame(image[:-12] + chunk + image[-12:])

    def test_cli_private_output_and_no_overwrite(self):
        raw, _ = self.bundle()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "evidence.zip", root / "labels.json"
            source.write_bytes(raw)
            args = ["--archive", str(source), "--runtime-id", RUN,
                    "--prepare", "holdout", "--output", str(output)]
            with patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(vision.main(args), 0)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                original = output.read_bytes()
                self.assertEqual(vision.main(args), 1)
                self.assertEqual(output.read_bytes(), original)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["evidence.zip", "labels.json"])
            self.assertEqual(source.read_bytes(), raw)

    def test_cli_unlabelled_data_returns_two_not_success(self):
        raw, _ = self.bundle()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evidence.zip").write_bytes(raw)
            (root / "labels.json").write_text(json.dumps(vision.prepare(raw, RUN, "holdout")))
            with patch("sys.stdout", new_callable=io.StringIO):
                code = vision.main(["--archive", str(root / "evidence.zip"), "--runtime-id", RUN,
                                    "--labels", str(root / "labels.json"), "--output", str(root / "report.json")])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads((root / "report.json").read_text())["status"], "no_ground_truth")

    def test_cli_symlink_and_hardlink_source_rejected(self):
        raw, _ = self.bundle()
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "real.zip").write_bytes(raw)
                source, output = root / "linked.zip", root / "out.json"
                if kind == "symlink":
                    source.symlink_to(root / "real.zip")
                else:
                    os.link(root / "real.zip", source)
                with patch("sys.stderr", new_callable=io.StringIO):
                    self.assertEqual(vision.main(["--archive", str(source), "--runtime-id", RUN,
                                                 "--prepare", "holdout", "--output", str(output)]), 1)
                self.assertFalse(output.exists())

    def test_cli_bad_labels_have_no_partial_or_verbose_output(self):
        raw, _ = self.bundle()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evidence.zip").write_bytes(raw)
            (root / "labels.json").write_text('{"private":"do-not-print", "private": 1}')
            with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                code = vision.main(["--archive", str(root / "evidence.zip"), "--runtime-id", RUN,
                                    "--labels", str(root / "labels.json"), "--output", str(root / "out.json")])
            self.assertEqual(code, 1)
            self.assertNotIn("do-not-print", stderr.getvalue())
            self.assertFalse((root / "out.json").exists())


if __name__ == "__main__":
    unittest.main()
