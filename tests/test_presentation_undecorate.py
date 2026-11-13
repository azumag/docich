"""WM-decoration enforcement for the projection ffplay (#4051).

After an output-display recreation (Xvfb :99) the window manager may
decorate the projection ffplay even though it is started with -noborder, so
the client area is shifted by its frame extents and the post-deploy realign
fails closed with exit 24. The presenter strips the decoration itself after
launch; the helpers below are best effort and must never break the
projection when the tools or the window are missing.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import presentation as mod  # noqa: E402

ENV = {"DISPLAY": ":99"}


class FrameExtentsTests(unittest.TestCase):
    def test_parses_xprop_output(self):
        with patch("subprocess.check_output",
                   return_value="_NET_FRAME_EXTENTS(CARDINAL) = 5, 5, 29, 5\n"):
            self.assertEqual(mod._frame_extents("4242", ENV), (5, 5, 29, 5))

    def test_returns_none_when_xprop_fails_or_output_is_unparsable(self):
        cases = [
            subprocess.CalledProcessError(1, "xprop"),
            OSError("xprop not installed"),
            subprocess.TimeoutExpired("xprop", 3),
        ]
        for error in cases:
            with self.subTest(error=type(error).__name__), \
                 patch("subprocess.check_output", side_effect=error):
                self.assertIsNone(mod._frame_extents("4242", ENV))
        with patch("subprocess.check_output", return_value="no extents here"):
            self.assertIsNone(mod._frame_extents("4242", ENV))


class UndecorateWindowTests(unittest.TestCase):
    def test_runs_hint_unmap_map_move_in_order(self):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0)

        with patch("subprocess.run", side_effect=fake_run), \
             patch("time.sleep") as sleep:
            mod._undecorate_window("4242", ENV, 239, 90)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][:6],
                         ["xprop", "-id", "4242", "-f", "_MOTIF_WM_HINTS", "32c"])
        self.assertIn("2, 0, 0, 0, 0", calls[0])
        self.assertEqual(calls[1], ["xdotool", "windowunmap", "4242"])
        self.assertEqual(calls[2], ["xdotool", "windowmap", "4242"])
        self.assertEqual(calls[3],
                         ["xdotool", "windowmove", "--sync", "4242", "239", "90"])
        sleep.assert_called_once_with(0.2)

    def test_tool_failure_is_swallowed(self):
        # Best effort: a failed hint change must never stop the projection.
        with patch("subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "xprop")):
            mod._undecorate_window("4242", ENV, 239, 90)


class EnsureProjectionUndecoratedTests(unittest.TestCase):
    def test_undecorates_only_when_frame_extents_are_nonzero(self):
        with patch.object(mod, "_projection_window_id", return_value="4242"), \
             patch.object(mod, "_frame_extents", return_value=(5, 5, 29, 5)), \
             patch.object(mod, "_undecorate_window") as undecorate:
            mod._ensure_projection_undecorated("title", ENV, 239, 90)
        undecorate.assert_called_once_with("4242", ENV, 239, 90)

    def test_skips_when_window_is_missing_or_undecorated(self):
        cases = [
            ("", (5, 5, 29, 5)),
            ("4242", (0, 0, 0, 0)),
            ("4242", None),
        ]
        for window_id, extents in cases:
            with self.subTest(window_id=window_id, extents=extents), \
                 patch.object(mod, "_projection_window_id",
                              return_value=window_id), \
                 patch.object(mod, "_frame_extents", return_value=extents), \
                 patch.object(mod, "_undecorate_window") as undecorate:
                mod._ensure_projection_undecorated("title", ENV, 239, 90)
            undecorate.assert_not_called()

    def test_lookup_failure_is_swallowed(self):
        with patch.object(mod, "_projection_window_id",
                          side_effect=OSError("xdotool missing")), \
             patch.object(mod, "_undecorate_window") as undecorate:
            mod._ensure_projection_undecorated("title", ENV, 239, 90)
        undecorate.assert_not_called()


class ProjectionWindowIdTests(unittest.TestCase):
    def test_returns_the_single_visible_window(self):
        with patch("subprocess.run",
                   return_value=subprocess.CompletedProcess(
                       ["xdotool"], 0, stdout="4242\n")):
            self.assertEqual(
                mod._projection_window_id("title", ENV, wait_s=0.0), "4242")

    def test_returns_empty_when_no_window_appears(self):
        with patch("subprocess.run",
                   return_value=subprocess.CompletedProcess(
                       ["xdotool"], 0, stdout="")):
            self.assertEqual(
                mod._projection_window_id("title", ENV, wait_s=0.0), "")

    def test_returns_empty_when_xdotool_fails(self):
        with patch("subprocess.run", side_effect=OSError("xdotool missing")):
            self.assertEqual(
                mod._projection_window_id("title", ENV, wait_s=0.0), "")


if __name__ == "__main__":
    unittest.main()
