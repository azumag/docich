from unittest.mock import patch
import pytest
from ops.vm_actions import reload_shared_overlay as mod


def test_reload_targets_only_the_shared_browser_and_checks_continuity():
    before={'stream':[1,10],'encoder':[2,20],'audio':[3,30],'active':{'game':'hanjuku-hero'}}
    with patch.object(mod,'snapshot',return_value=before), patch.object(mod,'ready',return_value=True), patch.object(mod.subprocess,'run') as run:
        receipt=mod.reload_shared_overlay()
    assert receipt['continuity']==before
    assert run.call_args.args[0]==['sudo','-n','systemctl','restart','soren-shared-overlay.service']


def test_changed_encoder_is_never_reported_as_success():
    with patch.object(mod,'snapshot',side_effect=[{'encoder':[2,20]},{'encoder':[4,40]}]), patch.object(mod.subprocess,'run'), patch.object(mod,'ready',return_value=True):
        with pytest.raises(RuntimeError,match='protected process'):
            mod.reload_shared_overlay()


def test_missing_or_transitioning_game_blocks_before_restart():
    with patch.object(mod,'snapshot',side_effect=RuntimeError('game transition underway')), patch.object(mod.subprocess,'run') as run:
        with pytest.raises(RuntimeError):mod.reload_shared_overlay()
        run.assert_not_called()


def test_new_surface_is_registered_in_canonical_deploy_hook():
    from pathlib import Path
    workflow=(Path(__file__).resolve().parents[3]/'.github/workflows/vm-operations.yml').read_text()
    block=workflow.split('- name: Reload shared display for reviewed game-gap runtime epoch',1)[1].split('- name:',1)[0]
    assert 'shared_overlay_reload_epoch' in block
    assert 'reload_shared_overlay.sh' in block
    assert 'restart_direct_stream_overlay.sh' not in block
