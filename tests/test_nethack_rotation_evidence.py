"""Synthetic fixed proof observations only: no VM, process or game is launched."""
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'nethack_rotation_evidence_collector', ROOT / 'ops/vm_actions/collect_diagnostics.py')
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)
SLOT = '11111111-1111-4111-8111-111111111111'
R0 = '22222222-2222-4222-8222-222222222222'
RETURN = '33333333-3333-4333-8333-333333333333'
LEASE = '44444444-4444-4444-8444-444444444444'
OTHER = '55555555-5555-4555-8555-555555555555'


class RotationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.now = 1700000000  # Arbitrary synthetic clock, unrelated to production.
        self.owner = dict(schema_version=1, game='nethack', status='failed',
            rotation_request_id=SLOT, switch_request_id=R0, rotation_runtime_id='g1-aaaaaa',
            finish_reason='terminal', started_at=self.at(-100), completed_at=self.at(-90))
        self.ledger = dict(schema_version=1, status='recovery_required', slot=7,
            pending=dict(corner='nethack', phase='dispatched', request_id=SLOT,
                         selected_at=self.now - 120), manual_pending=None,
            last_seen_at=self.now - 10,
            history=[dict(corner='nethack', at=self.now - 110, source='reservation')])
        self.original = self.receipt(R0, 2, 'rolled_back', -80, -60)
        self.original['result'].update(restored_generation=3, cleanup_pending=True)
        self.landed = self.receipt(RETURN, 4, 'succeeded', -40, -20)
        self.active = dict(game='sorengame', adapter='soren', runtime_id='g4-dddddd',
                           generation=4, lease_id=LEASE, adapter_session='docich-game-g4',
                           game_window='game-g4', agent_window='agent-g4', started_at=self.at(-25))
        self.landed['result'].update(active_runtime={k:self.active[k]
            for k in ('game', 'runtime_id', 'generation', 'lease_id')}, cleanup_pending=False)
        self.canonical = dict(schema_version=2, revision=1, phase='ready', active=self.active,
            request_id=None, operation=None, candidate=None, previous=None, deadline_at=None,
            retiring=[], last_result=self.landed['result'], updated_at=self.at(-10))
        self.first = self.boundary('g1-aaaaaa', R0, -70)
        self.second = self.boundary('g3-cccccc', RETURN, -30)
        self.write('corner_rotation.json', self.ledger)
        self.write('nethack_corner.json', self.owner)
        self.write('game_switch.json', self.canonical)
        self.write('game-switch/requests/' + R0 + '.json', self.original)
        self.write('game-switch/requests/' + RETURN + '.json', self.landed)
        self.write('runtimes/g1-aaaaaa/nethack_boundary.json', self.first)
        self.write('runtimes/g3-cccccc/nethack_boundary.json', self.second)
        for runtime, generation in [('g1-aaaaaa', 1), ('g3-cccccc', 3)]:
            self.write(f'runtimes/{runtime}/presentation.json', dict(status='stopped'))
            self.write(f'runtimes/{runtime}/nethack_tiles.json', dict(schema_version=1,
                runtime_id=runtime, generation=generation, adapter_session=f'docich-game-g{generation}',
                game_window=f'game-g{generation}', status='stopped', cleanup_complete=True))

    def at(self, offset):
        return dt.datetime.fromtimestamp(self.now + offset, dt.timezone.utc).isoformat()

    def receipt(self, request, generation, status, created, updated):
        runtime = f'g{generation}-dddddd'
        return dict(schema_version=1, request_id=request, operation='switch', target='sorengame',
            generation=generation, runtime_id=runtime,
            runtime_dir=str(self.root / 'runtimes' / runtime),
            adapter_session=f'docich-game-g{generation}', game_window=f'game-g{generation}',
            agent_window=f'agent-g{generation}', status=status, created_at=self.at(created),
            updated_at=self.at(updated), result=dict(request_id=request, operation='switch',
                status=status, from_game='nethack', to_game='sorengame', generation=generation))

    def boundary(self, runtime, request, at):
        return dict(schema_version=1, game='nethack', runtime_id=runtime,
            generation=int(runtime.split('-')[0][1:]), request_id=request,
            player_name='fixture_player', outcome='ended', recorded_at=self.at(at))

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def snapshot(self):
        return {str(p.relative_to(self.root)):(p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}

    def collect(self, **kwargs):
        return diag._collect_nethack_rotation_evidence(self.root, self.now,
            player='fixture_player', probe=kwargs.pop('probe', lambda _: True), **kwargs)

    def test_bound_terminal_chain_and_unknown_detached_resources(self):
        result = self.collect()
        self.assertEqual(result['status'], 'observed')
        self.assertTrue(result['terminal_chain_matches'])
        self.assertTrue(result['legacy_contract_applicable'])
        self.assertTrue(result['snapshot_stable'])
        self.assertTrue(result['canonical']['lease_matches'])
        self.assertTrue(result['original_resources']['tiles_cleanup_complete'])
        self.assertTrue(result['rollback_resources']['tmux_absent'])
        # No process-table attribution exists in these durable files.
        self.assertIsNone(result['original_resources']['all_resources_released'])
        self.assertFalse(result['recovery_authority'])

    def test_contract_conditions_explain_inputs_without_authorizing_recovery(self):
        result = self.collect()
        self.assertEqual(result['contract_conditions'], dict(
            previous_game='absent', finish_reason='terminal', pending_source_absent=True,
            original_source_absent=True, original_restored_identity_absent=True,
            return_source_absent=True, restore_request_distinct=True,
            original_cleanup_pending='pending', automatic_dispatch_history='matched',
            manual_reservation_since_selection='absent'))
        self.assertFalse(result['recovery_authority'])
        self.assertIsNone(result['original_resources']['all_resources_released'])

    def test_owner_key_absence_null_and_invalid_values_are_distinct_fixed_categories(self):
        for key, expected in [('previous_game', 'sorengame'), ('finish_reason', 'terminal')]:
            initial = dict(self.owner)
            for value, category in [(None, 'null'), (expected, expected),
                                    ('PRIVATE_DO_NOT_EMIT', 'other'), ([], 'invalid'),
                                    ({'secret': 'PRIVATE_DO_NOT_EMIT'}, 'invalid')]:
                with self.subTest(key=key, value=value):
                    changed = dict(initial); changed[key] = value
                    self.write('nethack_corner.json', changed)
                    result = self.collect()
                    self.assertEqual(result['contract_conditions'][key], category)
                    self.assertNotIn('PRIVATE_DO_NOT_EMIT', json.dumps(result))
            changed = dict(initial); changed.pop(key, None)
            self.write('nethack_corner.json', changed)
            self.assertEqual(self.collect()['contract_conditions'][key], 'absent')
            self.write('nethack_corner.json', initial)

    def test_present_null_source_markers_do_not_qualify_as_absent(self):
        self.ledger['pending']['source'] = None
        self.original['result']['restored_runtime'] = None
        self.write('corner_rotation.json', self.ledger)
        self.write('game-switch/requests/' + R0 + '.json', self.original)
        result = self.collect()
        self.assertFalse(result['contract_conditions']['pending_source_absent'])
        self.assertFalse(result['contract_conditions']['original_restored_identity_absent'])
        self.assertFalse(result['legacy_contract_applicable'])
        # A null modern source is already invalid evidence, so no new
        # affirmative input observations may survive that failure.
        self.original['result']['source_runtime'] = None
        self.write('game-switch/requests/' + R0 + '.json', self.original)
        self.assertIsNone(self.collect()['contract_conditions'])

    def test_original_cleanup_pending_is_not_inferred_from_cleanup_clear(self):
        for value, category in [(None, 'null'), (True, 'pending'), (False, 'clear')]:
            self.original['result']['cleanup_pending'] = value
            self.write('game-switch/requests/' + R0 + '.json', self.original)
            self.assertEqual(self.collect()['contract_conditions']['original_cleanup_pending'], category)
        self.original['result'].pop('cleanup_pending')
        self.write('game-switch/requests/' + R0 + '.json', self.original)
        self.assertEqual(self.collect()['contract_conditions']['original_cleanup_pending'], 'absent')

    def test_restore_request_must_be_distinct_from_the_rotation_slot(self):
        self.owner['rotation_request_id'] = R0
        self.ledger['pending']['request_id'] = R0
        self.write('nethack_corner.json', self.owner)
        self.write('corner_rotation.json', self.ledger)
        result = self.collect()
        self.assertFalse(result['contract_conditions']['restore_request_distinct'])
        self.assertFalse(result['recovery_authority'])

    def test_legacy_history_separates_producer_proof_and_later_manual_reservations(self):
        for at, corner, source, automatic, manual in [
                (self.now - 110, 'nethack', 'reservation', 'matched', 'absent'),
                (self.at(-110), 'nethack', 'reservation', 'matched', 'absent'),
                (self.now - 90, 'nethack', 'reservation', 'missing', 'absent'),
                (self.now - 121, 'nethack', 'manual-reservation', 'missing', 'absent'),
                (self.now - 110, 'nethack', 'manual-reservation', 'missing', 'present'),
                (self.now - 110, 'weather', 'manual-reservation', 'missing', 'absent'),
                (self.now - 110, 'nethack', 'PRIVATE_DO_NOT_EMIT', 'missing', 'absent')]:
            with self.subTest(at=at, corner=corner, source=source):
                self.ledger['history'] = [dict(at=at, corner=corner, source=source)]
                self.write('corner_rotation.json', self.ledger)
                result = self.collect(); conditions = result['contract_conditions']
                self.assertEqual(conditions['automatic_dispatch_history'], automatic)
                self.assertEqual(conditions['manual_reservation_since_selection'], manual)
                self.assertFalse(result['recovery_authority'])
                self.assertNotIn('PRIVATE_DO_NOT_EMIT', json.dumps(result))

    def test_invalid_or_unbounded_history_cannot_preserve_partial_positive_matches(self):
        valid = self.ledger['history'][0]
        for history, reason in [(None, 'invalid'), ([valid, {}], 'invalid'),
                                ([valid, []], 'invalid'),
                                ([valid, dict(at=True)], 'invalid'),
                                ([valid, dict(at=-1)], 'invalid'),
                                ([valid, dict(at=self.now + 1)], 'invalid'),
                                ([valid] * 513, 'scan_limit')]:
            with self.subTest(reason=reason, history=history):
                self.ledger['history'] = history
                self.write('corner_rotation.json', self.ledger)
                conditions = self.collect()['contract_conditions']
                self.assertEqual(conditions['automatic_dispatch_history'], reason)
                self.assertEqual(conditions['manual_reservation_since_selection'], reason)
        self.ledger['history'] = [valid] * 512
        self.write('corner_rotation.json', self.ledger)
        self.assertEqual(self.collect()['contract_conditions']['automatic_dispatch_history'], 'matched')

    def test_history_timestamp_context_is_required(self):
        for key, value in [('last_seen_at', None), ('last_seen_at', self.now + 1),
                           ('selected_at', True), ('selected_at', self.now - 99)]:
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(self.ledger)
                target = changed['pending'] if key == 'selected_at' else changed
                target[key] = value; self.write('corner_rotation.json', changed)
                conditions = self.collect()['contract_conditions']
                self.assertEqual(conditions['automatic_dispatch_history'], 'invalid')
                self.assertEqual(conditions['manual_reservation_since_selection'], 'invalid')

    def test_resource_unknown_reasons_explain_coverage_without_new_probes(self):
        result = self.collect()
        always = ['detached_children_unobserved', 'unregistered_workers_unobserved']
        self.assertEqual(result['original_resources']['unknown_reasons'], always)
        for name in ('presentation.json', 'nethack_tiles.json'):
            (self.root / 'runtimes/g1-aaaaaa' / name).unlink()
        probe = mock.Mock(return_value=None)
        result = self.collect(probe=probe)
        self.assertEqual(probe.call_args_list, [mock.call(1), mock.call(3)])
        self.assertEqual(result['original_resources']['unknown_reasons'],
            ['presentation_record_missing', 'tiles_record_missing', 'tmux_probe_unavailable'] + always)
        self.assertIsNone(result['original_resources']['all_resources_released'])
        self.assertFalse(result['recovery_authority'])

    def test_unproven_tile_cleanup_and_failed_tmux_probe_have_fixed_reasons(self):
        path = self.root / 'runtimes/g1-aaaaaa/nethack_tiles.json'
        manifest = json.loads(path.read_text())
        manifest.pop('cleanup_complete'); self.write(str(path.relative_to(self.root)), manifest)
        result = self.collect(probe=mock.Mock(side_effect=RuntimeError('PRIVATE_DO_NOT_EMIT')))
        self.assertIn('tiles_cleanup_unproven', result['original_resources']['unknown_reasons'])
        self.assertIn('tmux_probe_unavailable', result['original_resources']['unknown_reasons'])
        self.assertNotIn('PRIVATE_DO_NOT_EMIT', json.dumps(result))
        self.assertIsNone(result['original_resources']['all_resources_released'])

    def test_new_fields_stay_bounded_through_the_existing_gateway(self):
        from ops.vm_actions import gateway
        result = self.collect()
        clean = gateway._sanitize_diagnostics({'nethack_rotation_evidence': result})
        self.assertEqual(clean['nethack_rotation_evidence'], result)
        self.assertLess(len(json.dumps(clean).encode()), 4096)

    def test_no_lifecycle_or_write_calls(self):
        # Import before patching: modules importing the writer must retain the
        # real function after this test's mock context has exited.
        from docich import game_switch, nethack_corner

        before = self.snapshot()
        with (mock.patch.object(diag.os, 'kill', side_effect=AssertionError('signal')),
              mock.patch.object(diag.os, 'killpg', side_effect=AssertionError('signal')),
              mock.patch.object(diag.fcntl, 'flock', side_effect=AssertionError('lock')),
              mock.patch.object(diag.subprocess, 'run', side_effect=AssertionError('exec')),
              mock.patch.object(game_switch, 'atomic_write_json', side_effect=AssertionError('write')),
              mock.patch.object(game_switch.GameSwitchCoordinator, 'recover', side_effect=AssertionError('recover')),
              mock.patch.object(nethack_corner.NethackCornerManager, 'recover_failed_rotation',
                         side_effect=AssertionError('recover'))):
            result = self.collect()
        self.assertTrue(result['terminal_chain_matches'])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ['corner_rotation.json', 'game-switch', 'game_switch.json',
                          'nethack_corner.json', 'runtimes'])

    def test_previous_game_owner_with_source_less_receipts_is_legacy_applicable(self):
        self.owner.update(previous_game='sorengame')
        self.write('nethack_corner.json', self.owner)
        self.assertTrue(self.collect()['legacy_contract_applicable'])
        self.owner.update(previous_game='ninvaders')
        self.write('nethack_corner.json', self.owner)
        self.assertFalse(self.collect()['legacy_contract_applicable'])

    def test_current_owner_selects_recorded_contract_without_becoming_legacy(self):
        self.owner.update(previous_game='sorengame', restore_recovery={'secret':'DO_NOT_EMIT'})
        self.write('nethack_corner.json', self.owner)
        result = self.collect()
        self.assertFalse(result['legacy_contract_applicable'])
        self.assertTrue(result['restore_recovery_present'])
        self.assertNotIn('DO_NOT_EMIT', json.dumps(result))

    def test_private_identifiers_paths_payloads_and_errors_never_leave(self):
        for relative, value in [('nethack_corner.json', self.owner),
                                ('game_switch.json', self.canonical),
                                ('game-switch/requests/' + R0 + '.json', self.original)]:
            value.update(secret='TOKEN_DO_NOT_EMIT', prompt='PROMPT_DO_NOT_EMIT',
                         argv=['ARGV_DO_NOT_EMIT'], last_error='ERROR_DO_NOT_EMIT')
            self.write(relative, value)
        text = json.dumps(self.collect())
        for private in (SLOT, R0, RETURN, LEASE, str(self.root), 'g1-aaaaaa', 'g3-cccccc',
                        'TOKEN_DO_NOT_EMIT','PROMPT_DO_NOT_EMIT','ARGV_DO_NOT_EMIT','ERROR_DO_NOT_EMIT'):
            self.assertNotIn(private, text)

    def test_each_boundary_is_bound_to_the_request_runtime_player_and_time(self):
        for which, relative in [('first','runtimes/g1-aaaaaa/nethack_boundary.json'),
                                ('second','runtimes/g3-cccccc/nethack_boundary.json')]:
            original = getattr(self, which)
            for key, bad in [('request_id',OTHER), ('runtime_id','g8-eeeeee'),
                             ('generation',True), ('schema_version',True), ('player_name','foreign'),
                             ('outcome','suspended'), ('recorded_at',self.at(1))]:
                with self.subTest(which=which, key=key):
                    changed = dict(original); changed[key] = bad
                    self.write(relative, changed)
                    self.assertFalse(self.collect()['terminal_chain_matches'])
            self.write(relative, original)

    def test_current_lease_cleanup_or_driver_mismatch_is_not_terminal_proof(self):
        for key,bad in [('lease_id',OTHER), ('generation',True), ('game','nethack')]:
            with self.subTest(key=key):
                changed=copy.deepcopy(self.canonical); changed['active'][key]=bad
                self.write('game_switch.json',changed)
                self.assertIsNot(self.collect()['terminal_chain_matches'],True)
        for key,bad in [('retiring',[{}]), ('request_id',OTHER), ('candidate',{}),
                         ('deadline_at',self.at(10)), ('revision',True)]:
            with self.subTest(key=key):
                changed=copy.deepcopy(self.canonical); changed[key]=bad
                self.write('game_switch.json',changed)
                self.assertFalse(self.collect()['terminal_chain_matches'])

    def test_foreign_or_nonterminal_owner_is_rejected_before_resource_probes(self):
        for key,bad in [('rotation_request_id',OTHER), ('schema_version',True), ('status','active')]:
            with self.subTest(key=key):
                owner=dict(self.owner); owner[key]=bad; self.write('nethack_corner.json',owner)
                probe=mock.Mock(side_effect=AssertionError('foreign resources'))
                result=self.collect(probe=probe)
                self.assertEqual(result['reason'],'owner_mismatch'); probe.assert_not_called()

    def test_manual_owner_or_reservation_blocks_chain(self):
        self.write('nethack_corner_manual.json',dict(schema_version=1,status='active'))
        self.assertFalse(self.collect()['terminal_chain_matches'])
        (self.root/'nethack_corner_manual.json').unlink()
        self.ledger['manual_pending']={'request_id':OTHER}
        self.write('corner_rotation.json',self.ledger)
        self.assertFalse(self.collect()['terminal_chain_matches'])

    def test_changed_owner_or_new_optional_file_discards_positive_claims(self):
        def change(_):
            self.owner['status']='active'; self.write('nethack_corner.json',self.owner)
            return True
        result=self.collect(probe=change)
        self.assertFalse(result['snapshot_stable'])
        self.assertIsNone(result['terminal_chain_matches'])
        self.assertIsNone(result['canonical'])
        self.assertIsNone(result['contract_conditions'])
        self.assertFalse(result['recovery_authority'])

    def test_optional_resource_appearing_during_probe_is_a_changed_snapshot(self):
        path=self.root/'runtimes/g1-aaaaaa/presentation.json'; path.unlink()
        def change(_):
            self.write('runtimes/g1-aaaaaa/presentation.json',dict(status='stopped'))
            return True
        result=self.collect(probe=change)
        self.assertFalse(result['snapshot_stable'])
        self.assertIsNone(result['original_resources'])

    def test_all_recheck_failures_discard_affirmative_observations(self):
        path = self.root / 'nethack_corner.json'
        for kind in ('invalid_json', 'symlink', 'directory', 'non_object', 'duplicate', 'large'):
            with self.subTest(kind=kind):
                def change(_):
                    if path.is_dir():
                        return True
                    if path.is_symlink():
                        return True
                    path.unlink()
                    if kind == 'symlink': path.symlink_to('/etc/passwd')
                    elif kind == 'directory': path.mkdir()
                    elif kind == 'non_object': path.write_text('[]')
                    elif kind == 'duplicate': path.write_text('{"x":1,"x":2}')
                    elif kind == 'large': path.write_bytes(b' ' * 65537)
                    else: path.write_text('{')
                    return True
                result = self.collect(probe=change)
                self.assertEqual(result['status'], 'unavailable')
                self.assertFalse(result['snapshot_stable'])
                self.assertIsNone(result['terminal_chain_matches'])
                self.assertIsNone(result['canonical'])
                self.assertIsNone(result['original_resources'])
                self.assertIsNone(result['contract_conditions'])
                self.assertFalse(result['recovery_authority'])
                if path.is_dir(): path.rmdir()
                else: path.unlink()
                self.write('nethack_corner.json', self.owner)

    def test_present_modern_source_identities_match_each_selected_boundary(self):
        for receipt, runtime, generation in ((self.original, 'g1-aaaaaa', 1),
                                              (self.landed, 'g3-cccccc', 3)):
            receipt['result']['source_runtime'] = dict(game='nethack', adapter='cli',
                runtime_id=runtime, generation=generation, lease_id=OTHER)
            self.write('game-switch/requests/' + receipt['request_id'] + '.json', receipt)
        self.write('game_switch.json', self.canonical)
        result = self.collect()
        self.assertTrue(result['terminal_chain_matches'])
        self.assertFalse(result['legacy_contract_applicable'])
        self.assertFalse(result['contract_conditions']['original_source_absent'])
        self.assertFalse(result['contract_conditions']['return_source_absent'])
        for receipt in (self.original, self.landed):
            original = copy.deepcopy(receipt)
            for key, bad in (('game', 'sorengame'), ('adapter', 'soren'),
                             ('runtime_id', 'g8-eeeeee'), ('generation', True),
                             ('generation', 8), ('lease_id', 'invalid'), ('lease_id', None)):
                with self.subTest(request=receipt['request_id'], field=key, bad=bad):
                    changed = copy.deepcopy(original)
                    changed['result']['source_runtime'][key] = bad
                    self.write('game-switch/requests/' + receipt['request_id'] + '.json', changed)
                    self.assertIsNot(self.collect()['terminal_chain_matches'], True)
            for bad in (None, {}, []):
                changed = copy.deepcopy(original)
                changed['result']['source_runtime'] = bad
                self.write('game-switch/requests/' + receipt['request_id'] + '.json', changed)
                self.assertIsNot(self.collect()['terminal_chain_matches'], True)
            self.write('game-switch/requests/' + receipt['request_id'] + '.json', original)

    def test_original_source_generation_precedes_failed_switch_even_without_modern_identity(self):
        self.owner['rotation_runtime_id'] = 'g2-aaaaaa'
        self.write('nethack_corner.json', self.owner)
        self.write('runtimes/g2-aaaaaa/nethack_boundary.json', self.boundary('g2-aaaaaa', R0, -70))
        self.assertFalse(self.collect()['chronology_matches'])
        self.assertFalse(self.collect()['terminal_chain_matches'])

    def test_parent_directory_symlink_is_never_followed(self):
        directory=self.root/'runtimes/g1-aaaaaa'; outside=self.root/'external'
        directory.rename(outside); directory.symlink_to(outside,target_is_directory=True)
        self.assertEqual(self.collect()['status'],'unavailable')

    def test_fixed_player_definition_is_bounded_and_not_emitted(self):
        prod=self.root/'docich'; path=prod/'config/games/nethack.toml'
        path.parent.mkdir(parents=True); path.write_text('[nethack]\nplayer_name="fixture_player"\n')
        with mock.patch.object(diag,'PROD_ROOT',prod):
            self.assertEqual(diag._nethack_evidence_player(),'fixture_player')
            path.write_bytes(b'x'*16385)
            self.assertIsNone(diag._nethack_evidence_player())
            path.unlink(); path.symlink_to('/etc/passwd')
            self.assertIsNone(diag._nethack_evidence_player())

    def test_symlink_fifo_duplicate_and_oversize_records_fail_closed(self):
        relative='runtimes/g1-aaaaaa/nethack_boundary.json'; path=self.root/relative
        for kind in ('link','fifo','duplicate','large','non_object'):
            with self.subTest(kind=kind):
                path.unlink()
                if kind=='link': path.symlink_to('/etc/passwd')
                elif kind=='fifo': os.mkfifo(path)
                elif kind=='duplicate': path.write_text('{"schema_version":1,"schema_version":1}')
                elif kind=='large': path.write_bytes(b' '*65537)
                else: path.write_text('[]')
                self.assertEqual(self.collect()['status'],'unavailable')
                path.unlink(); self.write(relative,self.first)

    def test_directory_link_traversal_invalid_ids_and_scan_bounds(self):
        self.owner['rotation_runtime_id']='../../etc/passwd'
        self.write('nethack_corner.json',self.owner)
        self.assertEqual(self.collect()['reason'],'invalid_identity')
        self.owner['rotation_runtime_id']='g1-aaaaaa'; self.write('nethack_corner.json',self.owner)
        with mock.patch.object(diag._NethackEvidenceReader,'RUNTIME_LIMIT',1):
            self.assertEqual(self.collect()['reason'],'scan_limit')
        (self.root/'runtimes/g3-eeeeee').mkdir()
        self.assertEqual(self.collect()['reason'],'runtime_ambiguous')

    def test_missing_resources_are_unknown_and_live_or_foreign_resources_are_negative(self):
        self.assertFalse(self.collect(probe=lambda _:False)['original_resources']['all_resources_released'])
        self.assertIsNone(self.collect(probe=lambda _:None)['original_resources']['all_resources_released'])
        self.write('runtimes/g1-aaaaaa/presentation.json',dict(status='ready'))
        self.assertFalse(self.collect()['original_resources']['all_resources_released'])
        self.write('runtimes/g1-aaaaaa/nethack_tiles.json',dict(schema_version=1,runtime_id='g8-eeeeee'))
        self.assertFalse(self.collect()['original_resources']['tiles_identity_matches'])

    def test_default_tmux_probe_uses_only_exact_read_only_methods(self):
        probe=mock.Mock()
        probe.window_target_exists.return_value=False
        probe.session_target_exists.return_value=False
        with mock.patch('docich.hanjuku_manual_cancel._ProbeTmux',return_value=probe):
            self.assertTrue(diag._nethack_evidence_tmux_absent(3))
        self.assertEqual(probe.mock_calls,[
            mock.call.window_target_exists('docich:game-g3',strict=True),
            mock.call.window_target_exists('docich:agent-g3',strict=True),
            mock.call.session_target_exists('docich-game-g3',strict=True)])


if __name__=='__main__':
    unittest.main()
