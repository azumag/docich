import io
import os
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from docich import cli, overlay  # noqa: E402
from test_tts import TtsTestBase  # noqa: E402


class OverlayTestBase(TtsTestBase):
    def _write_overlay(self):
        self._write_game("sorengame")
        (self.submodule / "eloop_lib.sh").write_text(
            "#!/usr/bin/env bash\n"
            'echo "eloop loaded" >/dev/null\n',
            encoding="utf-8",
        )
        for name in overlay.ALLOWED_OVERLAY_KINDS.values():
            (self.submodule / name).write_text(
                "#!/usr/bin/env bash\n"
                'echo "overlay: $@"\n',
                encoding="utf-8",
            )


class TestBuildOverlay(OverlayTestBase):
    def test_status_invocation(self):
        self._write_overlay()
        inv = overlay.build_overlay_invocation(self.g, game_name="sorengame", kind="status")
        self.assertEqual(inv.cwd, self.submodule.resolve())
        self.assertIn("generate_status_overlay.sh", inv.argv)
        self.assertIn("once", inv.argv)
        self.assertEqual(inv.env["STATUS_OVERLAY_HTML_FILE"].endswith("status_overlay.html"), True)
        self.assertEqual(inv.env["DOCICH_CC_ENABLED"], "0")

    def test_kind_allowlist(self):
        self._write_overlay()
        with self.assertRaises(overlay.OverlayError):
            overlay.build_overlay_invocation(self.g, game_name="sorengame", kind="nope")

    def test_output_override(self):
        self._write_overlay()
        out = self.repo_root / "custom-out"
        inv = overlay.build_overlay_invocation(
            self.g, game_name="sorengame", kind="status", output=out
        )
        self.assertEqual(Path(inv.env["STATUS_OVERLAY_HTML_FILE"]).parent, out.resolve())

    def test_dry_run_repro(self):
        self._write_overlay()
        rc, detail = overlay.run_overlay(
            self.g, game_name="sorengame", kind="event", dry_run=True
        )
        self.assertEqual(rc, 0)
        self.assertIn("kind=event", detail)
        self.assertIn("generate_event_overlay.py", detail)

    @mock.patch("docich.overlay.run")
    def test_real_run_requires_env(self, fake_run):
        self._write_overlay()
        fake_run.return_value = mock.Mock(returncode=0, stderr="")
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(overlay.OverlayError) as cm:
                overlay.run_overlay(self.g, game_name="sorengame", kind="status")
        self.assertIn("DOCICH_ALLOW_REAL_OVERLAY", str(cm.exception))
        fake_run.assert_not_called()

    @mock.patch("docich.overlay.run")
    def test_real_run_with_env(self, fake_run):
        self._write_overlay()
        fake_run.return_value = mock.Mock(returncode=0, stderr="")
        with mock.patch.dict(os.environ, {"DOCICH_ALLOW_REAL_OVERLAY": "1"}):
            rc, _detail = overlay.run_overlay(
                self.g, game_name="sorengame", kind="status"
            )
        self.assertEqual(rc, 0)
        fake_run.assert_called_once()


class TestCliOverlay(OverlayTestBase):
    def test_parse(self):
        args = cli.build_parser().parse_args(
            ["overlay", "sorengame", "show_status", "--dry-run"]
        )
        self.assertEqual(args.kind, "show_status")
        self.assertTrue(args.dry_run)

    def test_dry_run_prints_preview(self):
        self._write_overlay()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(
                ["--config", str(self.toml), "overlay", "sorengame", "status", "--dry-run"]
            )
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("docich: overlay dry-run:", out.getvalue())
        self.assertIn("generate_status_overlay.sh", out.getvalue())


if __name__ == "__main__":
    unittest.main()
