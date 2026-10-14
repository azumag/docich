import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "restart_soren_bgm.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations.yml"


class RestartSorenBgmTest(unittest.TestCase):
    def test_script_is_fixed_scope_and_fail_closed(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('unit="soren-bgm.service"', text)
        self.assertIn('expected_exec="/home/ubuntu/soren/bgm_worker.sh"', text)
        self.assertIn('sudo -n systemctl restart "$unit"', text)
        self.assertIn('tagged > 1', text)
        self.assertNotIn("pkill", text)
        self.assertNotIn("killall", text)
        self.assertNotIn("RetroArch", text.replace("# Do not touch the game process, RetroArch, PulseAudio, OBS/ffmpeg or shared stream units.", ""))

    def test_workflow_runs_only_after_successful_production_push_deploy(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Restart Soren fallback BGM after reviewed runtime update", text)
        self.assertIn("github.event_name == 'push'", text)
        self.assertIn("steps.auth.outputs.target == 'production'", text)
        self.assertIn("games/soviet_now", text)
        self.assertIn("restart_soren_bgm.sh", text)
        self.assertIn('"exec docich production $SHA"', text)

    def _run(self, *, same_pid=False, exec_ok=True):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            bindir = d / "bin"
            bindir.mkdir()
            state = d / "pid"
            state.write_text("111\n", encoding="utf-8")
            systemctl = bindir / "systemctl"
            systemctl.write_text(textwrap.dedent(f"""\
                #!/usr/bin/env bash
                set -euo pipefail
                if [[ "$*" == "show soren-bgm.service --property=ExecStart --value" ]]; then
                  {'printf "%s\\n" "{ path=/home/ubuntu/soren/bgm_worker.sh ; }"' if exec_ok else 'printf "%s\\n" "{ path=/tmp/wrong.sh ; }"'}
                  exit 0
                fi
                if [[ "$*" == "show soren-bgm.service --property=ActiveState --value" ]]; then
                  echo active
                  exit 0
                fi
                if [[ "$*" == "show soren-bgm.service --property=MainPID --value" ]]; then
                  cat "$FAKE_PID_STATE"
                  exit 0
                fi
                exit 99
            """), encoding="utf-8")
            systemctl.chmod(0o755)
            sudo = bindir / "sudo"
            sudo.write_text(textwrap.dedent(f"""\
                #!/usr/bin/env bash
                set -euo pipefail
                [[ "$1" == "-n" ]]
                shift
                [[ "$1" == "systemctl" && "$2" == "restart" && "$3" == "soren-bgm.service" ]]
                {'true' if same_pid else 'printf "222\\n" >"$FAKE_PID_STATE"'}
            """), encoding="utf-8")
            sudo.chmod(0o755)
            pgrep = bindir / "pgrep"
            pgrep.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            pgrep.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{bindir}:/usr/bin:/bin",
                "FAKE_PID_STATE": str(state),
            }
            return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=8)

    def test_success_restarts_only_fixed_service(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pid=222", result.stdout)

    def test_unchanged_pid_is_rejected(self):
        result = self._run(same_pid=True)
        self.assertEqual(result.returncode, 13)

    def test_unexpected_execstart_is_rejected_before_restart(self):
        result = self._run(exec_ok=False)
        self.assertEqual(result.returncode, 12)


if __name__ == "__main__":
    unittest.main()
