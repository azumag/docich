"""Race injection at real syscall boundaries, with child-process writers."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("projection_race_gateway", Path(__file__).resolve().parents[1] / "gateway.py")
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)
pio = gw.projection_io


class ProjectionRaceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="projection-race-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "root"
        self.directory = self.root / "prompts"
        self.directory.mkdir(parents=True)
        self.path = self.directory / "ops_brief.md"
        self.path.write_bytes(b"old")
        self.path.chmod(0o644)
        self.binding = pio.ProjectionPath(self.root, "prompts/ops_brief.md")
        self.addCleanup(self.binding.close)
        self.change = {"path": self.path, "_binding": self.binding,
                       "old_data": b"old", "old_mode": 0o644,
                       "new_data": b"new", "new_mode": 0o644}

    def child_change(self, action):
        subprocess.run([sys.executable, "-c",
                        "from pathlib import Path; import sys; p=Path(sys.argv[1]); " + action,
                        str(self.path)], check=True)

    def claim_race(self, action):
        real_rename = os.rename

        def race(src, dst, **kwargs):
            self.child_change(action)
            return real_rename(src, dst, **kwargs)

        with mock.patch.object(pio.os, "rename", side_effect=race):
            with self.assertRaises((OSError, ValueError)):
                gw._apply_projection([self.change])

    def test_writer_after_gateway_assertion_is_not_overwritten(self):
        real_assert = gw._assert_projection_current

        def race(changes, prefix):
            real_assert(changes, prefix)
            self.child_change("p.write_bytes(b'concurrent')")

        with mock.patch.object(gw, "_assert_projection_current", side_effect=race):
            with self.assertRaises(ValueError):
                gw._apply_projection([self.change])
        self.assertEqual(self.path.read_bytes(), b"concurrent")

    def test_edit_at_claim_after_last_metadata_check_is_preserved(self):
        self.claim_race("p.write_bytes(b'concurrent')")
        self.assertEqual(self.path.read_bytes(), b"concurrent")

    def test_chmod_at_claim_is_preserved(self):
        self.claim_race("p.chmod(0o600)")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.read_bytes(), b"old")

    def test_delete_at_claim_is_not_resurrected_by_rollback(self):
        self.claim_race("p.unlink()")
        with self.assertRaises(ValueError):
            gw._rollback_projection([self.change])
        self.assertFalse(self.path.exists())

    def test_projection_delete_does_not_remove_concurrent_edit(self):
        self.change["new_data"] = self.change["new_mode"] = None
        self.claim_race("p.write_bytes(b'concurrent')")
        self.assertEqual(self.path.read_bytes(), b"concurrent")

    def test_projection_mode_change_does_not_clobber_concurrent_chmod(self):
        self.change["new_data"] = b"old"
        self.change["new_mode"] = 0o755
        self.claim_race("p.chmod(0o600)")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_concurrent_creation_at_publish_is_never_overwritten(self):
        real_publish = pio.publish

        def race(source_fd, source_name, destination_fd, destination_name):
            if source_name == "after":
                self.child_change("p.write_bytes(b'concurrent creation')")
            return real_publish(source_fd, source_name, destination_fd, destination_name)

        with mock.patch.object(pio, "publish", side_effect=race):
            with self.assertRaises(FileExistsError):
                gw._apply_projection([self.change])
        self.assertEqual(self.path.read_bytes(), b"concurrent creation")
        before = list(self.directory.glob(".vmops-projection-*/before"))
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0].read_bytes(), b"old")

    def test_symlink_directory_swap_after_planning_never_writes_outside_root(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / self.path.name).write_bytes(b"outside")
        self.directory.rename(self.root / "original-prompts")
        self.directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            gw._apply_projection([self.change])
        self.assertEqual((outside / self.path.name).read_bytes(), b"outside")
        self.assertEqual((self.root / "original-prompts" / self.path.name).read_bytes(), b"old")
        self.assertEqual(list(outside.iterdir()), [outside / self.path.name])

    def test_directory_swap_immediately_before_rename_uses_pinned_fd(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / self.path.name).write_bytes(b"outside")
        real_rename = os.rename

        def race(src, dst, **kwargs):
            real_rename(self.directory, self.root / "original-prompts")
            self.directory.symlink_to(outside, target_is_directory=True)
            return real_rename(src, dst, **kwargs)

        with mock.patch.object(pio.os, "rename", side_effect=race):
            with self.assertRaises((OSError, ValueError)):
                gw._apply_projection([self.change])
        self.assertEqual((outside / self.path.name).read_bytes(), b"outside")
        self.assertEqual(list(outside.iterdir()), [outside / self.path.name])
        self.assertEqual(next((self.root / "original-prompts").glob(".vmops-projection-*/before")).read_bytes(), b"old")

    def test_dangling_symlink_at_claim_is_preserved_without_following(self):
        self.claim_race("p.unlink(); p.symlink_to('../absent')")
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(os.readlink(self.path), "../absent")
        self.assertFalse((self.root / "absent").exists())

    def test_old_open_fd_write_is_retained_and_detected(self):
        with self.path.open("r+b") as old:
            real_publish = pio.publish

            def race(*args):
                result = real_publish(*args)
                if args[1] == "after":
                    old.write(b"concurrent via old fd")
                    old.flush()
                return result

            with mock.patch.object(pio, "publish", side_effect=race):
                with self.assertRaises(ValueError):
                    gw._apply_projection([self.change])
            with self.assertRaises(ValueError):
                gw._rollback_projection([self.change])
        backup = next(self.directory.glob(".vmops-projection-*/before"))
        self.assertEqual(backup.read_bytes(), b"concurrent via old fd")

    def test_old_open_fd_change_after_success_is_still_recoverable(self):
        with self.path.open("r+b") as old:
            gw._apply_projection([self.change])
            old.write(b"late writer")
            old.flush()
        self.assertEqual(next(self.directory.glob(".vmops-projection-*/before")).read_bytes(), b"late writer")
        with self.assertRaises(ValueError):
            gw._assert_projection_current([self.change], "new")

    def test_normal_write_delete_mode_and_rollback(self):
        for new_data, new_mode in ((b"new", 0o644), (None, None), (b"old", 0o755)):
            with self.subTest(new_data=new_data):
                self.change.update(new_data=new_data, new_mode=new_mode)
                gw._apply_projection([self.change])
                gw._assert_projection_current([self.change], "new")
                gw._rollback_projection([self.change])
                self.assertEqual(self.path.read_bytes(), b"old")
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)

    def test_missing_parent_creation_and_new_file_rollback_are_bound(self):
        bound = pio.ProjectionPath(self.root, "new/nested/file")
        self.addCleanup(bound.close)
        change = {"path": self.root / "new/nested/file", "_binding": bound,
                  "old_data": None, "old_mode": None, "new_data": b"created", "new_mode": 0o644}
        gw._apply_projection([change])
        self.assertEqual(change["path"].read_bytes(), b"created")
        gw._rollback_projection([change])
        self.assertFalse(change["path"].exists())

    def test_rollback_race_preserves_competing_writer(self):
        gw._apply_projection([self.change])
        real_rename = os.rename

        def race(src, dst, **kwargs):
            self.child_change("p.write_bytes(b'changed during rollback')")
            return real_rename(src, dst, **kwargs)

        with mock.patch.object(pio.os, "rename", side_effect=race):
            with self.assertRaises(ValueError):
                gw._rollback_projection([self.change])
        self.assertEqual(self.path.read_bytes(), b"changed during rollback")
