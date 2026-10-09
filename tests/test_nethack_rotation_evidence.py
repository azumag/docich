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
            pending=dict(corner='nethack', phase='dispatched', request_id=SLOT), manual_pending=None)
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

    def test_no_lifecycle_or_write_calls(self):
        before = self.snapshot()
        with (mock.patch.object(diag.os, 'kill', side_effect=AssertionError('signal')),
              mock.patch.object(diag.os, 'killpg', side_effect=AssertionError('signal')),
              mock.patch.object(diag.fcntl, 'flock', side_effect=AssertionError('lock')),
              mock.patch.object(diag.subprocess, 'run', side_effect=AssertionError('exec')),
              mock.patch('docich.game_switch.atomic_write_json', side_effect=AssertionError('write')),
              mock.patch('docich.game_switch.GameSwitchCoordinator.recover', side_effect=AssertionError('recover')),
              mock.patch('docich.nethack_corner.NethackCornerManager.recover_failed_rotation',
                         side_effect=AssertionError('recover'))):
            result = self.collect()
        self.assertTrue(result['terminal_chain_matches'])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ['corner_rotation.json', 'game-switch', 'game_switch.json',
                          'nethack_corner.json', 'runtimes'])

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
        self.assertFalse(result['recovery_authority'])

    def test_optional_resource_appearing_during_probe_is_a_changed_snapshot(self):
        path=self.root/'runtimes/g1-aaaaaa/presentation.json'; path.unlink()
        def change(_):
            self.write('runtimes/g1-aaaaaa/presentation.json',dict(status='stopped'))
            return True
        result=self.collect(probe=change)
        self.assertFalse(result['snapshot_stable'])
        self.assertIsNone(result['original_resources'])

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
