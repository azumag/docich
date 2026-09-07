import os
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / 'ops/vm_actions/gateway.py'

spec = importlib.util.spec_from_file_location('gateway_under_test', GATEWAY)
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)


class DeployStateTransactionTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask,os.umask(0o022))
        self.base = Path(tempfile.mkdtemp(prefix='vmops-state-txn-'))
        self.state = self.base / 'state'
        self.state.mkdir()
        self.docich = self.base / 'docich'
        self.docich.mkdir()
        self.cfg = {
            'state': str(self.state),
            'repos': {
                'docich': {'production': str(self.docich), 'mode': 'git'},
            },
        }


    def test_git_rolls_back_when_state_commit_fails(self):
        subprocess.run(['git', 'init', '-q', self.docich], check=True)
        subprocess.run(['git', '-C', self.docich, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', self.docich, 'config', 'user.name', 'T'], check=True)
        production = self.docich / 'app.py'
        production.write_text('v1\n')
        subprocess.run(['git', '-C', self.docich, 'add', 'app.py'], check=True)
        subprocess.run(['git', '-C', self.docich, 'commit', '-qm', 'v1'], check=True)
        old_sha = subprocess.check_output(['git', '-C', self.docich, 'rev-parse', 'HEAD'], text=True).strip()

        state_path = gw.current_file(self.cfg, 'docich')
        gw.write_json(state_path, {'mode': 'git', 'sha': old_sha, 'previous_head': None})

        candidate = self.base / 'candidate'
        subprocess.run(['git', 'clone', '-q', self.docich, candidate], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.name', 'T'], check=True)
        (candidate / 'app.py').write_text('v2\n')
        subprocess.run(['git', '-C', candidate, 'add', 'app.py'], check=True)
        subprocess.run(['git', '-C', candidate, 'commit', '-qm', 'v2'], check=True)
        new_sha = subprocess.check_output(['git', '-C', candidate, 'rev-parse', 'HEAD'], text=True).strip()
        bundle = gw.bundle_file(self.cfg, 'docich', new_sha)
        bundle.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-C', candidate, 'bundle', 'create', bundle, 'HEAD'], check=True)

        real_write_json = gw.write_json

        def fail_state_write(path, value):
            if Path(path) == state_path:
                raise OSError('state write failed')
            return real_write_json(path, value)

        with mock.patch.object(gw, 'write_json', side_effect=fail_state_write):
            with self.assertRaises(OSError):
                gw.deploy_git(self.cfg, 'docich', new_sha)

        head = subprocess.check_output(['git', '-C', self.docich, 'rev-parse', 'HEAD'], text=True).strip()
        self.assertEqual(head, old_sha)
        self.assertEqual(production.read_text(), 'v1\n')
        self.assertEqual(gw.read_json(state_path)['sha'], old_sha)


    def test_git_deploy_updates_owned_submodule_to_parent_gitlink(self):
        subremote = self.base / 'soviet-remote'
        subprocess.run(['git', 'init', '-q', subremote], check=True)
        subprocess.run(['git', '-C', subremote, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', subremote, 'config', 'user.name', 'T'], check=True)
        (subremote / 'game.txt').write_text('v1\n')
        subprocess.run(['git', '-C', subremote, 'add', 'game.txt'], check=True)
        subprocess.run(['git', '-C', subremote, 'commit', '-qm', 'v1'], check=True)

        subprocess.run(['git', 'init', '-q', self.docich], check=True)
        subprocess.run(['git', '-C', self.docich, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', self.docich, 'config', 'user.name', 'T'], check=True)
        subprocess.run(['git', '-C', self.docich, '-c', 'protocol.file.allow=always',
                        'submodule', 'add', '-q', str(subremote), 'games/soviet_now'], check=True)
        subprocess.run(['git', '-C', self.docich, 'commit', '-qam', 'parent-v1'], check=True)
        old_parent = subprocess.check_output(['git', '-C', self.docich, 'rev-parse', 'HEAD'], text=True).strip()
        gw.write_json(gw.current_file(self.cfg, 'docich'), {'mode': 'git', 'sha': old_parent, 'previous_head': None})

        (subremote / 'game.txt').write_text('v2\n')
        subprocess.run(['git', '-C', subremote, 'commit', '-qam', 'v2'], check=True)
        sub_v2 = subprocess.check_output(['git', '-C', subremote, 'rev-parse', 'HEAD'], text=True).strip()

        candidate = self.base / 'candidate-submodule'
        subprocess.run(['git', 'clone', '-q', self.docich, candidate], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.name', 'T'], check=True)
        subprocess.run(['git', '-C', candidate, 'update-index', '--cacheinfo',
                        f'160000,{sub_v2},games/soviet_now'], check=True)
        subprocess.run(['git', '-C', candidate, 'commit', '-qm', 'bump-submodule'], check=True)
        new_parent = subprocess.check_output(['git', '-C', candidate, 'rev-parse', 'HEAD'], text=True).strip()
        bundle = gw.bundle_file(self.cfg, 'docich', new_parent)
        bundle.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-C', candidate, 'bundle', 'create', bundle, 'HEAD'], check=True)

        with mock.patch.object(gw, 'OWNED_SUBMODULES', {'games/soviet_now': str(subremote)}):
            gw.deploy_git(self.cfg, 'docich', new_parent)

        actual = subprocess.check_output(
            ['git', '-C', self.docich / 'games/soviet_now', 'rev-parse', 'HEAD'], text=True
        ).strip()
        self.assertEqual(actual, sub_v2)


    def _projection_case(self, *, live_text='v1\n', change_second=False):
        live = self.base / 'soren-live'
        live.mkdir()
        (live / 'game.txt').write_text(live_text)
        (live / 'runtime.txt').write_text('runtime-live-mutated\n')
        (live / 'second.txt').write_text('second-v1\n')

        subremote = self.base / 'soviet-projection-remote'
        subprocess.run(['git', 'init', '-q', subremote], check=True)
        subprocess.run(['git', '-C', subremote, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', subremote, 'config', 'user.name', 'T'], check=True)
        (subremote / 'game.txt').write_text('v1\n')
        (subremote / 'runtime.txt').write_text('runtime-source-v1\n')
        (subremote / 'second.txt').write_text('second-v1\n')
        subprocess.run(['git', '-C', subremote, 'add', '-A'], check=True)
        subprocess.run(['git', '-C', subremote, 'commit', '-qm', 'v1'], check=True)

        subprocess.run(['git', 'init', '-q', self.docich], check=True)
        subprocess.run(['git', '-C', self.docich, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', self.docich, 'config', 'user.name', 'T'], check=True)
        subprocess.run(['git', '-C', self.docich, '-c', 'protocol.file.allow=always',
                        'submodule', 'add', '-q', str(subremote), 'games/soviet_now'], check=True)
        subprocess.run(['git', '-C', self.docich, 'commit', '-qam', 'parent-v1'], check=True)
        old_parent = subprocess.check_output(['git', '-C', self.docich, 'rev-parse', 'HEAD'], text=True).strip()
        self.cfg['repos']['docich']['projections'] = {'games/soviet_now': str(live)}
        gw.write_json(gw.current_file(self.cfg, 'docich'), {'mode': 'git', 'sha': old_parent, 'previous_head': None})

        (subremote / 'game.txt').write_text('v2\n')
        if change_second:
            (subremote / 'second.txt').write_text('second-v2\n')
        subprocess.run(['git', '-C', subremote, 'commit', '-qam', 'v2'], check=True)
        sub_v2 = subprocess.check_output(['git', '-C', subremote, 'rev-parse', 'HEAD'], text=True).strip()
        candidate = self.base / 'candidate-projection'
        subprocess.run(['git', 'clone', '-q', self.docich, candidate], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.email', 't@example.com'], check=True)
        subprocess.run(['git', '-C', candidate, 'config', 'user.name', 'T'], check=True)
        subprocess.run(['git', '-C', candidate, 'update-index', '--cacheinfo',
                        f'160000,{sub_v2},games/soviet_now'], check=True)
        subprocess.run(['git', '-C', candidate, 'commit', '-qm', 'bump-submodule'], check=True)
        new_parent = subprocess.check_output(['git', '-C', candidate, 'rev-parse', 'HEAD'], text=True).strip()
        bundle = gw.bundle_file(self.cfg, 'docich', new_parent)
        bundle.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-C', candidate, 'bundle', 'create', bundle, 'HEAD'], check=True)
        return live, subremote, old_parent, new_parent

    def test_projection_updates_only_changed_submodule_paths(self):
        live, subremote, _old_parent, new_parent = self._projection_case()
        with mock.patch.object(gw, 'OWNED_SUBMODULES', {'games/soviet_now': str(subremote)}):
            gw.deploy_git(self.cfg, 'docich', new_parent)
        self.assertEqual((live / 'game.txt').read_text(), 'v2\n')
        self.assertEqual((live / 'runtime.txt').read_text(), 'runtime-live-mutated\n')
        self.assertFalse(gw.read_json(gw.current_file(self.cfg,'docich')).get('managed_projection_files',{}).get('games/soviet_now'))
        (live / 'game.txt').write_text('runtime-updated\n')
        with mock.patch.object(gw, 'OWNED_SUBMODULES', {'games/soviet_now': str(subremote)}):
            gw.deploy_git(self.cfg, 'docich', new_parent)
        self.assertEqual((live / 'game.txt').read_text(), 'runtime-updated\n')


    def test_projection_partial_write_failure_rolls_back_prior_files(self):
        live, subremote, old_parent, new_parent = self._projection_case(change_second=True)
        real_atomic = gw.atomic_write
        calls = {'live': 0}

        def flaky_atomic(path, data, mode=0o600):
            path = Path(path)
            if path.parent == live and path.name in {'game.txt', 'second.txt'}:
                calls['live'] += 1
                if calls['live'] == 2:
                    raise OSError('simulated second projection write failure')
            return real_atomic(path, data, mode)

        with mock.patch.object(gw, 'OWNED_SUBMODULES', {'games/soviet_now': str(subremote)}), \
             mock.patch.object(gw, 'atomic_write', side_effect=flaky_atomic):
            with self.assertRaises(OSError):
                gw.deploy_git(self.cfg, 'docich', new_parent)
        self.assertEqual((live / 'game.txt').read_text(), 'v1\n')
        self.assertEqual((live / 'second.txt').read_text(), 'second-v1\n')
        self.assertEqual(subprocess.check_output(['git', '-C', self.docich, 'rev-parse', 'HEAD'], text=True).strip(), old_parent)

    def test_projection_adopts_exact_target_postimage_without_rewriting(self):
        live, subremote, _old_parent, new_parent = self._projection_case(live_text='v2\n')
        inode = (live / 'game.txt').stat().st_ino
        with mock.patch.object(gw, 'OWNED_SUBMODULES', {'games/soviet_now': str(subremote)}):
            gw.deploy_git(self.cfg, 'docich', new_parent)
        self.assertEqual((live / 'game.txt').read_text(), 'v2\n')
        self.assertEqual((live / 'game.txt').stat().st_ino, inode)
        self.assertEqual(gw.read_json(gw.current_file(self.cfg, 'docich'))['sha'], new_parent)

    def test_projection_mixes_adopted_postimage_and_old_preimage_safely(self):
        live, subremote, _old_parent, new_parent = self._projection_case(change_second=True)
        (live / 'game.txt').write_text('v2\n')
        game_inode = (live / 'game.txt').stat().st_ino
        with mock.patch.object(gw, 'OWNED_SUBMODULES', {'games/soviet_now': str(subremote)}):
            gw.deploy_git(self.cfg, 'docich', new_parent)
        self.assertEqual((live / 'game.txt').stat().st_ino, game_inode)
        self.assertEqual((live / 'second.txt').read_text(), 'second-v2\n')
        self.assertEqual(gw.read_json(gw.current_file(self.cfg, 'docich'))['sha'], new_parent)

    def test_projection_refuses_live_drift_on_changed_path(self):
        live, subremote, old_parent, new_parent = self._projection_case(live_text='manual-hotfix\n')
        with mock.patch.object(gw, 'OWNED_SUBMODULES', {'games/soviet_now': str(subremote)}):
            with self.assertRaises(ValueError):
                gw.deploy_git(self.cfg, 'docich', new_parent)
        self.assertEqual((live / 'game.txt').read_text(), 'manual-hotfix\n')
        self.assertEqual(subprocess.check_output(['git', '-C', self.docich, 'rev-parse', 'HEAD'], text=True).strip(), old_parent)


    def _record_pending(self, live, subremote, old_parent, text='v2\n'):
        patcher=mock.patch.dict('os.environ',{'VMOPS_TESTING':'1'})
        patcher.start();self.addCleanup(patcher.stop)
        old_sub=gw.submodule_gitlink_at(self.docich,old_parent,'games/soviet_now')
        before=gw._expected_meta(subremote,gw._tree_entry(subremote,old_sub,'game.txt'))
        (live/'game.txt').write_text(text)
        after=gw._live_meta(live/'game.txt')
        path=gw.current_file(self.cfg,'docich')
        state=gw.read_json(path)
        policy=self.base/'repair-policy.json'
        gw.write_json(policy,{'repair_kind':'test','projection':'games/soviet_now','health_command':['/usr/bin/true']})
        self.cfg['repair_policies']={'test':str(policy)}
        state['pending_repairs']=[{'policy_id':'test','id':'a'*32,'projection':'games/soviet_now','status':'active',
                                   'files':{'game.txt':{'before':before,'after':after}}}]
        gw.write_json(path,state)

    def test_deploy_adopts_repair_without_rewriting_live(self):
        live,subremote,old,new=self._projection_case()
        self._record_pending(live,subremote,old)
        inode=(live/'game.txt').stat().st_ino
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}):
            gw.deploy_git(self.cfg,'docich',new)
        self.assertEqual((live/'game.txt').stat().st_ino,inode)
        self.assertEqual(gw.read_json(gw.current_file(self.cfg,'docich'))['pending_repairs'],[])

    def test_partial_repair_adoption_manages_only_adopted_path(self):
        live,subremote,old,new=self._projection_case()
        self._record_pending(live,subremote,old)
        state_path=gw.current_file(self.cfg,'docich')
        state=gw.read_json(state_path)
        before=gw._expected_meta(subremote,gw._tree_entry(subremote,gw.submodule_gitlink_at(self.docich,old,'games/soviet_now'),'second.txt'))
        (live/'second.txt').write_text('second-repaired\n')
        state['pending_repairs'][0]['files']['second.txt']={'before':before,'after':gw._live_meta(live/'second.txt')}
        gw.write_json(state_path,state)
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}):
            gw.deploy_git(self.cfg,'docich',new)
        state=gw.read_json(state_path)
        self.assertEqual(set(state['pending_repairs'][0]['files']),{'second.txt'})
        self.assertIn('game.txt',state['managed_projection_files']['games/soviet_now'])
        self.assertNotIn('second.txt',state['managed_projection_files']['games/soviet_now'])
        (live/'game.txt').write_text('drift-after-adoption\n')
        self.assertEqual(gw.status_result(self.cfg,'docich','production',new)['status'],'drift')

    def test_later_main_updates_managed_repair_metadata(self):
        live,subremote,old,new=self._projection_case()
        self._record_pending(live,subremote,old)
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}):
            gw.deploy_git(self.cfg,'docich',new)
        (subremote/'game.txt').write_text('v3\n')
        subprocess.run(['git','-C',subremote,'commit','-qam','v3'],check=True)
        sub_v3=gw.git(subremote,'rev-parse','HEAD')
        candidate=self.base/'candidate-v3'
        subprocess.run(['git','clone','-q',self.docich,candidate],check=True)
        subprocess.run(['git','-C',candidate,'config','user.email','t@example.com'],check=True)
        subprocess.run(['git','-C',candidate,'config','user.name','T'],check=True)
        subprocess.run(['git','-C',candidate,'update-index','--cacheinfo',f'160000,{sub_v3},games/soviet_now'],check=True)
        subprocess.run(['git','-C',candidate,'commit','-qm','parent-v3'],check=True)
        parent_v3=gw.git(candidate,'rev-parse','HEAD')
        bundle=gw.bundle_file(self.cfg,'docich',parent_v3)
        subprocess.run(['git','-C',candidate,'bundle','create',bundle,'HEAD'],check=True)
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}):
            gw.deploy_git(self.cfg,'docich',parent_v3)
        self.assertEqual((live/'game.txt').read_text(),'v3\n')
        managed=gw.read_json(gw.current_file(self.cfg,'docich'))['managed_projection_files']['games/soviet_now']['game.txt']
        self.assertEqual(managed,gw._live_meta(live/'game.txt'))

    def test_deploy_conflict_preserves_repair_and_parent_state(self):
        live,subremote,old,new=self._projection_case()
        self._record_pending(live,subremote,old,'alternative-fix\n')
        before=gw.read_json(gw.current_file(self.cfg,'docich'))
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}):
            with self.assertRaisesRegex(ValueError,'pending repair conflict'):
                gw.deploy_git(self.cfg,'docich',new)
        self.assertEqual((live/'game.txt').read_text(),'alternative-fix\n')
        self.assertEqual(gw.git(self.docich,'rev-parse','HEAD'),old)
        self.assertEqual(gw.read_json(gw.current_file(self.cfg,'docich')),before)

    def test_failed_state_write_keeps_pending_fix_and_rolls_back_other_files(self):
        live,subremote,old,new=self._projection_case(change_second=True)
        self._record_pending(live,subremote,old)
        before=gw.read_json(gw.current_file(self.cfg,'docich'))
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}), mock.patch.object(gw,'write_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):gw.deploy_git(self.cfg,'docich',new)
        self.assertEqual((live/'game.txt').read_text(),'v2\n')
        self.assertEqual((live/'second.txt').read_text(),'second-v1\n')
        self.assertEqual(gw.read_json(gw.current_file(self.cfg,'docich')),before)

    def test_pending_drift_during_other_write_is_not_formalized(self):
        live,subremote,old,new=self._projection_case(change_second=True)
        self._record_pending(live,subremote,old)
        real_apply=gw._apply_projection
        def racing(changes):
            real_apply(changes)
            (live/'game.txt').write_text('manual-during-deploy\n')
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}), mock.patch.object(gw,'_apply_projection',side_effect=racing):
            with self.assertRaisesRegex(ValueError,'pending repair drift'):
                gw.deploy_git(self.cfg,'docich',new)
        self.assertEqual((live/'game.txt').read_text(),'manual-during-deploy\n')
        self.assertEqual((live/'second.txt').read_text(),'second-v1\n')
        self.assertEqual(gw.read_json(gw.current_file(self.cfg,'docich'))['sha'],old)

    def test_post_deploy_health_failure_preserves_repair(self):
        live,subremote,old,new=self._projection_case(change_second=True)
        self._record_pending(live,subremote,old)
        gw.write_json(self.base/'repair-policy.json',{'repair_kind':'test','projection':'games/soviet_now','health_command':['/usr/bin/false']})
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}):
            with self.assertRaisesRegex(ValueError,'health failed'):
                gw.deploy_git(self.cfg,'docich',new)
        self.assertEqual((live/'game.txt').read_text(),'v2\n')
        self.assertEqual((live/'second.txt').read_text(),'second-v1\n')
        self.assertEqual(gw.read_json(gw.current_file(self.cfg,'docich'))['sha'],old)

    def test_rollback_preserves_unknown_file_but_restores_parent_and_known_files(self):
        live,subremote,old,new=self._projection_case(change_second=True)
        real_apply=gw._apply_projection
        def race(changes):
            real_apply(changes)
            (live/'game.txt').write_text('manual\n')
            raise OSError('later failure')
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}),mock.patch.object(gw,'_apply_projection',side_effect=race):
            with self.assertRaisesRegex(ValueError,'recovery required'):gw.deploy_git(self.cfg,'docich',new)
        self.assertEqual((live/'game.txt').read_text(),'manual\n')
        self.assertEqual((live/'second.txt').read_text(),'second-v1\n')
        self.assertEqual(gw.git(self.docich,'rev-parse','HEAD'),old)
        self.assertEqual(gw.read_json(gw.current_file(self.cfg,'docich'))['sha'],old)

    def test_status_reports_pending_drift_and_incomplete_intent(self):
        live,subremote,old,new=self._projection_case()
        self._record_pending(live,subremote,old)
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}):
            self.assertEqual(gw.status_result(self.cfg,'docich','production',old)['status'],'configured')
            (live/'game.txt').write_text('manual\n')
            self.assertEqual(gw.status_result(self.cfg,'docich','production',old)['status'],'drift')
            path=gw.current_file(self.cfg,'docich');state=gw.read_json(path);state['pending_repairs'][0]['status']='applying';gw.write_json(path,state)
            self.assertEqual(gw.status_result(self.cfg,'docich','production',old)['status'],'recovery_required')

    def test_unknown_rollback_persists_recovery_and_blocks_next_deploy(self):
        live,subremote,old,new=self._projection_case(change_second=True)
        real_apply=gw._apply_projection
        def race(changes):
            real_apply(changes);(live/'game.txt').write_text('manual\n');raise OSError('failure')
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}):
            with mock.patch.object(gw,'_apply_projection',side_effect=race),self.assertRaises(ValueError):
                gw.deploy_git(self.cfg,'docich',new)
            self.assertEqual(gw.status_result(self.cfg,'docich','production',old)['status'],'recovery_required')
            with self.assertRaisesRegex(ValueError,'recovery required'):gw.deploy_git(self.cfg,'docich',new)

    def test_adopted_file_remains_monitored_after_state_commit(self):
        live,subremote,old,new=self._projection_case()
        self._record_pending(live,subremote,old)
        real_write=gw.write_json
        def racing_write(path,value):
            real_write(path,value)
            if value.get('sha')==new and 'deployment_intent' not in value:
                (live/'game.txt').write_text('manual-at-commit\n')
        with mock.patch.object(gw,'OWNED_SUBMODULES',{'games/soviet_now':str(subremote)}):
            with mock.patch.object(gw,'write_json',side_effect=racing_write),self.assertRaises(ValueError):
                gw.deploy_git(self.cfg,'docich',new)
            self.assertNotEqual(gw.status_result(self.cfg,'docich','production',old)['status'],'configured')
            with self.assertRaises(ValueError):gw.deploy_git(self.cfg,'docich',new)

if __name__ == '__main__':
    unittest.main()
