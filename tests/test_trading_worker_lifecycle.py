from __future__ import annotations

import os
import subprocess
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

    def test_run_trading_is_noop_when_worker_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            g = config.load_global(Path(tmp))
            with mock.patch("docich.cli.run_callable_loop") as loop:
                self.assertEqual(cli._run_trading(g), 0)
            loop.assert_not_called()

    def test_run_trading_uses_callable_supervisor_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "docich.toml"
            cfg.write_text("[trading]\npaper_worker_enabled=true\n", encoding="utf-8")
            g = config.load_global(root, config_path=cfg)
            with mock.patch("docich.cli.run_callable_loop") as loop:
                self.assertEqual(cli._run_trading(g), 0)
            self.assertEqual(loop.call_args.args[0], "trading")
            self.assertIs(loop.call_args.args[1], g)
            self.assertTrue(callable(loop.call_args.args[2]))

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

    def test_launcher_pins_trading_virtualenv_for_paper_runtime_paths(self):
        source_launcher = Path(__file__).resolve().parents[1] / "bin" / "docich"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "bin" / "docich"
            launcher.parent.mkdir(parents=True)
            launcher.write_text(source_launcher.read_text(encoding="utf-8"), encoding="utf-8")
            launcher.chmod(0o755)

            trading_python = root / ".venv-trading" / "bin" / "python3"
            trading_python.parent.mkdir(parents=True)
            trading_python.write_text("#!/bin/sh\nprintf 'TRADING\\n'\n", encoding="utf-8")
            trading_python.chmod(0o755)

            system_bin = root / "system-bin"
            system_bin.mkdir()
            system_python = system_bin / "python3"
            system_python.write_text("#!/bin/sh\nprintf 'SYSTEM\\n'\n", encoding="utf-8")
            system_python.chmod(0o755)
            env = dict(os.environ, PATH=f"{system_bin}:/usr/bin:/bin")

            commands = (
                [str(launcher), "--config", "live.toml", "run", "trading"],
                [str(launcher), "--config=live.toml", "run", "trading"],
                [str(launcher), "--config", "live.toml", "trading", "paper-improve", "--dry-run"],
                [str(launcher), "--config", "live.toml", "paper-corner", "status"],
            )
            for command in commands:
                result = subprocess.run(
                    command,
                    check=True,
                    text=True,
                    capture_output=True,
                    env=env,
                )
                self.assertEqual(result.stdout, "TRADING\n", command)

            status = subprocess.run(
                [str(launcher), "status", "--json"],
                check=True,
                text=True,
                capture_output=True,
                env=env,
            )
            self.assertEqual(status.stdout, "SYSTEM\n")

            unrelated_payload = subprocess.run(
                [str(launcher), "say", "sorengame", "run", "trading"],
                check=True,
                text=True,
                capture_output=True,
                env=env,
            )
            self.assertEqual(unrelated_payload.stdout, "SYSTEM\n")

            trading_python.unlink()
            fallback = subprocess.run(
                [str(launcher), "--config", "live.toml", "paper-corner", "status"],
                check=True,
                text=True,
                capture_output=True,
                env=env,
            )
            self.assertEqual(fallback.stdout, "SYSTEM\n")

    def test_manual_paper_operator_prefers_trading_virtualenv(self):
        source_launcher = Path(__file__).resolve().parents[1] / "bin" / "docich-paper-corner-operator"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "bin" / "docich-paper-corner-operator"
            launcher.parent.mkdir(parents=True)
            launcher.write_text(source_launcher.read_text(encoding="utf-8"), encoding="utf-8")
            launcher.chmod(0o755)

            trading_python = root / ".venv-trading" / "bin" / "python3"
            trading_python.parent.mkdir(parents=True)
            trading_python.write_text("#!/bin/sh\nprintf 'TRADING\\n'\n", encoding="utf-8")
            trading_python.chmod(0o755)

            system_bin = root / "system-bin"
            system_bin.mkdir()
            system_python = system_bin / "python3"
            system_python.write_text("#!/bin/sh\nprintf 'SYSTEM\\n'\n", encoding="utf-8")
            system_python.chmod(0o755)
            env = dict(os.environ, PATH=f"{system_bin}:/usr/bin:/bin")

            preferred = subprocess.run(
                [str(launcher), "--config", "live.toml", "--duration-minutes", "1"],
                check=True,
                text=True,
                capture_output=True,
                env=env,
            )
            self.assertEqual(preferred.stdout, "TRADING\n")

            trading_python.unlink()
            fallback = subprocess.run(
                [str(launcher), "--config", "live.toml", "--duration-minutes", "1"],
                check=True,
                text=True,
                capture_output=True,
                env=env,
            )
            self.assertEqual(fallback.stdout, "SYSTEM\n")


if __name__ == "__main__":
    unittest.main()
