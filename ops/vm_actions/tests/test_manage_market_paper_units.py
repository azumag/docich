import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "manage_market_paper_units.sh"

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$SYSTEMCTL_CALLS_LOG"
exit "${FAKE_SYSTEMCTL_EXIT:-0}"
"""

TEMPLATES = {
    "docich-market-worker@.service": "[Service]\nExecStart=__DOCICH_ROOT__/bin/docich-market-paper --market %i worker\n",
    "docich-market-corner@.service": "[Service]\nExecStart=__DOCICH_ROOT__/bin/docich-market-paper --market %i corner\n",
    "docich-market-corner@.timer": "[Timer]\nUnit=docich-market-corner@%i.service\n",
    "docich-market-improve@.service": "[Service]\nExecStart=__DOCICH_ROOT__/bin/docich-market-paper --market %i improve\n",
    "docich-market-improve@.timer": "[Timer]\nUnit=docich-market-improve@%i.service\n",
}


class ManageMarketPaperUnitsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="market-paper-units-")
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)
        self.docroot = self.base / "docich"
        (self.docroot / "scripts" / "systemd").mkdir(parents=True)
        for name, body in TEMPLATES.items():
            (self.docroot / "scripts" / "systemd" / name).write_text(body, encoding="utf-8")

        self.home = self.base / "home"
        self.home.mkdir()
        self.unit_dir = self.home / ".config" / "systemd" / "user"

        self.fake_bin = self.base / "bin"
        self.fake_bin.mkdir()
        fake = self.fake_bin / "systemctl"
        fake.write_text(FAKE_SYSTEMCTL, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

        self.calls_log = self.base / "calls.log"
        self.calls_log.write_text("", encoding="utf-8")

    def run_helper(self, action, market, exit_code="0"):
        env = dict(os.environ)
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env.pop("XDG_CONFIG_HOME", None)
        env["HOME"] = str(self.home)
        env["MARKET_PAPER_ACTION"] = action
        env["MARKET_PAPER_MARKET"] = market
        env["SYSTEMCTL_CALLS_LOG"] = str(self.calls_log)
        env["FAKE_SYSTEMCTL_EXIT"] = exit_code
        return subprocess.run(
            ["bash", str(HELPER), "--root", str(self.docroot)],
            env=env, capture_output=True, text=True, timeout=30, check=False,
        )

    def calls(self):
        return [line for line in self.calls_log.read_text(encoding="utf-8").splitlines() if line]

    def test_invalid_action_is_rejected(self):
        result = self.run_helper("wipe", "fx")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("invalid action", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_invalid_market_is_rejected(self):
        result = self.run_helper("install", "crypto")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("invalid market", result.stderr)

    def test_missing_root_is_rejected(self):
        env = dict(os.environ)
        env.pop("XDG_CONFIG_HOME", None)
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env["HOME"] = str(self.home)
        env["MARKET_PAPER_ACTION"] = "install"
        env["MARKET_PAPER_MARKET"] = "fx"
        env["SYSTEMCTL_CALLS_LOG"] = str(self.calls_log)
        result = subprocess.run(
            ["bash", str(HELPER), "--root", str(self.base / "does-not-exist")],
            env=env, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("root not found", result.stderr)

    def test_install_writes_all_templates_with_root_substituted(self):
        result = self.run_helper("install", "fx")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in TEMPLATES:
            written = self.unit_dir / name
            self.assertTrue(written.is_file(), name)
            text = written.read_text(encoding="utf-8")
            self.assertNotIn("__DOCICH_ROOT__", text)
            if "__DOCICH_ROOT__" in TEMPLATES[name]:
                self.assertIn(str(self.docroot), text)
        self.assertIn("--user daemon-reload", self.calls())

    def test_install_ignores_market_and_installs_shared_templates_once(self):
        self.run_helper("install", "stocks")
        stocks_files = {p.name for p in self.unit_dir.iterdir()}
        self.run_helper("install", "fx")
        fx_files = {p.name for p in self.unit_dir.iterdir()}
        self.assertEqual(stocks_files, fx_files)
        self.assertEqual(stocks_files, set(TEMPLATES))

    def test_install_fails_closed_on_missing_template(self):
        (self.docroot / "scripts" / "systemd" / "docich-market-worker@.service").unlink()
        result = self.run_helper("install", "fx")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing template", result.stderr)
        self.assertFalse(self.unit_dir.exists() and any(self.unit_dir.iterdir()))

    def test_enable_fx_only_touches_fx_units(self):
        result = self.run_helper("enable", "fx")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("--user enable --now docich-market-worker@fx.service", calls)
        self.assertIn("--user enable --now docich-market-corner@fx.timer", calls)
        self.assertIn("--user enable --now docich-market-improve@fx.timer", calls)
        self.assertFalse(any("stocks" in c for c in calls))
        self.assertEqual(len(calls), 3)

    def test_enable_stocks_only_touches_stocks_units(self):
        result = self.run_helper("enable", "stocks")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("--user enable --now docich-market-worker@stocks.service", calls)
        self.assertFalse(any("fx" in c for c in calls))

    def test_disable_touches_the_same_fixed_unit_set(self):
        result = self.run_helper("disable", "fx")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("--user disable --now docich-market-worker@fx.service", calls)
        self.assertIn("--user disable --now docich-market-corner@fx.timer", calls)
        self.assertIn("--user disable --now docich-market-improve@fx.timer", calls)

    def test_restart_only_touches_the_worker_service(self):
        result = self.run_helper("restart", "fx")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["--user restart docich-market-worker@fx.service"])

    def test_enable_failure_propagates_nonzero_exit(self):
        result = self.run_helper("enable", "fx", exit_code="1")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
