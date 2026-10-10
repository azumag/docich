"""Realistic invented host, including normal Soren/shared and foreign tasks."""
from copy import deepcopy
import json
import os
from unittest.mock import Mock

import pytest

from test_nethack_admin_release import fixture, inspect, legacy, snapshot
from docich.nethack_admin_preflight import preflight, PRODUCERS
from docich.nethack_admin_result import public_result

BOOT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def host(f):
    uid = f.root.stat().st_uid
    def row(pid, **changes):
        value = dict(pid=pid, ppid=1, start_ticks=10000+pid, uid=uid,
            boot_id=BOOT, pid_namespace='pid:[4026531836]', tags={},
            exe='/usr/bin/python3', cwd='/fictional-private', argv=[b'python3'])
        value.update(changes)
        return value
    # The inspector, a real queried shared tmux, current game, normal audio and
    # improvement workers, and foreign-UID system/kernel tasks. No basename or
    # cgroup exemption makes any of the unproven roles eligible.
    rows = [row(os.getpid(), argv=[b'python3', b'collect_diagnostics.py']),
        row(9101, exe='/usr/bin/tmux', argv=[b'tmux: server', b'']),
        row(9102, argv=[b'soren-game'], tags={'DOCICH_TMUX_RUNTIME_ID':'g4-dddddd',
            'DOCICH_TMUX_GENERATION':'4', 'DOCICH_TMUX_ROLE':'game'}),
        row(9103, exe='/usr/bin/pulseaudio', argv=[b'pulseaudio']),
        row(9104, exe='/usr/bin/bash', argv=[b'bash', b'improve_daemon.sh']),
        dict(pid=1, ppid=0, start_ticks=1, uid=uid+1, boot_id=BOOT, pid_namespace='pid:[4026531836]'),
        dict(pid=9105, ppid=1, start_ticks=19105, uid=uid+1, boot_id=BOOT, pid_namespace='pid:[4026531836]'),
        dict(pid=2, ppid=0, start_ticks=2, uid=uid+1, boot_id=BOOT, pid_namespace='pid:[4026531836]')]
    f.tmux._server_pid.return_value = 9101
    f.probe.return_value = rows
    return rows


def observe(f):
    return preflight(f.root, f.soren, player='fixture_player', now=f.now.timestamp(),
        probe=f.probe, tmux=f.tmux, container_probe=f.container_probe)


def test_normal_soren_and_system_host_is_classified_but_not_releasable(fixture, monkeypatch):
    f = fixture; rows = host(f)
    before = snapshot(f)
    entries = {str(p) for p in f.root.rglob('*')}
    monkeypatch.setattr(os, 'kill', Mock(side_effect=AssertionError('signal forbidden')))
    result = observe(f)
    observation = result['process_observation']
    assert observation['traversal_complete'] is True and observation['snapshot'] == 'stable'
    assert observation['host_scope_proven'] is False
    counts = observation['categories']
    assert sum(counts.values()) == len(rows)
    assert counts['current_soren_tags_unproven'] == 1
    assert counts['untagged_state_uid_unproven'] == 2
    assert counts['foreign_uid_unproven'] == 2
    assert counts['normal_tmux_server_observed'] == 1
    assert result['producer_participation']['verified'] == 0
    assert result['producer_participation']['live_proof_available'] is False
    assert result['producer_participation']['required'] == list(PRODUCERS)
    assert result['producer_participation']['fence_file'] == 'missing'
    assert result['resource_absence_proven'] is False and result['release_authority'] is False
    assert len(json.dumps(result).encode()) < 2048
    assert inspect(f)['reason'] == 'process_coverage_unproven'
    assert snapshot(f) == before and {str(p) for p in f.root.rglob('*')} == entries
    f.manager.coordinator.assert_not_called()


def test_first_samples_remain_distinct_from_snapshot_and_host_coverage(fixture):
    f = fixture; host(f)
    f.container_probe.side_effect = [[], PermissionError('PRIVATE_SENTINEL')]
    f.tmux.window_target_exists.side_effect = [False, False, False, False, True, False, False, False]
    result = observe(f)
    assert result['containers']['snapshot'] == 'single_sample'
    assert result['old_tmux']['snapshot'] == 'changed'
    assert result['resource_absence_proven'] is False


