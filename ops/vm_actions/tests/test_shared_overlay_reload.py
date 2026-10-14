from unittest.mock import patch
import unittest
from ops.vm_actions import reload_shared_overlay as mod


class SharedOverlayReloadTests(unittest.TestCase):
    def test_reload_targets_only_the_shared_browser_and_checks_continuity(self):
        before={'stream':[1,10],'encoder':[2,20],'audio':[3,30],'active':{'game':'hanjuku-hero'}}
        with patch.object(mod,'snapshot',return_value=before), patch.object(mod,'readiness',return_value=(True,0)), patch.object(mod.subprocess,'run') as run:
            receipt=mod.reload_shared_overlay()
        assert receipt['continuity']==before
        assert run.call_args.args[0]==['sudo','-n','systemctl','restart','soren-shared-overlay.service']


    def test_changed_encoder_is_never_reported_as_success(self):
        with patch.object(mod,'snapshot',side_effect=[{'encoder':[2,20]},{'encoder':[4,40]}]), patch.object(mod.subprocess,'run'), patch.object(mod,'readiness',return_value=(True,0)):
            with self.assertRaisesRegex(RuntimeError,'protected process'):
                mod.reload_shared_overlay()


    def test_missing_or_transitioning_game_blocks_before_restart(self):
        with patch.object(mod,'snapshot',side_effect=RuntimeError('game transition underway')), patch.object(mod.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):mod.reload_shared_overlay()
            run.assert_not_called()


    def test_timeout_preserves_fixed_last_readiness_code(self):
        before={'stream':[1,10],'encoder':[2,20],'audio':[3,30],'active':{'game':'hanjuku-hero'}}
        with patch.object(mod,'snapshot',return_value=before), patch.object(mod,'readiness',return_value=(False,mod.EXIT_LAYOUT_NOT_READY)), patch.object(mod.subprocess,'run'), patch.object(mod.time,'monotonic',side_effect=[0,0,2]), patch.object(mod.time,'sleep'):
            with self.assertRaises(mod.ReadinessTimeout) as raised:
                mod.reload_shared_overlay(timeout=1)
        assert raised.exception.exit_code == mod.EXIT_LAYOUT_NOT_READY


    def test_readiness_reports_each_public_health_gate_without_payload_details(self):
        healthy={'browserReady':True,'windowReady':True,'layoutReady':True,'overlayReady':True,'ready':True}
        cases=[
            ({'browserReady':False}, mod.EXIT_BROWSER_NOT_READY),
            ({'browserReady':True,'windowReady':False}, mod.EXIT_WINDOW_NOT_READY),
            ({'browserReady':True,'windowReady':True,'layoutReady':False}, mod.EXIT_LAYOUT_NOT_READY),
            ({'browserReady':True,'windowReady':True,'layoutReady':True,'overlayReady':False}, mod.EXIT_OVERLAY_NOT_READY),
            ({'browserReady':True,'windowReady':True,'layoutReady':True,'overlayReady':True,'ready':False}, mod.EXIT_HEALTH_NOT_READY),
        ]
        for health, expected in cases:
            with self.subTest(expected=expected), patch.object(mod,'_url_json',return_value=health):
                assert mod.readiness() == (False, expected)
        with patch.object(mod,'_url_json',side_effect=[healthy,{'gameGapEnabled':False}]):
            assert mod.readiness() == (False, mod.EXIT_GAME_GAP_DISABLED)
        with patch.object(mod,'_url_json',side_effect=[healthy,{'gameGapEnabled':True}]):
            assert mod.readiness() == (True, 0)


    def test_readiness_distinguishes_health_and_state_transport_failures(self):
        with patch.object(mod,'_url_json',side_effect=OSError('down')):
            assert mod.readiness() == (False, mod.EXIT_HEALTH_UNREACHABLE)
        health={'browserReady':True,'windowReady':True,'layoutReady':True,'overlayReady':True,'ready':True}
        with patch.object(mod,'_url_json',side_effect=[health,OSError('down')]):
            assert mod.readiness() == (False, mod.EXIT_STATE_UNREACHABLE)


    def test_new_surface_is_registered_in_canonical_deploy_hook(self):
        from pathlib import Path
        workflow=(Path(__file__).resolve().parents[3]/'.github/workflows/vm-operations.yml').read_text()
        block=workflow.split('- name: Reload shared display for reviewed game-gap runtime epoch',1)[1].split('- name:',1)[0]
        assert 'shared_overlay_reload_epoch' in block
        assert 'reload_shared_overlay.sh' in block
        assert 'restart_direct_stream_overlay.sh' not in block
