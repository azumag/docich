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

    def _run(
        self,
        *,
        same_pid=False,
        exec_ok=True,
        pulseaudio_pids=(),
        cgroups=None,
        unit_user="ubuntu",
        pulseaudio_mode="none",
        pulseaudio_rc=0,
        ffplay_mode="none",
        ffplay_pids=(),
        ffplay_rc=0,
        cmdlines=None,
        appear_after=2,
    ):
        """Run the helper against isolated stubs.

        pulseaudio_mode/ffplay_mode:
          none      - pgrep exits 1 (nothing matched)
          always    - pgrep lists the configured pids every time
          after_restart  - listed only once the fake sudo restart ran
          after_scans=N  - listed only from the (N+1)th scan onward, which
                           models a server appearing during the readiness
                           window after the immediate post-restart scan
        *_rc sets the pgrep exit status (2/3/127 = enumeration failure).
        """
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
                  printf '%s\\n' "${{FAKE_UNIT_USER:-ubuntu}}"
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
            pgrep.write_text(
                """#!/usr/bin/env bash
set -uo pipefail
printf '%s\\n' "$*" >>"$FAKE_PGREP_LOG"
target=""
for arg in "$@"; do
  case "$arg" in
    pulseaudio|ffplay) target="$arg" ;;
  esac
done
if [ "$target" = "pulseaudio" ]; then
  mode="$FAKE_PA_MODE"; pids="$FAKE_PA_PIDS"; rc="$FAKE_PA_RC"
else
  mode="$FAKE_FFPLAY_MODE"; pids="$FAKE_FFPLAY_PIDS"; rc="$FAKE_FFPLAY_RC"
fi
counter="$FAKE_COUNTER_DIR/$target.count"
count=0
[ -f "$counter" ] && count="$(cat "$counter")"
count=$((count + 1))
printf '%s\\n' "$count" >"$counter"
if [ "$mode" = "none" ]; then exit 1; fi
if [ "$rc" -ne 0 ]; then exit "$rc"; fi
if [ "$mode" = "after_restart" ] && [ ! -f "$FAKE_SUDO_LOG" ]; then exit 1; fi
if [ "$mode" = "after_scans" ]; then
  if [ "$count" -le "$FAKE_APPEAR_AFTER" ]; then exit 1; fi
fi
[ -n "$pids" ] || exit 1
printf '%s\\n' $pids
""",
                encoding="utf-8",
            )
            pgrep.chmod(0o755)
            proc_root = d / "proc"
            proc_root.mkdir(parents=True, exist_ok=True)
            for pid, content in (cgroups or {}).items():
                entry = proc_root / str(pid)
                entry.mkdir(parents=True, exist_ok=True)
                (entry / "cgroup").write_text(content, encoding="utf-8")
            for pid, content in (cmdlines or {}).items():
                entry = proc_root / str(pid)
                entry.mkdir(parents=True, exist_ok=True)
                (entry / "cmdline").write_bytes(content.encode("utf-8"))
            counter_dir = d / "counters"
            counter_dir.mkdir()
            sudo_log = d / "sudo.log"
            pgrep_log = d / "pgrep.log"
            env = {
                **os.environ,
                "PATH": f"{bindir}:/usr/bin:/bin",
                "FAKE_PID_STATE": str(state),
                "FAKE_SUDO_LOG": str(sudo_log),
                "FAKE_PGREP_LOG": str(pgrep_log),
                "FAKE_COUNTER_DIR": str(counter_dir),
                "SOREN_BGM_PROC_ROOT": str(proc_root),
                "FAKE_UNIT_USER": unit_user,
                "FAKE_PA_MODE": pulseaudio_mode,
                "FAKE_PA_PIDS": " ".join(str(pid) for pid in pulseaudio_pids),
                "FAKE_PA_RC": str(pulseaudio_rc),
                "FAKE_FFPLAY_MODE": ffplay_mode,
                "FAKE_FFPLAY_PIDS": " ".join(str(pid) for pid in ffplay_pids),
                "FAKE_FFPLAY_RC": str(ffplay_rc),
                "FAKE_APPEAR_AFTER": str(appear_after),
            }
            result = subprocess.run(
                ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=8
            )
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
        result = self._run(pulseaudio_mode="always", pulseaudio_pids=(4242,), cgroups={})
        self.assertIn("-u ubuntu -x pulseaudio", result.pgrep_log)

    def test_pulseaudio_in_the_unit_cgroup_blocks_the_restart(self):
        result = self._run(
            pulseaudio_mode="always",
            pulseaudio_pids=(4242,),
            cgroups={4242: "0::/system.slice/soren-bgm.service\n"},
        )
        self.assertEqual(result.returncode, 15)
        self.assertIn("inside the soren-bgm.service cgroup", result.stderr)
        self.assertEqual(result.sudo_log, "")

    def test_unverifiable_pulseaudio_cgroup_blocks_the_restart(self):
        result = self._run(pulseaudio_mode="always", pulseaudio_pids=(4242,), cgroups={})
        self.assertEqual(result.returncode, 15)
        self.assertIn("cannot verify the cgroup", result.stderr)
        self.assertEqual(result.sudo_log, "")

    def test_pulseaudio_in_another_cgroup_does_not_block(self):
        result = self._run(
            pulseaudio_mode="always",
            pulseaudio_pids=(4242,),
            cgroups={4242: "0::/user.slice/user-1001.slice/session.slice/pulseaudio.service\n"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.sudo_log, "restart\n")

    def test_restart_that_spawns_a_private_pulseaudio_fails_closed(self):
        result = self._run(
            pulseaudio_mode="after_restart",
            pulseaudio_pids=(4242,),
            cgroups={4242: "0::/system.slice/soren-bgm.service\n"},
        )
        self.assertEqual(result.returncode, 16)
        self.assertIn("spawned a private PulseAudio", result.stderr)
        # The restart itself already ran, so the deploy must stay red.
        self.assertEqual(result.sudo_log, "restart\n")

    # -- review P2 (2026-10-20): enumeration failure must never look healthy --

    def test_pulseaudio_enumeration_failure_blocks_the_restart(self):
        for rc in (2, 3, 127):
            with self.subTest(pgrep_rc=rc):
                result = self._run(pulseaudio_mode="always", pulseaudio_rc=rc)
                self.assertEqual(result.returncode, 15, result.stderr)
                self.assertIn("cannot enumerate pulseaudio processes", result.stderr)
                self.assertEqual(result.sudo_log, "")

    def test_unresolvable_unit_user_blocks_the_restart(self):
        for owner in ("", "not a user", "ubuntu;id"):
            with self.subTest(unit_user=owner):
                result = self._run(
                    pulseaudio_mode="always",
                    pulseaudio_pids=(4242,),
                    cgroups={4242: "0::/system.slice/soren-bgm.service\n"},
                    unit_user=owner,
                )
                self.assertEqual(result.returncode, 15, result.stderr)
                self.assertIn("refusing to restart", result.stderr)
                self.assertEqual(result.sudo_log, "")

    def test_ffplay_enumeration_failure_does_not_report_success(self):
        result = self._run(ffplay_mode="always", ffplay_pids=(555,), ffplay_rc=3)
        self.assertEqual(result.returncode, 14, result.stderr)
        self.assertIn("cannot enumerate ffplay processes", result.stderr)
        self.assertNotIn("restarted: pid=", result.stdout)

    # -- review P2 (2026-10-20): a server that appears after the immediate
    #    post-restart scan (during the Type=simple readiness window) ----------

    def test_pulseaudio_appearing_during_readiness_fails_closed(self):
        result = self._run(
            pulseaudio_mode="after_scans",
            appear_after=2,
            pulseaudio_pids=(4242,),
            cgroups={4242: "0::/system.slice/soren-bgm.service\n"},
        )
        self.assertEqual(result.returncode, 16, result.stderr)
        self.assertIn("private PulseAudio after startup", result.stderr)
        self.assertNotIn("restarted: pid=", result.stdout)

    def test_delayed_appearance_is_still_caught_at_success_point(self):
        text = SCRIPT.read_text(encoding="utf-8")
        # Two checks after the restart: the immediate one and the success-point
        # re-validation that also covers the readiness window.
        after = text.index("sudo -n systemctl restart")
        self.assertEqual(text.count("if pulseaudio_in_bgm_cgroup; then"), 3)
        second = text.index("private PulseAudio after startup")
        self.assertGreater(second, after)
        readiness = text.index("for _ in $(seq 1 40)")
        self.assertGreater(second, readiness, "re-validation must follow readiness")

    def test_guarantee_scope_is_documented(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("instantaneous scans at two points in time", text)
        self.assertIn("cannot prove no client will ever autospawn one", text)

    def test_script_checks_the_cgroup_before_and_after_the_restart(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("pulseaudio_in_bgm_cgroup", text)
        self.assertIn('[[ "$cgroup" == *"/$unit"* ]]', text)
        self.assertIn('pgrep -u "$owner" -x pulseaudio', text)
        self.assertIn('systemctl show "$unit" --property=User --value', text)
        self.assertIn("enumeration_failed", text)
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
