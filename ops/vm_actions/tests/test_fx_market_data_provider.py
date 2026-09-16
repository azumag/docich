import pathlib
import tomllib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
UNIT = ROOT / "scripts" / "systemd" / "docich-market-data-fx.service"
PREFLIGHT = ROOT / "ops" / "vm_actions" / "check_oanda_practice_env.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "market-data-fx.yml"
CONFIG = ROOT / "config" / "market-paper.toml"


class FxMarketDataProviderTests(unittest.TestCase):
    def test_unit_uses_mandatory_external_credentials_and_read_only_collector(self):
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn(".venv-trading/bin/python3 -m docich.trading.markets.oanda_market_data worker", text)
        self.assertIn("EnvironmentFile=%h/.config/docich/oanda-practice.env", text)
        self.assertNotIn("EnvironmentFile=-", text)
        self.assertIn("ExecStartPre=/usr/bin/bash __DOCICH_ROOT__/ops/vm_actions/check_oanda_practice_env.sh", text)
        self.assertIn("--symbols USD_JPY,EUR_JPY", text)
        self.assertIn("--interval 5", text)
        self.assertIn("NoNewPrivileges=true", text)
        self.assertIn("PrivateTmp=true", text)
        self.assertIn("UMask=0077", text)
        for forbidden in (
            "api-fxtrade.oanda.com", "/orders", "place_order", "create_order",
            "DOCICH_OANDA_TOKEN=fixture", "DOCICH_OANDA_ACCOUNT_ID=101-",
        ):
            self.assertNotIn(forbidden, text)

    def test_credential_preflight_requires_owner_mode_600_and_exact_keys(self):
        text = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn("! -L", text)
        self.assertIn("stat -c '%u'", text)
        self.assertIn("$(id -u)", text)
        self.assertIn("stat -c '%a'", text)
        self.assertIn('== "600"', text)
        self.assertIn("DOCICH_OANDA_ACCOUNT_ID", text)
        self.assertIn("DOCICH_OANDA_TOKEN", text)
        self.assertIn("account_count", text)
        self.assertIn("token_count", text)
        # The failure path must remain generic: never echo $line/$value.
        self.assertNotIn('echo "$line"', text)
        self.assertNotIn('echo "$value"', text)

    def test_owner_workflow_controls_fx_provider_not_fx_paper(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.actor_id == 9018513", text)
        self.assertIn("github.triggering_actor == 'azumag'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("github.ref_protected == true", text)
        self.assertIn("group: vm-operations-${{ github.repository }}", text)
        self.assertIn("options: [enable, disable, restart]", text)
        self.assertIn("MARKET_PAPER_ACTION=provider-%s", text)
        self.assertIn("MARKET_PAPER_MARKET=fx", text)
        self.assertNotIn("MARKET_PAPER_ACTION=enable", text)
        self.assertNotIn("docich-market-worker@fx.service", text)
        self.assertNotIn("DOCICH_OANDA_TOKEN", text)
        self.assertNotIn("DOCICH_OANDA_ACCOUNT_ID", text)

    def test_fx_returns_to_fail_closed_until_provider_is_verified(self):
        config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
        self.assertIs(config["fx"]["enabled"], False)
        self.assertEqual(config["fx"]["mode"], "paper")
        self.assertEqual(config["fx"]["feed"], "file")
        self.assertEqual(config["fx"]["symbols"], ["USD_JPY", "EUR_JPY"])


if __name__ == "__main__":
    unittest.main()
