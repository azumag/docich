import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "restart_active_market_paper_workers.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "market-paper-runtime-reload.yml"


class RestartActiveMarketPaperWorkersTests(unittest.TestCase):
    def _run(
        self,
        *,
        stocks_provider=False,
        fx_provider=False,
        crypto_worker=False,
        paper_corner_enabled=True,
    ):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        tmp = temp.name
        fake_bin = pathlib.Path(tmp) / "bin"
        fake_bin.mkdir()
        log = pathlib.Path(tmp) / "calls.log"
        systemctl = fake_bin / "systemctl"
        stocks_rc = 0 if stocks_provider else 3
        fx_rc = 0 if fx_provider else 3
        systemctl.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'systemctl %s\\n' \"$*\" >> \"$CALL_LOG\"\n"
            "if [[ \"$*\" == *'is-active --quiet docich-market-worker@fx.service' ]]; then exit 0; fi\n"
            "if [[ \"$*\" == *'is-active --quiet docich-market-worker@stocks.service' ]]; then exit 3; fi\n"
            f"if [[ \"$*\" == *'is-active --quiet docich-market-data-stocks.service' ]]; then exit {stocks_rc}; fi\n"
            f"if [[ \"$*\" == *'is-active --quiet docich-market-data-fx.service' ]]; then exit {fx_rc}; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        systemctl.chmod(0o755)

        tmux = fake_bin / "tmux"
        crypto_rc = 0 if crypto_worker else 1
        tmux.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'tmux %s\\n' \"$*\" >> \"$CALL_LOG\"\n"
            f"if [[ \"$1\" == 'has-session' ]]; then exit {crypto_rc}; fi\n"
            "if [[ \"$1\" == 'list-windows' ]]; then printf 'trading\\n'; exit 0; fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        tmux.chmod(0o755)

        prod_root = pathlib.Path(tmp) / "docich"
        (prod_root / "bin").mkdir(parents=True)
        (prod_root / "config").mkdir(parents=True)
        (prod_root / "scripts/systemd").mkdir(parents=True)
        operator = prod_root / "bin" / "docich-paper-corner-operator"
        operator.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'operator %s\\n' \"$*\" >> \"$CALL_LOG\"\n",
            encoding="utf-8",
        )
        # Intentionally leave the operator non-executable. Production reload
        # must invoke reviewed shell content through bash and must not depend on
        # deployment preserving an executable mode for this helper.
        (prod_root / "config" / "docich.soren-live.toml").write_text(
            f"[paper_corner]\nenabled = {'true' if paper_corner_enabled else 'false'}\n",
            encoding="utf-8",
        )
        for name in (
            "docich-paper-corner-watchdog.service",
            "docich-paper-corner-watchdog.timer",
        ):
            source = ROOT / "scripts/systemd" / name
            (prod_root / "scripts/systemd" / name).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )

        home = pathlib.Path(tmp) / "home"
        home.mkdir()
        env = os.environ.copy()
        env.update(
            PATH=f"{fake_bin}:{env.get('PATH', '')}",
            CALL_LOG=str(log),
            DOCICH_PROD_ROOT=str(prod_root),
            HOME=str(home),
        )
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        unit_dir = home / ".config/systemd/user"
        return result, log.read_text(encoding="utf-8"), unit_dir, prod_root

    def test_only_active_worker_is_restarted_when_providers_inactive(self):
        result, calls, unit_dir, prod_root = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        for unit in (
            "docich-market-worker@stocks.service", "docich-market-worker@fx.service",
            "docich-market-data-stocks.service", "docich-market-data-fx.service",
        ):
            self.assertIn(f"--user is-active --quiet {unit}", calls)
        self.assertNotIn("--user restart docich-market-worker@stocks.service", calls)
        self.assertIn("--user restart docich-market-worker@fx.service", calls)
        self.assertNotIn("--user restart docich-market-data-stocks.service", calls)
        self.assertNotIn("--user restart docich-market-data-fx.service", calls)
        self.assertNotIn("operator ", calls)

        self.assertIn("--user daemon-reload", calls)
        self.assertIn("--user enable --now docich-paper-corner-watchdog.timer", calls)
        self.assertNotIn("--user enable --now docich-market-", calls)
        for name in (
            "docich-paper-corner-watchdog.service",
            "docich-paper-corner-watchdog.timer",
        ):
            installed = (unit_dir / name).read_text(encoding="utf-8")
            self.assertIn(str(prod_root), installed)
            self.assertNotIn("__DOCICH_ROOT__", installed)

    def test_watchdog_enablement_follows_paper_corner_opt_in(self):
        result, calls, _, _ = self._run(paper_corner_enabled=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--user disable --now docich-paper-corner-watchdog.timer", calls)
        self.assertNotIn("--user enable --now docich-paper-corner-watchdog.timer", calls)

    def test_active_crypto_tmux_worker_is_reloaded_without_starting_absent_one(self):
        inactive_result, inactive_calls, _, _ = self._run(crypto_worker=False)
        self.assertEqual(inactive_result.returncode, 0, inactive_result.stderr)
        self.assertIn("tmux has-session -t docich", inactive_calls)
        self.assertNotIn("operator ", inactive_calls)

        active_result, active_calls, _, _ = self._run(crypto_worker=True)
        self.assertEqual(active_result.returncode, 0, active_result.stderr)
        self.assertIn("tmux has-session -t docich", active_calls)
        self.assertIn("tmux list-windows -t docich -F #{window_name}", active_calls)
        self.assertIn("operator --config", active_calls)
        self.assertIn("--reload-worker", active_calls)

    def test_active_read_only_providers_are_reloaded_independently(self):
        stocks_result, stocks_calls, _, _ = self._run(stocks_provider=True)
        self.assertEqual(stocks_result.returncode, 0, stocks_result.stderr)
        self.assertIn("--user restart docich-market-data-stocks.service", stocks_calls)
        self.assertNotIn("--user restart docich-market-data-fx.service", stocks_calls)

        fx_result, fx_calls, _, _ = self._run(fx_provider=True)
        self.assertEqual(fx_result.returncode, 0, fx_result.stderr)
        self.assertIn("--user restart docich-market-data-fx.service", fx_calls)
        self.assertNotIn("--user restart docich-market-data-stocks.service", fx_calls)

    def test_post_deploy_workflow_is_push_only_and_reuses_vm_concurrency_lane(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_run.event == 'push'", text)
        self.assertIn("workflow_run.conclusion == 'success'", text)
        self.assertIn("workflow_run.head_branch == 'main'", text)
        self.assertIn("group: vm-operations-${{ github.repository }}", text)
        self.assertIn("restart_active_market_paper_workers.sh", text)
        self.assertNotIn("systemctl --user enable", text)


if __name__ == "__main__":
    unittest.main()
