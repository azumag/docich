#!/usr/bin/env python3
"""Trusted local entrypoint for bounded, pre-review Soren repairs.

No new SSH operation or main/owner authorization bypass. Both this entrypoint
and CI use vm-operations.lock; the model cannot choose policy or commands.
"""
from __future__ import annotations
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
import gateway as gw

# Initially only leaf audio/overlay implementation is eligible. Enabling a new
# subsystem requires changing this reviewed control plane, not an agent prompt.
ALLOWED_FILES = {'external_game_audio.mjs', 'generate_event_overlay.py',
                 'generate_status_overlay.py', 'generate_show_status_overlay.py'}


def validate_allowed_paths(paths):
    if not isinstance(paths,list) or not paths or len(paths)>16:
        raise ValueError('invalid allowed paths')
    for rel in paths:
        if not isinstance(rel,str): raise ValueError('invalid repair path')
        p=PurePosixPath(rel)
        if p.is_absolute() or str(p)!=rel or '..' in p.parts or any(x.startswith('.') for x in p.parts):
            raise ValueError('unsafe repair path')
        if rel not in ALLOWED_FILES:  # exact, reviewed initial scope
            raise ValueError('repair path outside approved scope')
    if len(set(paths))!=len(paths): raise ValueError('duplicate repair path')
    return paths


def fixed_argv(policy,key):
    argv=policy.get(key)
    if not isinstance(argv,list) or not argv or not all(isinstance(v,str) and v and '\0' not in v for v in argv):
        raise ValueError(f'invalid {key}')
    if not Path(argv[0]).is_absolute(): raise ValueError('command must be absolute')
    return argv


def run_probe(policy, live, expected):
    result=subprocess.run(fixed_argv(policy,'health_command'),cwd=live,
                          env={'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8'},
                          stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          timeout=min(60,int(policy.get('health_timeout',30))))
    if result.returncode!=expected: raise ValueError(f'health probe did not return expected status {expected}')


def run_candidate_test(policy,candidate):
    command=fixed_argv(policy,'test_command')
    if os.environ.get('VMOPS_TESTING')=='1':
        argv=command;cwd=candidate
    else:
        argv=['/usr/local/bin/bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv']
        for directory in ('/usr','/bin','/lib','/lib64','/sbin'):
            if Path(directory).exists(): argv+=['--ro-bind',directory,directory]
        argv+=['--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/work',
               '--ro-bind',str(candidate),'/work','--chdir','/work',
               '--setenv','PATH','/usr/local/bin:/usr/bin:/bin','--setenv','HOME','/tmp','--',*command]
        cwd=Path('/')
    result=subprocess.run(argv,stdin=subprocess.DEVNULL,cwd=cwd,
                          env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8'},
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120)
    if result.returncode: raise ValueError('candidate test failed')


