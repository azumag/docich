import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HanjukuNarrationDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hanjuku-narration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = load_collector()

    def _log_path(self, soren):
        path = soren / "tmp" / ".say_queue" / "debug.log"
        path.parent.mkdir(parents=True)
        return path

    def test_playback_summary_projects_only_bounded_counters(self):
        soren = self.root / "soren"
        path = self._log_path(soren)
        path.write_text(
            "\n".join([
                "[_play_comment_queue 12:00:00 PID=11] 再生開始: tmp/.comment_queue/comment_announce_1_a_hanjuku_commentary.txt (hash=PRIVATE_HASH)",
                "[say_enqueue 12:00:01 PID=12/12] 外部killフラグ検出 → リトライ中止 | file=tmp/.comment_queue/comment_announce_1_a_hanjuku_commentary.playing token=PRIVATE_TOKEN label=hanjuku_commentary",
                "[_play_comment_queue 12:00:02 PID=11] 再生失敗: tmp/.comment_queue/comment_announce_1_a_hanjuku_commentary.playing",
                "[_play_comment_queue 12:00:03 PID=11] 再生開始: tmp/.comment_queue/comment_announce_2_b_hanjuku_commentary.txt (hash=PRIVATE_HASH)",
                "[say_enqueue 12:00:04 PID=13/13] say途中切断の疑い (elapsed=1s, expected=8s) | file=tmp/.comment_queue/comment_announce_2_b_hanjuku_commentary.playing token=PRIVATE_TOKEN label=hanjuku_commentary",
                "[say_enqueue 12:00:05 PID=13/13] sayは既に1秒再生済み → 重複防止のため再試行せず完了扱い | file=tmp/.comment_queue/comment_announce_2_b_hanjuku_commentary.playing token=PRIVATE_TOKEN label=hanjuku_commentary",
                "[_play_comment_queue 12:00:06 PID=11] 再生完了: tmp/.comment_queue/comment_announce_2_b_hanjuku_commentary.playing",
                "[_play_comment_queue 12:00:07 PID=11] 再生開始: tmp/.comment_queue/comment_announce_3_c_hanjuku_commentary.txt (hash=PRIVATE_HASH)",
                "[_play_comment_queue 12:00:08 PID=11] 再生開始: tmp/.comment_queue/comment_announce_4_other.txt (hash=PRIVATE_HASH)",
                "[say_enqueue 12:00:09 PID=14/14] 外部killフラグ検出 | file=tmp/.comment_queue/comment_announce_4_other.playing token=PRIVATE_TOKEN label=other",
                "PRIVATE_SPEECH_TEXT must never be returned",
            ]) + "\n",
            encoding="utf-8",
        )

        output = self.module._collect_hanjuku_narration_playback(soren)

        self.assertEqual(output, {
            "status": "available",
            "sampled_bytes": path.stat().st_size,
            "sampled_lines": 11,
            "tail_truncated": False,
            "queue_started": 3,
            "queue_completed": 1,
            "queue_failed": 1,
            "queue_unmatched_starts": 1,
            "external_kill_markers": 1,
            "truncated_playback_suspected": 1,
            "partial_audio_retry_suppressed": 1,
        })
        encoded = json.dumps(output)
        for private_value in ("PRIVATE_HASH", "PRIVATE_TOKEN", "PRIVATE_SPEECH_TEXT",
                              "comment_announce", str(path)):
            self.assertNotIn(private_value, encoded)

    def test_playback_summary_is_unavailable_for_symlinked_directory_or_log(self):
        external = self.root / "external"
        external_log = external / "debug.log"
        external.mkdir()
        external_log.write_text("PRIVATE_SPEECH_TEXT", encoding="utf-8")

        symlinked_directory = self.root / "soren-directory"
        (symlinked_directory / "tmp").mkdir(parents=True)
        (symlinked_directory / "tmp" / ".say_queue").symlink_to(
            external, target_is_directory=True)
        directory_output = self.module._collect_hanjuku_narration_playback(
            symlinked_directory)
        self.assertEqual(directory_output["status"], "unavailable")
        self.assertIsNone(directory_output["queue_started"])

        symlinked_file = self.root / "soren-file"
        file_path = self._log_path(symlinked_file)
        file_path.symlink_to(external_log)
        file_output = self.module._collect_hanjuku_narration_playback(symlinked_file)
        self.assertEqual(file_output["status"], "unavailable")
        self.assertIsNone(file_output["queue_started"])
        self.assertNotIn("PRIVATE_SPEECH_TEXT", json.dumps(directory_output))
        self.assertNotIn("PRIVATE_SPEECH_TEXT", json.dumps(file_output))

    def test_playback_summary_bounds_large_log_tail(self):
        soren = self.root / "soren"
        path = self._log_path(soren)
        start = (
            "[_play_comment_queue 12:00:00 PID=11] 再生開始: "
            "tmp/.comment_queue/comment_announce_5_z_hanjuku_commentary.txt"
        )
        path.write_bytes((b"padding\n" * 20000) + start.encode("utf-8") + b"\n")

        output = self.module._collect_hanjuku_narration_playback(soren)

        self.assertEqual(output["status"], "available")
        self.assertLessEqual(output["sampled_bytes"],
                             self.module.HANJUKU_PLAYBACK_LOG_MAX_BYTES)
        self.assertLessEqual(output["sampled_lines"],
                             self.module.HANJUKU_PLAYBACK_LOG_MAX_LINES)
        self.assertIs(output["tail_truncated"], True)
        self.assertEqual(output["queue_started"], 1)
        self.assertEqual(output["queue_unmatched_starts"], 1)

    def test_programs_attaches_playback_only_to_active_hanjuku(self):
        state_dir = self.root / "state"
        state_dir.mkdir()
        soren = self.root / "soren"
        self._log_path(soren).write_text(
            "[_play_comment_queue 12:00:00 PID=11] 再生開始: "
            "tmp/.comment_queue/comment_announce_1_a_hanjuku_commentary.txt\n",
            encoding="utf-8",
        )

        def collect_hanjuku_state(_state_dir, payload, _now):
            payload["game_switch"] = {
                "present": True, "readable": True, "active_game": "hanjuku-hero",
            }
            payload["retro_corner"] = {
                "present": True, "readable": True, "status": "active",
                "game": "hanjuku-hero",
            }

        def collect_mismatched_state(_state_dir, payload, _now):
            payload["game_switch"] = {
                "present": True, "readable": True, "active_game": "another-game",
            }
            payload["retro_corner"] = {
                "present": True, "readable": True, "status": "active",
                "game": "hanjuku-hero",
            }

        patches = (
            mock.patch.object(self.module, "_rotation_timer_selection",
                              return_value=("docich-corner-rotation.timer", False)),
            mock.patch.object(self.module, "_unit_is_active", return_value=True),
            mock.patch.object(self.module, "_unit_is_enabled", return_value=True),
            mock.patch.object(self.module, "_collect_boundary", return_value={}),
            mock.patch.object(self.module, "_collect_ab", return_value={}),
            mock.patch.object(self.module, "_collect_soren_game", return_value={}),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            with mock.patch.object(self.module, "_collect_corner_files",
                                   side_effect=collect_hanjuku_state):
                output = self.module._collect_programs(state_dir, soren, 0)
            playback = output["retro_corner"]["narration_playback"]
            self.assertEqual(playback["queue_started"], 1)
            self.assertEqual(playback["queue_unmatched_starts"], 1)

            with mock.patch.object(self.module, "_collect_corner_files",
                                   side_effect=collect_mismatched_state):
                mismatched = self.module._collect_programs(state_dir, soren, 0)
            self.assertNotIn("narration_playback", mismatched["retro_corner"])


if __name__ == "__main__":
    unittest.main()
