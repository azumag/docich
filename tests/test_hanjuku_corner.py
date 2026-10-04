import unittest
from unittest import mock

from docich import hanjuku_corner
from docich.config import ConfigError
from docich.corner_rotation import RotationError


class HanjukuCornerFixedFailureTests(unittest.TestCase):
    def test_rotation_failures_use_fixed_exit_codes(self):
        cases = {
            "common corner rotation is disabled": 70,
            "corner recovery required before manual reservation": 71,
            "clock regressed": 72,
            "no unique eligible manual corner": 73,
            "another manual corner is already queued": 74,
            "invalid manual queue inbox": 75,
            "conflicting manual queue ownership": 74,
            "new internal detail that must stay private": 77,
        }
        for detail, expected in cases.items():
            with self.subTest(detail=detail), mock.patch.object(
                hanjuku_corner, "start", side_effect=RotationError(detail)
            ):
                self.assertEqual(hanjuku_corner.main([]), expected)

    def test_non_rotation_failures_use_fixed_exit_codes(self):
        cases = (
            (PermissionError("/private/runtime/path"), 75),
            (ConfigError("secret-looking config detail"), 76),
            (RuntimeError("provider response must not escape"), 77),
        )
        for failure, expected in cases:
            with self.subTest(failure=type(failure).__name__), mock.patch.object(
                hanjuku_corner, "start", side_effect=failure
            ):
                self.assertEqual(hanjuku_corner.main([]), expected)

    def test_success_keeps_queue_contract(self):
        with mock.patch.object(
            hanjuku_corner, "start", return_value={"status": "queued", "request_id": "opaque"}
        ):
            self.assertEqual(hanjuku_corner.main([]), 0)


if __name__ == "__main__":
    unittest.main()
