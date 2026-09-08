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

    def test_all_vm_ssh_calls_enable_encrypted_keepalives(self):
        workflow = WF.read_text(encoding="utf-8")
        ssh_configs = workflow.count("ssh_args=(-F /dev/null")
        self.assertGreater(ssh_configs, 0)
        self.assertEqual(workflow.count("ServerAliveInterval=30"), ssh_configs)
        self.assertEqual(workflow.count("ServerAliveCountMax=3"), ssh_configs)

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

if __name__=='__main__': unittest.main()
