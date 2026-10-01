"""The user-bus environment every ``systemd-run --user`` spawn depends on (#947)."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.procs import OutputLimitExceeded, run_bounded_output, user_bus_env  # noqa: E402


class UserBusEnvTest(unittest.TestCase):
    def test_missing_runtime_dir_defaults_to_the_uid_runtime_dir(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            env = user_bus_env()
        self.assertEqual(env["XDG_RUNTIME_DIR"], f"/run/user/{os.getuid()}")

    def test_configured_values_are_never_overwritten(self):
        source = {
            "XDG_RUNTIME_DIR": "/tmp/runtime",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/runtime/custom-bus",
        }
        env = user_bus_env(source)
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/tmp/runtime")
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/tmp/runtime/custom-bus")

    def test_session_bus_address_follows_the_runtime_dir_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            env = user_bus_env({"XDG_RUNTIME_DIR": str(runtime)})
            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)
            socket = runtime / "bus"
            socket.write_bytes(b"")
            env = user_bus_env({"XDG_RUNTIME_DIR": str(runtime)})
            self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], f"unix:path={socket}")

    def test_input_mapping_is_not_mutated(self):
        source: dict = {}
        user_bus_env(source)
        self.assertEqual(source, {})

    def test_runtime_dir_without_a_bus_socket_stays_address_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            os.chmod(runtime, stat.S_IRWXU)
            env = user_bus_env({"XDG_RUNTIME_DIR": str(runtime)})
            self.assertEqual(env["XDG_RUNTIME_DIR"], str(runtime))
            self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)


class BoundedOutputTest(unittest.TestCase):
    def test_output_at_the_cap_is_returned(self):
        result = run_bounded_output(
            [sys.executable, "-c", "print('abcd', end='')"],
            max_output_bytes=4,
            timeout=2,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"abcd")

    def test_output_over_the_cap_is_stopped_and_rejected(self):
        with self.assertRaises(OutputLimitExceeded):
            run_bounded_output(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.write('x' * 1000000); sys.stdout.flush()",
                ],
                max_output_bytes=32,
                timeout=2,
            )

    def test_timeout_kills_a_slow_child(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run_bounded_output(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                max_output_bytes=32,
                timeout=0.05,
            )

    def test_bool_is_not_accepted_as_a_size_or_timeout(self):
        with self.assertRaises(ValueError):
            run_bounded_output(
                [sys.executable, "-c", "pass"],
                max_output_bytes=True,
                timeout=1,
            )
        with self.assertRaises(ValueError):
            run_bounded_output(
                [sys.executable, "-c", "pass"],
                max_output_bytes=1,
                timeout=True,
            )


if __name__ == "__main__":
    unittest.main()
