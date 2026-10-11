"""Save receipts are diagnosable without exporting paths, identities or bytes."""
import json
from pathlib import Path
import pytest
from test_collect_diagnostics import load_collector


def checkpoint(tmp_path, status='reached'):
    owner = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abcdef12', 'generation': 1, 'lease_id': 'PRIVATE-LEASE'}
    rd = tmp_path / 'runtimes' / owner['runtime_id']; (rd / 'states').mkdir(parents=True)
    state = rd / 'states/hanjuku-hero.state'; state.write_bytes(b'PRIVATE-SAVED-GAME')
    r = {**owner, 'status': status, 'request_id': 'PRIVATE-REQUEST', 'outcome': 'screen_stalled',
         'save_required': True, 'checkpoint': 'hanjuku-hero.state', 'sha256': 'a' * 64,
         'checkpoint_bytes': state.stat().st_size, 'checkpoint_mtime_ns': state.stat().st_mtime_ns,
         'text': 'PRIVATE-TEXT'}
    path = rd / 'retroarch_boundary.json'; path.write_text(json.dumps(r))
    return {'game': 'hanjuku-hero', 'bot_identity': owner}, r, state, path


def test_confirmed_receipt_and_metadata_project_through_corner_collector(tmp_path):
    mod = load_collector(); corner, r, state, receipt = checkpoint(tmp_path)
    (tmp_path / 'retro_corner.json').write_text(json.dumps(corner))
    out = {}; mod._collect_corner_files(tmp_path, out, 100)
    got = out['retro_corner']['saved_checkpoint']
    assert got['save_status'] == 'saved_receipt'
    assert got['file_present'] is True and got['file_metadata_matches'] is True
    assert 'PRIVATE' not in json.dumps(got) and r['sha256'] not in json.dumps(got)
    assert 'g1-' not in json.dumps(got) and str(tmp_path) not in json.dumps(got)


@pytest.mark.parametrize('error,expected', [(None, 'waiting'), ('save_timeout', 'failed'),
                                           ('save_failed', 'failed'), ('PRIVATE-error', 'waiting')])
def test_pending_and_failed_saves_are_distinguished_without_error_text(tmp_path, error, expected):
    mod = load_collector(); corner, r, state, receipt = checkpoint(tmp_path, 'waiting')
    r['save_error'] = error; receipt.write_text(json.dumps(r))
    got = mod._collect_hanjuku_checkpoint(tmp_path, corner)
    assert got['save_status'] == expected
    assert 'PRIVATE' not in json.dumps(got)


@pytest.mark.parametrize('bad', ['missing', 'changed', 'wrong_owner', 'unsafe_path', 'symlink'])
def test_receipt_does_not_prove_current_checkpoint_or_allow_arbitrary_files(tmp_path, bad):
    mod = load_collector(); corner, r, state, receipt = checkpoint(tmp_path)
    if bad == 'missing': state.unlink()
    elif bad == 'changed': state.write_bytes(b'changed')
    elif bad == 'wrong_owner': r['generation'] = 2
    elif bad == 'unsafe_path': r['checkpoint'] = '../PRIVATE-file'
    else:
        state.unlink(); state.symlink_to(receipt)
    receipt.write_text(json.dumps(r))
    got = mod._collect_hanjuku_checkpoint(tmp_path, corner)
    if bad == 'missing': assert got['file_present'] is False
    elif bad == 'changed': assert got['file_metadata_matches'] is False
    elif bad in ('wrong_owner', 'unsafe_path'): assert got['save_status'] == 'unverified'
    else: assert got['file_present'] is None
    assert 'PRIVATE' not in json.dumps(got)


def test_checkpoint_diagnostics_never_read_saved_game_contents(tmp_path, monkeypatch):
    mod = load_collector(); corner, _, state, _ = checkpoint(tmp_path)
    read_bytes = Path.read_bytes
    def forbid(path):
        assert path != state
        return read_bytes(path)
    monkeypatch.setattr(Path, 'read_bytes', forbid)
    assert mod._collect_hanjuku_checkpoint(tmp_path, corner)['file_present'] is True
    assert mod._collect_hanjuku_checkpoint(tmp_path, {'game': 'nethack'}) is None
