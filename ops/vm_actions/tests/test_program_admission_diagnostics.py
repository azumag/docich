"""Synthetic, read-only admission observations. No VM, locks or services."""
import json
import os
from pathlib import Path

import pytest

from ops.vm_actions import collect_diagnostics as diag
from docich import corner_boundary

REQUEST = '12345678-1234-1234-1234-123456789abc'
OTHER = '87654321-4321-4321-4321-cba987654321'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def admission(tmp_path, monkeypatch):
    monkeypatch.delenv('COMMENT_QUEUE_DIR', raising=False)
    owners = tmp_path / 'owners'
    soren = tmp_path / 'soren'
    runtime = soren / 'tmp' / 'state'
    owners.mkdir()
    runtime.mkdir(parents=True)
    write(owners / 'corner_rotation.json', {'pending': {
        'corner': 'moon-buggy', 'phase': 'dispatched', 'selected_at': 100,
        'request_id': REQUEST}})
    write(runtime / 'docich_program_queue' / 'retro_corner.json', {
        'status': 'waiting_turn', 'requested_at': 100, 'wait_deadline_ts': 700})
    write(runtime / 'current_prediction.json', {'status': 'RESOLVED'})
    write(runtime / 'corner_boundary_prediction.json', {'completed_at': 110})
    (runtime / 'prediction_worker.paused').touch()
    def collect():
        return diag._collect_program_admission_evidence(owners, soren, 200)
    return owners, soren, runtime, collect


def test_observed_pre_owner_is_bounded_and_does_not_write_or_lock(admission, monkeypatch):
    owners, soren, runtime, collect = admission
    paths = lambda: {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                     for p in owners.parent.rglob('*') if p.is_file()}
    before = paths()
    monkeypatch.setattr(diag.fcntl, 'flock', lambda *_: pytest.fail('must not flock'))
    result = collect()
    assert result['status'] == 'observed'
    assert result['reason'] == 'pre_owner'
    assert result['pending_queue_matches'] is True
    assert result['owner_created_for_request'] is False
    assert result['program_lock'] == {'state': 'unknown', 'present': False}
    assert result['registry_owner_free'] is True
    assert result['fifo_scan_complete'] is True
    assert result['earlier_waiter'] is False
    assert result['prediction']['waiting'] is False
    assert result['audio'] == {'readable': True, 'queued': 0, 'playing': 0,
                              'quiet': True, 'runtime_source_verified': False,
                              'source_basis': 'collector_env_or_default'}
    assert result['recovery_authority'] is False
    assert len(json.dumps(result)) < 1200
    assert paths() == before
    assert not (runtime / 'docich_program.lock').exists()


@pytest.mark.parametrize('field,value', [
    ('requested_at', 99), ('requested_at', True), ('requested_at', '100'),
    ('requested_at', None), ('status', 'secret-status')])
def test_stale_or_malformed_target_queue_is_unknown(admission, field, value):
    _, _, runtime, collect = admission
    queue = {'status': 'waiting_turn', 'requested_at': 100}
    queue[field] = value
    write(runtime / 'docich_program_queue' / 'retro_corner.json', queue)
    result = collect()
    assert result['pending_queue_matches'] is None
    assert result['registry_owner_free'] is None
    assert result['earlier_waiter'] is None
    assert result['recovery_authority'] is False


@pytest.mark.parametrize('change', ['absent', 'unknown_corner', 'selected', 'manual',
                                    'bad_uuid', 'future', 'boolean'])
def test_unavailable_pending_stays_unknown(admission, change):
    owners, _, _, collect = admission
    ledger = json.loads((owners / 'corner_rotation.json').read_text())
    if change == 'absent':
        ledger.pop('pending')
    elif change == 'unknown_corner':
        ledger['pending']['corner'] = 'moonbuggy'
    elif change == 'selected':
        ledger['pending']['phase'] = 'selected'
    elif change == 'manual':
        ledger['manual_pending'] = {'corner': 'moon-buggy'}
    elif change == 'bad_uuid':
        ledger['pending']['request_id'] = 'secret'
    else:
        ledger['pending']['selected_at'] = 201 if change == 'future' else True
    write(owners / 'corner_rotation.json', ledger)
    assert collect()['pending_queue_matches'] is None


