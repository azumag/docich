import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "manage_market_paper_units.sh"


class MarketPaperStockSdkTests(unittest.TestCase):
    def test_stock_install_provisions_pinned_market_data_sdk_when_trading_venv_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "docich"
            templates = root / "scripts" / "systemd"
            templates.mkdir(parents=True)
            for name in (
                "docich-market-worker@.service",
                "docich-market-corner@.service",
                "docich-market-corner@.timer",
                "docich-market-improve@.service",
                "docich-market-improve@.timer",
                "docich-market-data-stocks.service",
            ):
                (templates / name).write_text("WorkingDirectory=__DOCICH_ROOT__\n", encoding="utf-8")
            (root / "requirements-market-data.txt").write_text(
                "moomoo-api==10.10.7008\n", encoding="utf-8"
            )

            fake_bin = pathlib.Path(tmp) / "bin"
            fake_bin.mkdir()
            log = pathlib.Path(tmp) / "calls.log"
            systemctl = fake_bin / "systemctl"
            systemctl.write_text(
                "#!/usr/bin/env bash\nprintf 'systemctl %s\\n' \"$*\" >> \"$CALL_LOG\"\n",
                encoding="utf-8",
            )
            systemctl.chmod(0o755)

            python = root / ".venv-trading" / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.write_text(
                "#!/usr/bin/env bash\nprintf 'python %s\\n' \"$*\" >> \"$CALL_LOG\"\nexit 0\n",
                encoding="utf-8",
            )
            python.chmod(0o755)

            env = os.environ.copy()
            env.pop("XDG_CONFIG_HOME", None)
            env.update(
                PATH=f"{fake_bin}:{env.get('PATH', '')}",
                HOME=str(pathlib.Path(tmp) / "home"),
                CALL_LOG=str(log),
                MARKET_PAPER_ACTION="install",
                MARKET_PAPER_MARKET="stocks",
            )
            result = subprocess.run(
                ["bash", str(SCRIPT), "--root", str(root)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text(encoding="utf-8")
            self.assertIn("-m pip install --disable-pip-version-check --no-input -r", calls)
            self.assertIn("requirements-market-data.txt", calls)
            self.assertIn("-c import moomoo", calls)
            provider = pathlib.Path(tmp) / "home" / ".config" / "systemd" / "user" / "docich-market-data-stocks.service"
            self.assertTrue(provider.is_file())
            self.assertNotIn("enable --now docich-market-data-stocks.service", calls)

    def test_fx_install_does_not_provision_moomoo(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('[[ "$market" == "stocks" ]] || return 0', text)


if __name__ == "__main__":
    unittest.main()
