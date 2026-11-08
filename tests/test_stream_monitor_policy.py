"""Restart verdict contracts for the reconnect and audio-capture monitors."""
import itertools
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_monitor_policy as pol  # noqa: E402


class TwitchOfflineStreakTest(unittest.TestCase):
    def test_versioned(self):
        self.assertEqual(pol.STREAM_MONITOR_POLICY_CONTRACT_VERSION, 1)

    def test_transient_failure_never_counts_or_restarts(self):
        for streak in (0, 1, 5):
            self.assertEqual(pol.step_twitch_offline_streak(streak, None, 1), (streak, False))

    def test_online_resets(self):
        self.assertEqual(pol.step_twitch_offline_streak(9, True, 2), (0, False))

    def test_restart_only_when_the_streak_reaches_the_threshold(self):
        streak = 0
        verdicts = []
        for _ in range(3):
            streak, restart = pol.step_twitch_offline_streak(streak, False, 3)
            verdicts.append((streak, restart))
        self.assertEqual(verdicts, [(1, False), (2, False), (3, True)])

    def test_a_probe_failure_between_offlines_keeps_but_does_not_extend_the_streak(self):
        streak, restart = pol.step_twitch_offline_streak(0, False, 2)
        streak, restart = pol.step_twitch_offline_streak(streak, None, 2)
        self.assertEqual((streak, restart), (1, False))
        self.assertEqual(pol.step_twitch_offline_streak(streak, False, 2), (2, True))

    def test_only_an_exact_true_is_online(self):
        # Mirrors the legacy `live is True` check: no truthy value may reset.
        self.assertEqual(pol.step_twitch_offline_streak(1, 1, 5), (2, False))
        self.assertEqual(pol.step_twitch_offline_streak(0, False, 1), (1, True))

    def test_restart_is_never_possible_without_an_offline_observation(self):
        for streak, live, threshold in itertools.product(range(0, 12), (True, None), range(1, 11)):
            self.assertFalse(pol.step_twitch_offline_streak(streak, live, threshold)[1])


class AudioCaptureLossTest(unittest.TestCase):
    def test_missing_before_any_ok_never_restarts(self):
        consecutive, seen = 0, False
        for _ in range(20):
            consecutive, seen, restart = pol.step_audio_capture_loss("missing", consecutive, seen, 1)
            self.assertEqual((consecutive, seen, restart), (0, False, False))

    def test_loss_after_a_seen_capture_restarts_at_the_threshold(self):
        consecutive, seen, restart = pol.step_audio_capture_loss("ok", 0, False, 2)
        self.assertEqual((consecutive, seen, restart), (0, True, False))
        consecutive, seen, restart = pol.step_audio_capture_loss("missing", consecutive, seen, 2)
        self.assertEqual((consecutive, seen, restart), (1, True, False))
        consecutive, seen, restart = pol.step_audio_capture_loss("missing", consecutive, seen, 2)
        self.assertEqual((consecutive, seen, restart), (2, True, True))

    def test_unreachable_or_unknown_resets_and_never_restarts(self):
        for state in ("unreachable", "unknown", "garbage", ""):
            with self.subTest(state=state):
                self.assertEqual(pol.step_audio_capture_loss(state, 5, True, 1), (0, True, False))

    def test_recovery_resets_the_count(self):
        self.assertEqual(pol.step_audio_capture_loss("ok", 4, True, 5), (0, True, False))

    def test_restart_requires_missing_after_a_seen_capture_for_every_input(self):
        for state, consecutive, seen, threshold in itertools.product(
            ("ok", "missing", "unreachable", "unknown"), range(0, 6), (False, True), range(1, 6)
        ):
            _count, _seen, restart = pol.step_audio_capture_loss(state, consecutive, seen, threshold)
            if restart:
                self.assertEqual(state, "missing")
                self.assertTrue(seen)
                self.assertGreaterEqual(consecutive + 1, threshold)


class PurityTest(unittest.TestCase):
    def test_no_process_network_time_environment_or_game_access(self):
        import ast
        tree = ast.parse((ROOT / "src/docich/stream_monitor_policy.py").read_text())
        imported = set()
        attrs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Attribute):
                attrs.add(node.attr)
        self.assertEqual(imported, {"__future__"})
        self.assertFalse(attrs & {"kill", "send_signal", "sleep", "Popen", "Thread", "environ"})

    def test_module_loads_by_file_path_without_package(self):
        code = (
            "import importlib.util,sys;s=importlib.util.spec_from_file_location('x',sys.argv[1]);"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "print(m.step_twitch_offline_streak(1,False,2))"
        )
        proc = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT / "src/docich/stream_monitor_policy.py")],
                              capture_output=True, text=True, timeout=12, check=False)
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "(2, True)"), proc.stderr)


if __name__ == "__main__":
    unittest.main()