@pytest.mark.parametrize('status,stamp,expected', [
    ('waiting', 99, True), ('waiting_turn', 99, True),
    ('waiting', 100, False), ('waiting_turn', 101, False),
    ('waiting_boundary', 1, False), ('running', 1, False), ('done', 1, False),
    ('error', 1, False), ('cancelled', 1, False), ('expired', 1, False)])
def test_fifo_matches_strict_runtime_order_and_statuses(admission, status, stamp, expected):
    _, _, runtime, collect = admission
    write(runtime / 'docich_program_queue' / 'aaa.json',
          {'status': status, 'requested_at': stamp})
    result = collect()
    assert result['earlier_waiter'] is expected
    assert corner_boundary._earlier_waiter_exists(runtime, 'retro_corner', 100) is expected


def test_fifo_does_not_apply_lottery_three_hour_expiry(admission):
    _, _, runtime, collect = admission
    write(runtime / 'docich_program_queue' / 'aaa.json',
          {'status': 'waiting_turn', 'requested_at': 1})
    assert diag._collect_program_admission_evidence(runtime.parent.parent.parent / 'owners',
                                                   runtime.parent.parent, 20000)['earlier_waiter'] is True


@pytest.mark.parametrize('bad', [None, True, '99', -1, 201])
def test_fifo_incomplete_timestamp_is_unknown(admission, bad):
    _, _, runtime, collect = admission
    write(runtime / 'docich_program_queue' / 'aaa.json',
          {'status': 'waiting', 'requested_at': bad})
    assert collect()['earlier_waiter'] is None


@pytest.mark.parametrize('case', ['unknown_status', 'invalid_json', 'scan_limit', 'symlink'])
def test_fifo_incomplete_scan_never_claims_clear(admission, case):
    _, _, runtime, collect = admission
    queue = runtime / 'docich_program_queue'
    if case == 'scan_limit':
        for index in range(129):
            write(queue / f'other{index}.json', {'status': 'done'})
    elif case == 'symlink':
        (queue / 'other.json').symlink_to(queue / 'retro_corner.json')
    elif case == 'invalid_json':
        (queue / 'other.json').write_text('{')
    else:
        write(queue / 'other.json', {'status': 'secret', 'requested_at': 1})
    result = collect()
    assert result['earlier_waiter'] is None
    assert result['fifo_scan_complete'] is None


@pytest.mark.parametrize('owner,status,expected', [
    ('nethack_corner', 'active', False), ('nethack_corner', 'starting', False),
    ('nethack_corner', 'restoring', False), ('nethack_corner', 'recovery_required', False),
    ('nethack_corner', 'failed', False), ('nethack_corner', 'completed', True),
    ('paper_corner', 'failed', None), ('paper_corner_manual', 'failed', None),
    ('doushi_corner', 'failed', None), ('nethack_corner', 'secret', None),
    ('retro_corner', 'failed', True)])
def test_registry_exact_owner_contract(admission, owner, status, expected):
    owners, _, runtime, collect = admission
    write(owners / (owner + '.json'), {'status': status})
    write(runtime / 'docich_program_active.json', {'owner_state': str(owners / (owner + '.json'))})
    assert collect()['registry_owner_free'] is expected


@pytest.mark.parametrize('previous', ['/secret/elsewhere.json', 'retro_corner.json', None, 7])
def test_registry_does_not_follow_unreviewed_path(admission, previous):
    _, _, runtime, collect = admission
    write(runtime / 'docich_program_active.json', {'owner_state': previous})
    assert collect()['registry_owner_free'] is None


def test_registry_known_missing_owner_is_free(admission):
    owners, _, runtime, collect = admission
    write(runtime / 'docich_program_active.json', {'owner_state': str(owners / 'nethack_corner.json')})
    assert collect()['registry_owner_free'] is True


def test_owner_identity_is_not_inferred_from_other_reservation(admission):
    owners, _, _, collect = admission
    write(owners / 'retro_corner.json', {'status': 'completed', 'rotation_request_id': OTHER})
    assert collect()['owner_created_for_request'] is False
    write(owners / 'retro_corner.json', {'status': 'starting', 'rotation_request_id': REQUEST})
    assert collect()['owner_created_for_request'] is True
    assert collect()['reason'] == 'owner_already_created'


