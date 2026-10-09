"""Synthetic reproductions of unresolved return/cleanup safety boundaries."""
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

from test_soren_adapter import TestSorenCoordinatorAdapter as _SorenFixture
from docich.adapters.base import AdapterError
from docich.game_switch import DeadlineExceededError


def test_old_singleton_cannot_satisfy_fresh_start_readiness():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        adapter = _SorenFixture().make_adapter(root)
        proc = root / "fixture-proc"
        proc.mkdir()
        (proc / "uptime").write_text("100 0")
        for pid, name, ticks in ((101, "soren_loop.sh", 100), (102, "soviet_watchdog.sh", 6000)):
            entry = proc / str(pid)
            entry.mkdir()
            fields = ["S"] + ["0"] * 18 + [str(ticks)]
            (entry / "stat").write_text(f"{pid} (fixture) " + " ".join(fields))
            (entry / "cmdline").write_bytes(f"/bin/bash\0{root / name}\0".encode())
        adapter._fresh_started_at = 950
        with patch("docich.adapters.soren.Path", side_effect=lambda path: proc if path == "/proc" else Path(path)), \
             patch("docich.adapters.soren.time.time", return_value=1000), \
             patch("docich.adapters.soren.subprocess.check_output", return_value="100"):
            assert not adapter._live_process("soren_loop.sh")  # birth=901
            assert adapter._live_process("soviet_watchdog.sh")  # birth=960
            adapter._status = Mock(return_value={"ack": None})
            adapter._check = Mock(side_effect=[None, DeadlineExceededError("fixture deadline")])
            with patch("docich.adapters.soren.time.sleep"), patch("docich.adapters.soren.urllib.request.urlopen") as http:
                try:
                    adapter.readiness(time.monotonic() + 600, None)
                except DeadlineExceededError:
                    pass
                else:
                    raise AssertionError("old singleton unexpectedly passed readiness")
                http.assert_not_called()


def test_cleanup_without_stop_receipt_refuses_without_controller_call():
    with tempfile.TemporaryDirectory() as temp:
        adapter = _SorenFixture().make_adapter(Path(temp))
        adapter._status = Mock(return_value={"request": None, "ack": None, "resource": None})
        adapter._run = Mock()
        try:
            adapter.cleanup_runtime(time.monotonic() + 30, None)
        except AdapterError as exc:
            assert "stop request" in str(exc)
        else:
            raise AssertionError("cleanup accepted an unidentified singleton")
        adapter._run.assert_not_called()
