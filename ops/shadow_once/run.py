#!/usr/bin/env python3
"""Owner-operated, bounded shadow cycle. Never a scheduler or arbitrary exec API."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import uuid

ROOT=Path('/home/ubuntu/soren')
PAUSE_OWNER='tmp/state/step5-founding-20260906T201809Z'
SPEC=Path(__file__).resolve().parents[1]/'hotfixes/analysis_contract_20260907.json'
BOOTSTRAP='''printf '%s' "$$" > "${1%/*}/worker.pid" || exit 81
cd /home/ubuntu/soren || exit 81
source ./eloop_lib.sh || exit 81
if [[ "${SOREN_ISOLATED_RUNNER_MODE:-shadow}" != shadow ]]; then exit 81; fi
SOREN_ISOLATED_RUNNER_MODE=shadow
readonly SOREN_ISOLATED_RUNNER_MODE
export SOREN_ISOLATED_RUNNER_MODE
source "$1" "${@:2}"
'''
PROTECTED=('strategy.py','strategy_helpers','core','strategy','prompts','.env','eloop_lib.sh','tmp/state/improve_daemon.paused')


def validate_manifest(doc):
    if not isinstance(doc,dict) or set(doc)!= {'version','budget_seconds','analysis_seconds','game_num','turns','inputs'}:
        raise ValueError('manifest_schema')
    for key,lo,hi in [('version',1,1),('budget_seconds',1,600),('analysis_seconds',1,600),('game_num',0,10**9),('turns',0,10**9)]:
        if type(doc[key]) is not int or not lo<=doc[key]<=hi:raise ValueError('manifest_range')
    if doc['analysis_seconds']>doc['budget_seconds']:raise ValueError('analysis_budget')
    if not isinstance(doc['inputs'],list) or not 1<=len(doc['inputs'])<=64:raise ValueError('input_count')
    names=set()
    for row in doc['inputs']:
        if not isinstance(row,dict) or set(row)!= {'path','sha256','score'}:raise ValueError('input_schema')
        if not isinstance(row['path'],str) or not re.fullmatch(r'game_history/[A-Za-z0-9_-]+\.jsonl',row['path']):raise ValueError('input_path')
        if row['path'] in names:raise ValueError('duplicate_input')
        names.add(row['path'])
        if not isinstance(row['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',row['sha256']):raise ValueError('input_hash')
        if type(row['score']) is not int or not 0<=row['score']<=10**9:raise ValueError('input_score')
    return doc


def safe(root,rel):
    p=root/rel
    if any(x.is_symlink() for x in (p,*p.parents)):raise ValueError('symlink')
    return p


def digest_file(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(65536),b''):h.update(block)
    return h.hexdigest()


def capture(root):
    # Do not read/copy credential contents. .env is protected via mount and stat.
    files={p:digest_file(safe(root,p)) for p in ('strategy.py','core/config.sh')}
    helpers=safe(root,'strategy_helpers')
    for p in helpers.rglob('*'):
        if p.is_symlink():raise ValueError('helper_symlink')
        if p.is_file():files[p.relative_to(root).as_posix()]=digest_file(p)
    pause=safe(root,'tmp/state/improve_daemon.paused').read_text()
    service=subprocess.check_output(['systemctl','show','soren-runtime.service','-p','MainPID','-p','ActiveState'],text=True,timeout=10)
    if 'ActiveState=active' not in service or 'MainPID=0\n' in service:raise ValueError('stream_not_active')
    st=safe(root,'.env').stat()
    return {'files':files,'pause':pause,'service':service,'env_stat':[st.st_ino,st.st_size,st.st_mtime_ns]}


def preflight(root,doc):
    if safe(root,'tmp/state/improve_daemon.paused').read_text().strip()!=PAUSE_OWNER:raise ValueError('pause_owner')
    state=json.loads(safe(root,'tmp/state/improve_state.json').read_text())
    if state.get('status')!='idle' or state.get('pid')!=0 or safe(root,'tmp/improve.lock').exists():raise ValueError('not_idle')
    # Match executable argument positions, not shell command text or this probe.
    result=subprocess.run(['pgrep','-f',r'(^|[ /])eloop_improve(_runtime\.[^ /]+)?\.sh([ ]|$)'],capture_output=True,timeout=10)
    if result.returncode!=1:raise ValueError('worker_present_or_uninspectable')
    spec=json.loads(SPEC.read_text())
    for rel,want in spec['files'].items():
        p=safe(root,rel)
        if digest_file(p)!=want['new'] or p.stat().st_mode&0o777!=want['mode']:raise ValueError('runtime_drift')
    for row in doc['inputs']:
        p=safe(root,row['path'])
        if not p.is_file() or p.stat().st_size>16*1024*1024 or digest_file(p)!=row['sha256']:raise ValueError('input_drift')


def service_command(unit,run,doc):
    if not re.fullmatch(r'soren-shadow-once-[a-f0-9]+',unit):raise ValueError('unit_name')
    properties=['User=ubuntu','Group=ubuntu','Type=exec','KillMode=control-group',
                f'RuntimeMaxSec={doc["budget_seconds"]}','TimeoutStopSec=10','SendSIGKILL=yes',
                'NoNewPrivileges=yes','ProtectControlGroups=yes','RestrictSUIDSGID=yes',
                'StandardOutput=null','StandardError=null','UMask=0077',
                'ReadOnlyPaths='+' '.join(str(ROOT/p) for p in PROTECTED)+' '+str(run),
                'ReadWritePaths='+str(run/'worker.pid')]
    for row in doc['inputs']:
        properties.append('BindReadOnlyPaths='+str(run/row['path'])+':'+str(ROOT/row['path']))
    cmd=['sudo','-n','systemd-run','--quiet','--wait','--expand-environment=no','--unit='+unit,'--working-directory='+str(ROOT)]
    cmd+=['--property='+p for p in properties]
    cmd+=['--setenv=HOME=/home/ubuntu',
          '--setenv=PATH=/snap/opencode/current/bin:/usr/local/bin:/usr/bin:/bin',
          '--setenv=OPENCODE_DISABLE_AUTOUPDATE=1',
          '--setenv=SOREN_SCRIPT_ROOT='+str(ROOT),
          '--setenv=SOREN_IMPROVE_JOB_BUDGET_SEC='+str(doc['budget_seconds']),
          '--setenv=SOREN_IMPROVE_ANALYSIS_BUDGET_SEC='+str(doc['analysis_seconds']),
          '/bin/bash','-c',BOOTSTRAP,'shadow-once',str(run/'worker.sh'),
          ' '.join(r['path'] for r in doc['inputs']),' '.join(str(r['score']) for r in doc['inputs']),
          'unknown',str(doc['game_num']),str(doc['turns']),'normal']
    return cmd


def unit_state(unit):
    raw=subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','-p','Result','-p','MainPID','-p','ControlGroup','-p','ExecMainPID'],text=True,timeout=10)
    return dict(line.split('=',1) for line in raw.splitlines() if '=' in line)


def stop_owned(unit):
    subprocess.run(['sudo','-n','systemctl','stop',unit],check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def unit_empty(state):
    if state.get('ActiveState') not in ('inactive','failed') or state.get('MainPID')!='0':return False
    group=state.get('ControlGroup','')
    if not group:return True
    if not group.startswith('/system.slice/soren-shadow-once-') or '..' in group:return False
    events=Path('/sys/fs/cgroup'+group)/'cgroup.events'
    return not events.exists() or 'populated 0' in events.read_text().splitlines()


def supervise(command,unit,budget):
    try:
        proc=subprocess.run(command,timeout=budget+45,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        rc=proc.returncode
    except BaseException:
        stop_owned(unit)
        raise
    state=unit_state(unit)
    if not unit_empty(state):
        stop_owned(unit);state=unit_state(unit)
        if not unit_empty(state):raise RuntimeError('owned_processes_remain')
    return {'launcher_rc':rc,'unit':state}


def finish_state(root,record,policy):
    """Clear only a verified terminated worker's own progress, never a new job."""
    state_path=safe(root,'tmp/state/improve_state.json')
    raw=state_path.read_bytes();state=json.loads(raw)
    if state.get('status')=='idle' and state.get('pid')==0:return
    pid=record.get('worker_pid','0')
    if not pid.isdecimal() or int(pid)<=0 or state.get('pid')!=int(pid):
        raise RuntimeError('improve_state_owner_changed')
    if not unit_empty(record['unit']):raise RuntimeError('owned_processes_remain')
    if state_path.read_bytes()!=raw:raise RuntimeError('improve_state_changed')
    record['worker_phase']=state.get('phase')
    # Keep model-produced text out of the operator receipt. Runtime retains its
    # detailed diagnostics; this record does not infer candidate acceptance.
    detail=state.get('detail','')
    reasons=('analysis_hold','analysis_contract_invalid','isolated_runner_shadow',
             'isolated_runner_unavailable','rate_limited','deadline_exhausted')
    record['outcome']=next((r for r in reasons if r in detail),'unclassified_no_apply')
    if record['unit'].get('Result')=='timeout':record['outcome']='timeout'
    state.update(status='idle',pid=0,pid_birth_epoch=0,phase='failed_no_apply',
                 detail='shadow_once:'+record['outcome'])
    policy.atomic_write(state_path,json.dumps(state).encode(),0o600)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['check','run']);parser.add_argument('manifest',type=Path)
    args=parser.parse_args()
    if (args.manifest.parent!=ROOT/'tmp/state' or not re.fullmatch(r'shadow-once-input-[A-Za-z0-9_-]+\.json',args.manifest.name)
            or any(p.is_symlink() for p in (args.manifest,*args.manifest.parents)) or args.manifest.stat().st_size>65536):raise ValueError('manifest_path')
    doc=validate_manifest(json.loads(args.manifest.read_text()))
    spec=importlib.util.spec_from_file_location('policy',Path(__file__).resolve().parents[1]/'hotfixes/apply_founding_policy_20260907.py')
    policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
    policy.require_runtime_protocol(ROOT)
    with policy.improvement_quiescence(ROOT):
        preflight(ROOT,doc);before=capture(ROOT)
        if args.action=='check':print('preflight_passed_not_started');return
        run=Path(tempfile.mkdtemp(prefix='shadow-once-',dir=ROOT/'tmp/state'))
        unit='soren-shadow-once-'+uuid.uuid4().hex
        record={'unit_name':unit,'manifest':doc,'before':before,'status':'preparing'}
        record_path=run/'operator-result.json'
        def save():policy.atomic_write(record_path,json.dumps(record,sort_keys=True).encode(),0o600)
        save()
        (run/'worker.pid').write_text('')
        for row in doc['inputs']:
            raw=safe(ROOT,row['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('input_changed')
            dest=run/row['path'];dest.parent.mkdir(exist_ok=True);dest.write_bytes(raw)
        raw=safe(ROOT,'eloop_improve.sh').read_bytes();(run/'worker.sh').write_bytes(raw)
        record['worker_sha256']=hashlib.sha256(raw).hexdigest()
        if record['worker_sha256']!=json.loads(SPEC.read_text())['files']['eloop_improve.sh']['new']:raise ValueError('snapshot_drift')
        preflight(ROOT,doc)
        if capture(ROOT)!=before:raise ValueError('prelaunch_drift')
        def interrupted(signum,frame):raise KeyboardInterrupt
        old={s:signal.signal(s,interrupted) for s in (signal.SIGINT,signal.SIGTERM)}
        try:
            record['status']='running';save()
            record.update(supervise(service_command(unit,run,doc),unit,doc['budget_seconds']))
            record['status']='finished'
        except BaseException:
            record['status']='interrupted_or_failed'
            raise
        finally:
            for s,handler in old.items():signal.signal(s,handler)
            try:
                record['unit']=unit_state(unit)
                if not unit_empty(record['unit']):raise RuntimeError('owned_processes_remain')
                record['worker_pid']=(run/'worker.pid').read_text()
                record['after']=capture(ROOT)
                record['invariants_preserved']=record['after']==before
                if record['invariants_preserved']:finish_state(ROOT,record,policy)
            except Exception:
                record['invariants_preserved']=False
                record['postcheck_error']='invariant_check_failed'
            save()
        if not record['invariants_preserved']:raise RuntimeError('postrun_drift_no_rollback')
        print(str(record_path))
        if record['launcher_rc']:raise SystemExit(1)

if __name__=='__main__':main()
