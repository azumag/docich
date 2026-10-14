import json
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
if [[ "${RECORD_XDG_RUNTIME_DIR:-0}" == "1" ]]; then
  printf '%s\\n' "${XDG_RUNTIME_DIR:-}" > "$(dirname "$SYSTEMCTL_CALLS_LOG")/xdg_runtime_dir.txt"
fi
exit "${FAKE_SYSTEMCTL_EXIT:-0}"
"""

TEMPLATES = {
    "docich-market-worker@.service": "[Service]\nExecStart=__DOCICH_ROOT__/bin/docich-market-paper --market %i worker\n",
    "docich-market-corner@.service": "[Service]\nExecStart=__DOCICH_ROOT__/bin/docich-market-paper --market %i corner\n",
    "docich-market-corner@.timer": "[Timer]\nUnit=docich-market-corner@%i.service\n",
    "docich-market-improve@.service": "[Service]\nExecStart=__DOCICH_ROOT__/bin/docich-market-paper --market %i improve\n",
    "docich-market-improve@.timer": "[Timer]\nUnit=docich-market-improve@%i.service\n",
    "docich-moomoo-opend.service": "[Service]\nEnvironment=PYTHONPATH=__DOCICH_ROOT__/src\nExecStartPre=/usr/bin/bash __DOCICH_ROOT__/ops/vm_actions/check_moomoo_opend_runtime.sh __DOCICH_ROOT__\n",
    "docich-market-data-stocks.service": "[Service]\nExecStart=__DOCICH_ROOT__/.venv-trading/bin/python3 -m docich.trading.markets.moomoo_market_data worker\n",
    "docich-market-data-fx.service": "[Service]\nExecStart=__DOCICH_ROOT__/.venv-trading/bin/python3 -m docich.trading.markets.oanda_market_data worker\n",
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

        self.state_dir = self.base / "state"
        stub_pkg = self.docroot / "src" / "docich"
        stub_pkg.mkdir(parents=True)
        (self.docroot / "src" / "docich" / "__init__.py").write_text("", encoding="utf-8")
        (stub_pkg / "config.py").write_text(
            "from pathlib import Path\n"
            "from types import SimpleNamespace\n"
            f"STATE_DIR = Path({str(self.state_dir)!r})\n"
            "def load_global(repo_root, config_path):\n"
            "    return SimpleNamespace(state_dir=STATE_DIR)\n",
            encoding="utf-8",
        )

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

    def test_install_does_not_start_read_only_providers(self):
        result = self.run_helper("install", "stocks")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        for unit in (
            "docich-moomoo-opend.service",
            "docich-market-data-stocks.service",
            "docich-market-data-fx.service",
        ):
            self.assertFalse(any(unit in call and "enable" in call for call in calls))
            self.assertFalse(any(unit in call and "start" in call for call in calls))

    def test_install_fails_closed_on_missing_template(self):
        (self.docroot / "scripts" / "systemd" / "docich-market-worker@.service").unlink()
        result = self.run_helper("install", "fx")
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn("missing template", result.stderr)
        self.assertFalse(self.unit_dir.exists() and any(self.unit_dir.iterdir()))

    def test_install_fails_closed_when_provider_template_missing(self):
        (self.docroot / "scripts" / "systemd" / "docich-market-data-fx.service").unlink()
        result = self.run_helper("install", "fx")
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn("missing template", result.stderr)

    def test_install_fails_closed_when_unit_dir_cannot_be_created(self):
        (self.home / ".config").mkdir()
        (self.home / ".config" / "systemd").write_text("not a directory", encoding="utf-8")
        result = self.run_helper("install", "fx")
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIn("mkdir failed", result.stderr)

    def test_install_surfaces_daemon_reload_failure_distinctly(self):
        result = self.run_helper("install", "fx", exit_code="1")
        self.assertEqual(result.returncode, 13, result.stderr)
        self.assertIn("daemon-reload failed", result.stderr)
        for name in TEMPLATES:
            self.assertTrue((self.unit_dir / name).is_file(), name)

    def test_install_sets_xdg_runtime_dir_when_absent(self):
        env = dict(os.environ)
        env.pop("XDG_CONFIG_HOME", None)
        env.pop("XDG_RUNTIME_DIR", None)
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env["HOME"] = str(self.home)
        env["MARKET_PAPER_ACTION"] = "install"
        env["MARKET_PAPER_MARKET"] = "fx"
        env["SYSTEMCTL_CALLS_LOG"] = str(self.calls_log)
        env["RECORD_XDG_RUNTIME_DIR"] = "1"
        result = subprocess.run(
            ["bash", str(HELPER), "--root", str(self.docroot)],
            env=env, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = (self.base / "xdg_runtime_dir.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(recorded, f"/run/user/{os.getuid()}")

    def test_enable_fx_only_touches_fx_paper_units_not_provider(self):
        result = self.run_helper("enable", "fx")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("--user enable --now docich-market-worker@fx.service", calls)
        self.assertIn("--user enable --now docich-market-corner@fx.timer", calls)
        self.assertIn("--user enable --now docich-market-improve@fx.timer", calls)
        self.assertFalse(any("stocks" in c for c in calls))
        self.assertFalse(any("docich-market-data-fx.service" in c for c in calls))
        self.assertEqual(len(calls), 3)

    def test_enable_stocks_only_touches_stocks_paper_units_not_provider(self):
        result = self.run_helper("enable", "stocks")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("--user enable --now docich-market-worker@stocks.service", calls)
        self.assertFalse(any("fx" in c for c in calls))
        self.assertFalse(any("docich-market-data-stocks.service" in c for c in calls))

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

    def test_provider_enable_installs_units_then_touches_only_selected_provider(self):
        stocks = self.run_helper("provider-enable", "stocks")
        self.assertEqual(stocks.returncode, 0, stocks.stderr)
        self.assertEqual(self.calls(), [
            "--user daemon-reload",
            "--user enable --now docich-market-data-stocks.service",
        ])

        self.calls_log.write_text("", encoding="utf-8")
        fx = self.run_helper("provider-enable", "fx")
        self.assertEqual(fx.returncode, 0, fx.stderr)
        self.assertEqual(self.calls(), [
            "--user daemon-reload",
            "--user enable --now docich-market-data-fx.service",
        ])

    def test_provider_disable_and_restart_manage_stock_opend_dependency(self):
        disabled = self.run_helper("provider-disable", "fx")
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertEqual(self.calls(), ["--user disable --now docich-market-data-fx.service"])

        self.calls_log.write_text("", encoding="utf-8")
        stock_disabled = self.run_helper("provider-disable", "stocks")
        self.assertEqual(stock_disabled.returncode, 0, stock_disabled.stderr)
        self.assertEqual(self.calls(), [
            "--user disable --now docich-market-data-stocks.service",
            "--user stop docich-moomoo-opend.service",
        ])

        self.calls_log.write_text("", encoding="utf-8")
        restarted = self.run_helper("provider-restart", "stocks")
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.assertEqual(self.calls(), [
            "--user daemon-reload",
            "--user restart docich-moomoo-opend.service",
            "--user restart docich-market-data-stocks.service",
        ])

    def test_enable_failure_propagates_nonzero_exit(self):
        result = self.run_helper("enable", "fx", exit_code="1")
        self.assertNotEqual(result.returncode, 0)

    def test_seed_test_quote_is_fx_only(self):
        result = self.run_helper("seed-test-quote", "stocks")
        self.assertEqual(result.returncode, 21, result.stderr)
        self.assertIn("fx-only", result.stderr)

    def test_seed_test_quote_writes_valid_fixed_shape_quote(self):
        result = self.run_helper("seed-test-quote", "fx")
        self.assertEqual(result.returncode, 0, result.stderr)
        quote_path = self.state_dir / "market-data" / "market-fx-quotes.json"
        payload = json.loads(quote_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["market"], "fx")
        self.assertIs(payload["realtime"], True)
        self.assertEqual(len(payload["quotes"]), 1)
        quote = payload["quotes"][0]
        self.assertEqual(quote["symbol"], "USD_JPY")
        self.assertEqual(quote["currency"], "JPY")
        self.assertIs(quote["tradeable"], True)
        self.assertGreater(float(quote["bid_size"]), 0)
        self.assertGreater(float(quote["ask_size"]), 0)
        self.assertGreater(float(quote["ask"]), float(quote["bid"]))
        self.assertLessEqual(abs(quote["ts"] - int(__import__("time").time())), 5)

    def test_seed_test_quote_drifts_deterministically_across_calls(self):
        first = self.run_helper("seed-test-quote", "fx")
        self.assertEqual(first.returncode, 0, first.stderr)
        quote_path = self.state_dir / "market-data" / "market-fx-quotes.json"
        bid1 = float(json.loads(quote_path.read_text(encoding="utf-8"))["quotes"][0]["bid"])

        second = self.run_helper("seed-test-quote", "fx")
        self.assertEqual(second.returncode, 0, second.stderr)
        bid2 = float(json.loads(quote_path.read_text(encoding="utf-8"))["quotes"][0]["bid"])

        self.assertGreater(bid2, bid1)
        counter = (self.state_dir / "market-data" / ".test-feed-seed-counter").read_text(encoding="utf-8").strip()
        self.assertEqual(counter, "2")


if __name__ == "__main__":
    unittest.main()
