import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import os

spec = importlib.util.spec_from_file_location('worker', Path(__file__).resolve().parents[1] / 'worker.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

class WorkerTests(unittest.TestCase):
    def event(self):
        return dict(event_id='a'*32, category='stream_bug_report', time=10,
                    redacted_context_hash='b'*64, source='chat')
    def test_malformed_and_raw_rejected(self):
        for change in ({'event_id':'../escape'}, {'category':'other'}, {'comment':'raw'}, {'time':True}):
            with self.assertRaises(ValueError): w.validate_event(dict(self.event(), **change))
    def test_dedup_hash_cooldown(self):
        jobs = {'old':dict(event=self.event(), created=100, status='awaiting_review')}
        self.assertTrue(w.duplicate(self.event(), jobs, 101, 3600))
        event = dict(self.event(), event_id='c'*32)
        self.assertTrue(w.duplicate(event, jobs, 101, 3600))
        self.assertFalse(w.duplicate(event, jobs, 4000, 3600))
    def test_pr_failure_recovers_without_staging(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d).resolve()/'job.json'
            job = dict(status='staged', repair_id='a'*32, branch='codex/repair-a', event=self.event())
            publisher = Mock(side_effect=[RuntimeError('secret'), 'https://github.com/a/b/pull/1'])
            attach = Mock()
            w.publish(job,p,publisher,attach)
            self.assertEqual(json.loads(p.read_text())['status'], 'staged')
            self.assertNotIn('secret',p.read_text())
            w.publish(job,p,publisher,attach)
            self.assertEqual(job['status'],'awaiting_review')
            attach.assert_called_once()
    def test_replacement_escape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): w.replace_files(Path(d).resolve(), {'../x':'x'}, ['../x'])

    def test_push_failure_never_stages(self):
        with tempfile.TemporaryDirectory() as d:
            job=dict(status='candidate_ready', event=self.event(), branch='codex/x',base_sha='a'*40,candidate_sha='b'*40)
            with patch.object(w,'git',side_effect=RuntimeError('push failed')), patch.object(w,'run') as runner:
                with self.assertRaises(RuntimeError):
                    w.process({'stage_helper':'/helper','state_dir':d},Path('/policy'),{},job,Path(d).resolve()/'job.json')
                runner.assert_not_called()
                self.assertEqual(job['status'],'candidate_ready')

    def test_staged_retry_skips_git_and_apply(self):
        with tempfile.TemporaryDirectory() as d:
            job=dict(status='staged', event=self.event(), branch='codex/x',repair_id='a'*32)
            with patch.object(w,'git') as git, patch.object(w,'publish_pr',return_value='https://github.com/a/b/pull/1'), patch.object(w,'run',return_value=(0,'')) as runner:
                w.process({'stage_helper':'/helper','state_dir':d,'gateway_config':'/gateway'},Path('/policy'),{},job,Path(d).resolve()/'job.json')
                git.assert_not_called()
                self.assertEqual(runner.call_count,1)
                self.assertIn('attach-pr',runner.call_args.args[0])
                self.assertEqual(job['status'],'awaiting_review')

    def test_sandbox_has_no_network_or_host_home(self):
        with patch.object(w,'run',return_value=(0,'')) as runner:
            w.sandbox(['/usr/bin/python3','probe.py'],Path('/private/candidate'),30)
            argv=runner.call_args.args[0]
            self.assertIn('--unshare-all',argv)
            self.assertIn('--clearenv',argv)
            self.assertNotIn('--share-net',argv)
            self.assertNotIn('--bind',argv)
            self.assertNotIn('/home/ubuntu',argv)

    def test_symlink_replacement_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve(); (root/'outside').write_text('safe'); (root/'file').symlink_to(root/'outside')
            with self.assertRaises(ValueError): w.replace_files(root,{'file':'bad'},['file'])
            self.assertEqual((root/'outside').read_text(),'safe')

    def test_broker_receives_only_allowlisted_text_clean_environment(self):
        with tempfile.TemporaryDirectory() as d:
            candidate=Path(d).resolve(); (candidate/'safe.py').write_text('print(1)'); (candidate/'.env').write_text('SECRET=bad')
            policy=dict(generator_mode='broker',generator_command=['/usr/local/libexec/repair-broker'],repair_kind='audio',allowed_paths=['safe.py'])
            with patch.dict(os.environ,{'SECRET':'do-not-pass'}), patch.object(w,'run',return_value=(0,'{"replacements":{"safe.py":"print(2)"}}')) as runner:
                output=w.generate(policy,candidate)
                call=runner.call_args
                payload=json.loads(call.kwargs['stdin_data'])
                self.assertEqual(payload,{'repair_kind':'audio','files':{'safe.py':'print(1)'}})
                self.assertEqual(set(call.kwargs['env']),{'PATH','HOME'})
                self.assertNotEqual(str(call.kwargs['cwd']),str(candidate))
                self.assertNotIn(str(candidate),call.kwargs['stdin_data'].decode())
                self.assertIn('replacements',json.loads(output))

    def test_broker_rejects_escape_symlink_and_large_input(self):
        with tempfile.TemporaryDirectory() as d:
            candidate=Path(d).resolve(); (candidate/'large').write_text('a'*1048577); (candidate/'link').symlink_to(candidate/'large')
            policy=dict(generator_mode='broker',generator_command=['/usr/bin/false'],repair_kind='audio')
            for name in ('../escape','link','large'):
                with patch.object(w,'run') as runner:
                    with self.assertRaises(ValueError): w.generate(dict(policy,allowed_paths=[name]),candidate)
                    runner.assert_not_called()

    def test_generator_cannot_replace_tests_or_unknown_files(self):
        with tempfile.TemporaryDirectory() as d:
            candidate=Path(d).resolve(); (candidate/'allowed.py').write_text('before'); (candidate/'test.py').write_text('assert True')
            with self.assertRaises(ValueError): w.replace_files(candidate,{'allowed.py':'after','test.py':'pass'},['allowed.py'])
            self.assertEqual((candidate/'allowed.py').read_text(),'before')

    def test_forbidden_policy_fails_before_command_or_push(self):
        with patch.object(w,'trusted') as trust, patch.object(w,'run') as runner:
            with self.assertRaises(ValueError): w.policy_check({'allowed_paths':['core/config.sh']})
            trust.assert_not_called(); runner.assert_not_called()

    def test_allowed_files_synchronized_with_stage(self):
        import ast
        tree=ast.parse(Path(__file__).resolve().parents[2].joinpath('vm_actions/stage_repair.py').read_text())
        stage=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ALLOWED_FILES' for t in n.targets))
        self.assertEqual(w.ALLOWED_FILES,stage)

    def test_event_flood_and_capacity_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            for i in range(w.MAX_EVENT_SCAN+1): (root/(str(i)+'.json')).write_text('{}')
            event,status=w.next_event(root,{},1000)
            self.assertIsNone(event); self.assertEqual(status,'event_scan_capacity')
            event,status=w.next_event(root,dict.fromkeys(range(w.MAX_JOBS)),1000)
            self.assertIsNone(event); self.assertEqual(status,'job_capacity')

    def test_old_and_future_events_ignored_one_fresh_selected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve()
            for ident,timestamp in [('a',1),('b',9999),('c',1999),('d',1998)]:
                event=dict(self.event(),event_id=ident*32,time=timestamp)
                (root/(ident*32+'.json')).write_text(json.dumps(event))
            event,status=w.next_event(root,{},2000)
            self.assertEqual(event['event_id'],'c'*32); self.assertEqual(status,'ready')

    def test_custom_cooldown_is_used(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); event=dict(self.event(),time=2000,event_id='c'*32)
            w.save(root/(event['event_id']+'.json'),event)
            jobs={'old':{'event':self.event(),'created':1900,'status':'awaiting_review'}}
            self.assertEqual(w.next_event(root,jobs,2000,0)[1],'ready')
            self.assertEqual(w.next_event(root,jobs,2000,86400)[1],'idle')

    def test_production_service_identity_and_sandbox_path(self):
        root=Path(__file__).resolve().parents[1]
        unit=(root/'vm-self-repair.service').read_text()
        self.assertIn('User=ubuntu',unit);self.assertIn('Group=ubuntu',unit)
        with patch.object(w,'run',return_value=(0,'')) as run:
            w.sandbox(['/usr/bin/true'],Path('/candidate'),10)
        self.assertEqual(run.call_args.args[0][0],'/usr/local/bin/bwrap')

if __name__ == '__main__': unittest.main()

