"""Regression coverage for mixed-format completed evidence (PR #1876)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "soren91_evidence_export.py"
SPEC = importlib.util.spec_from_file_location("completed_screenshot_export", MODULE_PATH)
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


class CompletedScreenshotIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "soren91"
        self.now = 1_800_000_000_000
        self.shots = self.runtime / "tmp" / "game_screenshots" / "game_0104"
        self.shots.mkdir(parents=True)
        self.write("game_history/game_0104.jsonl", b'{"turn":0}\n')
        self.write("tmp/summaries/game_0104.json", b'{"gameNumber":104}\n')
        self.calls = []

    def write(self, name, data, age_ms=1000):
        path = self.runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        stamp_ns = (self.now - age_ms) * 1_000_000
        os.utime(path, ns=(stamp_ns, stamp_ns))
        return path

    def shot(self, name, data=b"image", age_ms=1000):
        return self.write(f"tmp/game_screenshots/game_0104/{name}", data, age_ms)

    def transcode(self, src, dst):
        self.calls.append((src, dst))
        dst.write_bytes(b"JPEG:" + src.read_bytes())

    def export(self, transcode=None):
        exporter.prepare_export(root=self.root, game_count=1, now_ms=self.now,
                                transcode=transcode or self.transcode)
        with tarfile.open(exporter._bundle_path(self.root), "r:gz") as archive:
            members = [m for m in archive.getmembers() if m.isfile()]
            self.assertEqual(len(members), len({m.name for m in members}))
            data = {m.name: archive.extractfile(m).read() for m in members}
        manifest = json.loads(data.pop("manifest.json"))
        metadata = manifest["files"]
        names = [row["name"] for row in metadata]
        self.assertEqual(len(names), len(set(names)), "duplicate manifest output name")
        self.assertEqual(set(names), set(data), "manifest/tar must be one-to-one")
        for row in metadata:
            self.assertEqual(row["sha256"], hashlib.sha256(data[row["name"]]).hexdigest())
            self.assertEqual(row["bytes"], len(data[row["name"]]))
        images = {row["turn"]: data[row["name"]] for row in metadata if row["kind"] == "screenshot"}
        return images

    def test_same_turn_png_jpeg_does_not_corrupt_manifest(self):
        self.shot("turn_3.png", b"old png", 2000)
        self.shot("turn_3.jpg", b"new jpeg", 1000)
        self.shot("turn_11.png", b"turn eleven", 500)
        self.assertEqual(self.export(), {3: b"JPEG:new jpeg", 11: b"JPEG:turn eleven"})
        self.assertEqual(len(self.calls), 2)

    def test_aliases_do_not_consume_multiple_turn_slots(self):
        self.shot("turn_3.png", b"old", 3000)
        self.shot("turn_0003.jpg", b"new", 2000)
        self.shot("turn_3_hold.jpeg", b"newest", 1000)
        self.shot("turn_11.jpg", b"eleven")
        self.shot("turn_20.png", b"twenty")
        self.assertEqual(self.export(), {3: b"JPEG:newest", 11: b"JPEG:eleven", 20: b"JPEG:twenty"})

    def test_png_kill_switch_chooses_newer_png(self):
        self.shot("turn_4.jpeg", b"old jpeg", 2000)
        self.shot("turn_4.png", b"new png", 1000)
        self.assertEqual(self.export(), {4: b"JPEG:new png"})

    def test_png_only_preserves_numeric_turn_order_and_limit(self):
        for turn in (20, 11, 3, 1):
            self.shot(f"turn_{turn}.png", str(turn).encode())
        self.assertEqual(self.export(), {1: b"JPEG:1", 3: b"JPEG:3", 11: b"JPEG:11"})

    def test_jpeg_only_including_uppercase_extension(self):
        self.shot("turn_4.JPG", b"four")
        self.shot("turn_10.jpeg", b"ten")
        self.assertEqual(self.export(), {4: b"JPEG:four", 10: b"JPEG:ten"})

    def test_equal_mtime_is_deterministic(self):
        self.shot("turn_3.jpg", b"jpeg")
        self.shot("turn_3.png", b"png")
        first = self.export()
        self.assertEqual(first, self.export())
        self.assertEqual(len(first), 1)

    def test_symlink_directory_and_oversize_do_not_displace_safe_image(self):
        source = self.shot("turn_3.jpg", b"safe")
        self.shots.joinpath("turn_3.png").symlink_to(source)
        self.shots.joinpath("turn_3.jpeg").mkdir()
        oversized = self.shot("turn_3_large.png")
        with oversized.open("wb") as handle:
            handle.truncate(exporter.MAX_SCREENSHOT_BYTES + 1)
        self.assertEqual(self.export(), {3: b"JPEG:safe"})

    def test_pinned_source_bytes_are_not_reopened_from_archive(self):
        source = self.shot("turn_3.jpg", b"original")
        def transcode(src, dst):
            source.write_bytes(b"changed after validation")
            self.assertNotEqual(src, source)
            self.transcode(src, dst)
        self.assertEqual(self.export(transcode), {3: b"JPEG:original"})
        self.assertFalse(self.calls[0][0].exists(), "staging source must be removed")

    def test_missing_snapshot_skips_optional_image(self):
        source = self.shot("turn_3.jpg")
        original = exporter._read_stable_optional
        def unstable(runtime, path, limit):
            if path == source:
                return None
            return original(runtime, path, limit)
        with patch.object(exporter, "_read_stable_optional", side_effect=unstable):
            self.assertEqual(self.export(), {})

    def test_changed_after_selection_is_not_misattributed(self):
        source = self.shot("turn_3.jpg", b"before")
        original = exporter._read_stable_optional
        def changed(runtime, path, limit):
            if path == source:
                source.write_bytes(b"after selection")
            return original(runtime, path, limit)
        with patch.object(exporter, "_read_stable_optional", side_effect=changed):
            self.assertEqual(self.export(), {})

    def test_empty_image_does_not_displace_nonempty_image(self):
        self.shot("turn_3.png", b"valid", 2000)
        self.shot("turn_3.jpg", b"", 1000)
        self.assertEqual(self.export(), {3: b"JPEG:valid"})


if __name__ == "__main__":
    unittest.main()
