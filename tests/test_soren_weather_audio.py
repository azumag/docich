"""Offline checks for the narrow shared-consumer CLI adapter."""
from __future__ import annotations

from pathlib import Path
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.soren_weather_audio import SharedWeatherAudioError, SorenWeatherAudioPort  # noqa: E402


def _queued(item_key):
    return {"item_key": item_key, "status": "queued"}


def _terminal(item_key):
    return {"item_key": item_key, "status": "interrupted"}


def test_interrupt_targets_only_the_matching_weather_item_and_waits_for_durable_stop_ack(tmp_path):
    execution_id = str(uuid.uuid4())
    item_key = f"weather_corner:{execution_id}:04"
    queue = tmp_path / "queue"
    queue.mkdir()
    target = queue / (
        f"comment_announce_weather_audio_12345678901234567890_"
        f"{execution_id}_04_weather_audio_item.txt"
    )
    target.write_text("literal", encoding="utf-8")
    port = SorenWeatherAudioPort(
        tmp_path / "soren", tmp_path / "state", queue_dir=queue,
        interrupt_wait_s=1, monotonic=lambda: 0,
        sleep=lambda _seconds: (queue / target.with_suffix(".playing").name).unlink(),
    )
    operations = []
    quiescence_count = [0]

    def run(operation, *args):
        operations.append((operation, args))
        if operation == "get":
            return _queued(item_key) if len([x for x in operations if x[0] == "get"]) == 1 else _terminal(item_key)
        if operation == "interrupt":
            Path(args[0]).rename(target.with_suffix(".playing"))
            return _terminal(item_key)
        if operation == "quiescence":
            quiescence_count[0] += 1
            return {
                "schema_version": 1, "item_key": item_key,
                "receipt": _terminal(item_key), "quiescent": quiescence_count[0] > 1,
            }
        raise AssertionError(operation)

    port._run_json = run
    result = port.interrupt_weather_audio(item_key)

    assert result == _terminal(item_key)
    assert operations[1] == ("interrupt", (str(target),))
    assert operations[2][0] == "quiescence"
    assert not target.exists() and not target.with_suffix(".playing").exists()


def test_lost_interrupt_response_is_resolved_by_quiescence_receipt(tmp_path):
    execution_id = str(uuid.uuid4())
    item_key = f"weather_corner:{execution_id}:03"
    queue = tmp_path / "queue"
    queue.mkdir()
    target = queue / (
        f"comment_announce_weather_audio_12345678901234567890_"
        f"{execution_id}_03_weather_audio_item.txt"
    )
    target.write_text("literal", encoding="utf-8")
    tick = [0.0]
    port = SorenWeatherAudioPort(
        tmp_path / "soren", tmp_path / "state", queue_dir=queue,
        interrupt_wait_s=1, monotonic=lambda: tick[0],
        sleep=lambda seconds: tick.__setitem__(0, tick[0] + seconds),
    )
    quiescence_count = [0]

    def run(operation, *args):
        if operation == "get":
            return _queued(item_key)
        if operation == "interrupt":
            Path(args[0]).rename(target.with_suffix(".playing"))
            # The helper committed its terminal receipt, but its response was lost.
            raise SharedWeatherAudioError("simulated lost response")
        if operation == "quiescence":
            quiescence_count[0] += 1
            return {
                "schema_version": 1, "item_key": item_key,
                "receipt": _terminal(item_key), "quiescent": quiescence_count[0] > 2,
            }
        raise AssertionError(operation)

    port._run_json = run
    assert port.interrupt_weather_audio(item_key) == _terminal(item_key)
    assert quiescence_count[0] == 3


def test_missing_playing_file_does_not_substitute_for_consumer_stop_ack(tmp_path):
    execution_id = str(uuid.uuid4())
    item_key = f"weather_corner:{execution_id}:02"
    tick = [0.0]
    port = SorenWeatherAudioPort(
        tmp_path / "soren", tmp_path / "state", queue_dir=tmp_path / "queue",
        interrupt_wait_s=0.1, monotonic=lambda: tick[0],
        sleep=lambda seconds: tick.__setitem__(0, tick[0] + seconds),
    )
    port._run_json = lambda operation, *_args: (
        _terminal(item_key) if operation == "get" else {
            "schema_version": 1, "item_key": item_key,
            "receipt": _terminal(item_key), "quiescent": False,
        }
    )

    with pytest.raises(SharedWeatherAudioError, match="quiescence"):
        port.interrupt_weather_audio(item_key)


def test_interrupt_fails_closed_when_matching_queue_target_is_ambiguous(tmp_path):
    execution_id = str(uuid.uuid4())
    item_key = f"weather_corner:{execution_id}:00"
    queue = tmp_path / "queue"
    queue.mkdir()
    for stamp in ("12345678901234567890", "12345678901234567891"):
        (queue / f"comment_announce_weather_audio_{stamp}_{execution_id}_00_weather_audio_item.txt").touch()
    port = SorenWeatherAudioPort(tmp_path / "soren", tmp_path / "state", queue_dir=queue)
    port._run_json = lambda operation, *_args: _queued(item_key) if operation == "get" else None

    with pytest.raises(SharedWeatherAudioError, match="ambiguous"):
        port.interrupt_weather_audio(item_key)
