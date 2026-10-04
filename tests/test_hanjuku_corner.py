import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

from docich import hanjuku_corner
from docich.config import ConfigError
from docich.corner_rotation import RotationError


class HanjukuCornerFixedFailureTests(unittest.TestCase):
    def test_rotation_failures_use_fixed_exit_codes(self):
        cases = {
            "rotation_disabled": 70,
            "recovery_required": 71,
            "clock_regressed": 72,
            "hanjuku_not_eligible": 73,
            "manual_queue_conflict": 74,
            "state_unavailable": 75,
            "unknown_future_code": 77,
        }
        for reason_code, expected in cases.items():
            error = RotationError("private runtime detail", reason_code=reason_code)
            stderr = io.StringIO()
            with self.subTest(reason_code=reason_code), mock.patch.object(
                hanjuku_corner, "start", side_effect=error
            ), redirect_stderr(stderr):
                self.assertEqual(hanjuku_corner.main([]), expected)
            self.assertNotIn("private runtime detail", stderr.getvalue())
            self.assertEqual(
                stderr.getvalue(),
                f"hanjuku_queue_failure={hanjuku_corner.EXIT_FAILURE_REASON[expected]}\n",
            )

    def test_non_rotation_failures_use_fixed_exit_codes(self):
        cases = (
            (PermissionError("/private/runtime/path"), 75),
            (ConfigError("secret-looking config detail"), 76),
            (RuntimeError("provider response must not escape"), 77),
        )
        for failure, expected in cases:
            stderr = io.StringIO()
            with self.subTest(failure=type(failure).__name__), mock.patch.object(
                hanjuku_corner, "start", side_effect=failure
            ), redirect_stderr(stderr):
                self.assertEqual(hanjuku_corner.main([]), expected)
            self.assertNotIn(str(failure), stderr.getvalue())
            self.assertEqual(
                stderr.getvalue(),
                f"hanjuku_queue_failure={hanjuku_corner.EXIT_FAILURE_REASON[expected]}\n",
            )

    def test_success_keeps_queue_contract(self):
        with mock.patch.object(
            hanjuku_corner, "start", return_value={"status": "queued", "request_id": "opaque"}
        ):
            self.assertEqual(hanjuku_corner.main([]), 0)


if __name__ == "__main__":
    unittest.main()
