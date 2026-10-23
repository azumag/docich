import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
ENTRY = ROOT / "ops" / "vm_actions" / "gateway_entry.py"
spec = importlib.util.spec_from_file_location("gateway_entry_stream_profile_test", ENTRY)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class StreamProfileDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _cfg(self, env_text, profile_text):
        soren = self.root / "soren"
        soren.mkdir()
        (soren / ".env").write_text(env_text, encoding="utf-8")
        (soren / "configure_linux_stream_profile.sh").write_text(profile_text, encoding="utf-8")
        return {
            "repos": {
                "docich": {
                    "production": str(self.root / "docich"),
                    "projections": {"games/soviet_now": str(soren)},
                }
            }
        }

    def test_reports_fps_drift_without_secret(self):
        cfg = self._cfg(
            "TWITCH_STREAM_KEY='DO-NOT-PUBLISH'\n"
            "SOREN_DIRECT_STREAM_SIZE='1280x720'\n"
            "SOREN_DIRECT_STREAM_FPS='20'\n"
            "SOREN_DIRECT_STREAM_VIDEO_KBPS='4500'\n",
            "set_env_value SOREN_DIRECT_STREAM_SIZE 1280x720\n"
            "set_env_value SOREN_DIRECT_STREAM_FPS 30\n"
            "set_env_value SOREN_DIRECT_STREAM_VIDEO_KBPS 4500\n",
        )

        result = entry._stream_profile_diagnostics(cfg, "docich")

        self.assertEqual(result, {
            "status": "warn",
            "effective": {"size": "1280x720", "fps": 20, "video_kbps": 4500},
            "profile_default": {"size": "1280x720", "fps": 30, "video_kbps": 4500},
            "drift": True,
            "drift_fields": ["fps"],
            "warning": "stream_profile_drift",
        })
        self.assertNotIn("DO-NOT-PUBLISH", json.dumps(result))

    def test_reports_matching_profile(self):
        cfg = self._cfg(
            "SOREN_DIRECT_STREAM_SIZE=1280x720\n"
            "SOREN_DIRECT_STREAM_FPS=30\n"
            "SOREN_DIRECT_STREAM_VIDEO_KBPS=4500\n",
            "set_env_value SOREN_DIRECT_STREAM_SIZE '1280x720'\n"
            "set_env_value SOREN_DIRECT_STREAM_FPS '30'\n"
            "set_env_value SOREN_DIRECT_STREAM_VIDEO_KBPS '4500'\n",
        )

        result = entry._stream_profile_diagnostics(cfg, "docich")

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["drift"])
        self.assertEqual(result["drift_fields"], [])
        self.assertNotIn("warning", result)

    def test_fails_closed_when_value_missing(self):
        cfg = self._cfg(
            "SOREN_DIRECT_STREAM_SIZE='1280x720'\n"
            "SOREN_DIRECT_STREAM_FPS='30'\n",
            "set_env_value SOREN_DIRECT_STREAM_SIZE 1280x720\n"
            "set_env_value SOREN_DIRECT_STREAM_FPS 30\n"
            "set_env_value SOREN_DIRECT_STREAM_VIDEO_KBPS 4500\n",
        )

        result = entry._stream_profile_diagnostics(cfg, "docich")

        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["drift"])
        self.assertEqual(result["warning"], "stream_profile_comparison_unavailable")
        self.assertEqual(result["missing"], ["effective.video_kbps"])

    def test_augment_promotes_overall_status_to_warn(self):
        cfg = self._cfg(
            "SOREN_DIRECT_STREAM_SIZE='1280x720'\n"
            "SOREN_DIRECT_STREAM_FPS='20'\n"
            "SOREN_DIRECT_STREAM_VIDEO_KBPS='4500'\n",
            "set_env_value SOREN_DIRECT_STREAM_SIZE 1280x720\n"
            "set_env_value SOREN_DIRECT_STREAM_FPS 30\n"
            "set_env_value SOREN_DIRECT_STREAM_VIDEO_KBPS 4500\n",
        )

        class Gateway:
            DIAGNOSTICS_JSON_MAX = 65536

        envelope = {"status": "diagnosed", "diagnostics": {"status": "ok"}}
        out = entry._augment_stream_profile(envelope, cfg, "docich", Gateway)

        self.assertEqual(out["diagnostics"]["status"], "warn")
        self.assertTrue(out["diagnostics"]["stream"]["drift"])
        self.assertEqual(out["diagnostics"]["stream"]["warning"], "stream_profile_drift")


if __name__ == "__main__":
    unittest.main()
