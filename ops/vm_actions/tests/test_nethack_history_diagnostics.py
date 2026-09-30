"""No production access: fixed evidence projection, hostile input and read-only checks."""
import importlib.util
import json
import os
import tempfile
import sys
from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))

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
                   started_at='2026-09-29T01:00:00+00:00', last_finished_at='2026-09-30T01:00:00+00:00',
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
                      {'last_finished_at': '2026-09-30'}, {'last_finished_at': 'SECRET'}):
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
        self.run_record(0, last_finished_at='2026-09-29T00:00:00+00:00')
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

    def test_legacy_root_timestamp_cannot_replace_producer_timestamp(self):
        self.run_record(last_finished_at=None, ended_at='2026-09-30T01:00:00+00:00')
        self.assertEqual(self.collect()['completed_runs']['invalid_records'], 1)

    def test_global_budget_keeps_latest_and_does_not_mark_missing_daily(self):
        for index in range(8):
            self.run_record(index, last_finished_at=f'2026-09-30T01:00:0{index}+00:00')
        self.daily.rmdir()
        payload = {'nethack_history': self.collect(), 'other': 'unchanged'}
        latest = payload['nethack_history']['completed_runs']['records'][0].copy()
        # Calculate a deterministic budget that fits exactly one run + metadata.
        import copy
        expected = copy.deepcopy(payload)
        expected['nethack_history']['completed_runs'].update(
            records=[latest], omitted_records=7, output_omitted=True)
        budget = len(json.dumps(expected, sort_keys=True, ensure_ascii=False).encode())
        with mock.patch.object(diag, 'MAX_JSON_BYTES', budget):
            text = diag._nethack_history_budget(payload)
        self.assertLessEqual(len(text.encode()), budget)
        self.assertEqual(payload, expected)
        self.assertNotIn('output_omitted', payload['nethack_history']['daily'])

    def test_budget_retains_both_latest_then_truthfully_omits(self):
        for index in range(8):
            self.run_record(index)
        self.daily_record()
        payload = {'nethack_history': self.collect(), 'other': 'unchanged'}
        import copy
        expected = copy.deepcopy(payload)
        runs = expected['nethack_history']['completed_runs']
        runs.update(records=runs['records'][:1], omitted_records=7, output_omitted=True)
        budget = len(json.dumps(expected, sort_keys=True, ensure_ascii=False).encode())
        with mock.patch.object(diag, 'MAX_JSON_BYTES', budget):
            self.assertLessEqual(len(diag._nethack_history_budget(payload).encode()), budget)
        self.assertEqual(payload, expected)
        with mock.patch.object(diag, 'MAX_JSON_BYTES', 1):
            diag._nethack_history_budget(payload)
        for source, count in (('daily', 1), ('completed_runs', 8)):
            self.assertTrue(payload['nethack_history'][source]['output_omitted'])
            self.assertEqual(payload['nethack_history'][source]['omitted_records'], count)
            self.assertEqual(payload['nethack_history'][source]['records'], [])
        self.assertEqual(payload['other'], 'unchanged')

    def test_real_run_store_and_retrospective_contract(self):
        from docich import config
        from docich.nethack_run import NethackRunStore
        from docich.nethack_retrospective import NethackRetrospectiveEngine

        repo = self.root / 'fixture-repo'
        games = repo / 'config/games'
        games.mkdir(parents=True)
        playground = self.root / 'playground'
        save = playground / 'save'
        dump = playground / 'dumps'
        save.mkdir(parents=True)
        dump.mkdir()
        xlog = playground / 'xlogfile'
        (repo / 'config/docich.toml').write_text(
            f'[paths]\nstate_dir = "{self.root}"\ngames_dir = "config/games"\n')
        (games / 'nethack.toml').write_text(
            '[game]\nname="nethack"\ntitle="NetHack"\nadapter="cli"\n'
            '[cli]\ncommand="nethack"\n[nethack]\npersistent_run=true\n'
            'player_name="docich"\n'
            f'save_dir="{save}"\ndump_dir="{dump}"\nxlogfile="{xlog}"\n')
        g = config.load_global(repo)
        store = NethackRunStore.from_global(g)
        start = datetime(2026, 9, 30, tzinfo=timezone.utc)
        probe = store.prepare_start(current_is_nethack=False, now=start)
        store.record_started(probe, now=start)
        finished = start + timedelta(hours=1)
        xlog.write_text('name=docich\tdeath=killed by a grid bug\tpoints=42'
                       '\tturns=120\tmaxlvl=3\tachieve=0x0\n')
        run = store.record_finished(now=finished, nethack_still_active=False)
        self.assertNotIn('ended_at', run)
        self.assertEqual(run['last_finished_at'], finished.isoformat())
        NethackRetrospectiveEngine(g).generate(run_id=run['run_id'], now=finished)
        before = {p: p.read_bytes() for p in self.runs.iterdir()}
        result = self.collect()['completed_runs']
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['invalid_records'], 0)
        self.assertEqual(len(result['records']), 1)
        record = result['records'][0]
        self.assertEqual(record['ended_at'], finished.timestamp())
        self.assertEqual(record['score'], 42)
        self.assertEqual(record['turns'], 120)
        self.assertTrue(record['retrospective_present'])
        self.assertNotIn('grid bug', json.dumps(record))
        self.assertEqual(before, {p: p.read_bytes() for p in self.runs.iterdir()})

    def test_full_budget_keeps_latest_after_existing_detail_reductions(self):
        for index in range(7):
            self.run_record(index, last_finished_at=f'2026-09-30T01:00:0{index}+00:00')
        self.daily.rmdir()
        payload = {'nethack_history': self.collect(),
                   'ai': {'recent_events': ['x' * diag.MAX_JSON_BYTES],
                          'anomalous_components': {'preserved': 1}},
                   'workers': {'details': {'large': 'old detail'}},
                   'soren91_drop_profile': {'profileStatus': 'missing'}}
        text = diag._diagnostics_budget(payload)
        self.assertLessEqual(len(text.encode()), diag.MAX_JSON_BYTES)
        runs = payload['nethack_history']['completed_runs']
        self.assertEqual([r['expedition'] for r in runs['records']], [6])
        self.assertEqual(runs['omitted_records'], 6)
        self.assertTrue(runs['output_omitted'])
        self.assertEqual(payload['ai']['recent_events'], [])
        self.assertTrue(payload['ai']['recent_events_omitted'])
        self.assertEqual(payload['ai']['anomalous_components'], {'preserved': 1})
        self.assertNotIn('output_omitted', payload['nethack_history']['daily'])

    def test_full_budget_can_drop_latest_only_after_other_reductions(self):
        self.run_record()
        self.daily.rmdir()
        payload = {'nethack_history': self.collect(), 'other': 'x' * 100,
                   'ai': {'recent_events': [], 'anomalous_components': {}},
                   'workers': {'details': {}},
                   'soren91_drop_profile': {'profileStatus': 'missing'}}
        import copy
        expected = copy.deepcopy(payload)
        expected['nethack_history']['completed_runs'].update(
            records=[], omitted_records=1, output_omitted=True)
        expected['ai']['recent_events_omitted'] = True
        budget = len(json.dumps(expected, sort_keys=True, ensure_ascii=False).encode())
        with mock.patch.object(diag, 'MAX_JSON_BYTES', budget):
            text = diag._diagnostics_budget(payload)
        self.assertLessEqual(len(text.encode()), budget)
        self.assertEqual(payload, expected)