@pytest.mark.parametrize('worker,status,boundary,waiting', [
    (True, 'ACTIVE', 99, True), (True, 'LOCKED', 99, True),
    (True, 'ACTIVE', 100, False), (True, 'LOCKED', 200, False),
    (True, 'ACTIVE', 201, True), (True, 'ACTIVE', None, None),
    (False, 'ACTIVE', 1, False), (False, 'LOCKED', None, False),
    (True, 'RESOLVED', 1, False), (True, 'private', 1, None),
    (None, 'ACTIVE', 1, None)])
def test_prediction_worker_local_status_and_request_boundary(admission, monkeypatch,
                                                            worker, status, boundary, waiting):
    _, _, runtime, collect = admission
    (runtime / 'prediction_worker.paused').unlink()
    (runtime / 'prediction_worker.pid').write_text('123')
    if worker is None:
        (runtime / 'prediction_worker.pid').unlink()
    elif worker:
        monkeypatch.setattr(diag.os, 'kill', lambda pid, signal: None)
    else:
        def stopped(*_):
            raise ProcessLookupError
        monkeypatch.setattr(diag.os, 'kill', stopped)
    write(runtime / 'current_prediction.json', {'status': status})
    if boundary is None:
        (runtime / 'corner_boundary_prediction.json').unlink()
    else:
        write(runtime / 'corner_boundary_prediction.json', {'completed_at': boundary})
    result = collect()['prediction']
    assert result['worker_running'] is worker
    assert result['waiting'] is waiting


@pytest.mark.parametrize('suffix,queued,playing', [('.txt', 1, 0), ('.playing', 0, 1)])
def test_audio_reuses_queue_counts_without_reading_bodies(admission, suffix, queued, playing):
    _, soren, _, collect = admission
    queue = soren / 'tmp' / '.comment_queue'
    queue.mkdir()
    (queue / ('comment_SECRET' + suffix)).write_text('PRIVATE BODY')
    result = collect()
    assert result['audio']['queued'] == queued
    assert result['audio']['playing'] == playing
    assert result['audio']['quiet'] is False
    assert 'PRIVATE' not in json.dumps(result)
    assert 'SECRET' not in json.dumps(result)


def test_dotenv_contents_never_read_or_assumed_to_match(admission):
    _, soren, _, collect = admission
    (soren / '.env').write_text('COMMENT_QUEUE_DIR=/private/secret\nTOKEN=SECRET')
    result = collect()
    assert result['audio']['runtime_source_verified'] is False
    assert 'SECRET' not in json.dumps(result)


def test_unreadable_audio_is_unknown_even_though_runtime_fails_open(admission, monkeypatch):
    original = diag._ProgramAdmissionReader.listing
    def unreadable(reader, parts):
        if reader.root.name == '.comment_queue':
            raise PermissionError
        return original(reader, parts)
    monkeypatch.setattr(diag._ProgramAdmissionReader, 'listing', unreadable)
    result = admission[-1]()
    assert result['audio']['quiet'] is None
    assert result['audio']['readable'] is None


@pytest.mark.parametrize('target', ['ledger', 'queue', 'prediction', 'owner'])
def test_oversized_or_symlink_sources_cannot_supply_evidence(admission, target):
    owners, _, runtime, collect = admission
    paths = {'ledger': owners / 'corner_rotation.json',
             'queue': runtime / 'docich_program_queue' / 'retro_corner.json',
             'prediction': runtime / 'current_prediction.json',
             'owner': owners / 'retro_corner.json'}
    path = paths[target]
    path.write_text('x' * 65537)
    assert collect()['status'] == 'unknown'
    path.unlink()
    path.symlink_to(owners / 'corner_rotation.json' if target != 'ledger' else runtime / 'current_prediction.json')
    assert collect()['status'] == 'unknown'


def test_symlink_ancestor_and_audio_item_are_not_followed(admission):
    owners, soren, runtime, collect = admission
    queue = soren / 'tmp' / '.comment_queue'
    queue.mkdir()
    (queue / 'comment_secret.txt').symlink_to(owners / 'corner_rotation.json')
    assert collect()['audio']['quiet'] is None
    (queue / 'comment_secret.txt').unlink()
    real = owners.parent / 'real'
    owners.rename(real)
    owners.symlink_to(real, target_is_directory=True)
    assert collect()['status'] == 'unknown'


