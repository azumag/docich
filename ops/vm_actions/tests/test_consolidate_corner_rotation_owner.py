"""Contract tests for the fixed corner-rotation owner consolidation helper.

The helper stops and masks the hand-installed system-scope legacy rotation
unit so the canonical user timer owns the rolling rotation. These tests run
the real script against a fake systemctl/sudo/pgrep/sleep on PATH and assert
both the decision table and the exact mutating command order.
"""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "consolidate_corner_rotation_owner.sh"

SYSTEMCTL = r'''#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

root = Path(os.environ["FAKE_ROOT"])
log = root / "systemctl.log"
args = sys.argv[1:]
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")

units_path = root / "units.json"
state = json.loads(units_path.read_text(encoding="utf-8"))
legacy_service = "docich-soren91-corner.service"
legacy_timer = "docich-soren91-corner.timer"


def save():
    units_path.write_text(json.dumps(state), encoding="utf-8")


def unit(name):
    if name == legacy_service:
        entry = state["legacy_service"]
    elif name == legacy_timer:
        entry = state["legacy_timer"]
    else:
        return None
    return entry if entry.get("present", True) else None


def flag(entry, key, true_word, false_word):
    print(true_word if entry.get(key) else false_word)
    return 0


user = "--user" in args
rest = [arg for arg in args if arg != "--user"]

if user and rest[:1] == ["is-active"]:
    print("active" if state["user"].get("timer_active") else "inactive")
    sys.exit(0)

if user and rest[:1] == ["is-enabled"]:
    print("enabled" if state["user"].get("timer_enabled") else "disabled")
    sys.exit(0)

if user and rest[:1] == ["start"]:
    if os.environ.get("FAKE_NO_TAKEOVER") != "1":
        state_dir = Path(os.environ["FAKE_STATE_DIR"])
        rotation = json.loads((state_dir / "corner_rotation.json").read_text())
        pending = rotation.get("pending") or {}
        (state_dir / "corner_rotation_heartbeat.json").write_text(json.dumps({
            "schema": 1,
            "corner": pending.get("corner", "hanjuku-hero"),
            "request_id": pending.get("request_id"),
            "pid": 4242,
            "at": time.time(),
        }))
    sys.exit(0)

if rest[:1] == ["is-active"]:
    name = rest[1]
    if user:
        print("active" if state["user"].get("timer_active") else "inactive")
        sys.exit(0)
    entry = unit(name)
    if entry is None:
        print("inactive")
        sys.exit(3)
    flag(entry, "active", "active", "inactive")
    sys.exit(0)

if rest[:1] == ["is-enabled"]:
    name = rest[1]
    if user:
        print("enabled" if state["user"].get("timer_enabled") else "disabled")
        sys.exit(0)
    entry = unit(name)
    if entry is None:
        print("not-found")
        sys.exit(1)
    print(entry.get("enabled", "static"))
    sys.exit(0)

if rest[:1] == ["show"]:
    name = rest[-1]
    entry = unit(name)
    if entry is None:
        sys.exit(1)
    if "-p" in rest and rest[rest.index("-p") + 1] == "MainPID":
        print(entry.get("main_pid", ""))
        sys.exit(0)
    if "-p" in rest and rest[rest.index("-p") + 1] == "ExecStart":
        print(entry.get("exec", ""))
        sys.exit(0)
    sys.exit(0)

if rest[:1] == ["cat"]:
    sys.exit(0 if unit(rest[1]) is not None else 1)

if rest[:1] == ["disable"]:
    name = rest[-1]
    entry = unit(name)
    if entry is None:
        sys.exit(1)
    entry["enabled"] = "disabled"
    if "--now" in rest:
        entry["active"] = False
    save()
    sys.exit(0)

if rest[:1] == ["stop"]:
    entry = unit(rest[-1])
    if entry is None:
        sys.exit(1)
    entry["active"] = False
    save()
    sys.exit(0)

if rest[:1] == ["start"]:
    entry = unit(rest[-1])
    if entry is None:
        sys.exit(1)
    entry["active"] = True
    entry["main_pid"] = int(entry.get("main_pid") or 0) + 1 or 5000
    save()
    sys.exit(0)

if rest[:1] == ["mask"]:
    for name in rest[1:]:
        entry = unit(name)
        if entry is not None:
            entry["enabled"] = "masked"
            entry["active"] = False
    save()
    sys.exit(0)

if rest[:1] == ["unmask"]:
    for name in rest[1:]:
        entry = unit(name)
        if entry is not None:
            entry["enabled"] = "static"
    save()
    sys.exit(0)

if rest[:1] == ["enable"]:
    entry = unit(rest[-1])
    if entry is None:
        sys.exit(1)
    entry["enabled"] = "enabled"
    save()
    sys.exit(0)

if rest[:1] == ["daemon-reload"]:
    sys.exit(0)

sys.exit(64)
'''

