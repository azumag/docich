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


if __name__ == '__main__':
    unittest.main()
