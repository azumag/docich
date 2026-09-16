import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
UNIT = ROOT / "scripts" / "systemd" / "docich-market-data-stocks.service"
WORKFLOW = ROOT / ".github" / "workflows" / "market-data-stocks.yml"


class StockMarketDataProviderTests(unittest.TestCase):
    def test_unit_runs_only_read_only_collector_on_loopback(self):
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn(".venv-trading/bin/python3 -m docich.trading.markets.moomoo_market_data worker", text)
        self.assertIn("--host 127.0.0.1", text)
        self.assertIn("--port 11111", text)
        self.assertIn("--interval 10", text)
        self.assertIn("Environment=PYTHONPATH=__DOCICH_ROOT__/src", text)
        self.assertIn("NoNewPrivileges=true", text)
        for forbidden in (
            "OpenSecTradeContext", "unlock_trade", "place_order", "modify_order",
            "cancel_order", "DOCICH_OANDA_TOKEN", "DOCICH_KABU_TOKEN",
        ):
            self.assertNotIn(forbidden, text)

    def test_owner_workflow_controls_provider_not_stock_paper(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.actor_id == 9018513", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("github.ref_protected == true", text)
        self.assertIn("group: vm-operations-${{ github.repository }}", text)
        self.assertIn("options: [enable, disable, restart]", text)
        self.assertIn("MARKET_PAPER_ACTION=provider-%s", text)
        self.assertIn("MARKET_PAPER_MARKET=stocks", text)
        self.assertNotIn("MARKET_PAPER_ACTION=enable", text)
        self.assertNotIn("docich-market-worker@stocks.service", text)


if __name__ == "__main__":
    unittest.main()
