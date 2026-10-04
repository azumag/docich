import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

from docich import hanjuku_corner
from docich.config import ConfigError
from docich.corner_rotation import RotationError


class HanjukuCornerFixedFailureTests(unittest.TestCase):
    def _assert_fixed_failure(self, failure, expected):
        stderr = io.StringIO()
        with mock.patch.object(
            hanjuku_corner, "start", side_effect=failure
        ), redirect_stderr(stderr):
            self.assertEqual(hanjuku_corner.main([]), expected)
        self.assertNotIn(str(failure), stderr.getvalue())
        self.assertEqual(
            stderr.getvalue(),
            f"hanjuku_queue_failure={hanjuku_corner.EXIT_FAILURE_REASON[expected]}\n",
        )

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
            with self.subTest(reason_code=reason_code):
                self._assert_fixed_failure(
                    RotationError("private runtime detail", reason_code=reason_code),
                    expected,
                )

    def test_rotation_kind_without_reason_code_uses_fixed_family(self):
        cases = {
            "adapter-state": 78,
            "adapter-timestamp": 79,
            "catalog-mismatch": 80,
            "execution-error": 81,
            "execution-unverified": 82,
            "invalid-state": 83,
            "unexpected": 84,
        }
        for kind, expected in cases.items():
            with self.subTest(kind=kind):
                self._assert_fixed_failure(
                    RotationError("private runtime detail", kind=kind),
                    expected,
                )

    def test_standard_failures_use_fixed_exit_codes(self):
        cases = (
            (PermissionError("/private/runtime/path"), 75),
            (ConfigError("secret-looking config detail"), 76),
            (ValueError("invalid private value"), 85),
            (KeyError("private-key"), 86),
            (TypeError("private type detail"), 87),
            (ModuleNotFoundError("private dependency name"), 88),
            (RuntimeError("provider response must not escape"), 77),
        )
        for failure, expected in cases:
            with self.subTest(failure=type(failure).__name__):
                self._assert_fixed_failure(failure, expected)

    def test_allowlisted_adapter_module_uses_fixed_family(self):
        adapter_error_type = type(
            "RetroCornerError",
            (RuntimeError,),
            {"__module__": "docich.retro_corner"},
        )
        self._assert_fixed_failure(
            adapter_error_type("private adapter detail"),
            89,
        )

    def test_unlisted_docich_module_does_not_enter_adapter_family(self):
        other_error_type = type(
            "UnrelatedError",
            (RuntimeError,),
            {"__module__": "docich.unrelated"},
        )
        self._assert_fixed_failure(
            other_error_type("private unrelated detail"),
            77,
        )

    def test_success_keeps_queue_contract(self):
        with mock.patch.object(
            hanjuku_corner, "start", return_value={"status": "queued", "request_id": "opaque"}
        ):
            self.assertEqual(hanjuku_corner.main([]), 0)


if __name__ == "__main__":
    unittest.main()
