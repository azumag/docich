"""The fixed recovery script must reach a failed meriken slot (#1969).

meriken runs on the soren91 game and its failed slot is in soren91_corner.json;
`retro-corner recover-failed` only reads retro_corner.json, so the script used
to end in "corner rotation recovery rejected" (exit 71) and the latch stayed.
Behavioural: a fake launcher records the argv the real script would execute.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/vm_actions/recover_corner_rotation.sh"
REQ = "11111111-1111-4111-8111-111111111111"


def run_script(tmp_path, corner, *, recover_failed_status="succeeded", manual=None,
               status_rc=0, status_stderr=""):
    prod = tmp_path / "prod"
    (prod / "bin").mkdir(parents=True)
    (prod / "config").mkdir()
    (prod / "config/docich.soren-live.toml").write_text("")
    cfg = tmp_path / "cfg/systemd/user"
    cfg.mkdir(parents=True)
    (cfg / "docich-corner-rotation.service").write_text("[Service]\n")
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    status = json.dumps({"status": "recovery_required",
                         "pending": {"corner": corner, "request_id": REQ},
                         "manual_pending": manual})
    status_err = tmp_path / "status_err.txt"
    status_err.write_text(status_stderr)
    launcher = prod / "bin/docich"
    launcher.write_text(f"""#!/bin/bash
echo "$*" >> {log}
case "$*" in
  *"corner-rotation status"*) printf '%s' '{status}';;
  *"recover-failed") printf '%s' '{{"status":"{recover_failed_status}"}}';;
esac
if [[ "$*" == *"corner-rotation status"* ]]; then
  cat "{status_err}" >&2
  exit {status_rc}
fi
exit 0
""")
    launcher.chmod(0o755)
    systemctl = bindir / "systemctl"
    systemctl.write_text(f"#!/bin/bash\necho \"systemctl $*\" >> {log}\n")
    systemctl.chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
           "XDG_RUNTIME_DIR": str(tmp_path), "DOCICH_PROD_ROOT": str(prod)}
    done = subprocess.run(["bash", str(SCRIPT)], env=env, text=True, capture_output=True, timeout=60)
    calls = log.read_text().splitlines() if log.exists() else []
    return done, calls


def corner_calls(calls):
    return [c.split("soren-live.toml ", 1)[1] for c in calls if "soren-live.toml" in c]


def test_meriken_slot_is_recovered_through_the_soren91_cli_not_retro(tmp_path):
    done, calls = run_script(tmp_path, "meriken")
    assert done.returncode == 0, done.stderr
    assert corner_calls(calls) == ["corner-rotation status", "soren91-corner recover-failed",
                                   "corner-rotation recover"]
    assert calls[-1].startswith("systemctl") and "restart" in calls[-1]


def test_other_slots_keep_the_retro_path(tmp_path):
    done, calls = run_script(tmp_path, "pacman4console")
    assert done.returncode == 0, done.stderr
    assert corner_calls(calls) == ["corner-rotation status", "retro-corner recover-failed",
                                   "corner-rotation recover"]


def test_a_rejected_meriken_recovery_stops_before_the_latch_commit_and_restart(tmp_path):
    done, calls = run_script(tmp_path, "meriken", recover_failed_status="failed")
    assert done.returncode == 71
    assert corner_calls(calls) == ["corner-rotation status", "soren91-corner recover-failed"]
    assert not any(c.startswith("systemctl") and "restart" in c for c in calls)


def test_meriken_with_a_manual_reservation_is_refused_before_any_recovery(tmp_path):
    manual = {"corner": "meriken", "request_id": REQ}
    done, calls = run_script(tmp_path, "meriken", manual=manual)
    assert done.returncode == 26
    assert corner_calls(calls) == ["corner-rotation status"]


def test_soren91_cli_exposes_recover_failed():
    done = subprocess.run([sys.executable, "-m", "docich", "soren91-corner", "--help"], cwd=ROOT, text=True,
                          capture_output=True, timeout=60,
                          env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr
    assert "recover-failed" in done.stdout


@pytest.mark.parametrize("status_rc,token", [
    (5, "corner-rotation status: initialization failed"),
    (6, "corner-rotation status: ledger read failed"),
])
def test_status_failure_surfaces_only_the_fixed_token(tmp_path, status_rc, token):
    # Extra lines (tracebacks, provider/config text) must never reach the
    # operator-visible failure message; only the reviewed literal does.
    done, calls = run_script(
        tmp_path, "meriken", status_rc=status_rc,
        status_stderr=f"{token}\nTraceback (most recent call last):\nsecret-material\n")
    assert done.returncode == 26
    assert f"cannot verify corner rotation reservation ({token})" in done.stderr
    # The surfaced message stays far below the reviewed 240-character bound.
    assert len(done.stderr) <= 240
    assert "Traceback" not in done.stderr
    assert "secret-material" not in done.stderr
    assert corner_calls(calls) == ["corner-rotation status"]


def test_status_failure_without_a_fixed_token_stays_plain(tmp_path):
    done, calls = run_script(
        tmp_path, "meriken", status_rc=1,
        status_stderr="some unrelated stderr line\n")
    assert done.returncode == 26
    assert done.stderr.strip() == "cannot verify corner rotation reservation"
    assert "some unrelated stderr line" not in done.stderr
    assert corner_calls(calls) == ["corner-rotation status"]
