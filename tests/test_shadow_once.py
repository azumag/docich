import importlib.util
from pathlib import Path
import unittest
import tempfile
import json
from types import SimpleNamespace
import subprocess
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'ops/shadow_once/run.py'
def load():
 s=importlib.util.spec_from_file_location('shadow_once',SCRIPT);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

class ShadowOnceTests(unittest.TestCase):
 def manifest(self):
  return {'version':1,'budget_seconds':60,'analysis_seconds':30,'game_num':13,'turns':20,'inputs':[{'path':'game_history/a.jsonl','sha256':'a'*64,'score':100}]}
 def test_manifest_accepts_fixed_inputs(self):
  self.assertEqual(load().validate_manifest(self.manifest())['budget_seconds'],60)
  d=self.manifest();d.update(budget_seconds=840,analysis_seconds=420)
  self.assertEqual(load().validate_manifest(d)['analysis_seconds'],420)
 def test_manifest_rejects_commands_traversal_duplicates_and_bad_budgets(self):
  m=load()
  for mutate in (lambda d:d.update(command='bash'),lambda d:d['inputs'][0].update(path='../.env'),lambda d:d['inputs'].append(d['inputs'][0]),lambda d:d.update(budget_seconds=841),lambda d:d.update(analysis_seconds=61),lambda d:d.update(budget_seconds=True)):
   d=self.manifest();mutate(d)
   with self.subTest(d=d),self.assertRaises(ValueError):m.validate_manifest(d)
 def test_unit_enforces_lifecycle_and_immutable_paths(self):
  cmd=load().service_command('soren-shadow-once-abc',Path('/tmp/owned'),self.manifest())
  for value in ('KillMode=control-group','RuntimeMaxSec=60','User=ubuntu','NoNewPrivileges=yes','TimeoutStopSec=10'):
   self.assertIn('--property='+value,cmd)
  self.assertTrue(any(x.startswith('--property=ReadOnlyPaths=') and '/home/ubuntu/soren/strategy.py' in x for x in cmd))
  self.assertNotIn('soren-runtime.service',cmd)
  self.assertIn('--expand-environment=no',cmd)
 def test_unit_uses_direct_opencode_binary_without_snap_privilege_helper(self):
  cmd=load().service_command('soren-shadow-once-abc',Path('/tmp/owned'),self.manifest())
  self.assertIn('--setenv=PATH=/snap/opencode/current/bin:/usr/local/bin:/usr/bin:/bin',cmd)
  self.assertIn('--setenv=OPENCODE_BIN=/snap/opencode/current/bin/opencode',cmd)
  self.assertIn('--setenv=OPENCODE_DISABLE_AUTOUPDATE=1',cmd)
  self.assertIn('--setenv=AI_BACKOFF_DIR=/tmp/owned/runtime/ai_backoff',cmd)
  self.assertIn('--setenv=AI_FAIL_STREAK_DIR=/tmp/owned/runtime/ai_fail_streak',cmd)
  self.assertIn('--setenv=AI_STATS_DIR=/tmp/owned/runtime/ai_stats',cmd)
  self.assertIn('--property=ReadWritePaths=/tmp/owned/worker.pid /tmp/owned/runtime',cmd)
  self.assertIn('--property=NoNewPrivileges=yes',cmd)
 def test_bootstrap_checks_mode_and_never_calls_normal_spawner(self):
  s=load().BOOTSTRAP
  self.assertIn('readonly SOREN_ISOLATED_RUNNER_MODE',s)
  self.assertLess(s.index('!= shadow'),s.index('source "$1"'))
  self.assertNotIn('_start_improvement_job',s)
  self.assertNotIn('soren91_start',s)
