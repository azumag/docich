import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(DIR))

class StageRepair(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask,os.umask(0o022))

    def test_module_and_deny_policy_control_paths(self):
        import stage_repair as s
        for path in ['../escape', '.env', '.github/workflows/x.yml', 'core/config.sh', 'strategy.py', 'codex_bug_dispatcher.sh', 'lib/ai_generate.sh']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                s.validate_allowed_paths([path])
        self.assertEqual(s.validate_allowed_paths(['external_game_audio.mjs']), ['external_game_audio.mjs'])

    def test_probe_error_is_not_reproduced_bug(self):
        import stage_repair as s
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'probe'):
                s.run_probe({'health_command':['/bin/sh','-c','exit 2']}, Path(d), 1)

    def test_apply_then_adoption_and_failure_rollback(self):
        import stage_repair as s
        g=s.gw
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'VMOPS_TESTING':'1'}):
            root=Path(d); doc=root/'doc'; doc.mkdir(); source=root/'source'; source.mkdir(); live=root/'live'; live.mkdir()
            for repo in [source,doc]:
                g.git(repo,'init','-q'); g.git(repo,'config','user.name','Test'); g.git(repo,'config','user.email','test@example.com')
            (source/'external_game_audio.mjs').write_text('broken')
            g.git(source,'add','.');g.git(source,'commit','-qm','base');base=g.git(source,'rev-parse','HEAD')
            g.git(doc,'update-index','--add','--cacheinfo',f'160000,{base},games/soviet_now')
            g.git(doc,'commit','-qm','parent');parent=g.git(doc,'rev-parse','HEAD')
            (source/'external_game_audio.mjs').write_text('fixed');g.git(source,'commit','-qam','fix');sha=g.git(source,'rev-parse','HEAD')
            (live/'external_game_audio.mjs').write_text('broken')
            cfg={'state':str(root/'state'),'repos':{'docich':{'production':str(doc),'mode':'git','projections':{'games/soviet_now':str(live)}}}}
            state=g.current_file(cfg,'docich');g.write_json(state,{'mode':'git','sha':parent})
            policy={'projection':'games/soviet_now','allowed_paths':['external_game_audio.mjs'],'health_command':['/bin/true'],'test_command':['/bin/true']}
            def concurrent_probe(*args):
                (live/'external_game_audio.mjs').write_text('manual')
            with mock.patch.object(g,'git_clean',return_value=True), mock.patch.object(s,'run_candidate_test'), mock.patch.object(s,'run_probe',side_effect=concurrent_probe):
                with self.assertRaisesRegex(ValueError,'concurrent'):s.apply(cfg,policy,'a'*32,source,base,sha)
            self.assertEqual((live/'external_game_audio.mjs').read_text(),'manual')
            self.assertFalse(g.read_json(state).get('pending_repairs'))
            (live/'external_game_audio.mjs').write_text('broken')
            with mock.patch.object(g,'git_clean',return_value=True), mock.patch.object(s,'run_candidate_test'), mock.patch.object(s,'run_probe',side_effect=[None,ValueError('probe failed')]):
                with self.assertRaises(ValueError):s.apply(cfg,policy,'a'*32,source,base,sha)
            self.assertEqual((live/'external_game_audio.mjs').read_text(),'broken')
            self.assertFalse(g.read_json(state).get('pending_repairs'))
            with mock.patch.object(g,'git_clean',return_value=True), mock.patch.object(s,'run_candidate_test'), mock.patch.object(s,'run_probe'):
                result=s.apply(cfg,policy,'a'*32,source,base,sha)
            self.assertEqual(result['status'],'active')
            self.assertEqual((live/'external_game_audio.mjs').read_text(),'fixed')
            self.assertEqual(g.read_json(state)['pending_repairs'][0]['candidate_sha'],sha)
            plans,left=g._plan_repaired_projection(source,live,base,sha,g.read_json(state)['pending_repairs'])
            self.assertEqual((plans,left),([],[]))

    def test_candidate_test_read_only_direct_argv(self):
        import stage_repair as s
        with mock.patch.dict(os.environ, {'VMOPS_TESTING':'0'}), mock.patch.object(s.subprocess,'run',return_value=mock.Mock(returncode=0)) as run:
            s.run_candidate_test({'test_command':['/usr/bin/true']},Path('/candidate'))
        argv=run.call_args.args[0]
        self.assertIn('--ro-bind',argv)
        self.assertNotIn('--bind',argv)
        self.assertEqual(argv[-1],'/usr/bin/true')
