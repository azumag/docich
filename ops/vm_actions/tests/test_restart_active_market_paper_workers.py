import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "restart_active_market_paper_workers.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "market-paper-runtime-reload.yml"


class RestartActiveMarketPaperWorkersTests(unittest.TestCase):
    def _run(self, provider_active):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        tmp = temp.name
        fake_bin = pathlib.Path(tmp) / "bin"
        fake_bin.mkdir()
        log = pathlib.Path(tmp) / "calls.log"
        systemctl = fake_bin / "systemctl"
        provider_rc = 0 if provider_active else 3
        systemctl.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$CALL_LOG\"\n"
            "if [[ \"$*\" == *'is-active --quiet docich-market-worker@fx.service' ]]; then exit 0; fi\n"
            "if [[ \"$*\" == *'is-active --quiet docich-market-worker@stocks.service' ]]; then exit 3; fi\n"
            f"if [[ \"$*\" == *'is-active --quiet docich-market-data-stocks.service' ]]; then exit {provider_rc}; fi\n"
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

    def test_only_active_worker_is_restarted_when_provider_inactive(self):
        result, calls = self._run(provider_active=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--user is-active --quiet docich-market-worker@stocks.service", calls)
        self.assertIn("--user is-active --quiet docich-market-worker@fx.service", calls)
        self.assertIn("--user is-active --quiet docich-market-data-stocks.service", calls)
        self.assertNotIn("--user restart docich-market-worker@stocks.service", calls)
        self.assertIn("--user restart docich-market-worker@fx.service", calls)
        self.assertNotIn("--user restart docich-market-data-stocks.service", calls)
        self.assertNotIn(" enable ", calls)
        self.assertNotIn(" start ", calls)

    def test_active_read_only_provider_is_reloaded_after_deploy(self):
        result, calls = self._run(provider_active=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--user restart docich-market-worker@fx.service", calls)
        self.assertIn("--user restart docich-market-data-stocks.service", calls)
        self.assertNotIn("--user restart docich-market-worker@stocks.service", calls)

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
