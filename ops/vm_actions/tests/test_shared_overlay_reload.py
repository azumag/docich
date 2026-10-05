import io
from unittest.mock import Mock, patch, sentinel
import unittest
from ops.vm_actions import reload_shared_overlay as mod


class SharedOverlayReloadTests(unittest.TestCase):
    def test_reload_targets_only_the_shared_browser_and_checks_continuity(self):
        before={'stream':[1,10],'encoder':[2,20],'audio':[3,30],'active':{'game':'hanjuku-hero'}}
        with patch.object(mod,'snapshot',return_value=before), patch.object(mod,'ready',return_value=True), patch.object(mod.subprocess,'run') as run:
            receipt=mod.reload_shared_overlay()
        assert receipt['continuity']==before
        assert run.call_args.args[0]==['sudo','-n','systemctl','restart','soren-shared-overlay.service']


    def test_ready_uses_proxy_free_loopback_probe(self):
        opener = Mock()
        opener.open.side_effect = [
            io.BytesIO(b'{"ready":true}'),
            io.BytesIO(b'{"gameGapEnabled":true}'),
        ]
        with patch.object(mod.urllib.request, 'ProxyHandler', return_value=sentinel.no_proxy) as proxy, \
                patch.object(mod.urllib.request, 'build_opener', return_value=opener) as build:
            self.assertTrue(mod.ready())
        proxy.assert_called_once_with({})
        build.assert_called_once_with(sentinel.no_proxy)
        self.assertEqual(
            [call.args for call in opener.open.call_args_list],
            [
                ('http://127.0.0.1:8092/healthz',),
                ('http://127.0.0.1:8092/__soren_overlay/broadcast/state',),
            ],
        )
        self.assertEqual(
            [call.kwargs for call in opener.open.call_args_list],
            [{'timeout': 2}, {'timeout': 2}],
        )


    def test_changed_encoder_is_never_reported_as_success(self):
        with patch.object(mod,'snapshot',side_effect=[{'encoder':[2,20]},{'encoder':[4,40]}]), patch.object(mod.subprocess,'run'), patch.object(mod,'ready',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'protected process'):
                mod.reload_shared_overlay()


    def test_missing_or_transitioning_game_blocks_before_restart(self):
        with patch.object(mod,'snapshot',side_effect=RuntimeError('game transition underway')), patch.object(mod.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):mod.reload_shared_overlay()
            run.assert_not_called()


    def test_new_surface_is_registered_in_canonical_deploy_hook(self):
        from pathlib import Path
        workflow=(Path(__file__).resolve().parents[3]/'.github/workflows/vm-operations.yml').read_text()
        block=workflow.split('- name: Reload shared display for reviewed game-gap runtime epoch',1)[1].split('- name:',1)[0]
        assert 'shared_overlay_reload_epoch' in block
        assert 'reload_shared_overlay.sh' in block
        assert 'restart_direct_stream_overlay.sh' not in block
