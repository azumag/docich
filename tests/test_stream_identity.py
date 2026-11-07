"""Exact classification of the live encoder runner's command line."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_identity as ident  # noqa: E402

SOREN = "/home/ubuntu/soren"


class LegacyTest(unittest.TestCase):
    def test_accepted_shapes(self):
        self.assertTrue(ident.is_legacy_runner_args(["python3", f"{SOREN}/lib/direct_stream.py", "run"], SOREN))
        self.assertTrue(ident.is_legacy_runner_args(["/usr/bin/python3.12", f"{SOREN}/lib/direct_stream.py", "run"], SOREN))
        self.assertTrue(ident.is_legacy_runner_args(["python3", "lib/direct_stream.py", "run"], SOREN, cwd=SOREN))

    def test_rejected_shapes(self):
        for args, cwd in (
            (["python3", "lib/direct_stream.py", "run"], None),                      # relative, unknown cwd
            (["python3", "lib/direct_stream.py", "run"], "/elsewhere"),               # relative, wrong cwd
            (["python3", f"{SOREN}/lib/direct_stream.py", "status"], None),
            (["python3", f"{SOREN}/lib/direct_stream.py", "stop"], None),
            (["python3", "/other/tree/lib/direct_stream.py", "run"], None),
            (["bash", f"{SOREN}/lib/direct_stream.py", "run"], None),
            (["python3", f"{SOREN}/lib/direct_stream.py", "run", "extra"], None),
            (["python3", f"{SOREN}/lib/direct_stream.py.bak", "run"], None),
            (["python3", f"{SOREN}/lib/../lib/direct_stream.py", "run"], None),
            ([], None),
        ):
            with self.subTest(args=args, cwd=cwd):
                self.assertFalse(ident.is_legacy_runner_args(args, SOREN, cwd=cwd))


class DocichTest(unittest.TestCase):
    def test_accepted_shapes(self):
        for args in (["python3", "-m", "docich.stream_supervisor", "run"],
                     ["/usr/bin/python3", "-m", "docich.stream_supervisor", "--root", SOREN, "run"],
                     ["python3.12", "-m", "docich.stream_supervisor", "--root", SOREN + "/", "run"]):
            with self.subTest(args=args):
                self.assertTrue(ident.is_docich_runner_args(args, SOREN))

    def test_rejected_shapes(self):
        for args in (["python3", "-m", "docich.stream_supervisor", "status"],
                     ["python3", "-m", "docich.stream_supervisor", "stop"],
                     ["python3", "-m", "docich.stream_supervisor", "record", "--output", "x"],
                     ["python3", "-m", "docich.stream_supervisor", "--root", "/other", "run"],
                     ["python3", "-m", "docich.stream_supervisor", "--root", "run"],
                     ["python3", "-m", "docich.stream_supervisor", "run", "extra"],
                     ["python3", "-m", "docich.stream_supervisor_evil", "run"],
                     ["python3", "-c", "docich.stream_supervisor", "run"],
                     ["bash", "-m", "docich.stream_supervisor", "run"],
                     ["python3", "-m", "docich.stream_supervisor"], []):
            with self.subTest(args=args):
                self.assertFalse(ident.is_docich_runner_args(args, SOREN))


class KindTest(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(ident.runner_kind(["python3", f"{SOREN}/lib/direct_stream.py", "run"], SOREN), "legacy")
        self.assertEqual(ident.runner_kind(["python3", "-m", "docich.stream_supervisor", "run"], SOREN), "docich")
        self.assertIsNone(ident.runner_kind(["ffmpeg", "-i", "x"], SOREN))

    def test_the_wrapper_produces_the_accepted_docich_shape(self):
        # bin/docich-stream-run execs: python3 -m docich.stream_supervisor --root <root> run
        text = (ROOT / "bin/docich-stream-run").read_text()
        self.assertIn('exec python3 -m docich.stream_supervisor --root "${SOREN_RUNTIME_REPO:-$RUNTIME_REPO}" "$@"', text)
        self.assertTrue(ident.is_docich_runner_args(
            ["python3", "-m", "docich.stream_supervisor", "--root", SOREN, "run"], SOREN))


if __name__ == "__main__":
    unittest.main()
