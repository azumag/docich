"""The user-bus environment every ``systemd-run --user`` spawn depends on (#947)."""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.procs import user_bus_env  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
