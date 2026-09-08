from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.overlay_queue import (  # noqa: E402
    OverlayQueueError,
    append_event,
    load_events,
    overlay_events_path,
    validate_event,
)


def event(*, ts: int = 100, title: str = "PAPER 取引") -> dict:
    return {
        "ts": ts,
        "category": "worker",
        "title": title,
        "body": "BTC/JPY PAPER 約定",
        "level": "info",
    }


class TestOverlayQueue(unittest.TestCase):
    def test_validate_normalizes_safe_event(self):
        got = validate_event(event())
        self.assertEqual(got["category"], "worker")
        self.assertEqual(got["title"], "PAPER 取引")
        self.assertEqual(got["level"], "info")

    def test_append_is_bounded_and_exact_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "soren"
            first = event(ts=100, title="PAPER 1")
            self.assertTrue(append_event(root, first, keep=2, strict=True, regenerate=False))
            self.assertFalse(append_event(root, first, keep=2, strict=True, regenerate=False))
            self.assertTrue(append_event(root, event(ts=101, title="PAPER 2"), keep=2, strict=True, regenerate=False))
            self.assertTrue(append_event(root, event(ts=102, title="PAPER 3"), keep=2, strict=True, regenerate=False))
            rows = load_events(root, strict=True)
            self.assertEqual([row["title"] for row in rows], ["PAPER 2", "PAPER 3"])

    def test_strict_append_refuses_corrupt_existing_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "soren"
            path = overlay_events_path(root)
            path.parent.mkdir(parents=True)
            path.write_text("not-json\n", encoding="utf-8")
            with self.assertRaises(OverlayQueueError):
                append_event(root, event(), strict=True, regenerate=False)
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json\n")

    def test_non_strict_load_preserves_webui_skip_corrupt_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "soren"
            path = overlay_events_path(root)
            path.parent.mkdir(parents=True)
            path.write_text("not-json\n" + json.dumps(event()) + "\n", encoding="utf-8")
            self.assertEqual(load_events(root, strict=False), [event()])

    def test_environment_override_path_is_relative_to_soren_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "soren"
            old = os.environ.get("EVENT_OVERLAY_EVENTS_FILE")
            os.environ["EVENT_OVERLAY_EVENTS_FILE"] = "tmp/custom-events.jsonl"
            try:
                self.assertEqual(overlay_events_path(root), root / "tmp/custom-events.jsonl")
            finally:
                if old is None:
                    os.environ.pop("EVENT_OVERLAY_EVENTS_FILE", None)
                else:
                    os.environ["EVENT_OVERLAY_EVENTS_FILE"] = old


if __name__ == "__main__":
    unittest.main()
