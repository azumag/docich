import pathlib
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
        self.assertNotIn("systemctl --user enable --now docich-market-worker@stocks.service", text)


if __name__ == "__main__":
    unittest.main()
