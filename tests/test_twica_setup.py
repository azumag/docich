import json
import os
from pathlib import Path
import pytest
from docich.twica_setup import prepare_environment
from docich.twica_state import set_owner


def test_prepare_preserves_unrelated_values_and_makes_private_backup(tmp_path):
    original='OTHER=keep\nEXISTING_PASSWORD=never-print\nexport DOCICH_TWICA_COMMON_ENABLED=0\n'
    (tmp_path/'.env').write_text(original)
    prepare_environment(tmp_path)
    result=(tmp_path/'.env').read_text()
    assert result=='OTHER=keep\nEXISTING_PASSWORD=never-print\nDOCICH_TWICA_COMMON_ENABLED=1\n'
    backups=list((tmp_path/'tmp/state/twica-common').glob('environment-before-*.bak'))
    assert len(backups)==1 and backups[0].read_text()==original
    assert backups[0].stat().st_mode & 0o077 == 0
    assert (tmp_path/'.env').stat().st_mode & 0o077 == 0
    prepare_environment(tmp_path)
    assert (tmp_path/'.env').read_text()==result


def test_prepare_never_changes_common_owner_or_follows_env_symlink(tmp_path):
    (tmp_path/'.env').write_text('SAFE=1\n')
    directory=tmp_path/'tmp/state/twica-common'
    set_owner(directory,'common')
    with pytest.raises(ValueError): prepare_environment(tmp_path)
    assert (tmp_path/'.env').read_text()=='SAFE=1\n'
    set_owner(directory,'legacy')
    (tmp_path/'.env').unlink()
    (tmp_path/'.env').symlink_to(tmp_path/'other')
    (tmp_path/'other').write_text('unchanged')
    with pytest.raises(OSError): prepare_environment(tmp_path)
    assert (tmp_path/'other').read_text()=='unchanged'


def test_fixed_operator_does_not_restart_encoder_or_accept_arbitrary_commands():
    root=Path(__file__).resolve().parents[1]
    shell=(root/'ops/vm_actions/twica_common_operation.sh').read_text()
    assert 'status|prepare|activate|rollback' in shell
    assert 'systemctl' not in shell and 'kill ' not in shell and 'git reset' not in shell
    workflow=(root/'.github/workflows/twica-common-operation.yml').read_text()
    for guard in ['github.actor_id == 9018513','github.ref_protected == true',"github.ref == 'refs/heads/main'",'CONFIRM','StrictHostKeyChecking=yes']:
        assert guard in workflow
    assert 'inputs.command' not in workflow
    assert 'exec docich production $sha' in workflow
    manifest=json.loads((root/'ops/vm_actions/twica_common_manifest.json').read_text())
    assert manifest['owner']=='direct_stream'
    assert not manifest['game_lifecycle_owned']