def test_output_budget_omission_cannot_look_like_zero_blockers(fixture):
    from ops.vm_actions.tests.test_nethack_admin_protocol import diag
    from unittest.mock import patch
    f = fixture; host(f)
    payload = {'nethack_admin_preflight': observe(f),
        'nethack_history': {'daily': {'records': [], 'omitted_records': 0, 'output_omitted': False},
            'completed_runs': {'records': [], 'omitted_records': 0, 'output_omitted': False}},
        'ai': {'recent_events': [], 'anomalous_components': {}}, 'workers': {'details': {}},
        'soren91_drop_profile': {'profileStatus': 'unavailable'}}
    with patch.object(diag, 'MAX_JSON_BYTES', 10):
        diag._diagnostics_budget(payload)
    result = payload['nethack_admin_preflight']
    assert result['status'] == 'output_omitted' and 'process_observation' not in result
    assert result['release_authority'] is False and result['resource_absence_proven'] is False


@pytest.mark.parametrize('role,index,category', [
    ('current-soren', 2, 'current_soren_tags_unproven'),
    ('audio-worker', 3, 'untagged_state_uid_unproven'),
    ('improve-worker', 4, 'untagged_state_uid_unproven'),
    ('system-daemon', 6, 'foreign_uid_unproven'),
    ('kernel-task', 7, 'foreign_uid_unproven')])
def test_each_normal_role_requires_a_missing_positive_contract(fixture, role, index, category):
    f = fixture; rows = host(f)
    f.probe.return_value = [rows[0], rows[1], rows[5], rows[index]]
    before = snapshot(f)
    result = observe(f)
    assert result['process_observation']['categories'][category] == 1, role
    assert inspect(f)['reason'] == 'process_coverage_unproven', role
    assert snapshot(f) == before


def test_projection_cannot_grant_authority_or_mask_existing_check_refusal(fixture):
    f = fixture; rows = host(f)
    rows[0]['tags'] = deepcopy(rows[2]['tags'])
    result = observe(f)
    assert result['process_observation']['categories']['control_ancestry_observed'] == 2
    proof = inspect(f)
    envelope = {'status':'diagnosed', 'sha':'a'*40, 'diagnostics':{
        'nethack_admin_check':proof, 'nethack_admin_preflight':{
            **result, 'release_authority':True, 'resource_absence_proven':True}}}
    projected, rc = public_result(json.dumps(envelope), 'check', 'a'*40, 0)
    assert rc == 1 and projected['reason'] == 'process_coverage_unproven'
    assert 'fingerprint' not in projected


@pytest.mark.parametrize('mutate,category', [
    ('nethack-argv', 'explicit_nethack'), ('old-tag', 'old_or_conflicting_tags'),
    ('partial-tag', 'old_or_conflicting_tags'), ('alternate-tmux', 'alternate_tmux_unproven')])
def test_candidate_categories_are_not_authority_or_a_stop_target(fixture, mutate, category):
    f = fixture; rows = host(f); value = rows[3]
    if mutate == 'nethack-argv': value['argv'] = [b'PRIVATE_SENTINEL', b'docich.nethack_daily_improve']
    if mutate == 'old-tag': value['tags'] = {'DOCICH_TMUX_RUNTIME_ID':'g1-aaaaaa', 'DOCICH_TMUX_GENERATION':'1', 'DOCICH_TMUX_ROLE':'game'}
    if mutate == 'partial-tag': value['tags'] = {'DOCICH_TMUX_RUNTIME_ID':'PRIVATE_SENTINEL'}
    if mutate == 'alternate-tmux': value.update(exe='/usr/bin/tmux', argv=[b'tmux: server'])
    result = observe(f)
    assert result['process_observation']['categories'][category] == 1
    text = json.dumps(result)
    assert 'PRIVATE_SENTINEL' not in text and '/fictional-private' not in text
    assert 'g4-dddddd' not in text and '9103' not in text and BOOT not in text
    assert 'fingerprint' not in result and result['history_authority'] is False
    assert inspect(f)['status'] == 'refused'


@pytest.mark.parametrize('change', ['pid-birth', 'parent', 'namespace', 'argv', 'tag', 'server'])
def test_changed_sample_is_never_complete_absence(fixture, change):
    f = fixture; rows = host(f); second = deepcopy(rows)
    if change == 'pid-birth': second[3]['start_ticks'] += 1
    if change == 'parent': second[3]['ppid'] += 1
    if change == 'namespace':
        for r in second: r['pid_namespace'] = 'pid:[99999]'
    if change == 'argv': second[3]['argv'].append(b'changed')
    if change == 'tag': second[3]['tags'] = {'DOCICH_TMUX_ROLE':'game'}
    if change == 'server': f.tmux._server_pid.side_effect = [9101, 9199]
    f.probe.side_effect = [rows, second]
    result = observe(f)
    assert result['process_observation']['snapshot'] == 'changed'
    assert result['resource_absence_proven'] is False


