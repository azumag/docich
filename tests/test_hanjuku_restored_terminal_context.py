"""Verified checkpoint context is distinct from actually seeing name entry."""
from copy import deepcopy
from pathlib import Path
import uuid

import pytest
from docich import hanjuku_run
from docich.adapters.base import AdapterError
from docich.game_switch import atomic_write_json
from docich.hanjuku_pixels import read_png
from test_hanjuku_script import IDENTITY, title_frame


def gameplay_frame():
    return read_png(Path(__file__).parent / 'fixtures/hanjuku/g713-zeus-cumin-healthy.png')


def receipt():
    return dict(IDENTITY, schema=1, status='verified',
                gameplay_context='verified_checkpoint_foreground',
                request_id=str(uuid.uuid4()), chapter=None, verified_at=999.,
                sha256='a' * 64, core_state_sha256='b' * 64, frame_sha256='c' * 64)


def write(tmp_path, record):
    atomic_write_json(tmp_path / 'hanjuku_resume.json', record)


def observe_return(tmp_path):
    run = hanjuku_run.observe(tmp_path, IDENTITY, gameplay_frame(), now=1, wall=1001)
    for now in (2, 3, 4):
        run = hanjuku_run.observe(tmp_path, IDENTITY, title_frame(), now=now, wall=1000 + now)
    return run


def test_verified_restoration_then_observed_gameplay_title_return_can_end_without_name(tmp_path):
    r = receipt(); write(tmp_path, r)
    run = observe_return(tmp_path)
    assert run['name_entered'] is False
    assert run['checkpoint_started'] is True and run['gameplay_seen'] is True
    assert run['checkpoint_start_request'] == r['request_id']
    assert run['terminal_reason'] == 'game_over'
    assert hanjuku_run.terminal(tmp_path, IDENTITY) == run


def test_verified_receipt_alone_does_not_prove_played_game_or_clear(tmp_path):
    write(tmp_path, receipt())
    for now in range(4):
        run = hanjuku_run.observe(tmp_path, IDENTITY, title_frame(), now=now, wall=1000 + now)
        assert run['checkpoint_started'] is True
        assert run['name_entered'] is False and run['gameplay_seen'] is False
        assert not run['terminal_candidate'] and run['terminal_reason'] is None


@pytest.mark.parametrize('key,value', [
    ('schema', True), ('schema', 0), ('status', 'claimed'),
    ('gameplay_context', None), ('gameplay_context', 'name_entered'),
    ('game', 'sorengame'), ('runtime_id', 'g2-abcdef'),
    ('generation', True), ('generation', 2), ('lease_id', 'other'),
    ('request_id', None), ('request_id', 'invalid'), ('chapter', True),
    ('chapter', 0), ('verified_at', True), ('verified_at', float('nan')),
    ('verified_at', 10000), ('sha256', None), ('core_state_sha256', 'z' * 64),
    ('frame_sha256', 'a' * 63)])
def test_incomplete_foreign_or_legacy_receipt_cannot_start_context(tmp_path, key, value):
    r = receipt(); r[key] = value; write(tmp_path, r)
    run = observe_return(tmp_path)
    assert run['checkpoint_started'] is False
    assert run['name_entered'] is False and run['gameplay_seen'] is False
    assert run['terminal_reason'] is None


def test_saved_flag_cannot_replace_missing_receipt_when_terminal_is_verified(tmp_path):
    write(tmp_path, receipt()); observe_return(tmp_path)
    (tmp_path / 'hanjuku_resume.json').unlink()
    with pytest.raises(AdapterError, match='terminal evidence'):
        hanjuku_run.terminal(tmp_path, IDENTITY)


def test_receipt_change_cannot_reuse_old_played_context(tmp_path):
    r = receipt(); write(tmp_path, r)
    first = hanjuku_run.observe(tmp_path, IDENTITY, gameplay_frame(), now=1, wall=1001)
    assert first['gameplay_seen']
    changed = deepcopy(r); changed['request_id'] = str(uuid.uuid4()); write(tmp_path, changed)
    for now in (2, 3, 4):
        run = hanjuku_run.observe(tmp_path, IDENTITY, title_frame(), now=now, wall=1000 + now)
        assert run['checkpoint_start_request'] == r['request_id']
        assert run['checkpoint_started'] is False and not run['terminal_candidate']
        assert run['terminal_reason'] is None


def test_first_checkpoint_context_cannot_borrow_an_unbound_played_flag(tmp_path):
    hanjuku_run.observe(tmp_path, IDENTITY, title_frame(), now=0, wall=1000)
    state = hanjuku_run.load(tmp_path, IDENTITY)
    state['gameplay_seen'] = True
    atomic_write_json(tmp_path / hanjuku_run.RUN_FILE, state)
    write(tmp_path, receipt())
    for now in (1, 2, 3):
        run = hanjuku_run.observe(tmp_path, IDENTITY, title_frame(), now=now, wall=1000 + now)
        assert run['checkpoint_started'] is True
        assert not run['gameplay_seen'] and not run['terminal_candidate']
