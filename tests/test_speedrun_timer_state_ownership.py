"""Run the hanjuku-speedrun timer projection contract under pytest/CI.

CI does not install Chromium, so the display rules live in an extractable
pure block of the overlay HTML (``tests/test_speedrun_timer_view.mjs``), while
this wrapper runs both node suites and pins the rta-written ``timer.json``
shape to the projection the overlay consumes.
"""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "overlays" / "direct_broadcast_overlay_state.mjs"
TEST = ROOT / "tests" / "test_speedrun_timer_state.mjs"
VIEW_TEST = ROOT / "tests" / "test_speedrun_timer_view.mjs"
RUNTIME = "g1300-abcdef12"


class SpeedrunTimerStateOwnershipTest(unittest.TestCase):
    def test_timer_projection_is_owned_and_tested_by_docich(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn("export function speedrunTimerState", source)
        self.assertIn("export const DEFAULT_SPEEDRUN_TIMER_FILE", source)
        self.assertIn("export const SPEEDRUN_TIMER_TTL_MS", source)
        self.assertNotIn("games/soviet_now", source)
        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        result = subprocess.run(
            ["node", "--test", str(TEST), str(VIEW_TEST)], cwd=ROOT,
            capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _project(self, timer_path: Path, switch_path: Path, now_ms: int):
        script = (
            "const m=await import(process.argv[1]);"
            "console.log(JSON.stringify(m.speedrunTimerState("
            "process.argv[2],process.argv[3],Number(process.argv[4]))));"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script, MODULE.as_uri(),
             str(timer_path), str(switch_path), str(now_ms)],
            cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_rta_written_timer_json_keeps_every_public_field(self):
        from docich.hanjuku_speedrun.rta import timer as rta_timer

        if shutil.which("node") is None:
            self.skipTest("Node.js is not installed")
        now = 1_800_000_000_000
        records = {"next_attempt_no": 1}
        timer = rta_timer.new_timer(RUNTIME, records, now)
        timer, records = rta_timer.start_attempt(timer, records, now, attempt_no=1)
        clock = now
        for index in range(10):
            clock += 1_000
            timer, records = rta_timer.confirm_split(
                timer, records, clock, index, "chapter_end_save")
        finished, _ = rta_timer.finish(timer, records, clock + 1_000)
        dnf, _ = rta_timer.dnf(timer, records, clock + 1_000, "game_over")

        switch = {
            "schema_version": 1,
            "phase": "ready",
            "active": {"game": "hanjuku-speedrun", "runtime_id": RUNTIME,
                       "generation": 1300},
        }
        with tempfile.TemporaryDirectory(prefix="docich-speedrun-contract-") as tmp:
            tmpdir = Path(tmp)
            timer_path = tmpdir / "timer.json"
            switch_path = tmpdir / "game_switch.json"
            switch_path.write_text(json.dumps(switch), encoding="utf-8")
            expected_keys = {
                "attempt_no", "status", "started_at_ms", "ended_at_ms",
                "current_index", "splits", "comparison", "heartbeat_at_ms",
                "pb_eligible", "total_verdict", "total_ms", "stale", "nowMs",
            }

            timer_path.write_text(json.dumps(finished), encoding="utf-8")
            view = self._project(timer_path, switch_path, clock + 2_000)
            self.assertIsNotNone(view)
            self.assertEqual(set(view), expected_keys)
            self.assertEqual(view["status"], "finished")
            self.assertEqual(view["attempt_no"], 1)
            self.assertEqual(view["current_index"], 12)
            self.assertEqual(view["started_at_ms"], now)
            self.assertEqual(view["ended_at_ms"], clock + 1_000)
            self.assertTrue(view["pb_eligible"])
            self.assertEqual(view["total_verdict"], "gold")
            self.assertEqual(view["total_ms"], finished["total_ms"])
            self.assertIsInstance(view["total_ms"], int)
            self.assertGreater(view["total_ms"], 0)
            # 10 individual chapter splits, then the merged closing range.
            self.assertEqual(len(view["splits"]), 11)
            self.assertEqual(view["splits"][-1]["index"], 10)
            self.assertEqual(view["splits"][-1]["end_index"], 11)
            self.assertEqual(view["splits"][-1]["verdict"], "white")
            # The first eligible finish becomes the PB.
            self.assertEqual(view["comparison"]["pb_total_ms"], view["total_ms"])
            self.assertFalse(view["stale"])
            self.assertEqual(view["nowMs"], clock + 2_000)

            timer_path.write_text(json.dumps(dnf), encoding="utf-8")
            dnf_view = self._project(timer_path, switch_path, clock + 2_000)
            self.assertIsNotNone(dnf_view)
            self.assertEqual(set(dnf_view), expected_keys)
            self.assertEqual(dnf_view["status"], "dnf")
            self.assertEqual(dnf_view["current_index"], 10)
            self.assertEqual(len(dnf_view["splits"]), 10)
            self.assertFalse(dnf_view["pb_eligible"])
            self.assertIsNone(dnf_view["total_verdict"])
            self.assertEqual(dnf_view["total_ms"], dnf["total_ms"])


if __name__ == "__main__":
    unittest.main()
