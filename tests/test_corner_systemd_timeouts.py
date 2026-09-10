import re
import tomllib
import unittest
from pathlib import Path


class TestCornerSystemdTimeouts(unittest.TestCase):
    def test_daily_corner_oneshots_have_finite_live_schedule_headroom(self):
        root = Path(__file__).resolve().parents[1]
        live = tomllib.loads((root / "config/docich.soren-live.toml").read_text(encoding="utf-8"))
        cases = (
            ("retro_corner", "scripts/systemd/docich-retro-corner.service"),
            ("paper_corner", "scripts/systemd/docich-paper-corner.service"),
        )

        for section, relative_service in cases:
            with self.subTest(section=section):
                service = (root / relative_service).read_text(encoding="utf-8")
                self.assertNotIn("TimeoutStartSec=infinity", service)
                match = re.search(r"^TimeoutStartSec=(\d+)h$", service, re.MULTILINE)
                self.assertIsNotNone(match, service)
                timeout_sec = int(match.group(1)) * 3600

                cfg = live[section]
                latest_legitimate_finish_sec = (
                    (24 - int(cfg["start_hour"])) * 3600
                    + int(cfg["duration_minutes"]) * 60
                )
                # A scheduled tick may wait for a boundary until end-of-day and
                # then still needs enough time to run the configured segment.
                self.assertGreater(timeout_sec, latest_legitimate_finish_sec)
                # Keep the process-lifetime safety ceiling close to the live
                # schedule so an obsolete/pre-deploy tick cannot linger for hours.
                self.assertLessEqual(timeout_sec - latest_legitimate_finish_sec, 30 * 60)


if __name__ == "__main__":
    unittest.main()
