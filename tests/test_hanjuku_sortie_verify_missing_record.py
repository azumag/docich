"""Regression coverage for missing sortie records during departure verification."""
from unittest.mock import patch

import pytest

from docich.hanjuku.flows import deploy


@pytest.mark.parametrize('record', [None, [], 'invalid', {}, {'general': None}, {'general': ''}])
def test_verify_sortie_source_holds_missing_or_invalid_record(record):
    mem = {'world': {'sorties': {} if record is None else {'step-1': record}},
           'active': {'step': 'step-1'}}
    order = {'step': 'step-1'}
    with (patch.object(deploy, '_present_generals', side_effect=AssertionError('must not inspect roster')),
          patch.object(deploy, '_record') as log,
          patch.object(deploy, 'pad', side_effect=lambda button: button)):
        commands = deploy._verify_sortie_source(None, mem, order)
    assert commands == ['b', 'b']
    assert mem['active'] is None
    assert mem['world']['sorties'] == ({} if record is None else {'step-1': record})
    log.assert_called_once()
    assert log.call_args.args[1] == 'sortie_verification_held'
