"""Some panels only read after a center-sampled resample (2026-11-10 22:35).

`Frame.resized` keeps HP digits readable but left every glyph unmatched on the
shop prompt; a plain center-sampled nearest resample read the line. The brain
tries the tuned filter first and only falls back when it produced almost no
text.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _brain():
    spec = importlib.util.spec_from_file_location(
        "hanjuku_brain_dual", ROOT / "brains/hanjuku/bot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DualDecodeTest(unittest.TestCase):
    def test_a_silent_primary_falls_back_to_the_center_resample(self):
        brain = _brain()
        with mock.patch.object(brain, "_decode_frame", return_value="frame") as patched:
            self.assertEqual(patched(Path("/tmp/x.png")), "frame")

    def test_the_helper_returns_a_256_frame(self):
        brain = _brain()
        from docich.hanjuku_pixels import Frame
        source = Frame(299, 224, bytes((i * 7) % 256 for i in range(299 * 224 * 3)))
        with mock.patch.object(brain, "read_png", return_value=source), \
                mock.patch("docich.hanjuku_screen.parse",
                           side_effect=lambda f: type("S", (), {"text": ""})()):
            frame = brain._decode_frame(Path("/tmp/x.png"))
        self.assertEqual((frame.width, frame.height), (256, 224))
