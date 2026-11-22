"""Read-only RTMP output safety contract, independent of soviet_now."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.stream_output_guard import (  # noqa: E402
    LOOPBACK_RTMP_OUTPUT_CONTRACT_VERSION,
    validate_loopback_rtmp_url,
)


class StreamOutputGuardTest(unittest.TestCase):
    def test_contract_is_versioned_and_has_no_live_side_effects(self):
        self.assertEqual(LOOPBACK_RTMP_OUTPUT_CONTRACT_VERSION, 1)

    def test_local_relay_is_the_only_allowed_destination(self):
        for value in (
            "rtmp://127.0.0.1:1935/soren/live",
            "rtmp://localhost:1935/live",
            "rtmp://[::1]:1935/soren/live",
        ):
            with self.subTest(url=value):
                self.assertEqual(validate_loopback_rtmp_url(value), value)

    def test_external_destinations_credentials_and_query_data_are_refused(self):
        for value, error in (
            ("rtmps://127.0.0.1/live", "must use rtmp://"),
            ("rtmp://stream.example/live", "must target loopback"),
            ("rtmp://127.0.0.2/live", "must target loopback"),
            ("rtmp://user:token@localhost/live", "must not contain credentials"),
            ("rtmp://localhost", "include an application/stream path"),
            ("rtmp://localhost/", "include an application/stream path"),
            ("rtmp://localhost/live?key=secret", "must not include query or fragment"),
            ("rtmp://localhost/live#secret", "must not include query or fragment"),
            ("rtmp://[::1/live", "valid loopback RTMP URL"),
        ):
            with self.subTest(url=value):
                with self.assertRaisesRegex(ValueError, error):
                    validate_loopback_rtmp_url(value)

    def test_no_submodule_or_runtime_required(self):
        text = (ROOT / "src/docich/stream_output_guard.py").read_text(encoding="utf-8")
        self.assertNotIn("games/soviet_now", text)
        self.assertNotIn("subprocess", text)
        self.assertNotIn("socket.", text)


if __name__ == "__main__":
    unittest.main()
