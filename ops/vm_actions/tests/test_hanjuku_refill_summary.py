"""Fixed monthly refill enums, not private state or inferred purchase success."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_collect_diagnostics import load_collector
from test_diagnostics_hard_ceiling import production_scale_payload


OWNER = {'game': 'hanjuku-hero', 'runtime_id': 'g889-1234abcd',
         'generation': 889, 'lease_id': 'PRIVATE-lease'}


class HanjukuRefillSummaryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_collector()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.runtime = self.root / 'runtimes' / OWNER['runtime_id']
        self.runtime.mkdir(parents=True)
        self.policy = {'_run_identity': deepcopy(OWNER), 'chapter': 2, 'month': '3-3',
                       'tick': 100, 'soldiers_seen': 0, 'soldiers_seen_key': '3-3',
                       'soldiers_seen_scope': [deepcopy(OWNER), 2, '3-3'],
                       'soldiers_seen_basis': 'screen_total',
                       'recruit_payroll_pending': ['PRIVATE-name'],
                       'army_count_check': {'scope': [deepcopy(OWNER), 2, '3-3'],
                                            'stage': 'unverified'},
                       'shop': {'key': '3-3', 'soldiers': 0, 'soldiers_done': True,
                                'refill_outcome': 'attempt_limit', 'hero_repair_reserve': 50,
                                'reserve': 50, 'recruit_reserve': 0,
                                'soldier_receipt': {'status': 'unverified', 'key': '3-3',
                                                    'reason': 'PRIVATE-reason'}},
                       'reason': 'PRIVATE-reason', 'secret': 'PRIVATE-secret'}
        self.write_records()

    def write_records(self):
        (self.root / 'game_switch.json').write_text(json.dumps({'phase': 'ready', 'active': OWNER}))
        (self.runtime / 'hanjuku_run.json').write_text(json.dumps({**OWNER, 'playing': True}))
        self.bot = self.runtime / 'hanjuku_bot.json'
        self.bot.write_text(json.dumps({'decision_trace': OWNER, 'policy': self.policy}))
        os.utime(self.bot, (1000, 1000))

    def collect(self, now=1010):
        return self.module._collect_hanjuku_tactical(self.root, now)

    def test_chapter_two_preserves_only_fixed_current_refill_gates(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*.json')}
        result = self.collect()
        self.assertEqual(result['status'], 'unsupported_chapter')  # castle projection is unchanged
        refill = result['refill']
        self.assertEqual(refill['scope'], 'current')
        self.assertEqual(refill['count'], 'zero')
        self.assertEqual(refill['basis'], 'screen_total')
        self.assertTrue(refill['payroll_pending'])
        self.assertEqual(refill['check'], 'unverified')
        self.assertEqual(refill['outcome'], 'attempt_limit')
        self.assertEqual(refill['receipt'], 'unverified')
        self.assertEqual(refill['repair'], 'hero')
        self.assertEqual(refill['planned'], 'zero')
        self.assertTrue(refill['done'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*.json')})

    def test_current_refill_survives_existing_soft_and_hard_budget_pressure(self):
        refill = self.collect()['refill']
        payload = production_scale_payload(hanjuku_tactical={
            'status': 'ok', 'basis': 'bot_record', 'detail': 'x' * 40000, 'refill': refill})
        result = json.loads(self.module._diagnostics_budget(payload))
        self.assertEqual(result['hanjuku_tactical']['refill'], refill)
        self.assertEqual(result['hanjuku_tactical']['status'], 'output_omitted')
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), self.module.HARD_JSON_BYTES)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_summary_stays_below_four_hundred_fifty_bytes(self):
        value = {key: max(allowed, key=len) for key, allowed in self.module._HANJUKU_REFILL_ENUMS.items()}
        value['scope'] = 'current'
        value.update({key: False for key in self.module._HANJUKU_REFILL_BOOLS})
        self.assertLessEqual(len(json.dumps(value, sort_keys=True).encode()), 450)

    def test_current_check_result_and_recruit_status_keep_writer_enums(self):
        self.policy['army_count_check'].update(stage='closing', result='unverified')
        self.policy['shop']['recruit'] = 'opened'
        self.write_records(); refill = self.collect()['refill']
        self.assertEqual(refill['check_result'], 'unverified')
        self.assertEqual(refill['recruit'], 'opened')
        self.policy['army_count_check']['result'] = 'PRIVATE-result'
        self.policy['shop']['recruit'] = ['PRIVATE-name']
        self.write_records(); refill = self.collect()['refill']
        self.assertEqual(refill['check_result'], 'unknown')
        self.assertEqual(refill['recruit'], 'unknown')

    def test_roster_gate_uses_current_membership_without_exposing_names(self):
        self.policy['recruit_roster'] = {
            'identity': deepcopy(OWNER), 'chapter': 2, 'month': '3-3',
            'tick': 90, 'names': ['PRIVATE-name'], 'complete': True}
        for pending, expected in ((None, 'complete_no_pending'),
                                  (['PRIVATE-name'], 'complete_all_seen'),
                                  (['PRIVATE-other'], 'complete_missing')):
            self.policy['recruit_payroll_pending'] = pending
            before = deepcopy(self.policy)
            self.write_records()
            refill = self.collect()['refill']
            self.assertEqual(refill['roster'], expected)
            self.assertNotIn('PRIVATE', json.dumps(refill))
            self.assertEqual(self.policy, before)
        self.policy['recruit_roster']['complete'] = False
        self.write_records()
        self.assertEqual(self.collect()['refill']['roster'], 'partial')
        self.policy['recruit_roster']['tick'] = -400
        self.write_records()
        self.assertEqual(self.collect()['refill']['roster'], 'unavailable')
        self.policy['recruit_roster'].update(tick=90, complete=True, month='3-2')
        self.write_records()
        self.assertEqual(self.collect()['refill']['roster'], 'unavailable')
        self.policy['recruit_roster'].update(month='3-3', identity={**OWNER, 'generation': 888})
        self.write_records()
        self.assertEqual(self.collect()['refill']['roster'], 'unavailable')

    def test_roster_scope_rejects_numeric_type_aliases(self):
        active = {**OWNER, 'generation': 1}
        self.policy.update(_run_identity=active, chapter=1)
        receipt = {'identity': deepcopy(active), 'chapter': 1, 'month': '3-3',
                   'tick': 90, 'names': ['PRIVATE-name'], 'complete': True}
        for field, value in (('generation', 1.0), ('generation', True),
                             ('chapter', 1.0), ('chapter', True)):
            with self.subTest(field=field, value=value):
                seen = deepcopy(receipt)
                if field == 'generation':
                    seen['identity'][field] = value
                else:
                    seen[field] = value
                self.policy['recruit_roster'] = seen
                refill = self.module._project_hanjuku_refill(self.policy, active)
                self.assertEqual(refill['roster'], 'unavailable')
        self.policy['recruit_roster'] = receipt
        self.assertEqual(self.module._project_hanjuku_refill(
            self.policy, active)['roster'], 'complete_all_seen')

    def test_roster_projection_matches_strategy_raw_and_completed_contract(self):
        from docich.hanjuku.os_layer.roster import fresh
        receipt = {'identity': deepcopy(OWNER), 'chapter': 2, 'month': '3-3',
                   'tick': 90, 'names': ['PRIVATE-name'], 'complete': True}
        survey = {'schema': 1, 'identity': deepcopy(OWNER), 'scope': [2, '3-3'],
                  'receipt': deepcopy(receipt), 'dirty': {},
                  'statuses': {'PRIVATE-name': {'general': 'PRIVATE-name', 'month': '3-3',
                               'observed_chapter': 2, 'observed_tick': 95, 'broken': False}}}
        cases = [dict(recruit_roster=receipt), dict(roster_survey=survey),
                 dict(roster_survey=survey, tick=1000),
                 dict(roster_survey=survey, house={'roster_started': 99}),
                 dict(roster_survey=survey, house={'roster_started': 99}, recruit_roster=receipt)]
        for field, value in (('schema', 2), ('scope', [2, '3-2']),
                             ('identity', {**OWNER, 'generation': 888}),
                             ('statuses', {}), ('dirty', {'PRIVATE-other': []}),
                             ('dirty_revisions', {'PRIVATE-name': -1}),
                             ('read_revisions', {'PRIVATE-name': True})):
            invalid = deepcopy(survey); invalid[field] = value
            cases.extend([dict(roster_survey=invalid),
                          dict(roster_survey=invalid, recruit_roster=receipt)])
        for field, value in (('observed_tick', 89), ('observed_chapter', 1),
                             ('month', '3-2'), ('broken', 0)):
            invalid = deepcopy(survey); invalid['statuses']['PRIVATE-name'][field] = value
            cases.append(dict(roster_survey=invalid))
        for changes in cases:
            with self.subTest(changes=changes):
                mem = deepcopy(self.policy); mem.update(deepcopy(changes))
                before = deepcopy(mem)
                expected = fresh(mem)
                same_identity = lambda value: (isinstance(value, dict) and value == OWNER
                    and value.keys() == OWNER.keys()
                    and all(type(value[k]) is type(v) for k, v in OWNER.items()))
                actual = self.module._hanjuku_refill_roster(mem, same_identity, 2, '3-3')
                self.assertEqual(actual, expected)
                self.assertEqual(mem, before)

    def test_scope_comparison_rejects_boolean_aliases(self):
        self.policy['chapter'] = 1
        for key in ('soldiers_seen_scope',):
            self.policy[key][1] = True
        self.policy['army_count_check']['scope'][1] = True
        self.policy['shop']['low_army_refill'] = {'scope': [OWNER, True, '3-3'], 'version': 1, 'active': True}
        self.policy.update(captured=[], garrison={}, sorties={}, general_location_unknown=[])
        self.write_records(); refill = self.collect()['refill']
        self.assertEqual(refill['count'], 'unknown')
        self.assertEqual(refill['check'], 'unknown')
        self.assertEqual(refill['check_result'], 'unknown')
        self.assertIsNone(refill['low_priority'])

    def test_absent_optional_writer_fields_are_absent_gates_not_paid_receipts(self):
        self.policy.pop('army_count_check'); self.policy.pop('recruit_payroll_pending')
        self.policy['shop'] = {'key': '3-3', 'soldiers': 3, 'soldiers_done': False}
        self.write_records(); refill = self.collect()['refill']
        self.assertEqual(refill['planned'], 'positive')
        self.assertFalse(refill['payroll_pending'])
        self.assertEqual(refill['receipt'], 'none')
        self.assertEqual(refill['outcome'], 'none')
        self.assertEqual(refill['repair'], 'none')
        self.assertFalse(refill['done'])

    def test_paid_receipt_never_fabricates_a_count_or_an_observed_payment(self):
        self.policy['shop']['soldier_receipt'].update(status='paid', qty=20)
        self.policy.pop('soldiers_seen_key')
        self.write_records(); refill = self.collect()['refill']
        self.assertEqual(refill['receipt'], 'paid')  # writer record, not independently checked
        self.assertEqual(refill['count'], 'unknown')
        self.assertEqual(refill['basis'], 'unknown')
        self.assertNotIn('qty', refill)

    def test_summary_reads_no_additional_state_and_keeps_existing_freshness_limit(self):
        original = self.module._read_hanjuku_record
        with mock.patch.object(self.module, '_read_hanjuku_record', wraps=original) as read:
            self.assertEqual(self.collect(now=1030)['refill']['scope'], 'current')
        self.assertEqual([Path(call.args[0]).name for call in read.call_args_list],
                         ['game_switch.json', 'hanjuku_bot.json', 'hanjuku_run.json', 'game_switch.json'])
        self.assertNotIn('refill', self.collect(now=1030.001))

    def test_count_thresholds_and_missing_freshness_are_not_confused(self):
        for count, expected in [(0, 'zero'), (1, 'low'), (20, 'low'),
                                (21, 'below_target'), (49, 'below_target'), (50, 'enough'),
                                (True, 'unknown'), (-1, 'unknown'), (None, 'unknown')]:
            with self.subTest(count=count):
                self.policy['soldiers_seen'] = count; self.write_records()
                self.assertEqual(self.collect()['refill']['count'], expected)
        self.policy['soldiers_seen'] = 0
        self.policy['soldiers_seen_scope'][1] = 1
        self.write_records()
        self.assertEqual(self.collect()['refill']['count'], 'unknown')
        self.assertEqual(self.collect()['refill']['basis'], 'unknown')

    def test_only_current_month_policy_can_emit_refill_state(self):
        for field, value in [('month', '3-2'), ('month', 'PRIVATE-month'),
                              ('_run_identity', None), ('_run_identity', {**OWNER, 'lease_id': 'other'})]:
            with self.subTest(field=field, value=value):
                original = deepcopy(self.policy); self.policy[field] = value
                self.write_records(); self.assertEqual(self.collect()['refill'], {'scope': 'unknown'})
                self.policy = original

    def test_foreign_subrecords_stay_unknown(self):
        self.policy['army_count_check']['scope'][0]['lease_id'] = 'other'
        self.policy['shop']['soldier_receipt']['key'] = '3-2'
        self.policy['shop']['low_army_refill'] = {'scope': [OWNER, 1, '3-3'], 'version': 1, 'active': True}
        self.write_records(); refill = self.collect()['refill']
        self.assertEqual(refill['check'], 'unknown')
        self.assertEqual(refill['receipt'], 'unknown')
        self.assertIsNone(refill['low_priority'])

    def test_writer_enums_are_allowlisted_not_reason_text(self):
        for outcome in ['cancelled', 'insufficient_funds', 'unverified', 'attempt_limit', 'not_attempted']:
            self.policy['shop']['refill_outcome'] = outcome; self.write_records()
            self.assertEqual(self.collect()['refill']['outcome'], outcome)
        self.policy['shop'].update(refill_outcome={'PRIVATE': 'body'}, soldiers=True,
                                   soldiers_done='PRIVATE', hero_repair_reserve=True,
                                   reserve='PRIVATE', recruit_reserve=-1)
        self.policy['army_count_check']['stage'] = 'PRIVATE-stage'
        self.policy['recruit_payroll_pending'] = {'PRIVATE': 'name'}
        self.write_records(); refill = self.collect()['refill']
        self.assertEqual(refill['outcome'], 'unknown')
        self.assertEqual(refill['planned'], 'unknown')
        self.assertEqual(refill['repair'], 'unknown')
        self.assertEqual(refill['check'], 'unknown')
        self.assertTrue(all(refill[k] is None for k in ('done', 'egg_hold', 'recruit_hold', 'payroll_pending')))
        self.assertNotIn('PRIVATE', json.dumps(refill))

    def test_stale_future_wrong_trace_and_terminal_sources_emit_no_summary(self):
        for change in ('stale', 'future', 'trace', 'terminal', 'symlink'):
            with self.subTest(change=change):
                self.write_records()
                if change == 'stale': os.utime(self.bot, (900, 900))
                elif change == 'future': os.utime(self.bot, (1020, 1020))
                elif change == 'trace':
                    self.bot.write_text(json.dumps({'decision_trace': {**OWNER, 'lease_id': 'other'}, 'policy': self.policy}))
                elif change == 'terminal':
                    (self.runtime / 'hanjuku_run.json').write_text(json.dumps({**OWNER, 'terminal_reason': 'game_over'}))
                else:
                    target = self.root / 'PRIVATE-target.json'; target.write_text(self.bot.read_text())
                    self.bot.unlink(); self.bot.symlink_to(target)
                self.assertNotIn('refill', self.collect())
                if self.bot.is_symlink(): self.bot.unlink()

    def test_identity_recheck_drops_even_a_prepared_summary(self):
        original = self.module._read_hanjuku_record
        calls = 0
        def read(path):
            nonlocal calls
            if Path(path).name == 'game_switch.json':
                calls += 1
                if calls == 2: return {'phase': 'ready', 'active': {**OWNER, 'generation': 890}}, 1000
            return original(path)
        with mock.patch.object(self.module, '_read_hanjuku_record', side_effect=read):
            result = self.collect()
        self.assertEqual(result['status'], 'identity_changed')
        self.assertNotIn('refill', result)

    def test_budget_revalidates_enums_and_never_copies_extra_fields(self):
        value = self.collect()['refill']
        value.update(reason='PRIVATE-reason', payroll_pending='PRIVATE-name', outcome='PRIVATE-text')
        out = self.module._compact_hanjuku_refill(value)
        self.assertNotIn('PRIVATE', json.dumps(out))
        self.assertEqual(out['outcome'], 'unknown')
        self.assertIsNone(out['payroll_pending'])


if __name__ == '__main__':
    unittest.main()
