"""Read-only numeric summaries; synthetic fixtures are NOT production evidence."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from ops.vm_actions.tests.test_collect_diagnostics import load_collector
from ops.vm_actions.gateway import _sanitize_diagnostics


class DropProfileDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.c = load_collector()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'soren91/tmp/state/soren91_loop_metrics.json'
        self.path.parent.mkdir(parents=True)

    def fixture(self, count=30):
        rows = []
        for i in range(1, count + 1):
            stages = {s: 0 for s in self.c.DROP_STAGES}
            stages.update(capture=i * 1000, input=1000)
            rows.append(dict(sample=i, game=i // 10, fromTurn=i, toTurn=i+1,
                             endedAtMs=1789670000000+i*1000, durationMs=(i+1)*1000,
                             stageMs=stages, phaseCalls={s: 1 for s in self.c.DROP_CALLS},
                             reasonCounts={s: 0 for s in self.c.DROP_REASONS},
                             observations=1, holds=i % 2, errors=0,
                             accountingValid=True, accountingErrorMs=0,
                             untrusted='secret-token-not-for-output'))
        return dict(schemaVersion=1, updatedAtMs=1789670040000, sinceDropSentMs=400,
                    game=3, turn=31, elapsedMs=99999,
                    outcome='observe', observations=12, holds=0,
                    stageMs={s: 250 for s in self.c.DROP_LOOP_STAGES},
                    reasonCounts={s: (1 if s == 'non-move' else 0)
                                  for s in self.c.DROP_REASONS},
                    lastFailure=None,
                    dropSentIntervalMs=dict(samples=count, last=1000, p50=900,
                                            p95=2000, max=2500),
                    dropProfile=dict(schemaVersion=1, basis='sent-to-sent',
                                     acceptedDropsMeasured=False,
                                     session='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                                     capacity=128, totalSamples=count,
                                     evictedSamples=0, records=rows))

    def collect(self, data):
        self.path.write_text(json.dumps(data))
        return self.c._collect_soren91_drop_profile(self.root)

    def test_numeric_summary_accounting_groups_and_redaction(self):
        result = self.collect(self.fixture())
        self.assertEqual(result['profileStatus'], 'ok')
        self.assertEqual(result['all']['samples'], 30)
        s = result['all']['phasesSeconds']['interval']
        self.assertEqual(s, dict(mean=16.5, median=16, p95=30, max=31,
                                total=495, calls=None, sharePercent=100))
        self.assertEqual(result['groups']['withHold']['samples'], 15)
        self.assertEqual(result['groups']['beforeTurn10']['samples'], 9)
        self.assertEqual(result['slowest'][0]['sample'], 30)
        self.assertEqual(result['all']['phasesSeconds']['capture']['calls'], 30)
        raw = json.dumps(result)
        self.assertNotIn('secret-token', raw)
        self.assertNotIn('records', raw)
        self.assertLess(len(raw), 24500)
        self.assertEqual(_sanitize_diagnostics({'soren91_drop_profile': result}),
                         {'soren91_drop_profile': result})

    def test_empty_old_missing_malformed_and_oversize(self):
        self.assertFalse(self.c._collect_soren91_drop_profile(self.root)['present'])
        self.assertEqual(self.collect({})['profileStatus'], 'missing')
        result = self.collect(self.fixture(0))
        self.assertIsNone(result['all']['phasesSeconds']['interval']['mean'])
        for raw in ('{', '[1]', '{"dropProfile":NaN}', ' ' * (self.c.DROP_MAX_BYTES+1)):
            self.path.write_text(raw)
            self.assertNotEqual(self.c._collect_soren91_drop_profile(self.root)['profileStatus'], 'ok')

    def test_exclusion_and_missing_are_explicit(self):
        data = self.fixture(4)
        data['dropProfile']['records'][0]['accountingValid'] = False
        data['dropProfile']['records'] = data['dropProfile']['records'][1:]
        data['dropProfile']['evictedSamples'] = 1
        data['dropProfile']['records'][0]['accountingValid'] = False
        result = self.collect(data)
        self.assertEqual(result['missingSamples'], 1)
        self.assertEqual(result['excludedSamples'], 1)
        self.assertEqual(result['all']['samples'], 2)

    def test_invalid_types_sequence_and_accounting_fail_closed(self):
        base = self.fixture(1)
        for key, value in [('sample', True), ('durationMs', float('inf')),
                           ('accountingErrorMs', 100), ('sample', 5), ('game', -1),
                           ('stageMs', {}), ('phaseCalls', {'capture': False})]:
            data = copy.deepcopy(base)
            data['dropProfile']['records'][0][key] = value
            self.assertEqual(self.collect(data)['profileStatus'], 'invalid', key)
        base['dropProfile']['session'] = 'secret-text'
        self.assertEqual(self.collect(base)['profileStatus'], 'invalid')

    def test_maximum_ring_and_group_budget(self):
        data = self.fixture(128)
        for i, row in enumerate(data['dropProfile']['records']):
            row['game'] = i
        result = self.collect(data)
        self.assertEqual(result['gameCount'], 128)
        self.assertGreaterEqual(result['omittedGameGroups'], 124)
        self.assertLess(len(json.dumps(result)), 24500)
        _sanitize_diagnostics({'soren91_drop_profile': result})

    def test_no_symlink_fifo_or_writes(self):
        target = self.root / 'target'
        target.write_text(json.dumps(self.fixture()))
        self.path.symlink_to(target)
        self.assertFalse(self.c._collect_soren91_drop_profile(self.root)['readable'])
        self.path.unlink()
        os.mkfifo(self.path)
        self.assertFalse(self.c._collect_soren91_drop_profile(self.root)['readable'])
        self.path.unlink()
        self.collect(self.fixture())
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        self.c._collect_soren91_drop_profile(self.root)
        self.assertEqual(before, (self.path.read_bytes(), self.path.stat().st_mtime_ns))

    def test_current_attempt_projection_is_fixed_vocabulary(self):
        result = self.collect(self.fixture())
        current = result['current']
        self.assertEqual(current['outcome'], 'observe')
        self.assertEqual(current['observations'], 12)
        self.assertEqual(current['holds'], 0)
        self.assertEqual(current['stageMs'], {s: 250 for s in self.c.DROP_LOOP_STAGES})
        self.assertEqual(current['reasonCounts'],
                         {s: (1 if s == 'non-move' else 0) for s in self.c.DROP_REASONS})
        self.assertIsNone(current['lastFailure'])
        self.assertEqual(current['dropSentIntervals'],
                         dict(samples=30, last=1000, p50=900, p95=2000, max=2500))
        self.assertEqual(_sanitize_diagnostics({'soren91_drop_profile': result}),
                         {'soren91_drop_profile': result})
        # A plot that sends no drop has no completed records: `all` stays empty
        # while `current` still names what the open attempt is doing.
        empty = self.collect(dict(self.fixture(0), outcome='error', observations=0,
                                  holds=0, reasonCounts={s: 0 for s in self.c.DROP_REASONS},
                                  lastFailure=dict(stage='capture',
                                                   errorClass='capture-timeout')))
        self.assertEqual(empty['all']['samples'], 0)
        self.assertEqual(empty['current']['outcome'], 'error')
        self.assertEqual(empty['current']['lastFailure'],
                         dict(stage='capture', errorClass='capture-timeout'))

    def test_current_attempt_degrades_untrusted_values_without_raw_text(self):
        data = self.fixture()
        data['outcome'] = 'secret-outcome-text'
        data['observations'] = 'many'
        data['stageMs'] = {'capture': float('inf'), 'analyze': 'secret-stage'}
        data['reasonCounts'] = {'non-move': True}
        data['lastFailure'] = {'stage': 'secret-stage', 'errorClass': 'secret-token-value'}
        data['dropSentIntervalMs'] = {'samples': 1.5, 'last': 'secret-interval'}
        result = self.collect(data)
        current = result['current']
        self.assertEqual(current['outcome'], 'unknown')
        self.assertIsNone(current['observations'])
        self.assertEqual(current['stageMs']['capture'], None)
        self.assertEqual(current['stageMs']['analyze'], None)
        self.assertEqual(current['reasonCounts']['non-move'], None)
        self.assertEqual(current['reasonCounts']['stable'], None)
        self.assertEqual(current['lastFailure'],
                         dict(stage='unknown', errorClass='unknown'))
        self.assertIsNone(current['dropSentIntervals']['samples'])
        self.assertIsNone(current['dropSentIntervals']['last'])
        raw = json.dumps(result)
        self.assertNotIn('secret', raw)

    def test_current_attempt_survives_missing_and_invalid_profile(self):
        missing = self.collect({'outcome': 'observe', 'stageMs': {'capture': 10}})
        self.assertEqual(missing['profileStatus'], 'missing')
        self.assertEqual(missing['current']['outcome'], 'observe')
        self.assertEqual(missing['current']['stageMs']['capture'], 10)
        self.assertEqual(missing['current']['lastFailure'], None)

        invalid = self.fixture()
        invalid['dropProfile']['session'] = 'secret-text'
        result = self.collect(invalid)
        self.assertEqual(result['profileStatus'], 'invalid')
        self.assertEqual(result['current']['observations'], 12)
        self.assertNotIn('secret-text', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
