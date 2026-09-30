import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('retention_collector', ROOT/'ops/vm_actions/collect_diagnostics.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class RetentionHealthTests(unittest.TestCase):
    def collect(self, root):
        with patch.object(collector.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            return collector._collect_opencode_retention(root, 10000)

    def test_projects_only_safe_enums_numbers_and_freshness(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);state=root/'tmp/state';state.mkdir(parents=True)
            (state/'opencode_retention_default.json').write_text(json.dumps(dict(status='deferred', reason='secret text', completed_at=9000, before_bytes=5000, after_bytes=5000, prompt='never expose', page_count=True)))
            item=self.collect(root)['default']
            self.assertEqual(item['status'],'deferred')
            self.assertEqual(item['reason'],'unknown')
            self.assertEqual(item['age_sec'],1000)
            self.assertFalse(item['stale'])
            self.assertNotIn('prompt',item);self.assertNotIn('page_count',item)
            self.assertNotIn('secret',json.dumps(item))

    def test_memory_compaction_status_is_sanitized_and_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);state=root/'tmp/state';state.mkdir(parents=True)
            (state/'opencode_retention_default.json').write_text(json.dumps(dict(status='deferred',reason='insufficient_memory',compact_storage='memory',completed_at=9999)))
            with patch.object(collector.subprocess,'run') as run:
                run.return_value.returncode=0
                result=collector._collect_opencode_retention(root,10000)
            self.assertEqual(result['default']['compact_storage'],'memory')
            self.assertEqual(result['default']['reason'],'insufficient_memory')

    def test_missing_malformed_large_and_symlink_are_not_success(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);state=root/'tmp/state';state.mkdir(parents=True)
            self.assertFalse(self.collect(root)['attempt']['present'])
            p=state/'opencode_db_retention.json'
            for value in ('not json','[]','x'*4097):
                p.write_text(value);self.assertFalse(self.collect(root)['attempt']['readable'])
            p.unlink();target=root/'private';target.write_text('{"status":"completed"}');p.symlink_to(target)
            self.assertFalse(self.collect(root)['attempt']['readable'])

    def test_old_completion_cannot_prove_current_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);state=root/'tmp/state';state.mkdir(parents=True)
            (state/'opencode_retention_default.json').write_text('{"status":"completed","completed_at":1}')
            result=collector._collect_opencode_retention(root,20000)
            self.assertTrue(result['default']['stale'])


class TimerInstallTests(unittest.TestCase):
    def test_installs_fixed_units_and_only_enables_timer(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);config=base/'config';bindir=base/'bin';bindir.mkdir();calls=base/'calls'
            stub=bindir/'systemctl';stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n');stub.chmod(0o755)
            env={**os.environ,'PATH':str(bindir)+':'+os.environ['PATH'],'DOCICH_PROD_ROOT':str(ROOT),'XDG_CONFIG_HOME':str(config),'CALLS':str(calls)}
            cmd=['bash',str(ROOT/'ops/vm_actions/ensure_opencode_retention_timer.sh')]
            p=subprocess.run(cmd,env=env,capture_output=True,text=True);self.assertEqual(p.returncode,0,p.stderr)
            service=(config/'systemd/user/docich-opencode-retention.service').read_text()
            self.assertIn(str(ROOT)+'/ops/vm_actions/opencode_db_retention_now.sh --scheduled',service)
            self.assertNotIn('__DOCICH_ROOT__',service)
            log=calls.read_text();self.assertIn('enable --now docich-opencode-retention.timer',log);self.assertNotIn('restart',log)
            dest=config/'systemd/user/docich-opencode-retention.service';dest.unlink();victim=base/'victim';victim.write_text('preserved');dest.symlink_to(victim)
            p=subprocess.run(cmd,env=env,capture_output=True,text=True);self.assertNotEqual(p.returncode,0);self.assertEqual(victim.read_text(),'preserved')

    def test_fixed_interval_deadline_and_gate_policy(self):
        timer=(ROOT/'scripts/systemd/docich-opencode-retention.timer').read_text()
        self.assertIn('OnUnitInactiveSec=1h',timer)
        helper=(ROOT/'ops/vm_actions/opencode_db_retention_now.sh').read_text()
        self.assertIn('--scheduled) retention_days=1',helper)
        self.assertIn('OPENCODE_ROTATION_GATE_WAIT_SEC=600',helper)
        workflow=(ROOT/'.github/workflows/vm-operations.yml').read_text()
        self.assertLess(workflow.index('Ensure OpenCode retention timer'),workflow.index('Retry gated OpenCode retention'))
