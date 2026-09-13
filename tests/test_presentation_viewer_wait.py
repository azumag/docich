"""presentation.py viewer-wait option: default stays 10s, SRT path can extend."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.presentation import _parser  # noqa: E402


def _args(extra=()):
    base = [
        "--display", ":97", "--title", "t",
        "--x", "0", "--y", "90", "--width", "960", "--height", "540",
    ]
    return _parser().parse_args(base + list(extra) + ["--", "true"])


class ViewerWaitTests(unittest.TestCase):
    def test_default_is_10s(self):
        self.assertEqual(_args().viewer_wait_sec, 10)

    def test_custom_budget_accepted(self):
        self.assertEqual(_args(("--viewer-wait-sec", "120")).viewer_wait_sec, 120)

    def test_non_positive_rejected(self):
        with self.assertRaises(SystemExit):
            _parser().parse_args([
                "--display", ":97", "--title", "t",
                "--x", "0", "--y", "0", "--width", "1", "--height", "1",
                "--viewer-wait-sec", "0", "--", "true",
            ])


if __name__ == "__main__":
    unittest.main()
