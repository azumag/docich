import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
AUTH=ROOT/'ops/vm_actions/authorize.py'
GATEWAY=ROOT/'ops/vm_actions/gateway.py'
WF=ROOT/'.github/workflows/vm-operations.yml'

class AuthorizeTests(unittest.TestCase):
    def run_auth(self, **overrides):
        env={
            'GITHUB_REPOSITORY':'azumag/docich',
            'GITHUB_REPOSITORY_ID':'1327276249',
            'GITHUB_REPOSITORY_OWNER':'azumag',
            'GITHUB_REPOSITORY_OWNER_ID':'9018513',
            'GITHUB_REPOSITORY_PRIVATE':'true',
            'GITHUB_ACTOR':'azumag',
            'GITHUB_ACTOR_ID':'9018513',
            'GITHUB_TRIGGERING_ACTOR':'azumag',
            'GITHUB_REF':'refs/heads/main',
            'GITHUB_WORKFLOW_REF':'azumag/docich/.github/workflows/vm-operations.yml@refs/heads/main',
            'GITHUB_EVENT_NAME':'workflow_dispatch',
            'GITHUB_SHA':'a'*40,
            'INPUT_OPERATION':'deploy',
            'INPUT_TARGET':'preview',
            'INPUT_REF':'feature/test',
            'INPUT_CONFIRM':'',
        }
        env.update(overrides)
        return subprocess.run(['python3', str(AUTH)], text=True, capture_output=True, env=env)

    def test_owner_manual_preview_allowed(self):
        p=self.run_auth(); self.assertEqual(p.returncode,0,p.stderr)
        data=json.loads(p.stdout); self.assertEqual(data['ref'],'feature/test')

    def test_soviet_now_context_is_denied(self):
        p=self.run_auth(
            GITHUB_REPOSITORY='azumag/soviet_now',
            GITHUB_REPOSITORY_ID='1155505884',
            GITHUB_WORKFLOW_REF='azumag/soviet_now/.github/workflows/vm-operations.yml@refs/heads/main',
        )
        self.assertNotEqual(p.returncode,0)

    def test_non_owner_actor_denied(self):
        p=self.run_auth(GITHUB_ACTOR='collab',GITHUB_ACTOR_ID='42')
        self.assertNotEqual(p.returncode,0)

    def test_non_owner_rerun_denied(self):
        p=self.run_auth(GITHUB_TRIGGERING_ACTOR='collab')
        self.assertNotEqual(p.returncode,0)

    def test_workflow_must_be_main_copy(self):
        p=self.run_auth(GITHUB_WORKFLOW_REF='azumag/docich/.github/workflows/vm-operations.yml@refs/heads/feature')
        self.assertNotEqual(p.returncode,0)

    def test_production_exec_needs_confirmation(self):
        p=self.run_auth(INPUT_OPERATION='exec',INPUT_TARGET='production',INPUT_REF='main',INPUT_CONFIRM='')
        self.assertNotEqual(p.returncode,0)
        p=self.run_auth(INPUT_OPERATION='exec',INPUT_TARGET='production',INPUT_REF='main',INPUT_CONFIRM='production')
        self.assertEqual(p.returncode,0,p.stderr)

    def test_repository_visibility_context_is_required(self):
        p=self.run_auth(GITHUB_REPOSITORY_PRIVATE='')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('invalid repository visibility context',p.stderr)

    def test_public_repository_disables_arbitrary_exec(self):
        p=self.run_auth(
            GITHUB_REPOSITORY_PRIVATE='false',
            INPUT_OPERATION='exec',
            INPUT_TARGET='preview',
            INPUT_REF='main',
        )
        self.assertNotEqual(p.returncode,0)
        self.assertIn('disabled when the repository is public',p.stderr)

    def test_public_repository_keeps_read_only_status_available(self):
        p=self.run_auth(
            GITHUB_REPOSITORY_PRIVATE='false',
            INPUT_OPERATION='status',
            INPUT_TARGET='production',
            INPUT_REF='main',
        )
        self.assertEqual(p.returncode,0,p.stderr)

    def test_configure_jev_requires_production_main_and_confirmation(self):
        p=self.run_auth(
            GITHUB_REPOSITORY_PRIVATE='false',
            INPUT_OPERATION='configure_jev',
            INPUT_TARGET='preview',
            INPUT_REF='main',
            INPUT_CONFIRM='production',
        )
        self.assertNotEqual(p.returncode,0)
        self.assertIn('production-only',p.stderr)
        p=self.run_auth(
            GITHUB_REPOSITORY_PRIVATE='false',
            INPUT_OPERATION='configure_jev',
            INPUT_TARGET='production',
            INPUT_REF='feature/test',
            INPUT_CONFIRM='production',
        )
        self.assertNotEqual(p.returncode,0)
        self.assertIn('must run from main',p.stderr)
        p=self.run_auth(
            GITHUB_REPOSITORY_PRIVATE='false',
            INPUT_OPERATION='configure_jev',
            INPUT_TARGET='production',
            INPUT_REF='main',
            INPUT_CONFIRM='',
        )
        self.assertNotEqual(p.returncode,0)
        self.assertIn('confirmation required',p.stderr)
        p=self.run_auth(
            GITHUB_REPOSITORY_PRIVATE='false',
            INPUT_OPERATION='configure_jev',
            INPUT_TARGET='production',
            INPUT_REF='main',
            INPUT_CONFIRM='production',
        )
        self.assertEqual(p.returncode,0,p.stderr)
        p=self.run_auth(
            GITHUB_REPOSITORY_PRIVATE='false',
            INPUT_OPERATION='disable_jev',
            INPUT_TARGET='production',
            INPUT_REF='main',
            INPUT_CONFIRM='production',
        )
        self.assertEqual(p.returncode,0,p.stderr)

    def test_all_vm_ssh_calls_enable_encrypted_keepalives(self):
        workflow = WF.read_text(encoding="utf-8")
        ssh_configs = workflow.count("ssh_args=(-F /dev/null")
        self.assertGreater(ssh_configs, 0)
        self.assertEqual(workflow.count("ServerAliveInterval=30"), ssh_configs)
        self.assertEqual(workflow.count("ServerAliveCountMax=3"), ssh_configs)

    def test_market_paper_requires_production_and_main(self):
        p=self.run_auth(INPUT_OPERATION='market_paper',INPUT_TARGET='preview',INPUT_REF='main',INPUT_CONFIRM='production')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('production-only',p.stderr)
        p=self.run_auth(INPUT_OPERATION='market_paper',INPUT_TARGET='production',INPUT_REF='feature',INPUT_CONFIRM='production')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('must run from main',p.stderr)

    def test_market_paper_needs_confirmation(self):
        p=self.run_auth(INPUT_OPERATION='market_paper',INPUT_TARGET='production',INPUT_REF='main',INPUT_CONFIRM='')
        self.assertNotEqual(p.returncode,0)
        p=self.run_auth(INPUT_OPERATION='market_paper',INPUT_TARGET='production',INPUT_REF='main',INPUT_CONFIRM='production')
        self.assertEqual(p.returncode,0,p.stderr)

    def test_market_paper_not_blocked_by_public_repo_exec_disable(self):
        p=self.run_auth(
            GITHUB_REPOSITORY_PRIVATE='false',
            INPUT_OPERATION='market_paper',INPUT_TARGET='production',INPUT_REF='main',INPUT_CONFIRM='production',
        )
        self.assertEqual(p.returncode,0,p.stderr)

    def test_push_is_fixed_to_production_deploy(self):
        p=self.run_auth(GITHUB_EVENT_NAME='push',INPUT_OPERATION='',INPUT_TARGET='',INPUT_REF='')
        self.assertEqual(p.returncode,0,p.stderr)
        data=json.loads(p.stdout); self.assertEqual((data['operation'],data['target'],data['ref']),('deploy','production','a'*40))


