import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENTRY = ROOT / "ops" / "vm_actions" / "gateway_entry.py"
spec = importlib.util.spec_from_file_location("gateway_entry_stream_profile_test", ENTRY)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def _cfg(tmp_path, env_text, profile_text):
    soren = tmp_path / "soren"
    soren.mkdir()
    (soren / ".env").write_text(env_text, encoding="utf-8")
    (soren / "configure_linux_stream_profile.sh").write_text(profile_text, encoding="utf-8")
    cfg = {
        "repos": {
            "docich": {
                "production": str(tmp_path / "docich"),
                "projections": {"games/soviet_now": str(soren)},
            }
        }
    }
    return cfg


def test_stream_profile_diagnostics_reports_fps_drift_without_secret(tmp_path):
    cfg = _cfg(
        tmp_path,
        "TWITCH_STREAM_KEY='DO-NOT-PUBLISH'\n"
        "SOREN_DIRECT_STREAM_SIZE='1280x720'\n"
        "SOREN_DIRECT_STREAM_FPS='20'\n"
        "SOREN_DIRECT_STREAM_VIDEO_KBPS='4500'\n",
        "set_env_value SOREN_DIRECT_STREAM_SIZE 1280x720\n"
        "set_env_value SOREN_DIRECT_STREAM_FPS 30\n"
        "set_env_value SOREN_DIRECT_STREAM_VIDEO_KBPS 4500\n",
    )

    result = entry._stream_profile_diagnostics(cfg, "docich")

    assert result == {
        "status": "warn",
        "effective": {"size": "1280x720", "fps": 20, "video_kbps": 4500},
        "profile_default": {"size": "1280x720", "fps": 30, "video_kbps": 4500},
        "drift": True,
        "drift_fields": ["fps"],
        "warning": "stream_profile_drift",
    }
    assert "DO-NOT-PUBLISH" not in json.dumps(result)


def test_stream_profile_diagnostics_reports_matching_profile(tmp_path):
    cfg = _cfg(
        tmp_path,
        "SOREN_DIRECT_STREAM_SIZE=1280x720\n"
        "SOREN_DIRECT_STREAM_FPS=30\n"
        "SOREN_DIRECT_STREAM_VIDEO_KBPS=4500\n",
        "set_env_value SOREN_DIRECT_STREAM_SIZE '1280x720'\n"
        "set_env_value SOREN_DIRECT_STREAM_FPS '30'\n"
        "set_env_value SOREN_DIRECT_STREAM_VIDEO_KBPS '4500'\n",
    )

    result = entry._stream_profile_diagnostics(cfg, "docich")

    assert result["status"] == "ok"
    assert result["drift"] is False
    assert result["drift_fields"] == []
    assert "warning" not in result


def test_stream_profile_diagnostics_fails_closed_when_value_missing(tmp_path):
    cfg = _cfg(
        tmp_path,
        "SOREN_DIRECT_STREAM_SIZE='1280x720'\n"
        "SOREN_DIRECT_STREAM_FPS='30'\n",
        "set_env_value SOREN_DIRECT_STREAM_SIZE 1280x720\n"
        "set_env_value SOREN_DIRECT_STREAM_FPS 30\n"
        "set_env_value SOREN_DIRECT_STREAM_VIDEO_KBPS 4500\n",
    )

    result = entry._stream_profile_diagnostics(cfg, "docich")

    assert result["status"] == "unavailable"
    assert result["drift"] is None
    assert result["warning"] == "stream_profile_comparison_unavailable"
    assert result["missing"] == ["effective.video_kbps"]


def test_augment_stream_profile_promotes_overall_status_to_warn(tmp_path):
    cfg = _cfg(
        tmp_path,
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

    assert out["diagnostics"]["status"] == "warn"
    assert out["diagnostics"]["stream"]["drift"] is True
    assert out["diagnostics"]["stream"]["warning"] == "stream_profile_drift"