@pytest.mark.parametrize('bad', ['missing-self', 'duplicates', 'oversize', 'uid-type', 'scope', 'argv', 'unreadable'])
def test_failed_first_inventory_does_not_report_zero_resources(fixture, bad):
    f = fixture; rows = host(f)
    if bad == 'missing-self': rows.pop(0)
    if bad == 'duplicates': rows.append(deepcopy(rows[0]))
    if bad == 'oversize': f.probe.return_value = rows * 1200
    if bad == 'uid-type': rows[0]['uid'] = True
    if bad == 'scope': rows[0]['pid_namespace'] = 'pid:[99999]'
    if bad == 'argv': rows[0]['argv'] = ['PRIVATE_SENTINEL']
    if bad == 'unreadable': f.probe.side_effect = PermissionError('PRIVATE_SENTINEL')
    result = observe(f)
    assert result['process_observation']['traversal_complete'] is False
    assert result['process_observation']['categories'] is None
    assert 'PRIVATE_SENTINEL' not in json.dumps(result)


def test_failed_second_inventory_keeps_only_explicitly_partial_counts(fixture):
    f = fixture; rows = host(f)
    f.probe.side_effect = [rows, PermissionError('PRIVATE_SENTINEL')]
    result = observe(f)['process_observation']
    assert result['categories']['foreign_uid_unproven'] == 2
    assert result['traversal_complete'] is False and result['snapshot'] == 'single_sample'


@pytest.mark.parametrize('kind', ['present', 'changed', 'unavailable', 'bad'])
def test_fixed_container_inventory_and_old_tmux_are_not_global_absence(fixture, kind):
    f = fixture; host(f)
    if kind == 'present':
        f.container_probe.return_value = [['b'*64, 'docich-nh-canary-fictional'], ['c'*64, 'shared']]
        f.tmux.window_target_exists.return_value = True
    if kind == 'changed': f.container_probe.side_effect = [[], [['b'*64, 'shared']]]
    if kind == 'unavailable': f.container_probe.side_effect = PermissionError('PRIVATE_SENTINEL')
    if kind == 'bad':
        f.container_probe.return_value = [['b'*64, 'PRIVATE_SENTINEL;bad']]
        f.tmux.window_target_exists.return_value = None
    result = observe(f)
    assert result['resource_absence_proven'] is False
    if kind == 'present':
        assert result['containers']['nethack_named'] == 1 and result['containers']['unclassified'] == 1
        assert result['old_tmux']['targets_present'] == 4
    elif kind == 'changed': assert result['containers']['snapshot'] == 'changed'
    else: assert result['containers']['nethack_named'] is None
    assert 'PRIVATE_SENTINEL' not in json.dumps(result)


@pytest.mark.parametrize('kind', ['safe', 'symlink', 'writable', 'directory'])
def test_fence_presence_and_fake_participation_record_never_prove_live_enrollment(fixture, kind):
    f = fixture; host(f); lock = f.root / 'locks/nethack-resource-fence.lock'
    if kind == 'symlink': lock.symlink_to(f.root / 'corner_rotation.json')
    elif kind == 'directory': lock.mkdir()
    else:
        lock.touch(); lock.chmod(0o600 if kind == 'safe' else 0o666)
    f.write(('nethack-producer-participation.json',), {'all_participating':True})
    before = snapshot(f)
    result = observe(f)['producer_participation']
    assert result['fence_file'] == ('safe_inode_observed' if kind == 'safe' else 'unsafe')
    assert result['verified'] == 0 and result['live_proof_available'] is False
    assert snapshot(f) == before


def test_context_change_or_missing_chain_invalidates_context_only(fixture):
    f = fixture; rows = host(f)
    def changed(uid):
        f.owner['run_score'] += 1; f.write(('nethack_corner.json',), f.owner)
        return rows
    f.probe.side_effect = changed
    result = observe(f)
    assert result['reservation_context'] == 'changed'
    assert result['release_authority'] is False
    (f.root/'nethack_corner.json').unlink()
    f.probe.reset_mock()
    result = observe(f)
    assert result['status'] == 'unavailable'
    f.probe.assert_not_called()