def apply(cfg,policy,report_id,candidate,base,sha):
    if not re.fullmatch('[a-f0-9]{32,64}',report_id): raise ValueError('invalid report id')
    if not gw.SHA_RE.fullmatch(base) or not gw.SHA_RE.fullmatch(sha): raise ValueError('invalid SHA')
    paths=validate_allowed_paths(policy['allowed_paths'])
    projection=policy.get('projection')
    root=Path(cfg['repos']['docich']['production'])
    mappings=cfg['repos']['docich'].get('projections',{})
    if projection!='games/soviet_now' or projection not in mappings: raise ValueError('unsupported projection')
    live=Path(mappings[projection])
    state_path=gw.current_file(cfg,'docich'); state=gw.read_json(state_path)
    if not state or state.get('mode')!='git': raise ValueError('bootstrap required')
    if state.get('deployment_intent'): raise ValueError('deployment recovery required')
    gw._verify_managed_projections(cfg,'docich',state)
    pending=state.get('pending_repairs',[])
    existing=[r for r in pending if r['id']==report_id]
    if existing:
        repair=existing[0]
        if repair.get('status')!='active' or repair.get('candidate_sha')!=sha:
            raise ValueError('incomplete or mismatched repair')
        for rel,meta in repair['files'].items():
            if gw._live_meta(gw._safe_projection_path(live,rel))!=meta['after']:
                raise ValueError('pending repair drift')
        return {'status':'active','id':report_id,'candidate_sha':sha}
    if gw.git(root,'rev-parse','HEAD')!=state['sha'] or not gw.git_clean(root):
        raise ValueError('tracked VM drift detected')
    if gw.submodule_gitlink_at(root,state['sha'],projection)!=base:
        raise ValueError('repair baseline advanced; rebuild candidate')
    if candidate.is_symlink() or gw.git(candidate,'rev-parse','HEAD')!=sha or gw.git(candidate,'status','--porcelain'):
        raise ValueError('candidate must be clean and pinned')
    gw.git(candidate,'merge-base','--is-ancestor',base,sha,capture=False)
    changed=gw._changed_paths(candidate,base,sha)
    if not changed or any(p not in paths for p in changed): raise ValueError('candidate outside allowed paths')
    occupied={p for r in pending for p in r['files']}
    if occupied.intersection(changed): raise ValueError('pending repair overlap')
    if any(r.get('status')!='active' for r in pending): raise ValueError('incomplete repair blocks new apply')
    files={}
    for rel in changed:
        before=gw._tree_entry(candidate,base,rel); after=gw._tree_entry(candidate,sha,rel)
        if before is None or after is None: raise ValueError('repair only supports existing files')
        if before['mode']!=after['mode']: raise ValueError('repair cannot change file mode')
        files[rel]={'before':gw._expected_meta(candidate,before),'after':gw._expected_meta(candidate,after)}
    changes=gw._plan_projection(candidate,live,base,sha)
    run_candidate_test(policy,candidate)
    run_probe(policy,live,1)  # 2/timeout means probe broken, never authorizes repair
    gw._assert_projection_current(changes,'old')
    repair={'policy_id':policy.get('repair_kind'),'id':report_id,'projection':projection,'base_sha':base,'candidate_sha':sha,
            'status':'applying','created_at':int(time.time()),'files':files,
            'verification':{'candidate_test':'passed','before':'reproduced'}}
    journal={**state,'pending_repairs':[*pending,repair]}
    # Durable intent before writing files: a crash blocks future deployment,
    # rather than leaving an apparently successful/unrecorded hotfix.
    gw.write_json(state_path,journal)
    try:
        gw._apply_projection(changes)
        run_probe(policy,live,0)
        for rel,meta in files.items():
            if gw._live_meta(gw._safe_projection_path(live,rel))!=meta['after']:
                raise ValueError('post-apply drift')
        repair={**repair,'status':'active','verification':{**repair['verification'],'after':'passed'}}
        gw.write_json(state_path,{**state,'pending_repairs':[*pending,repair]})
    except Exception:
        # Restore every provably-owned write, even if another path was changed
        # by an unknown writer. Keep intent journal if full recovery is unsafe.
        gw._rollback_projection(changes)
        gw.write_json(state_path,state)
        raise
    return {'status':'active','id':report_id,'candidate_sha':sha}


def attach_pr(cfg,report_id,url):
    if not re.fullmatch(r'https://github\.com/azumag/soviet_now/pull/[1-9][0-9]*',url):
        raise ValueError('invalid repair PR URL')
    path=gw.current_file(cfg,'docich');state=gw.read_json(path)
    for repair in (state or {}).get('pending_repairs',[]):
        if repair['id']==report_id and repair['status']=='active':
            if repair.get('pr_url') not in [None,url]: raise ValueError('PR identity mismatch')
            repair['pr_url']=url;gw.write_json(path,state)
            return {'status':'awaiting_review','id':report_id,'pr_url':url}
    raise ValueError('active repair not found')


def main():
    if len(sys.argv)<4: raise ValueError('usage: apply CONFIG POLICY ID CANDIDATE BASE SHA | attach-pr CONFIG ID URL')
    operation=sys.argv[1];cfg=gw.load_config(Path(sys.argv[2]))
    with open(gw.state_root(cfg)/'vm-operations.lock','a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if operation=='apply' and len(sys.argv)==8:
            policy_path=Path(sys.argv[3]);gw.config_ok(policy_path)
            policy=json.loads(policy_path.read_text())
            if cfg.get('repair_policies',{}).get(policy.get('repair_kind'))!=str(policy_path):
                raise ValueError('policy not registered in gateway')
            result=apply(cfg,policy,sys.argv[4],Path(sys.argv[5]),sys.argv[6],sys.argv[7])
        elif operation=='attach-pr' and len(sys.argv)==5:
            result=attach_pr(cfg,sys.argv[3],sys.argv[4])
        else: raise ValueError('invalid operation')
    print(json.dumps(result))

if __name__=='__main__':
    try: main()
    except Exception:
        print('self repair rejected; inspect trusted diagnostics',file=sys.stderr)
        raise SystemExit(1)
