"""Source-of-truth contract for the moved multigame broadcast-state module."""
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "overlays" / "direct_broadcast_overlay_state.mjs"

class BroadcastStateOwnershipTest(unittest.TestCase):
    def test_module_is_owned_by_docich_and_can_load_without_soviet(self):
        source = STATE.read_text(encoding="utf-8")
        self.assertIn("export function buildDirectBroadcastOverlayState", source)
        self.assertIn("export function extractLegacyOverlayText", source)
        self.assertIn("export function parseLegacyEventOverlayDocument", source)
        self.assertNotIn("from '../games/soviet_now", source)
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        script = (
            "const m=await import(process.argv[1]);"
            "if(m.DIRECT_BROADCAST_VERSION!==1)process.exit(2);"
            "if(m.DIRECT_BROADCAST_STATE_ROUTE!=='/__soren_overlay/broadcast/state')process.exit(3);"
            "const state=m.buildDirectBroadcastOverlayState({sources:{}}, 0);"
            "if(state.version!==1||!state.feeds||!state.notifications)process.exit(4);"
        )
        with tempfile.TemporaryDirectory(prefix="docich-broadcast-state-") as tmp:
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script, STATE.as_uri()],
                cwd=tmp, capture_output=True, text=True, timeout=10, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

class NextCornerNoticeTest(unittest.TestCase):
    """The sidebar notice is a bounded projection of docich's rotation record."""

    def _state(self, tmp: Path, payload: dict) -> str:
        path = tmp / "next_corner.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def _build(self, tmp: Path, file_uri: str, now_ms: int) -> dict:
        script = (
            "const m=await import(process.argv[1]);"
            "const s=m.buildDirectBroadcastOverlayState("
            "{sources:{nextCornerFile:process.argv[2]}}, Number(process.argv[3]));"
            "console.log(JSON.stringify(s.nextCorner));"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, STATE.as_uri(), file_uri, str(now_ms)],
            cwd=tmp, capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout or "null")

    def _payload(self, **overrides) -> dict:
        now = int(time.time())
        payload = {
            "schema": 1,
            "corner": "ninvaders",
            "label": "スペースインベーダー",
            "when": "23:46",
            "due_at": now + 3600,
            "source": "auto",
            "hold": None,
            "decided_at": now,
            "updated_at": now,
        }
        payload.update(overrides)
        return payload

    def test_valid_record_is_projected_without_private_rotation_fields(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        with tempfile.TemporaryDirectory(prefix="docich-next-corner-") as tmpdir:
            tmp = Path(tmpdir)
            payload = self._payload()
            notice = self._build(tmp, self._state(tmp, payload), int(time.time()) * 1000)
        self.assertEqual(notice["corner"], "ninvaders")
        self.assertEqual(notice["label"], "スペースインベーダー")
        self.assertEqual(notice["when"], "23:46")
        self.assertIsNone(notice["hold"])
        self.assertEqual(
            set(notice), {"corner", "label", "when", "dueAt", "updatedAt", "hold"}
        )

    def test_prediction_hold_stays_visible_with_its_provisional_flag(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        with tempfile.TemporaryDirectory(prefix="docich-next-corner-") as tmpdir:
            tmp = Path(tmpdir)
            # 48-game prediction in flight: the corner is still next, so the
            # sidebar keeps the card and marks the planned clock provisional.
            file_uri = self._state(tmp, self._payload(hold="prediction"))
            notice = self._build(tmp, file_uri, int(time.time()) * 1000)
        self.assertEqual(notice["corner"], "ninvaders")
        self.assertEqual(notice["hold"], "prediction")

    def test_prediction_hold_ignores_the_past_due_grace(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        now = int(time.time())
        with tempfile.TemporaryDirectory(prefix="docich-next-corner-") as tmpdir:
            tmp = Path(tmpdir)
            # The boundary can hold the slot past its planned clock; the card
            # must not vanish while the prediction is still running.
            file_uri = self._state(tmp, self._payload(
                hold="prediction", due_at=now - 3600, updated_at=now,
            ))
            notice = self._build(tmp, file_uri, now * 1000)
        self.assertEqual(notice["hold"], "prediction")

    def test_unknown_hold_is_not_projected(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        with tempfile.TemporaryDirectory(prefix="docich-next-corner-") as tmpdir:
            tmp = Path(tmpdir)
            file_uri = self._state(tmp, self._payload(hold="unknown"))
            self.assertIsNone(self._build(tmp, file_uri, int(time.time()) * 1000))

    def test_stale_and_past_records_are_ignored(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        now = int(time.time())
        with tempfile.TemporaryDirectory(prefix="docich-next-corner-") as tmpdir:
            tmp = Path(tmpdir)
            stale = self._state(tmp, self._payload(updated_at=now - 3600))
            self.assertIsNone(self._build(tmp, stale, now * 1000))
            Path(stale).unlink()
            past = self._state(tmp, self._payload(due_at=now - 3600, updated_at=now))
            self.assertIsNone(self._build(tmp, past, now * 1000))

    def test_untrusted_text_is_bounded(self):
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        now = int(time.time())
        with tempfile.TemporaryDirectory(prefix="docich-next-corner-") as tmpdir:
            tmp = Path(tmpdir)
            payload = self._payload(
                corner="../../etc/passwd",
                label="制御文字\u0000の混入",
                when="18:00\n<script>",
            )
            file_uri = self._state(tmp, payload)
            # An unsafe corner id drops the whole notice.
            self.assertIsNone(self._build(tmp, file_uri, now * 1000))
            payload["corner"] = "ninvaders"
            file_uri = self._state(tmp, payload)
            notice = self._build(tmp, file_uri, now * 1000)
        self.assertNotIn("\u0000", notice["label"])
        self.assertNotIn("\n", notice["when"])


if __name__ == "__main__":
    unittest.main()
