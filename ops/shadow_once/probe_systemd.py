#!/usr/bin/env python3
"""Disposable fixtures only: prove the actual systemd launch/containment path."""
import importlib.util,json,tempfile,uuid
from pathlib import Path
spec=importlib.util.spec_from_file_location('once',Path(__file__).with_name('run.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
root=Path(tempfile.mkdtemp(prefix='shadow-exact-builder-fixture-'));run=root/'run';run.mkdir()
for d in ('strategy_helpers','core','strategy','prompts','tmp/state','game_history'):(root/d).mkdir(parents=True,exist_ok=True)
(root/'strategy.py').write_text('original');(root/'.env').write_text('# synthetic only\n');(root/'tmp/state/improve_daemon.paused').write_text('fixture')
(root/'eloop_lib.sh').write_text('SOREN_ISOLATED_RUNNER_MODE=shadow\n')
(run/'worker.pid').write_text('');(run/'game_history').mkdir();(run/'game_history/a.jsonl').write_text('{}');(root/'game_history/a.jsonl').write_text('original input')
m.ROOT=root;m.BOOTSTRAP=m.BOOTSTRAP.replace('/home/ubuntu/soren',str(root))
doc={'budget_seconds':2,'analysis_seconds':1,'game_num':1,'turns':1,'inputs':[{'path':'game_history/a.jsonl','score':0}]}
(run/'worker.sh').write_text('''if printf changed > strategy.py; then exit 92; fi
[[ "$(cat game_history/a.jsonl)" == '{}' ]] || exit 93
touch readonly-and-input-pass
setsid /bin/sleep 60 &
echo $! > fixture-child.pid
wait
''')
unit='soren-shadow-once-'+uuid.uuid4().hex
result=m.supervise(m.service_command(unit,run,doc),unit,2)
assert (root/'readonly-and-input-pass').exists();assert (root/'strategy.py').read_text()=='original'
assert (run/'worker.pid').read_text().isdigit();assert result['unit']['Result']=='timeout';assert m.unit_empty(result['unit'])
pid=(root/'fixture-child.pid').read_text().strip();p=Path('/proc')/pid/'stat'
assert not p.exists() or p.read_text().split(')')[1].split()[0]=='Z'
(run/'worker.sh').write_text('[[ "$SOREN_ISOLATED_RUNNER_MODE" == shadow ]]\n')
unit='soren-shadow-once-'+uuid.uuid4().hex
normal=m.supervise(m.service_command(unit,run,doc),unit,2)
assert normal['launcher_rc']==0;assert m.unit_empty(normal['unit'])
(run/'worker.sh').write_text('/usr/local/bin/bwrap --unshare-all --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /lib /lib --proc /proc --dev /dev --tmpfs /tmp -- /usr/bin/true > bwrap.out 2> bwrap.err\n')
unit='soren-shadow-once-'+uuid.uuid4().hex
isolated=m.supervise(m.service_command(unit,run,doc),unit,2)
assert isolated['launcher_rc']==0,(isolated,(root/'bwrap.err').read_text())
print(json.dumps({'bubblewrap_inside_unit':True,'timeout_and_detached_cleanup':True,'normal_exit':True,'readonly_and_pinned_input':True,'pid_record':True,'fixture':str(root)}))
