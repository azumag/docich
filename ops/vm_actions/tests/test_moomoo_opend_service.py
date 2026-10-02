import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
OPEND_UNIT = ROOT / "scripts" / "systemd" / "docich-moomoo-opend.service"
COLLECTOR_UNIT = ROOT / "scripts" / "systemd" / "docich-market-data-stocks.service"
CHECK = ROOT / "ops" / "vm_actions" / "check_moomoo_opend_runtime.sh"
WAIT = ROOT / "ops" / "vm_actions" / "wait_moomoo_opend_ready.py"
MANAGE = ROOT / "ops" / "vm_actions" / "manage_market_paper_units.sh"


class MoomooOpenDServiceTests(unittest.TestCase):
    def test_opend_unit_is_loopback_remembered_login_only(self):
        text = OPEND_UNIT.read_text(encoding="utf-8")
        self.assertIn("-login_by_remember=1", text)
        self.assertIn("-api_ip=127.0.0.1", text)
        self.assertIn("-api_port=11111", text)
        self.assertIn("-console=0", text)
        self.assertIn("check_moomoo_opend_runtime.sh", text)
        self.assertIn("wait_moomoo_opend_ready.py --timeout 30", text)
        self.assertIn("NoNewPrivileges=true", text)
        self.assertIn("PrivateTmp=true", text)
        for forbidden in (
            "login_pwd=", "password=", "DOCICH_OANDA_TOKEN", "unlock_trade",
            "place_order", "OpenSecTradeContext", "api_ip=0.0.0.0",
        ):
            self.assertNotIn(forbidden, text)

    def test_collector_requires_managed_opend(self):
        text = COLLECTOR_UNIT.read_text(encoding="utf-8")
        self.assertIn("Requires=docich-moomoo-opend.service", text)
        self.assertIn("After=network-online.target docich-moomoo-opend.service", text)
        self.assertIn("--host 127.0.0.1", text)
        self.assertIn("--port 11111", text)

    def _runtime_fixture(self, base: pathlib.Path, config_mode: int = 0o600):
        root = base / "docich"
        python = root / ".venv-trading" / "bin" / "python3"
        python.parent.mkdir(parents=True)
        python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        python.chmod(0o755)

        home = base / "home"
        runtime = home / ".local" / "share" / "docich" / "moomoo-opend"
        runtime.mkdir(parents=True)
        binary = runtime / "OpenD"
        binary.write_text("stub", encoding="utf-8")
        binary.chmod(0o755)
        (runtime / "Appdata.dat").write_text("stub", encoding="utf-8")

        config = home / ".config" / "docich" / "moomoo-opend" / "OpenD.xml"
        config.parent.mkdir(parents=True)
        config.write_text("<xml/>", encoding="utf-8")
        config.chmod(config_mode)
        return root, home, config

    def _run_preflight(self, root: pathlib.Path, home: pathlib.Path):
        env = os.environ.copy()
        env["HOME"] = str(home)
        return subprocess.run(
            ["bash", str(CHECK), str(root)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "production preflight requires Linux and GNU stat",
    )
    def test_preflight_accepts_owned_private_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, home, _ = self._runtime_fixture(pathlib.Path(tmp))
            result = self._run_preflight(root, home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "production preflight requires Linux and GNU stat",
    )
    def test_preflight_rejects_group_or_world_readable_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, home, _ = self._runtime_fixture(pathlib.Path(tmp), config_mode=0o644)
            result = self._run_preflight(root, home)
            self.assertEqual(result.returncode, 35, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_preflight_never_reads_or_prints_config(self):
        text = CHECK.read_text(encoding="utf-8")
        self.assertIn(".local/share/docich/moomoo-opend", text)
        self.assertIn(".config/docich/moomoo-opend/OpenD.xml", text)
        self.assertIn("400|600", text)
        self.assertIn("-c 'import moomoo'", text)
        self.assertNotIn("cat \"$config\"", text)
        self.assertNotIn("echo \"$config\"", text)

    def test_readiness_handshake_is_fixed_loopback_probe(self):
        text = WAIT.read_text(encoding="utf-8")
        self.assertIn('host="127.0.0.1", port=11111', text)
        self.assertIn('result.get("opend_reachable") is True', text)
        self.assertNotIn("print(", text)

    def test_provider_first_use_installs_opend_unit_and_sdk(self):
        text = MANAGE.read_text(encoding="utf-8")
        self.assertIn("docich-moomoo-opend.service", text)
        self.assertIn("provider-enable) install_units; provision_stock_quote_sdk; enable_provider", text)
        self.assertIn("provider-restart) install_units; provision_stock_quote_sdk; restart_provider", text)
        self.assertIn('systemctl --user stop "$opend_unit"', text)
        self.assertIn('systemctl --user restart "$opend_unit"', text)
        self.assertNotIn("systemctl --user enable --now docich-market-worker@stocks.service", text)


if __name__ == "__main__":
    unittest.main()