class LifecycleTests(unittest.TestCase):
 def test_timeout_stops_only_owned_unit(self):
  m=load()
  with patch.object(m.subprocess,'run',side_effect=subprocess.TimeoutExpired('fixture',1)),patch.object(m,'stop_owned') as stop:
   with self.assertRaises(subprocess.TimeoutExpired):m.supervise(['fixture'],'soren-shadow-once-a',1)
   stop.assert_called_once_with('soren-shadow-once-a')
 def test_surviving_descendants_are_stopped_and_rechecked(self):
  m=load();done={'ActiveState':'inactive','MainPID':'0','ControlGroup':''}
  with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],0)),patch.object(m,'unit_state',side_effect=[{'ActiveState':'active','MainPID':'1'},done]),patch.object(m,'stop_owned') as stop:
   out=m.supervise(['fixture'],'soren-shadow-once-a',1)
   stop.assert_called_once_with('soren-shadow-once-a');self.assertEqual(out['unit'],done)
 def test_survivors_after_stop_are_failure(self):
  m=load()
  with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],0)),patch.object(m,'unit_state',return_value={'ActiveState':'active','MainPID':'1'}),patch.object(m,'stop_owned'):
   with self.assertRaisesRegex(RuntimeError,'owned_processes_remain'):m.supervise(['fixture'],'soren-shadow-once-a',1)
 def test_effective_enforce_refuses_before_worker(self):
  m=load()
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'eloop_lib.sh').write_text('SOREN_ISOLATED_RUNNER_MODE=enforce\n')
   (p/'worker.sh').write_text('touch reached\n')
   script=m.BOOTSTRAP.replace('/home/ubuntu/soren',tmp)
   result=subprocess.run(['bash','-c',script,'fixture',str(p/'worker.sh')],capture_output=True)
   self.assertEqual(result.returncode,81);self.assertFalse((p/'reached').exists())
 def test_worker_cannot_switch_shadow_to_enforce(self):
  m=load()
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'eloop_lib.sh').write_text(':\n')
   (p/'worker.sh').write_text('SOREN_ISOLATED_RUNNER_MODE=enforce\n[[ "$SOREN_ISOLATED_RUNNER_MODE" == shadow ]]\n')
   result=subprocess.run(['bash','-c',m.BOOTSTRAP.replace('/home/ubuntu/soren',tmp),'fixture',str(p/'worker.sh')],capture_output=True)
   self.assertEqual(result.returncode,0,result.stderr)
class StateOwnershipTests(unittest.TestCase):
 def test_only_owned_stopped_worker_state_is_cleared(self):
  m=load()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();p=root/'tmp/state/improve_state.json';p.parent.mkdir(parents=True)
   p.write_text(json.dumps({'status':'running','pid':321,'phase':'done','detail':'failed_no_apply:analysis_hold'}))
   policy=SimpleNamespace(atomic_write=lambda path,raw,mode:path.write_bytes(raw))
   record={'worker_pid':'321','unit':{'MainPID':'0','ActiveState':'inactive','ControlGroup':''}}
   m.finish_state(root,record,policy)
   self.assertEqual(json.loads(p.read_text())['pid'],0);self.assertEqual(record['outcome'],'analysis_hold')
 def test_other_worker_is_never_cleared(self):
  m=load()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();p=root/'tmp/state/improve_state.json';p.parent.mkdir(parents=True)
   raw='{"status":"running","pid":456}';p.write_text(raw)
   with self.assertRaisesRegex(RuntimeError,'owner_changed'):
    m.finish_state(root,{'worker_pid':'321','unit':{'MainPID':'0','ActiveState':'inactive','ControlGroup':''}},None)
   self.assertEqual(p.read_text(),raw)
 def test_live_owned_worker_is_not_cleared(self):
  m=load()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();p=root/'tmp/state/improve_state.json';p.parent.mkdir(parents=True)
   raw='{"status":"running","pid":321}';p.write_text(raw)
   with self.assertRaisesRegex(RuntimeError,'processes_remain'):
    m.finish_state(root,{'worker_pid':'321','unit':{'MainPID':'321','ActiveState':'active'}},None)
   self.assertEqual(p.read_text(),raw)
class PreflightTests(unittest.TestCase):
 def fixture(self,root):
  m=load();d=ShadowOnceTests().manifest()
  (root/'tmp/state').mkdir(parents=True);(root/'game_history').mkdir()
  (root/'tmp/state/improve_daemon.paused').write_text(m.PAUSE_OWNER)
  (root/'tmp/state/improve_state.json').write_text('{"status":"idle","pid":0}')
  (root/'game_history/a.jsonl').write_text('{}')
  d['inputs'][0]['sha256']=m.digest_file(root/'game_history/a.jsonl')
  p=root/'fixed.py';p.write_text('pass')
  spec=root/'spec.json';spec.write_text(json.dumps({'files':{'fixed.py':{'new':m.digest_file(p),'mode':p.stat().st_mode&0o777}}}))
  m.SPEC=spec
  return m,d
 def test_valid_preflight_and_drift_fail_closed(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();m,d=self.fixture(root)
   with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],1)):
    m.preflight(root,d)
    (root/'game_history/a.jsonl').write_text('changed')
    with self.assertRaisesRegex(ValueError,'input_drift'):m.preflight(root,d)
 def test_pause_and_existing_worker_refuse(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();m,d=self.fixture(root)
   with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
    with self.assertRaisesRegex(ValueError,'worker_present'):m.preflight(root,d)
   (root/'tmp/state/improve_daemon.paused').write_text('another-owner')
   with self.assertRaisesRegex(ValueError,'pause_owner'):m.preflight(root,d)
 def test_busy_state_refuses_before_any_process_query(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();m,d=self.fixture(root)
   (root/'tmp/state/improve_state.json').write_text('{"status":"running","pid":123}')
   with patch.object(m.subprocess,'run') as run:
    with self.assertRaisesRegex(ValueError,'not_idle'):m.preflight(root,d)
    run.assert_not_called()
if __name__=='__main__':unittest.main()
