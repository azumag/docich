from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import cli, config


class TestTradingWorkerLifecycle(unittest.TestCase):
    def test_run_parser_accepts_trading_component(self):
        args = cli.build_parser().parse_args(["run", "trading"])
        self.assertEqual(args.component, "trading")

    def test_cmd_run_dispatches_trading_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = config.load_global(Path(tmp))
            args = mock.Mock(component="trading", name=None, runtime_id=None, generation=None, lease_id=None)
            with mock.patch("docich.cli._run_trading", return_value=0) as run_trading:
                self.assertEqual(cli.cmd_run(g, args), 0)
            run_trading.assert_called_once_with(g)

    def test_up_starts_trading_window_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "docich.toml"
            cfg.write_text(
                "[display]\nnumber=99\nmanaged=false\n"
                "[audio]\nenabled=false\n"
                "[stream]\nmode=\"null\"\n"
                "[trading]\npaper_worker_enabled=true\n",
                encoding="utf-8",
            )
            g = config.load_global(root, config_path=cfg)
            with mock.patch("docich.cli.Tmux") as tmux_cls, mock.patch("docich.cli.XKit") as xkit_cls:
                tmux = tmux_cls.return_value
                tmux.has_window.return_value = False
                xkit_cls.return_value.display_ready.return_value = True
                xkit_cls.return_value.wait_display.return_value = True
                self.assertEqual(cli.cmd_up(g), 0)
            trading_calls = [call for call in tmux.new_window.call_args_list if call.args[0] == "trading"]
            self.assertEqual(len(trading_calls), 1)
            self.assertEqual(trading_calls[0].args[1][-2:], ["run", "trading"])


if __name__ == "__main__":
    unittest.main()