@pytest.mark.parametrize('race', ['queue', 'ledger_same_bytes', 'fifo_add', 'audio_add'])
def test_mid_observation_updates_reset_all_dependent_proofs(admission, monkeypatch, race):
    owners, soren, runtime, collect = admission
    original = diag._ProgramAdmissionReader.recheck
    changed = False
    def recheck(reader):
        nonlocal changed
        if not changed:
            changed = True
            if race == 'queue':
                write(runtime / 'docich_program_queue' / 'retro_corner.json',
                      {'status': 'waiting_turn', 'requested_at': 99})
            elif race == 'ledger_same_bytes':
                ledger = owners / 'corner_rotation.json'
                replacement = owners / 'replacement'
                replacement.write_bytes(ledger.read_bytes())
                replacement.replace(ledger)
            elif race == 'fifo_add':
                write(runtime / 'docich_program_queue' / 'other.json',
                      {'status': 'waiting', 'requested_at': 1})
            else:
                queue = soren / 'tmp' / '.comment_queue'
                queue.mkdir()
                (queue / 'comment_new.txt').touch()
        original(reader)
    monkeypatch.setattr(diag._ProgramAdmissionReader, 'recheck', recheck)
    result = collect()
    assert result['status'] == 'unknown'
    assert result['pending_queue_matches'] is None
    assert result['earlier_waiter'] is None
    assert result['audio']['quiet'] is None


def test_lock_presence_never_claims_held_or_free(admission):
    _, _, runtime, collect = admission
    (runtime / 'docich_program.lock').touch()
    assert collect()['program_lock'] == {'state': 'unknown', 'present': True}


def test_fixed_owner_catalog_mapping_covers_generic_and_dedicated(admission):
    owners, _, runtime, collect = admission
    for corner, stem in diag.PROGRAM_ADMISSION_OWNERS.items():
        write(owners / 'corner_rotation.json', {'pending': {
            'corner': corner, 'phase': 'dispatched', 'selected_at': 100,
            'request_id': REQUEST}})
        write(runtime / 'docich_program_queue' / (stem + '.json'),
              {'status': 'waiting_turn', 'requested_at': 100})
        assert collect()['pending_queue_matches'] is True, corner


def test_public_projection_omits_identifiers_paths_and_free_text(admission):
    owners, _, runtime, collect = admission
    write(owners / 'retro_corner.json', {'status': 'completed', 'request_id': OTHER,
                                        'error': 'TOPSECRET', 'path': '/secret/path'})
    result = json.dumps(collect())
    for private in (REQUEST, OTHER, str(owners), str(runtime), 'TOPSECRET', '/secret/path',
                    'moon-buggy', 'retro_corner'):
        assert private not in result
    assert 'admission_ready' not in result
    assert 'recovery_authority": false' in result


def test_deeply_nested_bounded_json_is_unknown_without_crashing(admission):
    _, _, runtime, collect = admission
    path = runtime / 'docich_program_queue' / 'other.json'
    raw = '{"status":"waiting","requested_at":99,"extra":' + '[' * 16000 + '0' + ']' * 16000 + '}'
    assert len(raw) < 65536
    path.write_text(raw)
    assert collect()['status'] == 'unknown'


def test_owner_missing_rotation_identity_is_unknown(admission):
    owners, _, _, collect = admission
    write(owners / 'retro_corner.json', {'status': 'active', 'request_id': REQUEST})
    result = collect()
    assert result['owner_created_for_request'] is None
    assert result['reason'] == 'owner_identity_unverified'


def test_audio_scans_remain_bounded_and_avoid_legacy_rescan(admission, monkeypatch):
    _, soren, _, collect = admission
    monkeypatch.setattr(diag, '_collect_audio_queue', lambda *_: pytest.fail('unbounded scan'))
    queue = soren / 'tmp' / '.comment_queue'
    queue.mkdir()
    assert collect()['audio']['quiet'] is True
    for index in range(129):
        (queue / f'comment_{index}.txt').touch()
    assert collect()['audio']['quiet'] is None


def test_collector_env_does_not_attest_worker_queue_source(admission, monkeypatch):
    _, soren, _, collect = admission
    monkeypatch.setenv('COMMENT_QUEUE_DIR', str(soren / 'tmp' / 'other_queue'))
    result = collect()['audio']
    assert result['quiet'] is True
    assert result['runtime_source_verified'] is False
    assert result['source_basis'] == 'collector_env_or_default'


