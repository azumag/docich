from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.overlay_queue import (  # noqa: E402
    OverlayQueueError,
    append_event,
    comment_gen_state_path,
    load_events,
    overlay_events_path,
    radio_state_path,
    regenerate_overlay,
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


    def test_source_id_retry_refreshes_timestamp_without_duplicate_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "soren"
            base = int(time.time())
            first = dict(event(ts=base, title="PAPER retry"), source_id="fill:paper:BTC/JPY:1")
            retry = dict(first, ts=base + 1)
            self.assertTrue(append_event(root, first, keep=5, strict=True, regenerate=False))
            self.assertTrue(append_event(root, retry, keep=5, strict=True, regenerate=False))
            rows = load_events(root, strict=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_id"], "fill:paper:BTC/JPY:1")
            self.assertEqual(rows[0]["ts"], base + 1)

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


    def test_legacy_generation_state_environment_paths_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "soren"
            old_comment = os.environ.get("COMMENT_GEN_STATE_FILE")
            old_radio = os.environ.get("RADIO_STATE_FILE")
            os.environ["COMMENT_GEN_STATE_FILE"] = "tmp/custom-comment-state"
            os.environ["RADIO_STATE_FILE"] = "tmp/custom-radio-state"
            try:
                self.assertEqual(comment_gen_state_path(root), root / "tmp/custom-comment-state")
                self.assertEqual(radio_state_path(root), root / "tmp/custom-radio-state")
            finally:
                if old_comment is None:
                    os.environ.pop("COMMENT_GEN_STATE_FILE", None)
                else:
                    os.environ["COMMENT_GEN_STATE_FILE"] = old_comment
                if old_radio is None:
                    os.environ.pop("RADIO_STATE_FILE", None)
                else:
                    os.environ["RADIO_STATE_FILE"] = old_radio

    def test_regenerate_reports_nonzero_generator_exit_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "soren"
            root.mkdir()
            (root / "generate_event_overlay.py").write_text("# stub\n", encoding="utf-8")
            with mock.patch("docich.overlay_queue.subprocess.run", return_value=mock.Mock(returncode=1)):
                self.assertFalse(regenerate_overlay(root))

if __name__ == "__main__":
    unittest.main()

class TestNativeDeadlineInterop(unittest.TestCase):
    def test_paper_append_preserves_native_deadline_event(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            path=root/'tmp/state/overlay_events.jsonl'
            path.parent.mkdir(parents=True)
            native={**event(ts=int(time.time()),title='期限通知'),'category':'deadline','level':'warn'}
            path.write_text(json.dumps(native)+'\n')
            append_event(root,event(ts=int(time.time())),regenerate=False)
            rows=load_events(root,strict=True)
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['category'],'deadline')
