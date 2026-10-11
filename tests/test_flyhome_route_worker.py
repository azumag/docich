from threading import Event
from time import perf_counter
from unittest.mock import patch
import pytest
from docich.flyhome import planner
from docich.flyhome.route_worker import RouteWorker
from docich.flyhome.vision import Observation


def _obs(hazards=()):
    return Observation(80, 60, None, (60, 30), None, list(hazards), [], False, False,
                       1, 1, bytearray(80 * 60), bytearray(80 * 60))


def test_slow_route_does_not_block_input_loop_and_old_attempt_is_discarded():
    worker = RouteWorker()
    entered, release = Event(), Event()
    original = worker._solve
    def slow(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)
    try:
        with patch.object(worker, '_solve', side_effect=slow):
            obs = _obs()
            started = perf_counter()
            assert worker.request(1, obs, obs, (20, 30), (60, 30), None, None)
            assert perf_counter() - started < .2
            assert entered.wait(1)
            assert worker.busy
            assert worker.poll(1) is None
            assert not worker.request(2, obs, obs, (20, 30), (60, 30), None, None)
            release.set()
            worker._pending.result(timeout=2)
            assert worker.poll(2) is None
            assert not worker.busy
            assert worker.request(2, obs, obs, (20, 30), (60, 30), None, None)
            worker._pending.result(timeout=2)
            assert worker.poll(2).attempt == 2
    finally:
        release.set()
        worker.close()


def test_route_failure_surfaces_on_poll():
    worker = RouteWorker()
    try:
        with patch.object(worker, '_solve', side_effect=RuntimeError('planner failed')):
            obs = _obs()
            worker.request(1, obs, obs, (20, 30), (60, 30), None, None)
            worker._pending.exception(timeout=1)
            with pytest.raises(RuntimeError, match='planner failed'):
                worker.poll(1)
    finally:
        worker.close()


def test_new_spike_invalidates_old_route_and_static_search_finds_detour():
    worker = RouteWorker()
    try:
        obs = _obs([(36, 26, 40, 34)])
        result = worker._solve(1, obs, obs, (20, 30), (60, 30), [(20, 30), (60, 30)], (4, 0, 1))
        assert result.detail is not None
        assert planner.path_length(result.path) > 40
        assert not planner.path_blocked(result.grid, result.path)
    finally:
        worker.close()
