"""docich#5112: bounded reload of the shared overlay browser on memory growth."""
import json
import tempfile
import unittest
from pathlib import Path

from ops.vm_actions import shared_overlay_memory_guard as guard


class MemoryCurrentTests(unittest.TestCase):
    def test_parses_bytes_only(self):
        assert guard.parse_memory_current('16059822080') == 16059822080
        assert guard.parse_memory_current('[not set]') is None
        assert guard.parse_memory_current('infinity') is None
        assert guard.parse_memory_current('') is None

    def test_memory_current_asks_only_the_unit(self):
        calls = []

        class Completed:
            stdout = '123456\n'

        def run(args, **kwargs):
            calls.append(args)
            return Completed()

        assert guard.memory_current(run=run) == 123456
        assert calls == [['systemctl', 'show', guard.UNIT, '-p', 'MemoryCurrent', '--value']]


class DecideTests(unittest.TestCase):
    def test_threshold_and_cooldown(self):
        limit = 100
        assert guard.decide(99, limit=limit) == 'ok'
        assert guard.decide(100, limit=limit) == 'reload'
        assert guard.decide(100, limit=limit, cooldown_remaining=1) == 'cooldown'
        assert guard.decide(None, limit=limit) == 'unmeasured'


class RunGuardTests(unittest.TestCase):
    def _state(self, directory):
        return Path(directory) / 'guard.json'

    def test_below_limit_never_reloads(self):
        with tempfile.TemporaryDirectory() as directory:
            reloaded = []
            result = guard.run_guard(limit=1000, memory=lambda: 999, reload=lambda: reloaded.append(1),
                                     state_path=self._state(directory))
            assert result['decision'] == 'ok'
            assert reloaded == []
            assert not self._state(directory).exists()

    def test_above_limit_reloads_once_then_honours_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            reloaded = []
            clock = [1000.0]
            kwargs = dict(limit=1000, cooldown=600, memory=lambda: 5000,
                          reload=lambda: reloaded.append(1),
                          now=lambda: clock[0], state_path=self._state(directory))
            first = guard.run_guard(**kwargs)
            assert first['decision'] == 'reloaded'
            assert reloaded == [1]
            assert json.loads(self._state(directory).read_text())['reloaded_at'] == 1000.0

            clock[0] += 599
            assert guard.run_guard(**kwargs)['decision'] == 'cooldown'
            assert reloaded == [1]

            clock[0] += 2
            assert guard.run_guard(**kwargs)['decision'] == 'reloaded'
            assert reloaded == [1, 1]

    def test_failed_reload_is_not_recorded_as_done(self):
        with tempfile.TemporaryDirectory() as directory:
            def fail():
                raise RuntimeError('protected process or active game changed')

            with self.assertRaisesRegex(RuntimeError, 'protected process'):
                guard.run_guard(limit=1, memory=lambda: 10, reload=fail,
                                now=lambda: 5.0, state_path=self._state(directory))
            assert not self._state(directory).exists()

    def test_unmeasured_memory_never_reloads(self):
        with tempfile.TemporaryDirectory() as directory:
            reloaded = []
            result = guard.run_guard(limit=1, memory=lambda: None,
                                     reload=lambda: reloaded.append(1),
                                     state_path=self._state(directory))
            assert result['decision'] == 'unmeasured'
            assert reloaded == []


if __name__ == '__main__':
    unittest.main()
