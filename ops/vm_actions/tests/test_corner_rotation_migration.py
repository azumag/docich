"""Execution tests for the staged corner-rotation unit rename.

A stub `systemctl --user` records every call and keeps enabled/active state
in a temporary directory, so the migration, rollback and deploy hook can be
exercised end to end without systemd. The scripts are the reviewed production
files; only `DOCICH_PROD_ROOT`, `HOME` and the fake `PATH` are redirected.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ENSURE = ROOT / "ops" / "vm_actions" / "ensure_corner_rotation_timer.sh"
MIGRATE = ROOT / "ops" / "vm_actions" / "migrate_corner_rotation_timer.sh"
ROLLBACK = ROOT / "ops" / "vm_actions" / "rollback_corner_rotation_timer.sh"

LEGACY_SERVICE = "docich-retro-corner.service"
LEGACY_TIMER = "docich-retro-corner.timer"
CANONICAL_SERVICE = "docich-corner-rotation.service"
CANONICAL_TIMER = "docich-corner-rotation.timer"
MIGRATION_EPOCH = "ops/vm_actions/corner_rotation_timer_migration_epoch"

FAKE_SYSTEMCTL = r"""#!/usr/bin/env bash
# Minimal `systemctl --user` stub for the corner-rotation rename tests.
set -u
printf '%s\n' "$*" >> "$SYSTEMCTL_CALLS_LOG"
state_dir="$FAKE_SYSTEMCTL_STATE"
unit_dir="$FAKE_SYSTEMCTL_UNIT_DIR"
mkdir -p "$state_dir" "$unit_dir/timers.target.wants"

sub="${2:-}"
unit=""
for arg in "$@"; do unit="$arg"; done

if [[ -n "${FAKE_SYSTEMCTL_FAIL_SUB:-}" && "$sub" == "$FAKE_SYSTEMCTL_FAIL_SUB" ]]; then
  exit 1
fi

case "$sub" in
  daemon-reload)
    exit 0
    ;;
  is-active)
    [[ -f "$state_dir/$unit.active" ]] && exit 0
    exit 3
    ;;
  is-enabled)
    [[ -f "$state_dir/$unit.enabled" ]] && exit 0
    exit 1
    ;;
  show)
    if [[ -L "$unit_dir/$unit" ]]; then
      target="$(basename "$(readlink "$unit_dir/$unit")")"
      if [[ "${FAKE_SYSTEMCTL_ID_UNRESOLVED:-0}" == "1" ]]; then
        printf 'Id=%s\n' "$unit"
        printf 'Names=%s %s\n' "$unit" "$target"
      else
        printf 'Id=%s\n' "$target"
      fi
    else
      printf 'Id=%s\n' "$unit"
    fi
    exit 0
    ;;
  enable)
    touch "$state_dir/$unit.enabled" "$state_dir/$unit.active"
    ln -sfn "../$unit" "$unit_dir/timers.target.wants/$unit"
    exit 0
    ;;
  disable)
    if [[ -n "${FAKE_SYSTEMCTL_ACTIVATE_ON_DISABLE:-}" ]]; then
      touch "$state_dir/$FAKE_SYSTEMCTL_ACTIVATE_ON_DISABLE.active"
    fi
    rm -f "$state_dir/$unit.enabled" "$state_dir/$unit.active"
    rm -f "$unit_dir/timers.target.wants/$unit"
    exit 0
    ;;
  restart|stop)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""


class UnitScriptFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="corner-rotation-units-")
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)
        self.docroot = self.base / "docich"
        (self.docroot / "scripts" / "systemd").mkdir(parents=True)
        for name in (LEGACY_SERVICE, LEGACY_TIMER, CANONICAL_SERVICE, CANONICAL_TIMER):
            shutil.copy(
                ROOT / "scripts" / "systemd" / name,
                self.docroot / "scripts" / "systemd" / name,
            )
        self.helper_dir = self.docroot / "ops" / "vm_actions"
        self.helper_dir.mkdir(parents=True)
        for script in (ENSURE, MIGRATE, ROLLBACK):
            shutil.copy(script, self.helper_dir / script.name)

        self.home = self.base / "home"
        self.unit_dir = self.home / ".config" / "systemd" / "user"
        self.unit_dir.mkdir(parents=True)
        self.fake_state = self.base / "systemctl-state"
        self.fake_state.mkdir()
        self.fake_bin = self.base / "bin"
        self.fake_bin.mkdir()
        fake = self.fake_bin / "systemctl"
        fake.write_text(FAKE_SYSTEMCTL, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.calls_log = self.base / "calls.log"
        self.calls_log.write_text("", encoding="utf-8")

        self.corner_state = self.base / "run-soren-live"
        (self.corner_state / "locks").mkdir(parents=True)
        (self.corner_state / "corners").mkdir()
        (self.corner_state / "game-switch" / "requests").mkdir(parents=True)
        (self.corner_state / "corner_rotation.json").write_text(
            '{"seed":"DO-NOT-TOUCH","status":"waiting"}', encoding="utf-8"
        )
        (self.corner_state / "retro_corner.json").write_text(
            '{"status":"idle"}', encoding="utf-8"
        )
        (self.corner_state / "locks" / "corner-rotation.lock").write_text(
            "lock\n", encoding="utf-8"
        )
        (self.corner_state / "locks" / "retro-corner.lock").write_text(
            "lock\n", encoding="utf-8"
        )
        (self.corner_state / "corners" / "paper.paused").write_text("", encoding="utf-8")
        (self.corner_state / "game-switch" / "requests" / "req-1.json").write_text(
            '{"status":"queued"}', encoding="utf-8"
        )
        self.state_before = self.snapshot_corner_state()

    # -- helpers ---------------------------------------------------------

    def snapshot_corner_state(self):
        snapshot = {}
        for path in sorted(self.corner_state.rglob("*")):
            if path.is_symlink() or path.is_file():
                snapshot[str(path.relative_to(self.corner_state))] = (
                    path.is_symlink(),
                    None if path.is_symlink() else path.read_bytes(),
                )
        return snapshot

    def env(self, **overrides):
        env = dict(os.environ)
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env["HOME"] = str(self.home)
        env.pop("XDG_CONFIG_HOME", None)
        env["XDG_RUNTIME_DIR"] = str(self.base / "runtime")
        env["DOCICH_PROD_ROOT"] = str(self.docroot)
        env["SYSTEMCTL_CALLS_LOG"] = str(self.calls_log)
        env["FAKE_SYSTEMCTL_STATE"] = str(self.fake_state)
        env["FAKE_SYSTEMCTL_UNIT_DIR"] = str(self.unit_dir)
        env.update(overrides)
        return env

    def run_helper(self, script, args=(), **overrides):
        return subprocess.run(
            ["bash", str(script), *args],
            env=self.env(**overrides),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def calls(self):
        return [line for line in self.calls_log.read_text(encoding="utf-8").splitlines() if line]

    def rendered(self, name):
        text = (ROOT / "scripts" / "systemd" / name).read_text(encoding="utf-8")
        return text.replace("__DOCICH_ROOT__", str(self.docroot))

    def write_legacy_units(self):
        for name in (LEGACY_SERVICE, LEGACY_TIMER):
            (self.unit_dir / name).write_text(self.rendered(name), encoding="utf-8")
        self.enable_fake(LEGACY_TIMER)

    def enable_fake(self, unit):
        (self.fake_state / f"{unit}.enabled").write_text("", encoding="utf-8")
        (self.fake_state / f"{unit}.active").write_text("", encoding="utf-8")
        wants = self.unit_dir / "timers.target.wants"
        wants.mkdir(parents=True, exist_ok=True)
        link = wants / unit
        if not link.is_symlink():
            link.symlink_to(f"../{unit}")

    def disable_fake(self, unit):
        for suffix in (".enabled", ".active"):
            path = self.fake_state / f"{unit}{suffix}"
            if path.exists():
                path.unlink()
        link = self.unit_dir / "timers.target.wants" / unit
        if link.is_symlink():
            link.unlink()

    def enabled_fake(self, unit):
        return (self.fake_state / f"{unit}.enabled").exists()

    def wants_timers(self):
        wants = self.unit_dir / "timers.target.wants"
        if not wants.is_dir():
            return set()
        return {path.name for path in wants.iterdir()}

    def assert_corner_state_untouched(self):
        self.assertEqual(self.state_before, self.snapshot_corner_state())

    def assert_no_shared_service_calls(self):
        for call in self.calls():
            for forbidden in ("restart", "stop", "docich.service", "soren", "ffmpeg", "audio", "radio"):
                self.assertNotIn(forbidden, call, call)

    # -- migration -------------------------------------------------------

    def test_migration_switches_to_canonical_with_a_single_timer(self):
        self.write_legacy_units()
        result = self.run_helper(MIGRATE)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in (CANONICAL_SERVICE, CANONICAL_TIMER):
            written = self.unit_dir / name
            self.assertTrue(written.is_file(), name)
            self.assertFalse(written.is_symlink(), name)
            self.assertEqual(written.read_text(encoding="utf-8"), self.rendered(name))
        self.assertTrue((self.unit_dir / LEGACY_SERVICE).is_symlink())
        self.assertEqual(os.readlink(self.unit_dir / LEGACY_SERVICE), CANONICAL_SERVICE)
        self.assertTrue((self.unit_dir / LEGACY_TIMER).is_symlink())
        self.assertEqual(os.readlink(self.unit_dir / LEGACY_TIMER), CANONICAL_TIMER)
        self.assertTrue(self.enabled_fake(CANONICAL_TIMER))
        self.assertFalse(self.enabled_fake(LEGACY_TIMER))
        self.assertEqual(self.wants_timers(), {CANONICAL_TIMER})
        self.assert_corner_state_untouched()
        self.assert_no_shared_service_calls()

    def test_migration_is_idempotent_and_never_creates_a_second_timer(self):
        self.write_legacy_units()
        self.assertEqual(self.run_helper(MIGRATE).returncode, 0)
        second = self.run_helper(MIGRATE)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already canonical", second.stdout)
        self.assertEqual(self.wants_timers(), {CANONICAL_TIMER})
        self.assertEqual(os.readlink(self.unit_dir / LEGACY_TIMER), CANONICAL_TIMER)
        self.assert_corner_state_untouched()

    def test_migration_aborts_without_killing_an_active_legacy_service(self):
        self.write_legacy_units()
        (self.fake_state / f"{LEGACY_SERVICE}.active").write_text("", encoding="utf-8")
        result = self.run_helper(MIGRATE)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("active", result.stderr)
        self.assertFalse((self.unit_dir / CANONICAL_TIMER).exists())
        self.assertFalse((self.unit_dir / CANONICAL_SERVICE).exists())
        self.assertTrue(self.enabled_fake(LEGACY_TIMER))
        self.assertFalse((self.unit_dir / LEGACY_TIMER).is_symlink())
        for call in self.calls():
            self.assertNotIn("kill", call)
            self.assertNotIn("restart", call)
            self.assertNotIn("disable", call)
        self.assert_corner_state_untouched()

    def test_migration_refuses_unexpected_legacy_unit_drift(self):
        self.write_legacy_units()
        with (self.unit_dir / LEGACY_SERVICE).open("a", encoding="utf-8") as handle:
            handle.write("\n# unexpected local edit\n")
        result = self.run_helper(MIGRATE)
        self.assertEqual(result.returncode, 32, result.stderr)
        self.assertIn("reviewed template", result.stderr)
        self.assertFalse((self.unit_dir / CANONICAL_TIMER).exists())
        self.assertFalse((self.unit_dir / CANONICAL_SERVICE).exists())
        self.assertTrue(self.enabled_fake(LEGACY_TIMER))
        self.assert_corner_state_untouched()

    def test_migration_requires_legacy_timer_stop_before_switching(self):
        self.write_legacy_units()
        result = self.run_helper(MIGRATE, FAKE_SYSTEMCTL_FAIL_SUB="disable")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.unit_dir / CANONICAL_TIMER).exists())
        self.assertFalse((self.unit_dir / CANONICAL_SERVICE).exists())
        self.assertFalse((self.unit_dir / LEGACY_TIMER).is_symlink())
        self.assert_corner_state_untouched()

    def test_migration_refuses_inconsistent_legacy_alias_state(self):
        self.write_legacy_units()
        (self.unit_dir / LEGACY_TIMER).unlink()
        (self.unit_dir / LEGACY_TIMER).symlink_to("somewhere-else.timer")
        result = self.run_helper(MIGRATE)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.unit_dir / CANONICAL_TIMER).exists())
        self.assert_corner_state_untouched()

    def test_migration_never_writes_through_the_legacy_alias(self):
        self.write_legacy_units()
        self.assertEqual(self.run_helper(MIGRATE).returncode, 0)
        canonical_before = (self.unit_dir / CANONICAL_TIMER).read_bytes()
        result = self.run_helper(ENSURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.unit_dir / CANONICAL_TIMER).read_bytes(), canonical_before)
        self.assertNotIn("docich-retro-corner.service", canonical_before.decode("utf-8"))
        self.assertEqual(os.readlink(self.unit_dir / LEGACY_TIMER), CANONICAL_TIMER)
        self.assertEqual(self.wants_timers(), {CANONICAL_TIMER})
        self.assert_corner_state_untouched()

    # -- rollback --------------------------------------------------------

    def test_rollback_restores_only_the_legacy_regular_units(self):
        self.write_legacy_units()
        self.assertEqual(self.run_helper(MIGRATE).returncode, 0)
        result = self.run_helper(ROLLBACK)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in (LEGACY_SERVICE, LEGACY_TIMER):
            restored = self.unit_dir / name
            self.assertTrue(restored.is_file(), name)
            self.assertFalse(restored.is_symlink(), name)
            self.assertEqual(restored.read_text(encoding="utf-8"), self.rendered(name))
        self.assertFalse(self.enabled_fake(CANONICAL_TIMER))
        self.assertTrue(self.enabled_fake(LEGACY_TIMER))
        self.assertEqual(self.wants_timers(), {LEGACY_TIMER})
        self.assert_corner_state_untouched()
        self.assert_no_shared_service_calls()

    def test_rollback_is_idempotent(self):
        self.write_legacy_units()
        self.assertEqual(self.run_helper(MIGRATE).returncode, 0)
        self.assertEqual(self.run_helper(ROLLBACK).returncode, 0)
        second = self.run_helper(ROLLBACK)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already legacy", second.stdout)
        self.assertEqual(self.wants_timers(), {LEGACY_TIMER})

    def test_rollback_aborts_without_killing_an_active_canonical_service(self):
        self.write_legacy_units()
        self.assertEqual(self.run_helper(MIGRATE).returncode, 0)
        (self.fake_state / f"{CANONICAL_SERVICE}.active").write_text("", encoding="utf-8")
        result = self.run_helper(ROLLBACK)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("active", result.stderr)
        self.assertTrue(self.enabled_fake(CANONICAL_TIMER))
        self.assertTrue((self.unit_dir / LEGACY_TIMER).is_symlink())
        self.assert_corner_state_untouched()

    # -- deploy hook -----------------------------------------------------

    def test_deploy_hook_stage1_reconciles_only_the_legacy_timer(self):
        result = self.run_helper(ENSURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in (LEGACY_SERVICE, LEGACY_TIMER):
            written = self.unit_dir / name
            self.assertTrue(written.is_file(), name)
            self.assertFalse(written.is_symlink(), name)
            self.assertEqual(written.read_text(encoding="utf-8"), self.rendered(name))
        self.assertFalse((self.unit_dir / CANONICAL_TIMER).exists())
        self.assertFalse((self.unit_dir / CANONICAL_SERVICE).exists())
        self.assertTrue(self.enabled_fake(LEGACY_TIMER))
        self.assertEqual(self.wants_timers(), {LEGACY_TIMER})
        self.assert_corner_state_untouched()
        self.assert_no_shared_service_calls()

    def test_deploy_hook_migrates_when_the_reviewed_epoch_is_present(self):
        self.write_legacy_units()
        epoch = self.docroot / MIGRATION_EPOCH
        epoch.parent.mkdir(parents=True, exist_ok=True)
        epoch.write_text("2026-09-22T00:00:00+09:00\n", encoding="utf-8")
        result = self.run_helper(ENSURE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.enabled_fake(CANONICAL_TIMER))
        self.assertFalse(self.enabled_fake(LEGACY_TIMER))
        self.assertEqual(self.wants_timers(), {CANONICAL_TIMER})
        self.assertEqual(os.readlink(self.unit_dir / LEGACY_TIMER), CANONICAL_TIMER)
        self.assertEqual(os.readlink(self.unit_dir / LEGACY_SERVICE), CANONICAL_SERVICE)
        second = self.run_helper(ENSURE)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.wants_timers(), {CANONICAL_TIMER})
        self.assert_corner_state_untouched()

    def test_deploy_hook_refuses_to_write_through_a_canonical_symlink(self):
        self.write_legacy_units()
        self.assertEqual(self.run_helper(MIGRATE).returncode, 0)
        decoy = self.base / "decoy.timer"
        decoy.write_text("DO-NOT-OVERWRITE\n", encoding="utf-8")
        (self.unit_dir / CANONICAL_TIMER).unlink()
        (self.unit_dir / CANONICAL_TIMER).symlink_to(decoy)
        result = self.run_helper(ENSURE)
        self.assertEqual(result.returncode, 22, result.stderr)
        self.assertIn("symlink", result.stderr)
        self.assertEqual(decoy.read_text(encoding="utf-8"), "DO-NOT-OVERWRITE\n")
        self.assert_corner_state_untouched()

    def test_deploy_hook_requires_a_reviewed_regular_epoch_file(self):
        self.write_legacy_units()
        epoch = self.docroot / MIGRATION_EPOCH
        epoch.parent.mkdir(parents=True, exist_ok=True)
        epoch.symlink_to(self.base / "outside-epoch")
        result = self.run_helper(ENSURE)
        self.assertEqual(result.returncode, 41, result.stderr)
        self.assertFalse(self.enabled_fake(CANONICAL_TIMER))
        self.assertFalse((self.unit_dir / LEGACY_TIMER).is_symlink())
        self.assert_corner_state_untouched()

    def test_migration_restores_the_legacy_timer_when_a_tick_starts_during_disable(self):
        self.write_legacy_units()
        result = self.run_helper(
            MIGRATE, FAKE_SYSTEMCTL_ACTIVATE_ON_DISABLE=LEGACY_SERVICE
        )
        self.assertEqual(result.returncode, 33, result.stderr)
        self.assertIn("restored", result.stderr)
        self.assertTrue(self.enabled_fake(LEGACY_TIMER))
        self.assertFalse((self.unit_dir / LEGACY_TIMER).is_symlink())
        self.assertFalse((self.unit_dir / CANONICAL_TIMER).exists())
        self.assert_corner_state_untouched()

    def test_rollback_restores_the_canonical_timer_when_a_tick_starts_during_disable(self):
        self.write_legacy_units()
        self.assertEqual(self.run_helper(MIGRATE).returncode, 0)
        result = self.run_helper(
            ROLLBACK, FAKE_SYSTEMCTL_ACTIVATE_ON_DISABLE=CANONICAL_SERVICE
        )
        self.assertEqual(result.returncode, 33, result.stderr)
        self.assertIn("restored", result.stderr)
        self.assertTrue(self.enabled_fake(CANONICAL_TIMER))
        self.assertTrue((self.unit_dir / LEGACY_TIMER).is_symlink())
        self.assert_corner_state_untouched()

    def test_migration_refuses_unexpected_canonical_unit_drift(self):
        self.write_legacy_units()
        (self.unit_dir / CANONICAL_TIMER).write_text(
            "[Timer]\nUnit=somewhere-else.timer\n", encoding="utf-8"
        )
        result = self.run_helper(MIGRATE)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn("unexpected content", result.stderr)
        self.assertTrue(self.enabled_fake(LEGACY_TIMER))
        self.assertFalse((self.unit_dir / LEGACY_TIMER).is_symlink())
        self.assertFalse((self.unit_dir / CANONICAL_SERVICE).exists())
        self.assert_corner_state_untouched()

    def test_migration_accepts_systemd_names_fallback_for_alias_resolution(self):
        self.write_legacy_units()
        result = self.run_helper(MIGRATE, FAKE_SYSTEMCTL_ID_UNRESOLVED="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.enabled_fake(CANONICAL_TIMER))
        self.assertEqual(self.wants_timers(), {CANONICAL_TIMER})

    def test_helpers_ignore_arbitrary_arguments(self):
        self.write_legacy_units()
        injected = self.run_helper(
            MIGRATE, args=("restart-service", "docich.service", "rm -rf /")
        )
        self.assertEqual(injected.returncode, 0, injected.stderr)
        self.assertTrue(self.enabled_fake(CANONICAL_TIMER))
        self.assertEqual(self.wants_timers(), {CANONICAL_TIMER})
        hook = self.run_helper(ENSURE, args=("--unit", "evil.service"))
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(self.wants_timers(), {CANONICAL_TIMER})
        self.assert_corner_state_untouched()


class UnitScriptContractTests(unittest.TestCase):
    def test_scripts_accept_no_arguments_or_arbitrary_commands(self):
        for script in (ENSURE, MIGRATE, ROLLBACK):
            text = script.read_text(encoding="utf-8")
            self.assertNotIn("eval ", text)
            self.assertNotIn("sudo", text)
            self.assertNotIn("systemctl --user restart", text)
            self.assertNotIn("systemctl --user stop", text)
            self.assertNotIn("docich.service", text)
            self.assertNotIn('> "$unit_dir', text)
            self.assertNotIn("run-soren-live", text)
            self.assertNotIn("corner_rotation.json", text)
            self.assertNotIn("corner-rotation.lock", text)

    def test_migration_helper_path_is_fixed_in_the_deploy_hook(self):
        text = ENSURE.read_text(encoding="utf-8")
        self.assertIn("migrate_corner_rotation_timer.sh", text)
        self.assertIn("corner_rotation_timer_migration_epoch", text)
        self.assertIn('bash "$migration_helper"', text)


if __name__ == "__main__":
    unittest.main()
