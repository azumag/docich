import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "restart_active_market_paper_workers.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "market-paper-runtime-reload.yml"


class RestartActiveMarketPaperWorkersTests(unittest.TestCase):
    def _run(self, *, stocks_provider=False, fx_provider=False):
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
            "printf '%s\\n' \"$*\" >> \"$CALL_LOG\"\n"
            "if [[ \"$*\" == *'is-active --quiet docich-market-worker@fx.service' ]]; then exit 0; fi\n"
            "if [[ \"$*\" == *'is-active --quiet docich-market-worker@stocks.service' ]]; then exit 3; fi\n"
            f"if [[ \"$*\" == *'is-active --quiet docich-market-data-stocks.service' ]]; then exit {stocks_rc}; fi\n"
            f"if [[ \"$*\" == *'is-active --quiet docich-market-data-fx.service' ]]; then exit {fx_rc}; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        systemctl.chmod(0o755)

        env = os.environ.copy()
        env.update(PATH=f"{fake_bin}:{env.get('PATH', '')}", CALL_LOG=str(log))
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        return result, log.read_text(encoding="utf-8")

    def test_only_active_worker_is_restarted_when_providers_inactive(self):
        result, calls = self._run()
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
        self.assertNotIn(" enable ", calls)
        self.assertNotIn(" start ", calls)

    def test_active_read_only_providers_are_reloaded_independently(self):
        stocks_result, stocks_calls = self._run(stocks_provider=True)
        self.assertEqual(stocks_result.returncode, 0, stocks_result.stderr)
        self.assertIn("--user restart docich-market-data-stocks.service", stocks_calls)
        self.assertNotIn("--user restart docich-market-data-fx.service", stocks_calls)

        fx_result, fx_calls = self._run(fx_provider=True)
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
