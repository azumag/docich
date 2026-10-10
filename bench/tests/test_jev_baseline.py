"""Offline identity, denominator, reproduction, and retry regressions for #1974."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / 'bench/results/2026-10-09_jev_baseline'
sys.path.insert(0, str(BASE))
import evidence_inputs as inputs
import pipeline_records as records
import score_baseline as baseline
spec = importlib.util.spec_from_file_location('independent', BASE / 'verify/score_independent.py')
independent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(independent)


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def record(cid, pass_n, status='ok', latency=12):
    return {'case_id': cid, 'pass': pass_n, 'rc': 0, 'latency_ms': latency,
            'event': {'batch_size': 1, 'status': status, 'rows': [
                {'selected': 'chitchat', 'baseline': 'other', 'status': status}]}}


class MetricsTests(unittest.TestCase):
    def test_public_precision_absorbs_float_summation_tail(self):
        a = {'f1': 0.7922320098474617, 'count': 103, 'nested': [0.6407766990291262]}
        b = {'f1': 0.7922320098474618, 'count': 103, 'nested': [0.6407766990291263]}
        self.assertEqual(baseline.public_numbers(a), baseline.public_numbers(b))
        self.assertEqual(baseline.public_numbers(a)['count'], 103)

    def test_misses_stay_in_full_and_live_denominators(self):
        pred = {'a': 'chitchat', 'b': None, 'c': 'card_gacha'}
        gold = {'a': 'chitchat', 'b': 'chitchat', 'c': 'other'}
        tags = {'a': {'live-log'}, 'b': {'live-log'}, 'c': set()}
        common = baseline.score(pred, list(gold), gold, tags)
        self.assertEqual(common, independent.score(pred, list(gold), gold, tags))
        self.assertEqual(common['accuracy_all'], 1 / 3)
        self.assertEqual(common['accuracy_on_available'], 1 / 2)
        self.assertEqual(common['live_log_accuracy'], 1 / 2)
        self.assertEqual(common['live_log_accuracy_on_available'], 1)
        self.assertEqual(common['notification']['fp_per_100'], 100 / 3)
        self.assertEqual(common['parse_failures'], 1)

    def test_other_scope_predictions_do_not_affect_macro_f1(self):
        for scorer in (baseline.score, independent.score):
            self.assertEqual(scorer({'a': 'other', 'outside': 'card_gacha'}, ['a'],
                                   {'a': 'other'}, {'a': set()})['macro_f1'], 1)

    def test_empty_and_all_missing(self):
        for ids in ([], ['a']):
            self.assertEqual(baseline.score({}, ids, {'a': 'other'}, {'a': set()}),
                             independent.score({}, ids, {'a': 'other'}, {'a': set()}))


class SavedEvidenceTests(unittest.TestCase):
    def test_readme_headline_rows_match_canonical_report(self):
        report = baseline.build_report()
        readme = (BASE / 'README.md').read_text()
        for s in (report['current_jev']['ungated_full_106'],
                  report['current_jev']['ungated_matched_103'],
                  report['llama3.1_8b_q4_K_M']['per_run_matched_103'][1]):
            fields = (f"| {s['correct']}/{s['n']} = {s['accuracy_all']:.4f} | "
                      f"{s['accuracy_on_available']:.4f} | {s['macro_f1']:.4f} | "
                      f"{s['live_log_correct']}/{s['live_log_n']} = {s['live_log_accuracy']:.4f} | "
                      f"{s['coverage']:.4f} | {s['parse_failures']} |")
            self.assertIn(fields, readme)

    def test_external_source_is_a_content_identifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'input.jsonl'
            p.write_text('{}\n')
            ref = inputs.source(p, REPO)
            self.assertEqual(ref['path'], 'sha256:' + ref['sha256'])
            self.assertNotIn(tmp, json.dumps(ref))

    def test_reproduces_committed_reports_and_independent_arithmetic(self):
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            a = baseline.build_report()
            b = baseline.build_report(scorer=independent.score)
        self.assertEqual(a, b)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(json.loads((BASE / 'report.json').read_text()), sort_keys=True))
        saved = json.loads((BASE / 'verify/independent_summary.json').read_text())
        saved['grader'] = a['grader']
        # JSON object keys are strings on disk, so compare canonical encoding.
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(saved, sort_keys=True))
        u = a['current_jev']['ungated_full_106']
        self.assertEqual((u['correct'], u['n'], u['available']), (71, 106, 100))
        self.assertEqual((u['live_log_correct'], u['live_log_n']), (46, 78))
        m = a['current_jev']['ungated_matched_103']
        self.assertEqual((m['correct'], m['n'], m['live_log_correct'], m['live_log_n']), (69, 103, 44, 75))
        self.assertEqual(a['delta_matched_103_jev_minus_llama']['live_log_accuracy'], 0)
        l = a['llama3.1_8b_q4_K_M']
        self.assertEqual((l['pooled_notification_fp'], l['n_attempts']), (6, 315))
        self.assertEqual((l['matched_notification_fp'], l['matched_attempts']), (6, 309))
        self.assertEqual([s['correct'] for s in l['per_run_matched_103'].values()], [66] * 3)

    def test_metadata_scope_totals_and_identity(self):
        report = baseline.build_report()
        for key in ('suite', 'critical_suite'):
            s = report[key]
            self.assertEqual(sum(s['label_counts'].values()), s['n'])
            self.assertEqual(sum(s['intent_family_counts'].values()), s['n'])
            self.assertEqual(inputs.sha256(REPO / s['path']), s['sha256'])
        self.assertEqual(report['suite']['label_counts']['other'], 17)
        self.assertEqual(report['suite']['n'], 106)
        self.assertEqual(report['critical_suite']['n'], 2)
        self.assertEqual(report['matched_public']['n'], 103)
        self.assertEqual(report['llama_measured']['n'], 105)

    def test_legacy_pipeline_is_not_silently_assigned_case_ids(self):
        report = baseline.build_report()
        self.assertEqual(report['pipeline_reproduction']['state'], 'unavailable')
        self.assertEqual(report['pipeline_reproduction']['events'], 106)
        self.assertNotIn('pipeline_full_106', report['current_jev'])
        self.assertNotIn('heuristic_full_106', report['current_jev'])
        self.assertEqual(inputs.sha256(BASE / 'pipeline_metrics.jsonl'),
                         report['sources']['pipeline']['sha256'])

    def test_all_runs_are_used_not_only_run_one(self):
        data = inputs.load(REPO)
        cid = data['matched'][0]
        data['by_run'][2][cid]['choice'] = None
        data['by_run'][2][cid]['parse_ok'] = False
        with patch.object(baseline, 'load', return_value=data):
            report = baseline.build_report()
        self.assertEqual(report['llama3.1_8b_q4_K_M']['matched_attempts'], 309)
        self.assertEqual(report['llama3.1_8b_q4_K_M']['parse_failures'], 1)
        self.assertNotEqual(report['llama3.1_8b_q4_K_M']['per_run_matched_103'][1],
                            report['llama3.1_8b_q4_K_M']['per_run_matched_103'][2])

    def test_rejects_missing_duplicate_and_wrong_ids(self):
        rows = [{'case_id': 'a'}, {'case_id': 'b'}]
        for bad in (rows[:1], rows + [rows[0]], [{'case_id': 'a'}, {'case_id': 'c'}], [{}]):
            with self.assertRaises(ValueError):
                inputs.indexed(bad, ['a', 'b'], 'fixture')
        # ID joins permit reordering, unlike a positional zip.
        self.assertEqual(list(inputs.indexed(rows[::-1], ['a', 'b'], 'fixture')), ['b', 'a'])

    def test_suite_hash_is_verified_not_just_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'suite.jsonl'
            p.write_bytes((REPO / 'bench/jev_eval_v1/public_cases.jsonl').read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                inputs.suite(p, inputs.PUBLIC_SHA256, 106, REPO)

    def test_per_run_missing_duplicate_wrong_warmup_gold_fail(self):
        original = inputs.jsonl(REPO / inputs.LLAMA_PATH)
        variants = [original[1:], original + [original[4]]]
        bad = copy.deepcopy(original); bad[0]['warmup'] = False; variants.append(bad)
        bad = copy.deepcopy(original); bad[4]['category_expected'] = 'other'; variants.append(bad)
        actual_jsonl = inputs.jsonl
        for variant in variants:
            def load_jsonl(path):
                return variant if Path(path) == REPO / inputs.LLAMA_PATH else actual_jsonl(path)
            with self.subTest(variant=variants.index(variant)), patch.object(inputs, 'jsonl', side_effect=load_jsonl):
                with self.assertRaises(ValueError):
                    inputs.load(REPO)

    def test_pipeline_requires_identity_count_and_single_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'pipeline.jsonl'
            rows = [record('jev-0001', 1), record('jev-0002', 2)]
            write_jsonl(p, rows[::-1])
            by_id, state = inputs.pipeline_input(p, ['jev-0001', 'jev-0002'])
            self.assertEqual(by_id['jev-0001']['pass'], 1)
            self.assertEqual(state['state'], 'verified_case_ids')
            for bad in (rows[:1], rows + [rows[0]], [{**rows[0], 'case_id': 'jev-0003'}, rows[1]],
                        [{**rows[0], 'event': {'batch_size': 1, 'rows': []}}, rows[1]]):
                write_jsonl(p, bad)
                with self.assertRaises(ValueError):
                    inputs.pipeline_input(p, ['jev-0001', 'jev-0002'])

    def test_clean_checkouts_have_byte_identical_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = []
            for folder in ('checkout-A', 'another/checkout-B'):
                root = Path(tmp) / folder
                for path in (inputs.RESULT_PATH, Path('bench/jev_eval_v1'),
                             inputs.LLAMA_PATH.parent, Path('src')):
                    shutil.copytree(REPO / path, root / path)
                out = root / 'result.json'
                subprocess.run([sys.executable, str(root / inputs.RESULT_PATH / 'score_baseline.py'),
                                '--out', str(out)], cwd=tmp, check=True, capture_output=True)
                outputs.append(out.read_bytes())
                independent_out = root / 'independent.json'
                subprocess.run([sys.executable, str(root / inputs.RESULT_PATH / 'verify/score_independent.py'),
                                '--out', str(independent_out)], cwd=tmp, check=True, capture_output=True)
                content = independent_out.read_text()
                self.assertNotIn(tmp, content)
            self.assertEqual(outputs[0], outputs[1])
            self.assertNotIn(tmp.encode(), outputs[0])
            self.assertEqual(outputs[0], (BASE / 'report.json').read_bytes())


class RetryTests(unittest.TestCase):
    def test_batch_manifest_has_relative_names_and_rejects_duplicate_ids(self):
        import build_batches
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp) / 'suite.jsonl'
            rows = [{'case_id': 'jev-0001', 'input': {'comment': 'synthetic'},
                     'expected': {'category': 'other', 'intent_family': 'chat'}}]
            write_jsonl(suite, rows)
            manifest = build_batches.build(suite, Path(tmp) / 'batches')
            self.assertEqual(manifest[0]['batch'], 'jev-0001.txt')
            self.assertNotIn(tmp, json.dumps(manifest))
            write_jsonl(suite, rows * 2)
            with self.assertRaises(ValueError):
                build_batches.build(suite, Path(tmp) / 'duplicate')

    def test_latest_merge_keeps_prior_success_event_and_latency(self):
        first = [record('jev-0001', 1, latency=17), record('jev-0002', 1, 'cooldown', 23)]
        second = [record('jev-0002', 2, latency=29)]
        result = records.merge_passes([first, second], ['jev-0001', 'jev-0002'])
        self.assertEqual(result, [first[0], second[0]])
        self.assertEqual(first[1]['latency_ms'], 23)  # immutable prior pass
        self.assertEqual(result[0]['latency_ms'], 17)

    def test_retry_pass_rejects_missing_duplicate_reordered_and_final_cases(self):
        ids = ['jev-0001', 'jev-0002']
        first = [record(i, 1, 'cooldown') for i in ids]
        for bad in (first[:1], first[::-1], first + [first[0]], [{**first[0], 'case_id': 'jev-0003'}, first[1]]):
            with self.assertRaisesRegex(ValueError, 'IDs/count/order'):
                records.merge_passes([bad], ids)
        final = [record(ids[0], 1), first[1]]
        with self.assertRaises(ValueError):
            records.merge_passes([final, [record(i, 2) for i in ids]], ids)
        with self.assertRaises(ValueError):
            records.merge_passes([first, [record(ids[0], 3), record(ids[1], 3)]], ids)

    def test_capture_fails_on_zero_multiple_events_or_multiple_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'metrics-day.jsonl'
            good = record('jev-0001', 1)['event']
            for events in ([], [good, good], [{**good, 'batch_size': 2, 'rows': good['rows'] * 2}]):
                write_jsonl(p, events)
                with self.assertRaises(ValueError):
                    records.record_case('jev-0001', 1, tmp, 0, 12)
            write_jsonl(p, [good])
            self.assertEqual(records.record_case('jev-0001', 1, tmp, 0, 12)['case_id'], 'jev-0001')

    def test_shell_runner_with_fake_classifier_preserves_two_case_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            here = root / inputs.RESULT_PATH
            here.mkdir(parents=True)
            for name in ('run_baseline.sh', 'pipeline_records.py', 'evidence_inputs.py'):
                shutil.copy(BASE / name, here / name)
            (root / 'bin').mkdir()
            classifier = root / 'bin/docich-comment-classify'
            classifier.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
cid = pathlib.Path(sys.argv[1]).stem
out = pathlib.Path(os.environ['COMMENT_CLASSIFIER_JEV_METRICS_DIR'])
status = 'cooldown' if cid == 'jev-0002' and 'pass1' in str(out) else 'ok'
event = {'batch_size': 1, 'status': status, 'rows': [{'selected': 'chitchat', 'status': status}]}
(out / 'metrics-day.jsonl').write_text(json.dumps(event) + '\\n')
''')
            classifier.chmod(0o755)
            batches = Path(tmp) / 'batches'; batches.mkdir()
            ids = ['jev-0001', 'jev-0002']
            for cid in ids:
                (batches / (cid + '.txt')).write_text('probe: synthetic\n')
            (batches / 'manifest.json').write_text(json.dumps([{'case_id': i, 'batch': i + '.txt'} for i in ids]))
            import os
            env = {**os.environ, 'BATCH_DIR': str(batches), 'RUN_DIR': str(Path(tmp) / 'run')}
            subprocess.run(['bash', str(here / 'run_baseline.sh')], env=env, check=True, capture_output=True)
            latest = inputs.jsonl(Path(tmp) / 'run/latest.jsonl')
            self.assertEqual([r['case_id'] for r in latest], ids)
            self.assertEqual([r['pass'] for r in latest], [1, 2])
            self.assertEqual([r['event']['status'] for r in latest], ['ok', 'ok'])
            first = inputs.jsonl(Path(tmp) / 'run/pass1/events.jsonl')
            self.assertEqual(latest[0], first[0])
            self.assertEqual(first[1]['event']['status'], 'cooldown')


if __name__ == '__main__':
    unittest.main()