class GatewayTests(unittest.TestCase):
    def test_gateway_accepts_docich_only_config_and_rejects_soviet_now(self):
        base=Path(tempfile.mkdtemp(prefix='vmops-config-'))
        state=base/'state'; state.mkdir()
        doc=base/'docich'; doc.mkdir()
        subprocess.run(['git','init','-q',doc],check=True)
        good=base/'good.json'
        good.write_text(json.dumps({'state':str(state),'repos':{'docich':{'production':str(doc),'mode':'git'}}}))
        env=os.environ.copy(); env['VMOPS_TESTING']='1'
        code='import importlib.util, pathlib; p=pathlib.Path(r"%s"); s=importlib.util.spec_from_file_location("g", r"%s"); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.load_config(p)' % (good, GATEWAY)
        ok=subprocess.run(['python3','-c',code],env=env,capture_output=True)
        self.assertEqual(ok.returncode,0,ok.stderr.decode())
        bad=base/'bad.json'
        bad.write_text(json.dumps({'state':str(state),'repos':{'docich':{'production':str(doc),'mode':'git'},'soviet_now':{'production':str(base),'mode':'overlay'}}}))
        code=code.replace(str(good),str(bad))
        denied=subprocess.run(['python3','-c',code],env=env,capture_output=True)
        self.assertNotEqual(denied.returncode,0)

    def setUp(self):
        self.base=Path(tempfile.mkdtemp(prefix='vmops-gw-'))
        self.state=self.base/'state'; self.state.mkdir()
        self.doc=self.base/'docich'; self.doc.mkdir()
        subprocess.run(['git','init','-q',self.doc],check=True)
        subprocess.run(['git','-C',self.doc,'config','user.email','t@example.com'],check=True)
        subprocess.run(['git','-C',self.doc,'config','user.name','T'],check=True)
        (self.doc/'app.py').write_text('v1\n')
        subprocess.run(['git','-C',self.doc,'add','app.py'],check=True)
        subprocess.run(['git','-C',self.doc,'commit','-qm','v1'],check=True)
        self.config=self.base/'config.json'
        self.config.write_text(json.dumps({'state':str(self.state),'repos':{
            'docich':{'production':str(self.doc),'mode':'git'}
        }}))
    def call(self, cmd, payload=b''):
        env=os.environ.copy(); env['SSH_ORIGINAL_COMMAND']=cmd; env['VMOPS_TESTING']='1'
        return subprocess.run(['python3',str(GATEWAY),str(self.config)],input=payload,capture_output=True,env=env)

    def make_docich_bundle(self, text='v2\n'):
        candidate=self.base/'candidate'
        subprocess.run(['git','clone','-q',self.doc,candidate],check=True)
        subprocess.run(['git','-C',candidate,'config','user.email','t@example.com'],check=True)
        subprocess.run(['git','-C',candidate,'config','user.name','T'],check=True)
        (candidate/'app.py').write_text(text)
        subprocess.run(['git','-C',candidate,'add','app.py'],check=True)
        subprocess.run(['git','-C',candidate,'commit','-qm','candidate'],check=True)
        sha=subprocess.check_output(['git','-C',candidate,'rev-parse','HEAD'],text=True).strip()
        bundle=self.base/'candidate.bundle'
        subprocess.run(['git','-C',candidate,'bundle','create',bundle,'HEAD'],check=True)
        return sha,bundle.read_bytes()

    def test_docich_production_uses_git_bundle_and_preserves_git_head(self):
        sha,bundle=self.make_docich_bundle()
        p=self.call(f'upload docich production {sha}',bundle)
        self.assertEqual(p.returncode,0,p.stderr.decode())
        p=self.call(f'bootstrap docich production {sha}')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        p=self.call(f'deploy docich production {sha}')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        head=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        self.assertEqual(head,sha)
        self.assertEqual((self.doc/'app.py').read_text(),'v2\n')
        status=subprocess.check_output(['git','-C',self.doc,'status','--porcelain','--untracked-files=no','--ignore-submodules=all'],text=True)
        self.assertEqual(status,'')

    def test_docich_git_deploy_refuses_tracked_drift(self):
        sha,bundle=self.make_docich_bundle()
        self.assertEqual(self.call(f'upload docich production {sha}',bundle).returncode,0)
        (self.doc/'app.py').write_text('manual-hotfix\n')
        p=self.call(f'deploy docich production {sha}')
        self.assertNotEqual(p.returncode,0)
        self.assertEqual((self.doc/'app.py').read_text(),'manual-hotfix\n')


    def _write_baseline(self, sha):
        (self.state/'current').mkdir(parents=True,exist_ok=True)
        (self.state/'current'/'docich.json').write_text(json.dumps({'mode':'git','sha':sha,'previous_head':None,'pending_repairs':[]}))

    def test_deploy_reject_reports_fixed_reason_code(self):
        sha,bundle=self.make_docich_bundle()
        self.assertEqual(self.call(f'upload docich production {sha}',bundle).returncode,0)
        self.assertEqual(self.call(f'bootstrap docich production {sha}').returncode,0)
        (self.doc/'app.py').write_text('manual-hotfix\n')
        p=self.call(f'deploy docich production {sha}')
        self.assertNotEqual(p.returncode,0)
        err=p.stderr.decode()
        self.assertIn('VM operation rejected: tracked_vm_drift',err)
        self.assertNotIn('tracked VM drift detected',err)

    # Issue #410: unexpected deploy exceptions must not bypass fixed reason
    # codes with raw tracebacks (argv/paths) on Actions output.
    def _load_gateway(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location("gw410",str(GATEWAY))
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _run_main_with_failure(self, module):
        import io
        from contextlib import redirect_stderr
        sha=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        env=dict(os.environ)
        env['SSH_ORIGINAL_COMMAND']=f'deploy docich production {sha}'
        env['VMOPS_TESTING']='1'
        old_argv, old_environ = sys.argv, dict(os.environ)
        os.environ.clear(); os.environ.update(env)
        sys.argv=['gateway.py',str(self.config)]
        buf=io.StringIO()
        try:
            with redirect_stderr(buf):
                module.main()
        except SystemExit as exc:
            code=exc.code
        finally:
            sys.argv=old_argv; os.environ.clear(); os.environ.update(old_environ)
        return code, buf.getvalue(), sha

    def _raise_unexpected(self, exc):
        def _fail(cfg, repo, sha):
            raise exc
        return _fail

    def test_unexpected_deploy_errors_use_fixed_code_without_leak(self):
        import subprocess as sp
        module=self._load_gateway()
        failures=[
            sp.CalledProcessError(128,['git','SECRET-ARGV-MARKER','/tmp/SECRET-PATH-MARKER']),
            sp.TimeoutExpired(['git','SECRET-ARGV-MARKER'],300),
            OSError('SECRET-OSERROR-MARKER /tmp/SECRET-PATH-MARKER'),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                before=set((self.state/'logs').glob('unknown-*.log')) if (self.state/'logs').exists() else set()
                module.deploy_prod=self._raise_unexpected(failure)
                code, err, sha=self._run_main_with_failure(module)
                self.assertEqual(code,1)
                self.assertEqual(err,'VM operation rejected: operation_rejected\n')
                for marker in ('Traceback','SECRET-ARGV-MARKER','SECRET-PATH-MARKER','SECRET-OSERROR-MARKER'):
                    self.assertNotIn(marker,err)
                after=set((self.state/'logs').glob('unknown-*.log'))
                new_logs=after-before
                self.assertEqual(len(new_logs),1)
                log=next(iter(new_logs))
                self.assertEqual(oct(log.stat().st_mode & 0o777),'0o600')
                self.assertIn(type(failure).__name__,log.read_text(errors='replace'))

    def test_keyboard_interrupt_is_not_swallowed(self):
        module=self._load_gateway()
        module.deploy_prod=self._raise_unexpected(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self._run_main_with_failure(module)

    def test_known_value_error_keeps_specific_code(self):
        module=self._load_gateway()
        module.deploy_prod=self._raise_unexpected(ValueError('tracked VM drift detected'))
        code, err, sha=self._run_main_with_failure(module)
        self.assertEqual(code,1)
        self.assertEqual(err,'VM operation rejected: tracked_vm_drift\n')

    def test_broken_repo_deploy_never_prints_traceback(self):
        import shutil
        sha,bundle=self.make_docich_bundle()
        self.assertEqual(self.call(f'upload docich production {sha}',bundle).returncode,0)
        self.assertEqual(self.call(f'bootstrap docich production {sha}').returncode,0)
        shutil.rmtree(self.doc/'.git')
        (self.doc/'.git').write_text('broken\n')
        p=self.call(f'deploy docich production {sha}')
        self.assertNotEqual(p.returncode,0)
        err=p.stderr.decode()
        self.assertTrue(err.startswith('VM operation rejected: '),err)
        self.assertNotIn('Traceback (most recent call last)',err)

    def test_rebaseline_recovers_ancestor_drift_then_deploys(self):
        base=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        subprocess.run(['git','-C',self.doc,'commit','--allow-empty','-qm','B'],check=True)
        drift=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        cand=self.base/'rebased-cand'
        subprocess.run(['git','clone','-q',self.doc,cand],check=True)
        subprocess.run(['git','-C',cand,'config','user.email','t@example.com'],check=True)
        subprocess.run(['git','-C',cand,'config','user.name','T'],check=True)
        (Path(cand)/'app.py').write_text('v3\n')
        subprocess.run(['git','-C',cand,'add','app.py'],check=True)
        subprocess.run(['git','-C',cand,'commit','-qm','C'],check=True)
        sha=subprocess.check_output(['git','-C',cand,'rev-parse','HEAD'],text=True).strip()
        bundle_path=self.base/'rebased.bundle'
        subprocess.run(['git','-C',cand,'bundle','create',bundle_path,'HEAD'],check=True)
        self.assertEqual(self.call(f'upload docich production {sha}',bundle_path.read_bytes()).returncode,0)
        self._write_baseline(base)
        p=self.call(f'rebaseline docich production {sha}')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        state=json.loads((self.state/'current'/'docich.json').read_text())
        self.assertEqual(state['sha'],drift)
        p=self.call(f'deploy docich production {sha}')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        self.assertEqual(subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip(),sha)

    def test_rebaseline_refuses_non_ancestor(self):
        sha,bundle=self.make_docich_bundle()
        self.assertEqual(self.call(f'upload docich production {sha}',bundle).returncode,0)
        base=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        subprocess.run(['git','-C',self.doc,'commit','--allow-empty','-qm','D'],check=True)
        self._write_baseline(base)
        p=self.call(f'rebaseline docich production {sha}')
        self.assertNotEqual(p.returncode,0)
        self.assertIn('rebaseline_not_ancestor',p.stderr.decode())


    def test_preview_deploy_and_exec(self):
        sha,bundle=self.make_docich_bundle()
        self.assertEqual(self.call(f'upload docich preview {sha}',bundle).returncode,0)
        p=self.call(f'deploy docich preview {sha}')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        p=self.call(f'exec docich preview {sha}',b'printf hello')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        self.assertIn(b'hello',p.stdout)
        self.assertEqual((self.state/'releases'/'docich'/sha/'app.py').read_text(),'v2\n')


    def test_production_exec_hides_command_output(self):
        sha=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        p=self.call(f'exec docich production {sha}',b'printf SUPERSECRET')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        self.assertNotIn(b'SUPERSECRET',p.stdout)
        logs=list((self.state/'logs').glob('*.log'))
        self.assertTrue(logs)
        self.assertIn('SUPERSECRET',logs[-1].read_text())

    def test_configure_jev_uses_fixed_reviewed_script_and_withholds_key(self):
        soren=self.base/'soren'; soren.mkdir()
        script_dir=self.doc/'ops'/'vm_actions'; script_dir.mkdir(parents=True)
        (script_dir/'configure_comment_classifier_jev.py').write_text(
            'import os\n'
            'from pathlib import Path\n'
            'Path(os.environ["SOREN_ROOT"], "observed").write_text('
            '"present" if os.environ.get("TYPESAFE_API_KEY") else "missing")\n'
            'print("configured api_key=present")\n',
            encoding='utf-8',
        )
        subprocess.run(['git','-C',self.doc,'add','.'],check=True)
        subprocess.run(['git','-C',self.doc,'commit','-qm','jev configurator'],check=True)
        sha=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        self.config.write_text(json.dumps({'state':str(self.state),'repos':{
            'docich':{'production':str(self.doc),'mode':'git',
                      'projections':{'games/soviet_now':str(soren)}}
        }}))
        p=self.call(f'configure_jev docich production {sha}',b'SECRET_KEY')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        self.assertNotIn(b'SECRET_KEY',p.stdout)
        self.assertEqual((soren/'observed').read_text(), 'present')
        logs=list((self.state/'logs').glob('*.log'))
        self.assertTrue(logs)
        self.assertNotIn('SECRET_KEY', logs[-1].read_text())
        p=self.call(f'disable_jev docich production {sha}')
        self.assertEqual(p.returncode,0,p.stderr.decode())
        self.assertEqual((soren/'observed').read_text(), 'missing')

    def test_configure_jev_returns_only_fixed_failure_code(self):
        soren=self.base/'soren'; soren.mkdir()
        script_dir=self.doc/'ops'/'vm_actions'; script_dir.mkdir(parents=True)
        (script_dir/'configure_comment_classifier_jev.py').write_text(
            'import sys\n'
            'print("configuration failed: chat_worker_model_mismatch", file=sys.stderr)\n'
            'raise SystemExit(1)\n',
            encoding='utf-8',
        )
        subprocess.run(['git','-C',self.doc,'add','.'],check=True)
        subprocess.run(['git','-C',self.doc,'commit','-qm','jev failure'],check=True)
        sha=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        self.config.write_text(json.dumps({'state':str(self.state),'repos':{
            'docich':{'production':str(self.doc),'mode':'git',
                      'projections':{'games/soviet_now':str(soren)}}
        }}))
        p=self.call(f'configure_jev docich production {sha}',b'SECRET_KEY')
        self.assertNotEqual(p.returncode,0)
        result=json.loads(p.stdout)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error_code'],'chat_worker_model_mismatch')
        self.assertNotIn('SECRET_KEY',p.stdout.decode())

    # Issue #225: dedicated narrow reconcile operation (no exec channel).
    def _reconcile_shas(self):
        old_root='0'*40
        old_sub='1'*40
        new_sub='2'*40
        return old_root, old_sub, new_sub

    def test_reconcile_rejects_non_production_target(self):
        old_root, old_sub, new_sub=self._reconcile_shas()
        sha=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        p=self.call(f'reconcile docich preview {sha}',
                    f'{old_root} {old_sub} {new_sub} lineage'.encode())
        self.assertNotEqual(p.returncode,0)
        self.assertIn('VM operation rejected: reconcile_production_only',p.stderr.decode())

    def test_reconcile_rejects_malformed_payloads(self):
        sha=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        bad_payloads=[
            b'',
            b'only two tokens here',
            f'{"0"*39} {"1"*40} {"2"*40} lineage'.encode(),  # short sha
            f'{"0"*40} {"1"*40} {"1"*40} lineage'.encode(),  # no advance
            f'NOTHEX{"0"*34} {"1"*40} {"2"*40} lineage'.encode(),
            f'{"0"*40} {"1"*40} {"2"*40} bogus'.encode(),  # bad marker
            f'{"0"*40} {"1"*40} {"2"*40} lineage only-one'.encode(),  # partial triple
            f'{"0"*40} {"1"*40} {"2"*40} lineage has space {"f"*64} 100644'.encode(),
            f'{"0"*40} {"1"*40} {"2"*40} lineage goodpath {"g"*64} 100644'.encode(),  # non-hex digest
            f'{"0"*40} {"1"*40} {"2"*40} lineage goodpath {"f"*64} 100777'.encode(),  # bad mode
            b'\x00'+b'0'*40+b' '+b'1'*40+b' '+b'2'*40,
            ('0'*40).encode()+b' '+b'1'*40+b' '+b'2'*40+b' \xe9\x81\x95',
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload[:24]):
                p=self.call(f'reconcile docich production {sha}',payload)
                self.assertNotEqual(p.returncode,0)
                self.assertIn('VM operation rejected: reconcile_invalid_request',p.stderr.decode())

    def test_reconcile_rejects_oversized_payload(self):
        sha=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        p=self.call(f'reconcile docich production {sha}',b'x'*8193)
        self.assertNotEqual(p.returncode,0)
        self.assertIn('VM operation rejected: reconcile_invalid_request',p.stderr.decode())

    def test_reconcile_rejects_unknown_candidate_object(self):
        # Valid hex but absent locally: deterministic without network, since
        # the local object check precedes any fetch.
        unknown='3'*40
        old_root, old_sub, new_sub=self._reconcile_shas()
        p=self.call(f'reconcile docich production {unknown}',
                    f'{old_root} {old_sub} {new_sub} lineage'.encode())
        self.assertNotEqual(p.returncode,0)
        self.assertIn('VM operation rejected: reconcile_object_missing',p.stderr.decode())

    def test_reconcile_rejects_missing_reviewed_helpers(self):
        # Temp HEAD has no helper files: rejected before any mutation.
        sha=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        old_root, old_sub, new_sub=self._reconcile_shas()
        p=self.call(f'reconcile docich production {sha}',
                    f'{old_root} {old_sub} {new_sub} lineage'.encode())
        self.assertNotEqual(p.returncode,0)
        self.assertIn('VM operation rejected: reconcile_helper_missing',p.stderr.decode())

    def test_reconcile_runs_reviewed_helpers_with_fixed_argv_and_minimal_env(self):
        # Stub helpers committed at the helper relpaths record their argv and
        # environment; the op must pass fixed argv over argv-lists (no shell)
        # with a minimal secret-free environment.
        script_dir=self.doc/'ops'/'vm_actions'; script_dir.mkdir(parents=True)
        observed=self.base/'observed.json'
        stub=('import json,os,sys\n'
              '__import__("pathlib").Path(r"%s").write_text(\n'
              '    json.dumps({"argv":sys.argv[1:],"env":sorted(os.environ.keys())}))\n' % observed)
        (script_dir/'reconcile_presynced_root.py').write_text(stub,encoding='utf-8')
        (script_dir/'reconcile_presynced_submodule.py').write_text(stub,encoding='utf-8')
        subprocess.run(['git','-C',self.doc,'add','.'],check=True)
        subprocess.run(['git','-C',self.doc,'commit','-qm','stub helpers'],check=True)
        sha=subprocess.check_output(['git','-C',self.doc,'rev-parse','HEAD'],text=True).strip()
        # Minimal env must not carry the sentinel even when exported.
        env=os.environ.copy()
        env['RECONCILE_PARENT_SENTINEL']='MUST_NOT_LEAK_9f1c'
        code=('import importlib.util;'
              'spec=importlib.util.spec_from_file_location("gw",r"%s");'
              'gw=importlib.util.module_from_spec(spec);spec.loader.exec_module(gw);'
              'print(gw._run_reviewed_helper('
              'gw._reviewed_helper(__import__("pathlib").Path(r"%s"),r"%s",'
              '"ops/vm_actions/reconcile_presynced_root.py"),'
              '["--sentinel-check"],open(r"%s","wb")))' % (GATEWAY,self.doc,sha,self.base/'helper.log'))
        p=subprocess.run(['python3','-c',code],env=env,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr.decode())
        self.assertEqual(p.stdout.decode().strip(),'0')
        record=json.loads(observed.read_text())
        self.assertEqual(record['argv'],['--sentinel-check'])
        self.assertNotIn('RECONCILE_PARENT_SENTINEL',record['env'])
        self.assertIn('PATH',record['env'])

class WorkflowPolicyTests(unittest.TestCase):
    def test_installer_projects_docich_owned_soviet_submodule_only(self):
        text=(ROOT/'ops/vm_actions/install_vm_gateway.sh').read_text()
        self.assertIn('\"projections\"', text)
        self.assertIn('games/soviet_now', text)
        self.assertIn('/home/ubuntu/soren', text)
        self.assertNotIn('\"soviet_now\": {\"production\"', text)

    def test_owner_and_environment_gates_present(self):
        text=WF.read_text()
        self.assertIn("environment: vm-operations",text)
        self.assertIn("github.actor_id == 9018513",text)
        self.assertIn("github.triggering_actor == 'azumag'",text)
        self.assertIn("github.ref_protected == true",text)
        self.assertIn("GITHUB_REPOSITORY_PRIVATE: ${{ github.event.repository.private }}",text)
        self.assertIn("persist-credentials: false",text)
        self.assertIn("submodules: false",text)
        self.assertNotIn('pull_request_target:',text)
    def test_ssh_is_pinned_and_no_interactive_shell(self):
        text=WF.read_text()
        self.assertIn('StrictHostKeyChecking=yes',text)
        self.assertIn('ForwardAgent=no',text)
        self.assertIn('ClearAllForwardings=yes',text)
        self.assertIn('VM_SSH_KNOWN_HOSTS',text)
        self.assertNotIn('ssh-keyscan',text)
        self.assertIn('bundle create',text)
        self.assertNotIn('build_archive.py',text)

    def test_jev_configuration_uses_environment_secret_and_fixed_gateway_operation(self):
        text=WF.read_text()
        self.assertIn('configure_jev',text)
        self.assertIn('disable_jev',text)
        self.assertIn('TYPESAFE_API_KEY: ${{ secrets.TYPESAFE_API_KEY }}',text)
        self.assertIn('printf \'%s\' "$TYPESAFE_API_KEY" | ssh',text)
        self.assertIn('"configure_jev docich production $SHA"',text)
        self.assertIn('The VM gateway loads the',text)
        self.assertNotIn('VM_COMMAND: ${{ secrets.TYPESAFE_API_KEY }}',text)

    def test_reconcile_uses_dedicated_narrow_operation_without_exec_shell(self):
        # Issue #225: the reviewed pre-synced reconcile must not ride the
        # arbitrary exec channel; only strict tokens go over the dedicated op.
        text=WF.read_text()
        self.assertIn('"reconcile docich production $SHA"',text)
        start=text.index('Reconcile reviewed pre-synced Soren checkout')
        end=text.index('Normalize other owned submodule checkouts',start)
        block=text[start:end]
        self.assertNotIn('"exec docich production $SHA"',block)
        self.assertNotIn('reconcile_presynced_root.py\' | python3',block)
        self.assertNotIn('reconcile_presynced_submodule.py\' | python3',block)

if __name__=='__main__': unittest.main()
