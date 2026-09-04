from types import SimpleNamespace
from unittest import TestCase, mock

from docich.xkit import XKit


class TestFindWindow(TestCase):
    def test_without_timeout_uses_immediate_search(self):
        result = SimpleNamespace(returncode=1, stdout="", stderr="")
        with mock.patch("docich.xkit.procs.run", return_value=result) as run:
            self.assertIsNone(XKit(":98").find_window("Chrom"))

        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["xdotool", "search"])
        self.assertNotIn("--sync", argv)
        self.assertIsNone(run.call_args.kwargs["timeout"])

    def test_with_timeout_uses_sync_search_bounded_by_timeout(self):
        result = SimpleNamespace(returncode=0, stdout="123\n", stderr="")
        with mock.patch("docich.xkit.procs.run", return_value=result) as run:
            self.assertEqual(XKit(":99").find_window("docich-robots", timeout=0.25), "123")

        argv = run.call_args.args[0]
        self.assertEqual(argv[:3], ["xdotool", "search", "--sync"])
        self.assertEqual(run.call_args.kwargs["timeout"], 0.25)


if __name__ == "__main__":
    import unittest

    unittest.main()
