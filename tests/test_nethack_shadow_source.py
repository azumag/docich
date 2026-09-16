from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docich.nethack_shadow_source import (
    CommandShadowSource,
    ShadowSourceConfig,
    ShadowSourceWriter,
    SourceCaptureResult,
    load_shadow_source_config,
    snapshot_to_dict,
)
from docich.nethack_shadow import parse_shadow_snapshot


def snapshot_payload(*, source="test-source", captured_at=123.0) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": source,
        "captured_at": captured_at,
        "public": {
            "message": "msg",
            "map_rows": ["..@.."],
            "player": [2, 0],
            "vitals": {
                "hp": 10,
                "hp_max": 10,
                "power": 4,
                "power_max": 4,
                "ac": 5,
                "experience_level": 2,
                "dungeon_level": 3,
                "gold": 7,
                "turn": 12,
            },
            "conditions": [],
            "prompt": "none",
        },
    }


class FakeSource:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def capture(self):
        self.calls += 1
        return self.result


class TestShadowSourceConfig(unittest.TestCase):
    def test_disabled_config_needs_no_command(self) -> None:
        game = SimpleNamespace(raw={"nethack": {"shadow_source": {"enabled": False, "command": []}}})
        cfg = load_shadow_source_config(game)
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.command, ())

    def test_enabled_config_requires_bounded_command(self) -> None:
        with self.assertRaises(ValueError):
            load_shadow_source_config(
                SimpleNamespace(raw={"nethack": {"shadow_source": {"enabled": True, "command": []}}})
            )
        with self.assertRaises(ValueError):
            load_shadow_source_config(
                SimpleNamespace(
                    raw={
                        "nethack": {
                            "shadow_source": {
                                "enabled": True,
                                "command": ["probe"],
                                "timeout_s": 31,
                            }
                        }
                    }
                )
            )


class TestCommandShadowSource(unittest.TestCase):
    def test_valid_source_output_is_strictly_parsed(self) -> None:
        seen = {}

        def runner(command, **kwargs):
            seen["command"] = command
            seen["input"] = kwargs["input"]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(snapshot_payload()),
                stderr="",
            )

        source = CommandShadowSource(["probe"], runner=runner)
        result = source.capture()
        self.assertEqual(result.status, "snapshot")
        self.assertEqual(result.snapshot.source, "test-source")
        self.assertEqual(seen["command"], ["probe"])
        request = json.loads(seen["input"])
        self.assertEqual(request["request"], "public_shadow_snapshot")
        self.assertIn("no hidden map", request["constraints"])

    def test_hidden_field_timeout_nonzero_and_size_fail_closed(self) -> None:
        hidden = snapshot_payload()
        hidden["public"]["monster_id"] = 99

        def hidden_runner(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(hidden), stderr="")

        result = CommandShadowSource(["probe"], runner=hidden_runner).capture()
        self.assertEqual(result.status, "error")
        self.assertIn("non-public", result.error)

        def timeout_runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        self.assertEqual(
            CommandShadowSource(["probe"], runner=timeout_runner).capture().status,
            "error",
        )

        def nonzero_runner(command, **kwargs):
            return SimpleNamespace(returncode=4, stdout="", stderr="adapter down")

        nonzero = CommandShadowSource(["probe"], runner=nonzero_runner).capture()
        self.assertEqual(nonzero.status, "error")
        self.assertIn("code 4", nonzero.error)

        def huge_runner(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout="x" * 2000, stderr="")

        huge = CommandShadowSource(
            ["probe"], max_response_bytes=1024, runner=huge_runner
        ).capture()
        self.assertEqual(huge.status, "error")
        self.assertIn("size limit", huge.error)


class TestShadowSourceWriter(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.g = SimpleNamespace(state_dir=self.root / "state", repo_root=self.root)
        self.game = SimpleNamespace(
            raw={
                "nethack": {
                    "shadow": {
                        "enabled": False,
                        "path": "",
                        "max_age_s": 5.0,
                        "max_bytes": 131072,
                    }
                }
            }
        )
        self.cfg = ShadowSourceConfig(
            enabled=True,
            command=("probe",),
            interval_s=1.0,
            timeout_s=2.0,
            max_response_bytes=131072,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_validated_snapshot_is_atomically_published_and_statused(self) -> None:
        snapshot = parse_shadow_snapshot(snapshot_payload())
        source = FakeSource(SourceCaptureResult(status="snapshot", snapshot=snapshot))
        writer = ShadowSourceWriter(
            self.g,
            self.game,
            config=self.cfg,
            source=source,
            wall_time=lambda: 456.0,
        )
        result = writer.capture_once()
        self.assertEqual(result.status, "published")
        self.assertEqual(source.calls, 1)

        published = json.loads(writer.output_path.read_text(encoding="utf-8"))
        self.assertEqual(published, snapshot_to_dict(snapshot))
        self.assertNotIn("monster_id", json.dumps(published))

        status = json.loads(writer.status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "published")
        self.assertEqual(status["source"], "test-source")
        self.assertEqual(status["updated_at"], 456.0)

    def test_source_error_preserves_last_good_snapshot(self) -> None:
        writer = ShadowSourceWriter(
            self.g,
            self.game,
            config=self.cfg,
            source=FakeSource(SourceCaptureResult(status="error", error="bad snapshot")),
        )
        writer.output_path.parent.mkdir(parents=True, exist_ok=True)
        writer.output_path.write_text("LAST-GOOD", encoding="utf-8")
        result = writer.capture_once()
        self.assertEqual(result.status, "error")
        self.assertEqual(writer.output_path.read_text(encoding="utf-8"), "LAST-GOOD")

    def test_disabled_writer_does_not_call_source_or_publish(self) -> None:
        source = FakeSource(
            SourceCaptureResult(
                status="snapshot",
                snapshot=parse_shadow_snapshot(snapshot_payload()),
            )
        )
        writer = ShadowSourceWriter(
            self.g,
            self.game,
            config=ShadowSourceConfig(enabled=False),
            source=source,
        )
        self.assertEqual(writer.capture_once().status, "disabled")
        self.assertEqual(source.calls, 0)
        self.assertFalse(writer.output_path.exists())


if __name__ == "__main__":
    unittest.main()