@pytest.mark.parametrize('case,stage,code', [
    ('missing_pending', 'pending', 'missing'),
    ('invalid_pending_json', 'pending', 'json_invalid'),
    ('invalid_pending_schema', 'pending', 'schema_invalid'),
    ('oversized_owner', 'owner', 'oversize'),
    ('owner_directory', 'owner', 'non_regular'),
    ('missing_queue', 'queue', 'missing'),
    ('queue_mismatch', 'queue', 'queue_mismatch'),
    ('symlink_lock', 'lock', 'unsafe_path'),
    ('invalid_registry_json', 'registry', 'json_invalid'),
    ('fifo_directory', 'fifo', 'non_regular'),
    ('fifo_scan_limit', 'fifo', 'scan_limit'),
    ('prediction_array', 'prediction', 'schema_invalid'),
    ('prediction_symlink', 'prediction', 'unsafe_path'),
    ('audio_directory', 'audio', 'non_regular'),
])
def test_failure_provenance_distinguishes_safe_fixed_stages(admission, case, stage, code):
    owners, soren, runtime, collect = admission
    if case == 'missing_pending':
        (owners / 'corner_rotation.json').unlink()
    elif case == 'invalid_pending_json':
        (owners / 'corner_rotation.json').write_text('{SECRET')
    elif case == 'invalid_pending_schema':
        write(owners / 'corner_rotation.json', {'pending': {'corner': 'SECRET'}})
    elif case == 'oversized_owner':
        (owners / 'retro_corner.json').write_text('SECRET' * 12000)
    elif case == 'owner_directory':
        (owners / 'retro_corner.json').mkdir()
    elif case == 'missing_queue':
        (runtime / 'docich_program_queue' / 'retro_corner.json').unlink()
    elif case == 'queue_mismatch':
        write(runtime / 'docich_program_queue' / 'retro_corner.json',
              {'status': 'waiting_turn', 'requested_at': 99})
    elif case == 'symlink_lock':
        (runtime / 'docich_program.lock').symlink_to(owners / 'corner_rotation.json')
    elif case == 'invalid_registry_json':
        (runtime / 'docich_program_active.json').write_text('{SECRET')
    elif case == 'fifo_directory':
        (runtime / 'docich_program_queue' / 'SECRET').mkdir()
    elif case == 'fifo_scan_limit':
        for index in range(129):
            write(runtime / 'docich_program_queue' / f'SECRET{index}.json', {'status': 'done'})
    elif case == 'prediction_array':
        write(runtime / 'current_prediction.json', [])
    elif case == 'prediction_symlink':
        (runtime / 'current_prediction.json').unlink()
        (runtime / 'current_prediction.json').symlink_to(owners / 'corner_rotation.json')
    else:
        (soren / 'tmp' / '.comment_queue' / 'comment_SECRET.txt').mkdir(parents=True)
    result = collect()
    assert result['status'] == 'unknown'
    assert result['failure_stage'] == stage
    assert result['failure_code'] == code
    assert result['recheck_completed'] is False
    assert result['pending_queue_matches'] is None
    assert result['recovery_authority'] is False
    for private in ('SECRET', REQUEST, str(owners), str(soren)):
        assert private not in json.dumps(result)


@pytest.mark.parametrize('directory', ['audio_dedup', '.weather_audio_receipts'])
def test_public_audio_writer_metadata_directory_is_not_an_audio_item(admission, directory):
    # Public fixed Soren gitlink68f02445 writes these normal queue directories:
    # lib/outbound_queue.sh:324 and lib/weather_audio_consumer.py:242.
    # Audio ignores non-item names without stat or descent; FIFO stays strict.
    _, soren, _, collect = admission
    (soren / 'tmp' / '.comment_queue' / directory).mkdir(parents=True)
    result = collect()
    assert result['status'] == 'observed'
    assert result['audio']['quiet'] is True
    assert (result['audio']['queued'], result['audio']['playing']) == (0, 0)
    assert result['recheck_completed'] is True
    assert directory not in json.dumps(result)


def test_success_truthfully_marks_completed_recheck(admission):
    result = admission[-1]()
    assert result['status'] == 'observed'
    assert result['recheck_completed'] is True
    assert result['failure_stage'] == 'none'
    assert result['failure_code'] == 'none'


