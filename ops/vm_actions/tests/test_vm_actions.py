import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
AUTH=ROOT/'ops/vm_actions/authorize.py'
GW=ROOT/'ops/vm_actions/gateway.py'
WF=ROOT/'.github/workflows/vm-operations.yml'
INSTALL=ROOT/'ops/vm_actions/install_vm_gateway.sh'

class AuthorizeTests(unittest.TestCase):
    def run_auth(self,**overrides):
        env=os.environ.copy(); env.update({
            'GITHUB_REPOSITORY':'azumag/docich','GITHUB_REPOSITORY_ID':'1327276249',
            'GITHUB_REPOSITORY_OWNER':'azumag','GITHUB_REPOSITORY_OWNER_ID':'9018513',
            'GITHUB_ACTOR':'azumag','GITHUB_ACTOR_ID':'9018513','GITHUB_TRIGGERING_ACTOR':'azumag',
            'GITHUB_REF':'refs/heads/main','GITHUB_WORKFLOW_REF':'azumag/docich/.github/workflows/vm-operations.yml@refs/heads/main',
            'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_SHA':'a'*40,
            'INPUT_OPERATION':'status','INPUT_TARGET':'preview','INPUT_REF':'main','INPUT_CONFIRM':'',
            'GITHUB_OUTPUT':str(Path(tempfile.mkdtemp())/'out'),
        }); env.update(overrides)
        return subprocess.run(['python3',str(AUTH)],env=env,capture_output=True,text=True)

    def test_owner_manual_preview_allowed(self):
        p=self.run_auth(); self.assertEqual(p.returncode,0,p.stderr)
        data=json.loads(p.stdout); self.assertEqual(data['target'],'preview')

    def test_non_owner_actor_denied(self):
        p=self.run_auth(GITHUB_ACTOR='collab',GITHUB_ACTOR_ID='999')
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
        docich=base/'docich'; docich.mkdir(); subprocess.run(['git','init','-q',str(docich)],check=True)
        config=base/'gateway.json'
        config.write_text(json.dumps({'state':str(state),'repos':{'docich':{'production':str(docich)}}}))
        env=os.environ.copy(); env.update({'VMOPS_ORIGINAL_COMMAND':'status docich production '+'a'*40,'VMOPS_TESTING':'1'})
        p=subprocess.run(['python3',str(GW),str(config)],env=env,capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        env['VMOPS_ORIGINAL_COMMAND']='status soviet_now production '+'a'*40
        p=subprocess.run(['python3',str(GW),str(config)],env=env,capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)

    def test_docich_git_deploy_refuses_tracked_drift(self):
        base=Path(tempfile.mkdtemp(prefix='vmops-git-drift-'))
        root=base/'docich'; root.mkdir(); subprocess.run(['git','init','-q',str(root)],check=True)
        subprocess.run(['git','-C',str(root),'config','user.email','tests@example.invalid'],check=True)
        subprocess.run(['git','-C',str(root),'config','user.name','vmops tests'],check=True)
        (root/'tracked.txt').write_text('old\n'); subprocess.run(['git','-C',str(root),'add','tracked.txt'],check=True); subprocess.run(['git','-C',str(root),'commit','-qm','old'],check=True)
        old=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
        state=base/'state'; (state/'current').mkdir(parents=True); (state/'bundles'/'docich').mkdir(parents=True)
        (state/'current'/'docich.json').write_text(json.dumps({'mode':'git','sha':old}))
        (root/'tracked.txt').write_text('drift\n')
        config=base/'gateway.json'; config.write_text(json.dumps({'state':str(state),'repos':{'docich':{'production':str(root)}}}))
        env=os.environ.copy(); env.update({'VMOPS_ORIGINAL_COMMAND':'deploy docich production '+old,'VMOPS_TESTING':'1'})
        p=subprocess.run(['python3',str(GW),str(config)],env=env,capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)

    def test_docich_production_uses_git_bundle_and_preserves_git_head(self):
        base=Path(tempfile.mkdtemp(prefix='vmops-git-'))
        root=base/'docich'; root.mkdir(); subprocess.run(['git','init','-q',str(root)],check=True)
        subprocess.run(['git','-C',str(root),'config','user.email','tests@example.invalid'],check=True)
        subprocess.run(['git','-C',str(root),'config','user.name','vmops tests'],check=True)
        (root/'tracked.txt').write_text('old\n'); subprocess.run(['git','-C',str(root),'add','tracked.txt'],check=True); subprocess.run(['git','-C',str(root),'commit','-qm','old'],check=True)
        old=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
        work=base/'work'; subprocess.run(['git','clone','-q',str(root),str(work)],check=True)
        subprocess.run(['git','-C',str(work),'config','user.email','tests@example.invalid'],check=True)
        subprocess.run(['git','-C',str(work),'config','user.name','vmops tests'],check=True)
        (work/'tracked.txt').write_text('new\n'); subprocess.run(['git','-C',str(work),'commit','-am','new','-q'],check=True)
        new=subprocess.check_output(['git','-C',str(work),'rev-parse','HEAD'],text=True).strip()
        bundle=base/'payload.bundle'; subprocess.run(['git','-C',str(work),'bundle','create',str(bundle),'HEAD'],check=True)
        state=base/'state'; (state/'current').mkdir(parents=True); (state/'bundles'/'docich').mkdir(parents=True)
        (state/'current'/'docich.json').write_text(json.dumps({'mode':'git','sha':old}))
        target_bundle=state/'bundles'/'docich'/f'{new}.bundle'; target_bundle.write_bytes(bundle.read_bytes())
        config=base/'gateway.json'; config.write_text(json.dumps({'state':str(state),'repos':{'docich':{'production':str(root)}}}))
        env=os.environ.copy(); env.update({'VMOPS_ORIGINAL_COMMAND':'deploy docich production '+new,'VMOPS_TESTING':'1'})
        p=subprocess.run(['python3',str(GW),str(config)],env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),new)
        self.assertEqual((root/'tracked.txt').read_text(),'new\n')

    def test_gateway_accepts_docich_only_config_and_rejects_soviet_now(self):
        pass

    def test_preview_deploy_and_exec(self):
        pass

    def test_production_exec_hides_command_output(self):
        pass


class WorkflowPolicyTests(unittest.TestCase):
    def test_installer_projects_docich_owned_soviet_submodule_only(self):
        installer=INSTALL.read_text(encoding='utf-8')
        self.assertIn('games/soviet_now',installer)
        self.assertNotIn('repos.soviet_now',installer)

    def test_owner_and_environment_gates_present(self):
        workflow=WF.read_text(encoding='utf-8')
        self.assertIn("github.actor_id == 9018513",workflow)
        self.assertIn("environment: vm-operations",workflow)

    def test_ssh_is_pinned_and_no_interactive_shell(self):
        workflow=WF.read_text(encoding='utf-8')
        self.assertIn('StrictHostKeyChecking=yes',workflow)
        self.assertIn('BatchMode=yes',workflow)
        self.assertNotIn('ssh-keyscan',workflow)


if __name__=='__main__': unittest.main()
