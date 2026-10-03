import importlib.util
import json
import os
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "soren91_evidence_export.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PartialEvidenceExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="soren91-partial-evidence-")
        self.root = Path(self.tmp.name) / "soren"
        self.runtime = self.root / "soren91"
        for rel in (
            "game_history",
            "tmp/summaries",
            "tmp/game_screenshots",
            "tmp/screenshots",
            "tmp/strategy_snapshots",
            "tmp/state",
        ):
            (self.runtime / rel).mkdir(parents=True, exist_ok=True)
        self.mod = load(HELPER, "soren91_partial_evidence_export_test")
        self.now_ms = 1_800_000_000_000

    def tearDown(self):
        self.tmp.cleanup()

    def write_loop_metrics(self, *, age_minutes: int = 5, **updates):
        path = self.runtime / "tmp" / "state" / "soren91_loop_metrics.json"
        value = {
            "schemaVersion": 1,
            "updatedAtMs": self.now_ms - age_minutes * 60_000,
            "game": 9,
            "turn": 2,
            "elapsedMs": 90_000,
            "dropProfile": {
                "session": "2f63f769-e7e6-4272-8230-16664c594344",
                "records": [{"captureStageMs": {"screenshot": 9631.3}}],
            },
        }
        value.update(updates)
        path.write_text(
            json.dumps(value) + "\n"
        )
        mtime = (self.now_ms - age_minutes * 60_000) / 1000
        os.utime(path, (mtime, mtime))
        return path

    @staticmethod
    def iso(ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def png(width=3, height=2):
        def chunk(name, data):
            return struct.pack("!I", len(data)) + name + data + struct.pack("!I", zlib.crc32(name + data))
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\0\x80\0" * width) * height))
            + chunk(b"IEND", b"")
        )

    @staticmethod
    def fake_transcode(src, dst):
        dst.write_bytes(b"JPEG:" + src.read_bytes())

    def write_partial(self, *, age_minutes=5):
        self.write_loop_metrics(age_minutes=age_minutes)
        updated = self.now_ms - age_minutes * 60_000
        shot = self.runtime / "tmp" / "screenshots" / "turn_0002.png"
        shot.write_bytes(self.png())
        os.utime(shot, ((updated - 3000) / 1000,) * 2)
        history = self.runtime / "game_history" / "latest_0009.jsonl"
        history.write_text(json.dumps({"turn": 1, "timestamp": self.iso(updated - 60_000), "state": {"pieces": []}}) + "\n")
        os.utime(history, ((updated - 60_000) / 1000,) * 2)
        calibration = self.runtime / "tmp" / "calibration.json"
        value = {
            "screen": {"width": 3, "height": 2},
            "board": {"left": 1, "right": 2, "top": 0.5, "bottom": 1.5, "width": 1, "height": 1},
            "walls": {"leftOuter": 0, "leftInner": 1, "rightInner": 2, "rightOuter": 3},
            "dropArea": {"pixelLeft": 1.1, "pixelRight": 1.9},
            "pixelsPerUnit": 0.14, "confidence": 0.82, "method": "profile", "isFallback": False,
            "timestamp": self.iso(updated - 120_000),
        }
        calibration.write_text(json.dumps(value) + "\n")
        os.utime(calibration, ((updated - 120_000) / 1000,) * 2)
        return shot, history, calibration

    def read_bundle(self, **kwargs):
        state = self.mod.prepare_export(
            self.root, game_count=2, now_ms=self.now_ms,
            transcode=kwargs.pop("transcode", self.fake_transcode), **kwargs,
        )
        bundle = self.runtime / "tmp" / "state" / self.mod.BUNDLE_NAME
        with tarfile.open(bundle, "r:gz") as archive:
            files = {name: archive.extractfile(name).read() for name in archive.getnames()}
        return state, json.loads(files["manifest.json"]), files

    def rewrite_json(self, path, mutate):
        info = path.stat()
        value = json.loads(path.read_bytes())
        mutate(value)
        path.write_text(json.dumps(value) + "\n")
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))

    def test_fresh_telemetry_exports_without_completed_game(self):
        self.write_loop_metrics()

        state = self.mod.prepare_export(self.root, game_count=2, now_ms=self.now_ms)
        self.assertEqual(state["games"], [0])
        self.assertEqual(state["evidenceMode"], "telemetry_only")

        bundle = self.runtime / "tmp" / "state" / self.mod.BUNDLE_NAME
        with tarfile.open(bundle, "r:gz") as archive:
            names = set(archive.getnames())
            manifest = json.load(archive.extractfile("manifest.json"))

        self.assertEqual(
            names,
            {"manifest.json", "telemetry/soren91_loop_metrics.json"},
        )
        self.assertEqual(manifest["evidenceMode"], "telemetry_only")
        self.assertEqual(manifest["games"], [])
        self.assertFalse(any(name.startswith("game_") for name in names))

    def test_stale_telemetry_does_not_bypass_completed_game_gate(self):
        self.write_loop_metrics(age_minutes=72 * 60 + 1)

        with self.assertRaisesRegex(
            self.mod.EvidenceError,
            "no completed Soren91 evidence in the last 72 hours",
        ):
            self.mod.prepare_export(self.root, game_count=2, now_ms=self.now_ms)

    def test_completed_game_keeps_completed_mode_and_real_game_identity(self):
        token = "0042"
        history = self.runtime / "game_history" / f"game_{token}.jsonl"
        summary = self.runtime / "tmp" / "summaries" / f"game_{token}.json"
        history.write_text('{"turn":1}\n')
        summary.write_text('{"game":42,"turns":1}\n')
        mtime = (self.now_ms - 60_000) / 1000
        os.utime(summary, (mtime, mtime))
        self.write_loop_metrics()

        state = self.mod.prepare_export(self.root, game_count=1, now_ms=self.now_ms)
        self.assertEqual(state["games"], [42])
        self.assertEqual(state["evidenceMode"], "completed_games")

        bundle = self.runtime / "tmp" / "state" / self.mod.BUNDLE_NAME
        with tarfile.open(bundle, "r:gz") as archive:
            manifest = json.load(archive.extractfile("manifest.json"))
        self.assertEqual(manifest["games"], [42])
        self.assertEqual(manifest["evidenceMode"], "completed_games")

    def test_historical_partial_exports_exact_fixed_sources_with_age_and_identity(self):
        shot, history, calibration = self.write_partial(age_minutes=24 * 60)
        self.rewrite_json(calibration, lambda value: value.update({"unknownSecret": "do-not-export"}))
        self.rewrite_json(calibration, lambda value: value["screen"].update({"url": "do-not-export"}))
        before = {path: path.read_bytes() for path in (shot, history, calibration)}
        (shot.parent / "turn_2.png").write_bytes(b"wrong-alias")
        (shot.parent / "calibration.png").write_bytes(b"not-requested")
        (self.runtime / "tmp" / "state" / "secret.env").write_text("do-not-export")
        (self.runtime / "strategy.mjs").write_text("do-not-export")
        state, manifest, files = self.read_bundle()
        self.assertEqual(state["games"], [0])
        self.assertEqual(state["evidenceMode"], "partial_game")
        self.assertEqual(manifest["games"], [])
        partial = manifest["partialEvidence"]
        self.assertIs(partial["completed"], False)
        self.assertEqual((partial["game"], partial["turn"]), (9, 2))
        self.assertEqual(partial["telemetrySession"], "2f63f769-e7e6-4272-8230-16664c594344")
        self.assertNotIn("session", partial)
        self.assertEqual(partial["telemetryAgeMs"], 24 * 60 * 60_000)
        self.assertEqual(set(files), {
            "manifest.json", "telemetry/soren91_loop_metrics.json",
            "partial/game_0009/history.jsonl", "partial/game_0009/screenshots/turn_0002.jpg",
            "partial/game_0009/calibration.json",
        })
        projection = json.loads(files["partial/game_0009/calibration.json"])
        self.assertNotIn("unknownSecret", projection)
        self.assertNotIn("url", projection["screen"])
        self.assertNotIn(b"do-not-export", b"".join(files.values()))
        self.assertEqual(files["partial/game_0009/history.jsonl"], before[history])
        image_meta = next(item for item in manifest["files"] if item["kind"] == "partial-screenshot")
        self.assertEqual(image_meta["sourceMinusTelemetryMs"], -3000)
        self.assertEqual(image_meta["sourceAgeMs"], 24 * 60 * 60_000 + 3000)
        self.assertEqual((image_meta["sourceWidth"], image_meta["sourceHeight"]), (3, 2))
        cal_meta = next(item for item in manifest["files"] if item["kind"] == "partial-calibration")
        self.assertEqual(cal_meta["relationship"], "separate-saved-calibration")
        self.assertIs(cal_meta["screenMatchesScreenshot"], True)
        history_meta = next(item for item in manifest["files"] if item["kind"] == "partial-history")
        self.assertEqual(history_meta["relationship"], "separate-saved-history")
        self.assertIs(history_meta["sessionAttributed"], False)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_completed_and_partial_have_separate_game_identities(self):
        self.write_partial()
        summary = self.runtime / "tmp" / "summaries" / "game_0008.json"
        summary.write_text('{"game":8}\n')
        os.utime(summary, ((self.now_ms - 600_000) / 1000,) * 2)
        (self.runtime / "game_history" / "game_0008.jsonl").write_text('{"turn":0}\n')
        state, manifest, files = self.read_bundle()
        self.assertEqual(state["games"], [8])
        self.assertEqual(manifest["games"], [8])
        self.assertEqual(state["evidenceMode"], "completed_games")
        self.assertEqual(manifest["partialEvidence"]["game"], 9)
        self.assertIn("game_0008/history.jsonl", files)
        self.assertNotIn("game_0009/history.jsonl", files)

    def test_invalid_loop_identity_or_timestamp_cannot_select_partial_paths(self):
        cases = [
            {"game": True}, {"game": "../9"}, {"game": 10000}, {"turn": -1}, {"turn": True},
            {"updatedAtMs": self.now_ms + 1}, {"updatedAtMs": self.now_ms - self.mod.MAX_AGE_MS - 1},
            {"updatedAtMs": self.now_ms - 1000}, {"elapsedMs": float("nan")}, {"elapsedMs": 10 ** 500},
            {"dropProfile": {"session": "private/path"}}, {"schemaVersion": 2},
        ]
        for updates in cases:
            with self.subTest(updates=list(updates)):
                self.write_partial()
                self.write_loop_metrics(**updates)
                _, manifest, files = self.read_bundle()
                self.assertNotIn("partialEvidence", manifest)
                self.assertFalse(any(name.startswith("partial/") for name in files))

    def test_alias_or_previous_game_screenshot_is_not_attributed_to_current_turn(self):
        shot, _, _ = self.write_partial()
        shot.rename(shot.with_name("turn_2.png"))
        _, manifest, files = self.read_bundle()
        self.assertNotIn("partial/game_0009/screenshots/turn_0002.jpg", files)
        self.assertEqual(manifest["partialEvidence"]["calibrationStatus"], "screenshot-unavailable")
        for delta in (-100_000, 2000):
            with self.subTest(delta=delta):
                shot, _, _ = self.write_partial()
                os.utime(shot, ((self.now_ms - 300_000 + delta) / 1000,) * 2)
                _, _, files = self.read_bundle()
                self.assertFalse(any(item.endswith(".jpg") for item in files))

    def test_either_completion_marker_excludes_partial_even_without_other_marker(self):
        self.write_partial()
        for path in (
            self.runtime / "game_history" / "game_0009.jsonl",
            self.runtime / "tmp" / "summaries" / "game_0009.json",
        ):
            with self.subTest(path=path.name):
                path.write_text("{}\n")
                _, manifest, files = self.read_bundle()
                self.assertNotIn("partialEvidence", manifest)
                self.assertFalse(any(name.startswith("partial/") for name in files))
                path.unlink()

    def test_changed_telemetry_revision_discards_partial_snapshot(self):
        self.write_partial()
        original = self.mod._read_stable_optional
        def racing_read(runtime, path, limit):
            result = original(runtime, path, limit)
            if path.name == "calibration.json":
                self.write_loop_metrics(updatedAtMs=self.now_ms - 299_999)
            return result
        with patch.object(self.mod, "_read_stable_optional", side_effect=racing_read):
            _, manifest, files = self.read_bundle()
        self.assertNotIn("partialEvidence", manifest)
        self.assertFalse(any(name.startswith("partial/") for name in files))

    def test_completion_during_source_reads_discards_partial(self):
        self.write_partial()
        original = self.mod._read_stable_optional
        def completing_read(runtime, path, limit):
            result = original(runtime, path, limit)
            if path.name == "calibration.json":
                (self.runtime / "game_history" / "game_0009.jsonl").write_text("{}\n")
            return result
        with patch.object(self.mod, "_read_stable_optional", side_effect=completing_read):
            _, manifest, files = self.read_bundle()
        self.assertNotIn("partialEvidence", manifest)
        self.assertFalse(any(name.startswith("partial/") for name in files))

    def test_transcode_reads_checked_copy_despite_live_source_replacement(self):
        shot, _, _ = self.write_partial()
        original = shot.read_bytes()
        def mutate_then_transcode(src, dst):
            self.assertNotEqual(src, shot)
            shot.write_bytes(b"private-new-source")
            self.assertEqual(src.read_bytes(), original)
            self.fake_transcode(src, dst)
        _, _, files = self.read_bundle(transcode=mutate_then_transcode)
        self.assertEqual(files["partial/game_0009/screenshots/turn_0002.jpg"], b"JPEG:" + original)
        self.assertFalse(any(name.endswith(".png") for name in files))

    def test_symlinked_partial_sources_are_rejected(self):
        for index in range(3):
            with self.subTest(index=index):
                paths = self.write_partial()
                path = paths[index]
                target = path.with_name("unreviewed-target")
                path.rename(target)
                path.symlink_to(target.name)
                try:
                    with self.assertRaisesRegex(self.mod.EvidenceError, "symlink"):
                        self.read_bundle()
                finally:
                    path.unlink()
                    target.rename(path)

    def test_symlinked_screenshot_directory_and_nonregular_file_are_rejected(self):
        shot, _, _ = self.write_partial()
        directory = shot.parent
        target = directory.with_name("unreviewed-shots")
        directory.rename(target)
        directory.symlink_to(target.name)
        with self.assertRaisesRegex(self.mod.EvidenceError, "symlink"):
            self.read_bundle()
        directory.unlink()
        target.rename(directory)
        shot.unlink()
        os.mkfifo(shot)
        with self.assertRaisesRegex(self.mod.EvidenceError, "size/type"):
            self.read_bundle()

    def test_oversize_partial_sources_are_rejected(self):
        for index, limit in enumerate((self.mod.MAX_SCREENSHOT_BYTES, self.mod.MAX_HISTORY_BYTES, self.mod.MAX_CALIBRATION_BYTES)):
            with self.subTest(index=index):
                paths = self.write_partial()
                with paths[index].open("wb") as handle:
                    handle.truncate(limit + 1)
                with self.assertRaisesRegex(self.mod.EvidenceError, "size/type"):
                    self.read_bundle()

    def test_incomplete_png_is_omitted_and_dimensions_remain_bounded(self):
        for data in (b"", b"not-a-png"):
            with self.subTest(size=len(data)):
                shot, _, _ = self.write_partial()
                shot.write_bytes(data)
                os.utime(shot, ((self.now_ms - 303_000) / 1000,) * 2)
                _, manifest, files = self.read_bundle()
                self.assertEqual(manifest["partialEvidence"]["screenshotStatus"], "invalid-or-incomplete-png")
                self.assertNotIn("partial/game_0009/screenshots/turn_0002.jpg", files)
                self.assertIn("partial/game_0009/history.jsonl", files)
        shot, _, _ = self.write_partial()
        shot.write_bytes(self.png()[:16] + struct.pack("!II", 8193, 2) + self.png()[24:])
        os.utime(shot, ((self.now_ms - 303_000) / 1000,) * 2)
        with self.assertRaisesRegex(self.mod.EvidenceError, "size/type"):
            self.read_bundle()

    def test_invalid_or_misaligned_calibration_is_omitted_without_freeform_fields(self):
        cases = [
            (lambda c: c.update(method="private-url"), "invalid-schema"),
            (lambda c: c["board"].update(bottom=float("nan")), "invalid-schema"),
            (lambda c: c["board"].update(bottom=10 ** 500), "invalid-schema"),
            (lambda c: c["screen"].update(width=True), "invalid-schema"),
            (lambda c: c.update(timestamp="not-a-date"), "invalid-schema"),
            (lambda c: c.update(timestamp=self.iso(self.now_ms + 1000)), "timestamp-mismatch"),
            (lambda c: c["screen"].update(width=4), "screen-mismatch"),
        ]
        for mutate, status in cases:
            with self.subTest(status=status):
                _, _, calibration = self.write_partial()
                self.rewrite_json(calibration, mutate)
                _, manifest, files = self.read_bundle()
                self.assertEqual(manifest["partialEvidence"]["calibrationStatus"], status)
                self.assertNotIn("partial/game_0009/calibration.json", files)
                self.assertNotIn(b"private-url", b"".join(files.values()))

    def test_partial_history_must_have_complete_bounded_turn_records(self):
        for data in (
            b'{"turn":1',
            (json.dumps({"turn": 3, "timestamp": self.iso(self.now_ms - 360_000)}) + "\n").encode(),
            b'{"turn":1,"timestamp":"private-value"}\n',
            ((json.dumps({"turn": 1, "timestamp": self.iso(self.now_ms - 360_000)}) + "\n") * 2).encode(),
        ):
            with self.subTest(size=len(data)):
                _, history, _ = self.write_partial()
                history.write_bytes(data)
                os.utime(history, ((self.now_ms - 360_000) / 1000,) * 2)
                _, _, files = self.read_bundle()
                self.assertNotIn("partial/game_0009/history.jsonl", files)
                self.assertIn("partial/game_0009/screenshots/turn_0002.jpg", files)

    def test_optional_transcode_failure_preserves_history_and_size_limits_still_hold(self):
        self.write_partial()
        def failed_transcode(src, dst):
            raise subprocess.CalledProcessError(1, "ffmpeg")
        for transcode, status in (
            (failed_transcode, "transcode-failed"),
            (lambda src, dst: dst.write_bytes(b""), "empty-transcode"),
        ):
            with self.subTest(status=status):
                _, manifest, files = self.read_bundle(transcode=transcode)
                self.assertEqual(manifest["partialEvidence"]["screenshotStatus"], status)
                self.assertIn("partial/game_0009/history.jsonl", files)
                self.assertNotIn("partial/game_0009/screenshots/turn_0002.jpg", files)
                self.assertNotIn("partial/game_0009/calibration.json", files)
        with self.assertRaisesRegex(self.mod.EvidenceError, "size contract"):
            self.read_bundle(transcode=lambda src, dst: dst.write_bytes(b"x" * (self.mod.MAX_SCREENSHOT_BYTES + 1)))
        with patch.object(self.mod, "MAX_BUNDLE_BYTES", 128):
            with self.assertRaisesRegex(self.mod.EvidenceError, "bundle outside size"):
                self.read_bundle()

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg unavailable")
    def test_real_png_transcodes_to_single_jpeg_with_bounded_height_and_width(self):
        for width, height in ((3, 2), (16, 4096)):
            with self.subTest(width=width, height=height):
                shot, _, _ = self.write_partial()
                shot.write_bytes(self.png(width, height))
                os.utime(shot, ((self.now_ms - 303_000) / 1000,) * 2)
                _, _, files = self.read_bundle(transcode=self.mod._default_transcode)
                image = files["partial/game_0009/screenshots/turn_0002.jpg"]
                self.assertTrue(image.startswith(b"\xff\xd8"))
                self.assertTrue(image.endswith(b"\xff\xd9"))
                offset = 2
                output_dimensions = None
                while offset + 8 < len(image):
                    self.assertEqual(image[offset], 0xFF)
                    marker = image[offset + 1]
                    if marker in (0xC0, 0xC1, 0xC2):
                        output_dimensions = struct.unpack("!HH", image[offset + 5:offset + 9])
                        break
                    offset += 2 + int.from_bytes(image[offset + 2:offset + 4], "big")
                self.assertIsNotNone(output_dimensions)
                self.assertTrue(all(1 <= value <= 960 for value in output_dimensions))


if __name__ == "__main__":
    unittest.main()
