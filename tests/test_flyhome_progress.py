from threading import Event
from time import perf_counter
from unittest.mock import patch

import pytest
from docich.flyhome.progress import StatePublisher


def test_slow_state_file_does_not_delay_next_step_and_keeps_every_step(tmp_path):
    path = tmp_path / "game-state.txt"
    publisher = StatePublisher(path)
    entered, release = Event(), Event()
    writes = []
    real_write = publisher._write

    def blocked_write(body):
        writes.append(body)
        if body == "step1\n":
            entered.set()
            assert release.wait(2)
        real_write(body)

    try:
        with patch.object(publisher, "_write", side_effect=blocked_write):
            publisher.publish("step1\n")
            assert entered.wait(1)
            start = perf_counter()
            publisher.publish("step2\n")
            publisher.publish("step3\n")
            assert perf_counter() - start < .2
            release.set()
            publisher.close()
        assert writes == ["step1\n", "step2\n", "step3\n"]
        assert path.read_text(encoding="utf-8") == "step3\n"
        assert publisher.metrics()["state_written_sequence"] == 3
        assert publisher.metrics()["state_pending"] == 0
    finally:
        release.set()
        publisher.close()


def test_temporary_windows_reader_lock_is_retried_without_dropping_step(tmp_path):
    import os
    publisher = StatePublisher(tmp_path / "game-state.txt")
    real_replace = os.replace
    calls = 0

    def reader_lock(source, destination):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError("temporary reader lock")
        real_replace(source, destination)

    with patch("docich.flyhome.progress.os.replace", side_effect=reader_lock):
        publisher.publish("位置を更新\n")
        publisher.close()
    assert calls == 3
    assert publisher.path.read_text(encoding="utf-8") == "位置を更新\n"


def test_async_file_error_is_reported_when_flushing(tmp_path):
    publisher = StatePublisher(tmp_path / "game-state.txt")
    with patch.object(publisher, "_write", side_effect=OSError("disk write failed")):
        publisher.publish("step1\n")
        with pytest.raises(OSError, match="disk write failed"):
            publisher.close()
