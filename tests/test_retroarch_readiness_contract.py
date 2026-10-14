"""Focused regression tests for RetroArch coordinator cancel/deadline behavior."""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters import retroarch  # noqa: E402
from docich.game_switch import ReadinessTimeoutError  # noqa: E402


class _CancelOnWait:
    def __init__(self):
        self.cancelled = False
        self.wait_calls = 0

    def is_set(self):
        return self.cancelled

    def wait(self, _timeout):
        self.wait_calls += 1
        self.cancelled = True
        return True


class TestRetroArchReadinessContract(unittest.TestCase):
    def test_udp_wait_is_shorter_than_cancel_grace_and_poll_wait_is_interruptible(self):
        adapter = object.__new__(retroarch.RetroArchCoordinatorAdapter)
        adapter._network_port = lambda: retroarch.NETWORK_CMD_PORT + 1
        cancel = _CancelOnWait()

        with mock.patch("docich.adapters.retroarch.send_ra_cmd", return_value=None) as send:
            with self.assertRaises(ReadinessTimeoutError):
                adapter._probe_network_status(time.monotonic() + 5, cancel)

        self.assertLessEqual(
            send.call_args.kwargs["wait_reply_s"], retroarch.RA_READY_IO_TIMEOUT_S
        )
        self.assertLess(retroarch.RA_READY_IO_TIMEOUT_S, 0.5)
        self.assertEqual(cancel.wait_calls, 1)


if __name__ == "__main__":
    unittest.main()