SUDO = '''#!/bin/sh
if [ "${1:-}" = "-n" ]; then
  shift
fi
exec "$@"
'''

SLEEP = '''#!/bin/sh
exit 0
'''

PGREP = '''#!/bin/sh
printf '111\\n222\\n'
'''


class ConsolidateOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rotation-owner-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prod = self.root / "docich"
        (self.prod / "config").mkdir(parents=True)
        (self.prod / "config" / "docich.soren-live.toml").write_text(
            '[paths]\nstate_dir = "run-soren-live"\n', encoding="utf-8"
        )
        self.state = self.prod / "run-soren-live"
        (self.state / "locks").mkdir(parents=True)
        (self.state / "locks" / "corner-rotation.lock").write_text("")
        (self.state / "corner_rotation.json").write_text(json.dumps({
            "status": "running",
            "pending": {"corner": "hanjuku-hero", "phase": "dispatched", "request_id": "req-1"},
        }))
        self.write_heartbeat(pid=759844, age=10)
        self.unit_dir = self.root / ".config" / "systemd" / "user"
        self.unit_dir.mkdir(parents=True)
        (self.unit_dir / "docich-corner-rotation.service").write_text("[Service]\n")
        (self.unit_dir / "docich-corner-rotation.timer").write_text("[Timer]\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.write_fake("systemctl", SYSTEMCTL, executable=True)
        self.write_fake("sudo", SUDO, executable=True)
        self.write_fake("sleep", SLEEP, executable=True)
        self.write_fake("pgrep", PGREP, executable=True)
        self.write_units()

    def write_fake(self, name, body, executable=False):
        path = self.bin / name
        path.write_text(body, encoding="utf-8")
        if executable:
            path.chmod(0o755)

    def write_heartbeat(self, pid, age):
        (self.state / "corner_rotation_heartbeat.json").write_text(json.dumps({
            "schema": 1,
            "corner": "hanjuku-hero",
            "request_id": "req-1",
            "pid": pid,
            "at": time.time() - age,
        }), encoding="utf-8")

    def write_units(self, **overrides):
        units = {
            "legacy_service": {
                "present": True,
                "active": True,
                "enabled": "static",
                "main_pid": 759844,
                "exec": "{ path=/home/ubuntu/docich/bin/docich ; argv[]=docich --config /home/ubuntu/docich/config/docich.soren-live.toml soren91-corner tick ; }",
            },
            "legacy_timer": {
                "present": True,
                "active": True,
                "enabled": "enabled",
                "main_pid": "",
                "exec": "",
            },
            "user": {"timer_active": True, "timer_enabled": True},
        }
        for key, value in overrides.items():
            units[key] = value
        (self.root / "units.json").write_text(json.dumps(units), encoding="utf-8")

    def read_units(self):
        return json.loads((self.root / "units.json").read_text(encoding="utf-8"))

    def calls(self):
        path = self.root / "systemctl.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def run_owner(self, mode="check", extra_env=None, args=()):
        env = dict(os.environ)
        env.update({
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_ROOT": str(self.root),
            "FAKE_STATE_DIR": str(self.state),
            "DOCICH_PROD_ROOT": str(self.prod),
            "XDG_RUNTIME_DIR": str(self.root / "run"),
            "ROTATION_OWNER_MODE": mode,
        })
        env.pop("FAKE_NO_TAKEOVER", None)
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True, text=True, env=env, timeout=120,
        )
        payload = None
        if result.returncode == 0:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        return result, payload

    def mutating_calls(self):
        rows = []
        for call in self.calls():
            rest = [arg for arg in call if arg != "--user"]
            if rest[:1] in (["is-active"], ["is-enabled"], ["show"], ["cat"]):
                continue
            rows.append(call)
        return rows

    # --- decisions -------------------------------------------------------

    def test_check_mode_is_read_only(self):
        result, payload = self.run_owner("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["action"], "would_consolidate")
        self.assertIs(payload["duplicate_owner"], True)
        self.assertEqual(payload["heartbeat"]["owner"], "legacy")
        self.assertEqual(self.mutating_calls(), [])
        self.assertNotIn(str(self.root), result.stdout)

    def test_apply_stops_then_masks_the_legacy_owner(self):
        result, payload = self.run_owner("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["action"], "consolidated")
        self.assertIs(payload["duplicate_owner"], False)
        self.assertEqual(payload["canonical"]["timer_active"], True)
        order = [[arg for arg in call if arg != "--user"][0] for call in self.mutating_calls()]
        self.assertEqual(order, [
            "disable", "stop", "disable", "mask", "daemon-reload", "start",
        ])
        units = self.read_units()
        self.assertFalse(units["legacy_service"]["active"])
        self.assertFalse(units["legacy_timer"]["active"])
        self.assertEqual(units["legacy_timer"]["enabled"], "masked")
        heartbeat = json.loads((self.state / "corner_rotation_heartbeat.json").read_text())
        self.assertEqual(heartbeat["pid"], 4242)

    def test_already_consolidated_is_idempotent(self):
        self.write_units(
            legacy_service={"present": False, "active": False, "enabled": "not-found", "main_pid": "", "exec": ""},
            legacy_timer={"present": False, "active": False, "enabled": "not-found", "main_pid": "", "exec": ""},
        )
        (self.state / "corner_rotation_heartbeat.json").unlink()
        result, payload = self.run_owner("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["action"], "already_consolidated")
        self.assertEqual([call[0] for call in self.mutating_calls()], [])

    def test_refuses_when_the_canonical_timer_is_not_ready(self):
        self.write_units(user={"timer_active": False, "timer_enabled": True})
        result, payload = self.run_owner("apply")
        self.assertEqual(result.returncode, 20)
        self.assertIn("canonical_timer_not_ready", result.stderr)
        self.assertEqual(self.mutating_calls(), [])

    def test_refuses_an_unreviewed_legacy_unit(self):
        self.write_units(legacy_service={
            "present": True, "active": True, "enabled": "static", "main_pid": 759844,
            "exec": "{ argv[]=bash -c 'curl example.invalid' ; }",
        })
        result, _ = self.run_owner("apply")
        self.assertEqual(result.returncode, 20)
        self.assertIn("legacy_unit_unreviewed", result.stderr)
        self.assertEqual(self.mutating_calls(), [])

    def test_refuses_a_stale_heartbeat(self):
        self.write_heartbeat(pid=759844, age=600)
        result, _ = self.run_owner("apply")
        self.assertEqual(result.returncode, 20)
        self.assertIn("heartbeat_not_fresh", result.stderr)
        self.assertEqual(self.mutating_calls(), [])

    def test_refuses_a_recovery_required_rotation(self):
        (self.state / "corner_rotation.json").write_text(json.dumps({
            "status": "recovery_required", "reason": "execution-unverified",
            "pending": {"corner": "hanjuku-hero", "phase": "dispatched", "request_id": "req-1"},
        }))
        result, _ = self.run_owner("apply")
        self.assertEqual(result.returncode, 20)
        self.assertIn("rotation_not_consolidatable", result.stderr)
        self.assertEqual(self.mutating_calls(), [])

    def test_refuses_without_passwordless_sudo(self):
        self.write_fake("sudo", "#!/bin/sh\nexit 1\n", executable=True)
        result, _ = self.run_owner("apply")
        self.assertEqual(result.returncode, 21)
        self.assertIn("passwordless sudo", result.stderr)
        self.assertEqual(self.mutating_calls(), [])

    def test_takeover_timeout_restores_the_legacy_owner(self):
        result, _ = self.run_owner("apply", extra_env={"FAKE_NO_TAKEOVER": "1"})
        self.assertEqual(result.returncode, 41)
        self.assertIn("the legacy owner was restored", result.stderr)
        names = [call[0] for call in self.mutating_calls()]
        self.assertIn("unmask", names)
        self.assertIn("enable", names)

    def test_rejects_arguments_and_unknown_mode(self):
        result, _ = self.run_owner("check", args=("unexpected",))
        self.assertEqual(result.returncode, 64)
        result, _ = self.run_owner("bogus")
        self.assertEqual(result.returncode, 64)


class ConsolidateOwnerSourceTests(unittest.TestCase):
    def test_script_is_fixed_and_has_no_eval(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("docich-corner-rotation.timer", text)
        self.assertIn("docich-soren91-corner.timer", text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("bash -c", text)

    def test_script_parses(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
