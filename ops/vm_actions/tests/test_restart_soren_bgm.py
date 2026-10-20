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

    def _run(self, *, same_pid=False, exec_ok=True, pulseaudio_pids=(), cgroups=None, proc_root_missing=False, pulseaudio_appears_after_restart=False, unit_user="ubuntu"):
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
                if [[ "$*" == "show soren-bgm.service --property=User --value" ]]; then
                  printf '%s\n' "${{FAKE_UNIT_USER:-ubuntu}}"
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
                printf 'restart\\n' >>"$FAKE_SUDO_LOG"
                {'true' if same_pid else 'printf "222\\n" >"$FAKE_PID_STATE"'}
            """), encoding="utf-8")
            sudo.chmod(0o755)
            pgrep = bindir / "pgrep"
            if pulseaudio_appears_after_restart:
                # Models the new unit generation spawning a private server:
                # absent before the restart, present once sudo restarted it.
                pgrep.write_text(
                    "#!/usr/bin/env bash\n"
                    '[[ -f "$FAKE_SUDO_LOG" ]] || exit 1\n'
                    "printf '%s\\n' "
                    + " ".join(str(pid) for pid in pulseaudio_pids)
                    + "\n",
                    encoding="utf-8",
                )
            elif pulseaudio_pids:
                pgrep.write_text(
                    "#!/usr/bin/env bash\nprintf '%s\\n' "
                    + " ".join(str(pid) for pid in pulseaudio_pids)
                    + "\n",
                    encoding="utf-8",
                )
            else:
                pgrep.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            # Record the -u argument so the contract test can assert that the
            # unit's own user is scanned rather than whoever runs the helper.
            pgrep.write_text(
                pgrep.read_text(encoding="utf-8").replace(
                    "#!/usr/bin/env bash\n",
                    '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >>"$FAKE_PGREP_LOG"\n',
                    1,
                ),
                encoding="utf-8",
            )
            pgrep.chmod(0o755)
            proc_root = d / "proc"
            if not proc_root_missing:
                for pid, content in (cgroups or {}).items():
                    entry = proc_root / str(pid)
                    entry.mkdir(parents=True)
                    (entry / "cgroup").write_text(content, encoding="utf-8")
                proc_root.mkdir(parents=True, exist_ok=True)
            sudo_log = d / "sudo.log"
            pgrep_log = d / "pgrep.log"
            env = {
                **os.environ,
                "PATH": f"{bindir}:/usr/bin:/bin",
                "FAKE_PID_STATE": str(state),
                "FAKE_SUDO_LOG": str(sudo_log),
                "SOREN_BGM_PROC_ROOT": str(proc_root),
                "FAKE_UNIT_USER": unit_user,
                "FAKE_PGREP_LOG": str(pgrep_log),
            }
            result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=8)
            result.sudo_log = sudo_log.read_text(encoding="utf-8") if sudo_log.exists() else ""
            result.pgrep_log = pgrep_log.read_text(encoding="utf-8") if pgrep_log.exists() else ""
            return result

    def test_success_restarts_only_fixed_service(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pid=222", result.stdout)
        self.assertEqual(result.sudo_log, "restart\n")

    def test_unchanged_pid_is_rejected(self):
        result = self._run(same_pid=True)
        self.assertEqual(result.returncode, 13)

    def test_unexpected_execstart_is_rejected_before_restart(self):
        result = self._run(exec_ok=False)
        self.assertEqual(result.returncode, 12)

    # ---- docich #2008: never restart over a shared PulseAudio server -------

    def test_guard_scans_the_unit_user_not_the_caller(self):
        result = self._run(pulseaudio_pids=(4242,), cgroups={}, unit_user="ubuntu")
        self.assertIn("-u ubuntu -x pulseaudio", result.pgrep_log)

    def test_pulseaudio_in_the_unit_cgroup_blocks_the_restart(self):
        result = self._run(
            pulseaudio_pids=(4242,),
            cgroups={4242: "0::/system.slice/soren-bgm.service\n"},
        )
        self.assertEqual(result.returncode, 15)
        self.assertIn("inside the soren-bgm.service cgroup", result.stderr)
        self.assertEqual(result.sudo_log, "")

    def test_unverifiable_pulseaudio_cgroup_blocks_the_restart(self):
        result = self._run(pulseaudio_pids=(4242,), cgroups={})
        self.assertEqual(result.returncode, 15)
        self.assertIn("cannot verify the cgroup", result.stderr)
        self.assertEqual(result.sudo_log, "")

    def test_pulseaudio_in_another_cgroup_does_not_block(self):
        result = self._run(
            pulseaudio_pids=(4242,),
            cgroups={4242: "0::/user.slice/user-1001.slice/session.slice/pulseaudio.service\n"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.sudo_log, "restart\n")

    def test_restart_that_spawns_a_private_pulseaudio_fails_closed(self):
        result = self._run(
            pulseaudio_pids=(4242,),
            cgroups={4242: "0::/system.slice/soren-bgm.service\n"},
            pulseaudio_appears_after_restart=True,
        )
        self.assertEqual(result.returncode, 16)
        self.assertIn("spawned a private PulseAudio", result.stderr)
        # The restart itself already ran, so the deploy must stay red.
        self.assertEqual(result.sudo_log, "restart\n")

    def test_script_checks_the_cgroup_before_and_after_the_restart(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("pulseaudio_in_bgm_cgroup", text)
        self.assertIn('[[ "$cgroup" == *"/$unit"* ]]', text)
        self.assertIn('pgrep -u "$owner" -x pulseaudio', text)
        self.assertIn('systemctl show "$unit" --property=User --value', text)
        self.assertEqual(text.count("if pulseaudio_in_bgm_cgroup; then"), 2)
        self.assertIn("exit 15", text)
        self.assertIn("exit 16", text)
        self.assertNotIn("pkill", text)
        self.assertNotIn("killall", text)
        self.assertNotIn("systemctl restart pulseaudio", text)
        self.assertLess(
            text.index("exit 15"), text.index("sudo -n systemctl restart")
        )
        self.assertGreater(
            text.index("exit 16"), text.index("sudo -n systemctl restart")
        )


if __name__ == "__main__":
    unittest.main()
