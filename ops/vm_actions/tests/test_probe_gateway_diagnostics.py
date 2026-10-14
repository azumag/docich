import unittest
from unittest import mock

from ops.vm_actions import probe_gateway_diagnostics as probe


SHA = "a" * 40


class ProbeGatewayDiagnosticsTests(unittest.TestCase):
    def test_success_is_silent_zero_path(self):
        with mock.patch.object(probe.gateway, "load_config", return_value={"ok": True}) as load, \
             mock.patch.object(probe.gateway, "diagnostics_result", return_value={"status": "diagnosed"}) as run:
            self.assertEqual(probe.main(["probe", SHA]), 0)
        load.assert_called_once_with(probe.CONFIG_PATH)
        run.assert_called_once_with({"ok": True}, "docich", "production", SHA)

    def test_known_value_errors_have_fixed_bounded_codes(self):
        with mock.patch.object(probe.gateway, "load_config", return_value={}):
            for message, expected in probe._REASON_EXIT.items():
                with self.subTest(message=message):
                    with mock.patch.object(probe.gateway, "diagnostics_result", side_effect=ValueError(message)):
                        self.assertEqual(probe.main(["probe", SHA]), expected)
                        self.assertGreaterEqual(expected, 20)
                        self.assertLess(expected, 32)

    def test_unknown_errors_do_not_escape_or_expose_text(self):
        with mock.patch.object(probe.gateway, "load_config", return_value={}), \
             mock.patch.object(probe.gateway, "diagnostics_result", side_effect=ValueError("private detail")):
            self.assertEqual(probe.main(["probe", SHA]), probe.UNKNOWN_VALUE_ERROR_EXIT)
        with mock.patch.object(probe.gateway, "load_config", side_effect=RuntimeError("private detail")):
            self.assertEqual(probe.main(["probe", SHA]), probe.INTERNAL_ERROR_EXIT)

    def test_invalid_sha_fails_before_config_read(self):
        with mock.patch.object(probe.gateway, "load_config") as load:
            self.assertEqual(probe.main(["probe", "not-a-sha"]), 2)
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