def test_public_prediction_writer_without_status_remains_unverified(admission, monkeypatch):
    # Public twitch_predictions.sh:845-855 writes this shape without status.
    _, _, runtime, collect = admission
    (runtime / 'prediction_worker.paused').unlink()
    (runtime / 'prediction_worker.pid').write_text('123')
    monkeypatch.setattr(diag.os, 'kill', lambda *_: None)
    write(runtime / 'current_prediction.json', {
        'id': 'SECRET', 'title': 'SECRET', 'outcomes': {'a': 'SECRET', 'b': 'SECRET'},
        'created_at': 90, 'lock_at': 110})
    result = collect()
    assert result['status'] == 'observed'
    assert result['prediction']['worker_running'] is True
    assert result['prediction']['local_active'] is None
    assert result['prediction']['waiting'] is None
    assert result['recheck_completed'] is True
    assert 'SECRET' not in json.dumps(result)


def test_read_error_never_exposes_exception_body_or_filename(admission, monkeypatch):
    original = diag._ProgramAdmissionReader.sample
    def unreadable(reader, parts, mode='json'):
        if tuple(parts) == ('retro_corner.json',):
            raise PermissionError(13, 'SECRET exception body', '/SECRET/path')
        return original(reader, parts, mode)
    monkeypatch.setattr(diag._ProgramAdmissionReader, 'sample', unreadable)
    result = admission[-1]()
    assert (result['failure_stage'], result['failure_code']) == ('owner', 'read_error')
    assert result['recheck_completed'] is False
    assert 'SECRET' not in json.dumps(result)


def test_recheck_failure_is_not_reported_as_completed(admission, monkeypatch):
    original = diag._ProgramAdmissionReader.recheck
    def changed(reader):
        raise diag._ProgramAdmissionFailure('evidence_changed')
    monkeypatch.setattr(diag._ProgramAdmissionReader, 'recheck', changed)
    result = admission[-1]()
    assert (result['failure_stage'], result['failure_code']) == ('recheck_owners', 'evidence_changed')
    assert result['recheck_completed'] is False
    monkeypatch.setattr(diag._ProgramAdmissionReader, 'recheck', original)
    assert admission[-1]()['recheck_completed'] is True


def test_failure_vocabularies_are_closed_and_do_not_echo_input():
    result = diag._program_admission_empty(stage='SECRET', code='SECRET')
    assert result['failure_stage'] in diag.PROGRAM_ADMISSION_FAILURE_STAGES
    assert result['failure_code'] in diag.PROGRAM_ADMISSION_FAILURE_CODES
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('name', ['audio_dedup', '.weather_audio_receipts',
                                  'comment_SECRET.tmp', 'other_SECRET.txt'])
def test_non_audio_entries_are_not_statted_followed_or_opened(admission, monkeypatch, name):
    _, soren, _, collect = admission
    queue = soren / 'tmp' / '.comment_queue'
    queue.mkdir()
    outside = soren.parent / 'SECRET_private_directory'
    outside.mkdir()
    (outside / 'comment_SECRET.txt').write_text('SECRET body')
    (queue / name).symlink_to(outside, target_is_directory=True)
    inode = queue.stat().st_ino
    original = diag.os.scandir
    class GuardedEntries:
        def __init__(self, context):
            self.context = context
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.context.close()
        def __iter__(self):
            for entry in self.context:
                assert entry.name == name
                class NoStat:
                    def __init__(self, name):
                        self.name = name
                    def stat(self, *args, **kwargs):
                        pytest.fail('non-audio entry must not be statted')
                yield NoStat(entry.name)
    def guarded(directory):
        context = original(directory)
        if isinstance(directory, int) and os.fstat(directory).st_ino == inode:
            return GuardedEntries(context)
        return context
    monkeypatch.setattr(diag.os, 'scandir', guarded)
    result = collect()
    assert result['status'] == 'observed'
    assert result['recheck_completed'] is True
    assert result['audio']['quiet'] is True
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('suffix', ['.txt', '.playing'])
@pytest.mark.parametrize('kind,code', [('symlink', 'unsafe_path'), ('directory', 'non_regular'),
                                     ('fifo', 'non_regular')])
