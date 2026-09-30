"""No production access: fixed evidence projection, hostile input and read-only checks."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('nethack_history_collector', ROOT / 'ops/vm_actions/collect_diagnostics.py')
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.runs = self.root / 'nethack/runs'
        self.daily = self.root / 'nethack/daily-improvements'
        self.runs.mkdir(parents=True)
        self.daily.mkdir()

    def run_record(self, index=0, **extra):
        raw = dict(schema_version=1, status='dead', expedition=index,
                   started_at='2026-09-29T01:00:00+00:00', ended_at='2026-09-30T01:00:00+00:00',
                   score=5, turns=10, max_depth=2, death_reason='SECRET', run_id='SECRET',
                   dump_file='/SECRET', lessons=[{'text': 'SECRET'}],
                   retrospective=dict(schema_version=1, generated_at='2026-09-30T02:00:00+00:00',
                       source='p5a_retrospective', run_id='SECRET', terminal_status='dead',
                       same_death_total_count=2, progress_evidence=dict(status='ok', sample_count=3,
                       same_frame_sent_pairs=2, max_same_frame_sent_streak=3, secret='SECRET')))
        raw['run_id'] = f'00000000-0000-0000-0000-{index:012x}'
        raw['retrospective']['run_id'] = raw['run_id']
        raw.update(extra)
        path = self.runs / f'00000000-0000-0000-0000-{index:012x}.json'
        path.write_text(json.dumps(raw))
        return path

    def daily_record(self, **extra):
        raw = dict(schema_version=1, date='2026-09-30', generated_at='2026-09-30T02:00:00+00:00',
                   status='review_ready', run_count=1, candidate_state='pending_canary_evaluation',
                   candidates=[{'category': 'progress_stall', 'text': 'SECRET'}],
                   policy_effect='none', automatic_promotion=False, candidate_catalog_path='/SECRET')
        raw.update(extra)
        path = self.daily / '2026-09-30.json'
        path.write_text(json.dumps(raw))
        return path

    def collect(self):
        return diag._collect_nethack_history(self.root, 1790726400)

    def test_valid_redacted_read_only(self):
        self.run_record()
        self.daily_record()
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with mock.patch.object(diag.subprocess, 'run', side_effect=AssertionError('no exec')), mock.patch.object(diag.fcntl, 'flock', side_effect=AssertionError('no locks')):
            result = self.collect()
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertFalse((self.root / 'nethack/.lock').exists())
        self.assertEqual(result['daily']['records'][0]['candidate_state'], 'pending_canary_evaluation')
        self.assertEqual(result['completed_runs']['records'][0]['progress']['same_frame_sent_pairs'], 2)

    def test_missing_empty_active_and_no_retrospective(self):
        self.assertEqual(self.collect()['daily']['status'], 'empty')
        self.daily.rmdir()
        self.assertEqual(self.collect()['daily']['status'], 'missing')
        self.run_record(status='active')
        self.run_record(1, status='ended_unknown', retrospective=None)
        result = self.collect()['completed_runs']
        self.assertEqual(result['excluded_active'], 1)
        self.assertEqual(result['records'][0]['progress']['status'], 'missing')
        self.assertIsNone(result['records'][0]['same_death_total_count'])

    def test_hostile_values_and_schema(self):
        for extra in ({'schema_version': True}, {'schema_version': 2}, {'status': []},
                      {'ended_at': '2026-09-30'}, {'ended_at': 'SECRET'}):
            self.run_record(**extra)
            self.assertEqual(self.collect()['completed_runs']['invalid_records'], 1)
        self.run_record(score=True, turns='SECRET')
        self.assertIsNone(self.collect()['completed_runs']['records'][0]['score'])
        self.daily_record(automatic_promotion=True)
        self.assertEqual(self.collect()['daily']['invalid_records'], 1)
        self.daily_record(status=[])
        self.assertEqual(self.collect()['daily']['invalid_records'], 1)

    def test_links_fifo_oversize_malformed(self):
        path = self.run_record()
        path.write_bytes(b'x' * (diag.NETHACK_HISTORY_FILE_BYTES + 1))
        self.assertEqual(self.collect()['completed_runs']['invalid_records'], 1)
        path.write_text('{')
        self.assertEqual(self.collect()['completed_runs']['invalid_records'], 1)
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(self.collect()['completed_runs']['invalid_records'], 1)
        path.unlink()
        path.symlink_to('/etc/passwd')
        self.assertEqual(self.collect()['completed_runs']['invalid_records'], 1)
        self.daily.rmdir()
        self.daily.symlink_to(self.runs, target_is_directory=True)
        self.assertEqual(self.collect()['daily']['status'], 'unavailable')

    def test_scan_and_output_bounds(self):
        for index in range(140):
            self.run_record(index)
        result = self.collect()['completed_runs']
        self.assertFalse(result['scan_complete'])
        self.assertEqual(result['scanned_entries'], 128)
        self.assertEqual(len(result['records']), 8)
        self.assertEqual(result['omitted_records'], 120)
        self.assertLess(len(json.dumps(self.collect()).encode()), 12000)

    def test_latest_order_and_nullable_progress(self):
        self.run_record(0, ended_at='2026-09-29T00:00:00+00:00')
        self.run_record(1)
        records = self.collect()['completed_runs']['records']
        self.assertEqual(records[0]['expedition'], 1)
        projected = diag._nethack_progress({'status': 'SECRET', 'sample_count': True,
                    'min_hp_ratio': float('nan'), 'first_ts': 10**400})
        self.assertEqual(projected['status'], 'unknown')
        self.assertIsNone(projected['sample_count'])
        self.assertIsNone(projected['first_ts'])
        self.assertIsNone(projected['min_hp_ratio'])

    def test_stale_retrospective_and_gateway_shape(self):
        self.run_record(retrospective={'schema_version': 1, 'source': 'p5a_retrospective',
                        'run_id': 'other', 'terminal_status': 'dead', 'same_death_total_count': 99})
        result = self.collect()
        self.assertFalse(result['completed_runs']['records'][0]['retrospective_present'])
        gateway_spec = importlib.util.spec_from_file_location('history_gateway', ROOT / 'ops/vm_actions/gateway.py')
        gateway = importlib.util.module_from_spec(gateway_spec)
        gateway_spec.loader.exec_module(gateway)
        self.assertEqual(gateway._sanitize_diagnostics(result), result)

    def test_global_budget_omits_history_without_changing_existing_evidence(self):
        for index in range(8):
            self.run_record(index)
        self.daily_record()
        payload = {'nethack_history': self.collect(), 'other': 'x' * (diag.MAX_JSON_BYTES - 2000)}
        original = payload['other']
        text = diag._nethack_history_budget(payload)
        self.assertLessEqual(len(text.encode()), diag.MAX_JSON_BYTES)
        self.assertEqual(payload['other'], original)
        for source, count in (('daily', 1), ('completed_runs', 8)):
            self.assertTrue(payload['nethack_history'][source]['output_omitted'])
            self.assertEqual(payload['nethack_history'][source]['omitted_records'], count)
            self.assertEqual(payload['nethack_history'][source]['records'], [])
