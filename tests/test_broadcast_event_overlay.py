"""The shared event-overlay renderer must operate without Soviet game code."""
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "src" / "docich" / "broadcast_event_overlay.py"
SPEC = importlib.util.spec_from_file_location("docich_event_overlay", RENDERER)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class BroadcastEventOverlayTests(unittest.TestCase):
    def test_reusable_renderer_preserves_toast_work_and_generation_indicators(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            out = root / "overlay.html"
            work = root / "work.json"
            comment = root / "comment_state"
            events.write_text(
                '{"ts":99,"category":"system","title":"fresh-title","body":"fresh-body"}\n'
                '{"ts":1,"category":"system","title":"stale-title","body":"stale-body"}\n',
                encoding="utf-8",
            )
            work.write_text(
                '{"active":true,"ts":90,"title":"work-title","body":"work-body"}',
                encoding="utf-8",
            )
            comment.write_text("generating:comment:98\n", encoding="utf-8")
            env = {
                "EVENT_OVERLAY_STATE_BASE": str(root),
                "EVENT_OVERLAY_COMMENT_GEN_STATE": str(comment),
                "EVENT_OVERLAY_RADIO_STATE": str(root / "missing-radio"),
                "EVENT_OVERLAY_SAY_QUEUE_DIR": str(root / "missing-say"),
                "CODEX_WORK_OVERLAY_STALE_SEC": "3600",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                MODULE.render_event_overlay(events, out, keep=180, visible_sec=18, work_state_path=work, now=100)
            html = out.read_text(encoding="utf-8")
            self.assertIn("fresh-title", html)
            self.assertNotIn("stale-title", html)
            self.assertIn("work-title", html)
            self.assertIn("コメント生成中", html)
            self.assertIn('http-equiv="refresh" content="2"', html)
            self.assertIn("const VISIBLE_SEC = 18;", html)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o644)

    def test_standalone_cli_renders_without_submodule(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            output = root / "overlay.html"
            events.write_text(
                '{"ts":4102444800,"category":"system","title":"cli-ready","body":"ok"}\n',
                encoding="utf-8",
            )
            cmd = [sys.executable, str(RENDERER), str(events), str(output), "10", "18"]
            env = dict(os.environ, EVENT_OVERLAY_STATE_BASE=str(root),
                       EVENT_OVERLAY_COMMENT_GEN_STATE="", EVENT_OVERLAY_RADIO_STATE="",
                       EVENT_OVERLAY_SAY_QUEUE_DIR="")
            result = subprocess.run(cmd, cwd=root, env=env, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("cli-ready", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