def test_target_audio_nonregular_is_always_rejected(admission, suffix, kind, code):
    owners, soren, _, collect = admission
    queue = soren / 'tmp' / '.comment_queue'
    queue.mkdir()
    target = queue / ('comment_SECRET' + suffix)
    if kind == 'symlink':
        target.symlink_to(owners / 'corner_rotation.json')
    elif kind == 'directory':
        target.mkdir()
    else:
        os.mkfifo(target)
    result = collect()
    assert (result['failure_stage'], result['failure_code']) == ('audio', code)
    assert result['audio']['quiet'] is None
    assert result['recheck_completed'] is False
    assert 'SECRET' not in json.dumps(result)


def test_non_audio_metadata_does_not_change_real_audio_counts(admission):
    _, soren, _, collect = admission
    queue = soren / 'tmp' / '.comment_queue'
    (queue / 'audio_dedup').mkdir(parents=True)
    (queue / '.weather_audio_receipts').mkdir()
    (queue / 'comment_not_audio.tmp').mkdir()
    (queue / 'other.txt').mkdir()
    (queue / 'comment_queued.txt').touch()
    (queue / 'comment_active.playing').touch()
    result = collect()
    assert result['status'] == 'observed'
    assert (result['audio']['queued'], result['audio']['playing']) == (1, 1)
    assert result['audio']['quiet'] is False


@pytest.mark.parametrize('count,accepted', [(128, True), (129, False)])
def test_non_audio_entries_still_count_toward_scan_limit(admission, count, accepted):
    _, soren, _, collect = admission
    queue = soren / 'tmp' / '.comment_queue'
    queue.mkdir()
    for index in range(count):
        (queue / f'metadata{index}').mkdir()
    result = collect()
    if accepted:
        assert result['status'] == 'observed'
        assert result['recheck_completed'] is True
    else:
        assert (result['failure_stage'], result['failure_code']) == ('audio', 'scan_limit')
        assert result['recheck_completed'] is False


@pytest.mark.parametrize('change', ['ignored_add', 'ignored_rename', 'audio_metadata'])
def test_audio_listing_namespace_and_target_metadata_are_rechecked(admission, monkeypatch, change):
    _, soren, _, collect = admission
    queue = soren / 'tmp' / '.comment_queue'
    (queue / 'audio_dedup').mkdir(parents=True)
    (queue / 'comment_item.txt').write_text('first')
    original = diag._ProgramAdmissionReader.recheck
    def changed(reader):
        if reader.root == queue:
            if change == 'ignored_add':
                (queue / 'new_metadata').mkdir()
            elif change == 'ignored_rename':
                (queue / 'audio_dedup').rename(queue / 'moved_metadata')
            else:
                (queue / 'comment_item.txt').write_text('same count, changed metadata')
        return original(reader)
    monkeypatch.setattr(diag._ProgramAdmissionReader, 'recheck', changed)
    result = collect()
    assert (result['failure_stage'], result['failure_code']) == ('recheck_audio', 'evidence_changed')
    assert result['pending_queue_matches'] is None
    assert result['audio']['quiet'] is None
    assert result['recheck_completed'] is False


def test_audio_metadata_children_are_not_descended_or_rechecked(admission, monkeypatch):
    _, soren, _, collect = admission
    queue = soren / 'tmp' / '.comment_queue'
    metadata = queue / 'audio_dedup'
    metadata.mkdir(parents=True)
    original = diag._ProgramAdmissionReader.recheck
    def metadata_activity(reader):
        if reader.root == queue:
            (metadata / 'SECRET_child').write_text('SECRET body')
        return original(reader)
    monkeypatch.setattr(diag._ProgramAdmissionReader, 'recheck', metadata_activity)
    result = collect()
    assert result['status'] == 'observed'
    assert result['audio']['quiet'] is True
    assert result['recheck_completed'] is True
    assert 'SECRET' not in json.dumps(result)


def test_audio_root_symlink_is_still_unsafe(admission):
    _, soren, _, collect = admission
    outside = soren.parent / 'outside'
    outside.mkdir()
    (soren / 'tmp' / '.comment_queue').symlink_to(outside, target_is_directory=True)
    result = collect()
    assert (result['failure_stage'], result['failure_code']) == ('audio', 'unsafe_path')
    assert result['recheck_completed'] is False


def test_default_fifo_listing_does_not_use_audio_filter(admission):
    _, _, runtime, collect = admission
    (runtime / 'docich_program_queue' / 'audio_dedup').mkdir()
    result = collect()
    assert (result['failure_stage'], result['failure_code']) == ('fifo', 'non_regular')
    assert result['earlier_waiter'] is None
