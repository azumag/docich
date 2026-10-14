"""Run the real deploy hook with isolated units and a strict systemctl stub.

The rotation templates/migration helper are fixtures, not a simulation of the
real migration: its existing execution suite remains test_corner_rotation_migration.
No systemd service, timer, AI provider or production state is touched here.
"""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
ENSURE = ROOT / "ops/vm_actions/ensure_corner_rotation_timer.sh"
SERVICE = "docich-paper-corner.service"
TIMER = "docich-paper-corner.timer"
DROP_IN = "50-docich-paper-ai.conf"
ENV_LINE = "EnvironmentFile=-%h/.config/docich/paper-ai.env"
EXPECTED = f"[Service]\n{ENV_LINE}\n"

SYSTEMCTL = r"""#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$CALLS"
[[ "$1" == --user ]] || exit 91
case " $* " in *docich-paper-corner*) exit 92 ;; esac
unit="${@: -1}"
case "$2" in
  daemon-reload)
    if [[ "${EXPECT_PAPER_DROP_IN:-0}" == 1 ]]; then
      grep -Fxq 'EnvironmentFile=-%h/.config/docich/paper-ai.env' \
        "$UNIT_DIR/docich-paper-corner.service.d/50-docich-paper-ai.conf"
    fi
    [[ "${FAIL_RELOAD:-0}" != 1 ]]
    ;;
  enable|is-enabled|is-active)
    [[ "$unit" == docich-retro-corner.timer || "$unit" == docich-corner-rotation.timer ]]
    ;;
  show)
    [[ "$unit" == docich-retro-corner.service || "$unit" == docich-retro-corner.timer ]]
    printf 'Id=%s\n' "$(readlink "$UNIT_DIR/$unit")"
    ;;
  *) exit 93 ;;
esac
"""


class PaperCornerEnvironmentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="paper-unit-env-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.home = self.base / "home"
        self.units = self.home / ".config/systemd/user"
        self.units.mkdir(parents=True)
        self.docroot = self.base / "docich"
        self.templates = self.docroot / "scripts/systemd"
        self.templates.mkdir(parents=True)
        for stem in ("docich-retro-corner", "docich-corner-rotation"):
            for suffix in ("service", "timer"):
                (self.templates / f"{stem}.{suffix}").write_text(
                    "# rotation fixture __DOCICH_ROOT__\n", encoding="utf-8"
                )
        self.bin = self.base / "bin"
        self.bin.mkdir()
        ctl = self.bin / "systemctl"
        ctl.write_text(SYSTEMCTL, encoding="utf-8")
        ctl.chmod(0o755)
        self.log = self.base / "systemctl.log"
        self.log.write_text("", encoding="utf-8")
        # A real user pause and capability file are never inputs to this hook.
        self.pause = self.docroot / "run/corners/paper.paused"
        self.pause.parent.mkdir(parents=True)
        self.pause.write_bytes(b"keep-user-pause\n")
        self.capability = self.home / ".config/docich/paper-ai.env"
        self.capability.parent.mkdir(parents=True)
        self.capability.write_bytes(b"DO_NOT_READ_OR_PRINT=test-sentinel\n")
        self.capability.chmod(0o600)

    @property
    def service(self):
        return self.units / SERVICE

    @property
    def drop_in(self):
        return self.units / f"{SERVICE}.d" / DROP_IN

    def installed_service(self):
        # Deliberately not the current template: preserve hand-installed
        # WorkingDirectory/ExecStart and other settings rather than replacing it.
        self.service.write_text(
            "[Service]\nType=oneshot\nWorkingDirectory=/old/reviewed/path\n"
            "ExecStart=/bin/true\nEnvironment=KEEP_LOCAL_SETTING=1\n",
            encoding="utf-8",
        )

    def run_hook(self, **overrides):
        env = dict(os.environ)
        env.pop("XDG_CONFIG_HOME", None)
        env.update(
            HOME=str(self.home), DOCICH_PROD_ROOT=str(self.docroot),
            XDG_RUNTIME_DIR=str(self.base / "runtime"),
            PATH=f"{self.bin}:{os.defpath}", CALLS=str(self.log),
            UNIT_DIR=str(self.units),
        )
        env.update(overrides)
        return subprocess.run(
            ["bash", str(ENSURE)], env=env, text=True,
            capture_output=True, timeout=20, check=False,
        )

    def assert_preserved(self, result):
        self.assertEqual(self.pause.read_bytes(), b"keep-user-pause\n")
        self.assertEqual(self.capability.read_bytes(), b"DO_NOT_READ_OR_PRINT=test-sentinel\n")
        self.assertNotIn("test-sentinel", result.stdout + result.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertNotIn("docich-paper-corner", calls)
        for verb in (" restart ", " stop ", " unmask ", " disable "):
            self.assertNotIn(verb, calls)
        self.assertFalse(list(self.units.rglob(".paper-ai.*")))

    def assert_reconciled(self):
        self.installed_service()
        original = self.service.read_bytes()
        result = self.run_hook(EXPECT_PAPER_DROP_IN="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.drop_in.read_text(encoding="utf-8"), EXPECTED)
        self.assertEqual(stat.S_IMODE(self.drop_in.stat().st_mode), 0o644)
        self.assertEqual(self.service.read_bytes(), original)
        self.assertIn("--user daemon-reload", self.log.read_text(encoding="utf-8"))
        self.assert_preserved(result)

    def test_legacy_rotation_path_reconciles_existing_paper_service(self):
        self.assert_reconciled()

    def test_already_canonical_path_reconciles_existing_paper_service(self):
        for suffix in ("service", "timer"):
            (self.units / f"docich-retro-corner.{suffix}").symlink_to(
                f"docich-corner-rotation.{suffix}"
            )
        self.assert_reconciled()

    def test_epoch_path_also_reconciles_before_reload(self):
        helpers = self.docroot / "ops/vm_actions"
        helpers.mkdir(parents=True)
        (helpers / "corner_rotation_timer_migration_epoch").write_text("reviewed\n")
        # Only exercises the deploy hook's epoch branch, not migration itself.
        (helpers / "migrate_corner_rotation_timer.sh").write_text(
            '#!/bin/bash\nset -eu\nfor suffix in service timer; do\n'
            '  ln -s "docich-corner-rotation.$suffix" '
            '"$UNIT_DIR/docich-retro-corner.$suffix"\ndone\n', encoding="utf-8"
        )
        self.assert_reconciled()

    def test_repeat_deploy_keeps_identical_drop_in_inode_and_mtime(self):
        self.assert_reconciled()
        original = self.drop_in.stat()
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        current = self.drop_in.stat()
        self.assertEqual((current.st_ino, current.st_mtime_ns),
                         (original.st_ino, original.st_mtime_ns))
        self.assert_preserved(result)

    def test_missing_service_is_not_installed_or_enabled(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.service.exists())
        self.assertFalse(self.drop_in.parent.exists())
        self.assertFalse((self.units / TIMER).exists())
        self.assert_preserved(result)

    def test_masked_service_stays_masked(self):
        self.service.symlink_to("/dev/null")
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.readlink(self.service), "/dev/null")
        self.assertFalse(self.drop_in.parent.exists())
        self.assert_preserved(result)

    def test_disabled_or_masked_timer_and_running_service_are_not_operated(self):
        self.installed_service()
        timer = self.units / TIMER
        timer.symlink_to("/dev/null")
        running = self.base / "running-paper-service"
        running.write_bytes(b"pid-and-start-time-sentinel\n")
        result = self.run_hook(EXPECT_PAPER_DROP_IN="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(os.readlink(timer), "/dev/null")
        self.assertEqual(running.read_bytes(), b"pid-and-start-time-sentinel\n")
        self.assertFalse((self.units / "timers.target.wants" / TIMER).exists())
        self.assert_preserved(result)

    def test_unexpected_service_symlinks_fail_without_following_them(self):
        target = self.base / "external.service"
        target.write_text("do-not-change\n", encoding="utf-8")
        for name in (target, self.base / "absent.service"):
            with self.subTest(target=name.name):
                self.service.unlink(missing_ok=True)
                self.service.symlink_to(name)
                result = self.run_hook()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(os.readlink(self.service), str(name))
                self.assertEqual(target.read_text(), "do-not-change\n")
                self.assertFalse(self.drop_in.parent.exists())
                self.assertEqual(self.log.read_text(), "")
                self.service.unlink()

    def test_service_directory_is_rejected(self):
        self.service.mkdir()
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.service.is_dir())
        self.assertFalse(self.drop_in.parent.exists())
        self.assertEqual(self.log.read_text(), "")

    def test_symlinked_drop_in_directory_is_not_followed(self):
        self.installed_service()
        external = self.base / "external"
        external.mkdir()
        self.drop_in.parent.symlink_to(external, target_is_directory=True)
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(external.iterdir()), [])
        self.assertTrue(self.drop_in.parent.is_symlink())
        self.assertEqual(self.log.read_text(), "")

    def test_symlinked_drop_in_is_not_followed(self):
        self.installed_service()
        self.drop_in.parent.mkdir()
        target = self.base / "external.conf"
        target.write_text("do-not-change\n", encoding="utf-8")
        for name in (target, self.base / "absent.conf", Path("/dev/null")):
            with self.subTest(target=name.name):
                self.drop_in.unlink(missing_ok=True)
                self.drop_in.symlink_to(name)
                result = self.run_hook()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.read_text(), "do-not-change\n")
                self.assertEqual(os.readlink(self.drop_in), str(name))
                self.assertEqual(self.log.read_text(), "")
                self.drop_in.unlink()

    def test_conflicting_owned_drop_in_is_not_overwritten(self):
        self.installed_service()
        self.drop_in.parent.mkdir()
        self.drop_in.write_text("[Service]\nEnvironment=LOCAL=1\n", encoding="utf-8")
        original = self.drop_in.read_bytes()
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected", result.stderr)
        self.assertEqual(self.drop_in.read_bytes(), original)
        self.assertEqual(self.log.read_text(), "")
        self.assert_preserved(result)

    def test_non_directory_drop_in_parent_is_rejected(self):
        self.installed_service()
        self.drop_in.parent.write_text("keep\n", encoding="utf-8")
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.drop_in.parent.read_text(), "keep\n")
        self.assertEqual(self.log.read_text(), "")

    def test_directory_at_owned_drop_in_path_is_rejected(self):
        self.installed_service()
        self.drop_in.mkdir(parents=True)
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.drop_in.is_dir())
        self.assertEqual(list(self.drop_in.iterdir()), [])
        self.assertEqual(self.log.read_text(), "")

    def test_reload_failure_is_not_reported_as_success(self):
        self.installed_service()
        result = self.run_hook(FAIL_RELOAD="1", EXPECT_PAPER_DROP_IN="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.drop_in.read_text(), EXPECTED)
        self.assertNotIn("--user enable", self.log.read_text())
        self.assert_preserved(result)

    def test_custom_xdg_config_home_is_respected(self):
        default_units = self.units
        self.units = self.base / "custom-config/systemd/user"
        self.units.mkdir(parents=True)
        self.installed_service()
        result = self.run_hook(XDG_CONFIG_HOME=str(self.base / "custom-config"),
                               EXPECT_PAPER_DROP_IN="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.drop_in.read_text(), EXPECTED)
        self.assertEqual(list(default_units.iterdir()), [])
        self.assert_preserved(result)

    def test_drop_in_keeps_the_reviewed_service_template_capability_contract(self):
        template = (ROOT / "scripts/systemd" / SERVICE).read_text(encoding="utf-8")
        self.assertEqual(template.splitlines().count(ENV_LINE), 1)
        self.assert_reconciled()

    def test_failed_atomic_install_removes_its_temporary_file(self):
        self.installed_service()
        original = self.service.read_bytes()
        move = self.bin / "mv"
        move.write_text("#!/bin/sh\nexit 44\n", encoding="utf-8")
        move.chmod(0o755)
        result = self.run_hook()
        self.assertEqual(result.returncode, 44)
        self.assertFalse(self.drop_in.exists())
        self.assertEqual(self.service.read_bytes(), original)
        self.assertEqual(self.log.read_text(), "")
        self.assert_preserved(result)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze is unavailable")
    def test_systemd_accepts_the_service_and_generated_drop_in(self):
        self.assert_reconciled()
        env = dict(os.environ)
        paths = [str(self.units)]
        paths.extend(path for path in ("/usr/lib/systemd/system", "/lib/systemd/system",
                                       "/etc/systemd/system") if Path(path).is_dir())
        env["SYSTEMD_UNIT_PATH"] = ":".join(paths)
        result = subprocess.run(
            ["systemd-analyze", "verify", str(self.service)], env=env,
            text=True, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_capability_file_stays_missing(self):
        self.installed_service()
        self.capability.unlink()
        result = self.run_hook(EXPECT_PAPER_DROP_IN="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.drop_in.read_text(), EXPECTED)
        self.assertFalse(self.capability.exists())
        self.assertEqual(self.pause.read_bytes(), b"keep-user-pause\n")
        self.assertNotIn("docich-paper-corner", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
