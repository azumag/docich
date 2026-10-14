#!/usr/bin/env python3
"""Metadata-only, policy-bound repair dispatch; never polls or merges PRs."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ALLOWED_FILES = {'external_game_audio.mjs', 'generate_event_overlay.py',
                 'generate_status_overlay.py', 'generate_show_status_overlay.py'}
MAX_JOBS=1000
MAX_EVENT_SCAN=512
MAX_EVENT_AGE=900

TOKEN = re.compile(r'[a-f0-9]{32,64}\Z')
SHA = re.compile(r'[a-f0-9]{40}\Z')


def validate_event(event):
    if not isinstance(event, dict) or set(event) != {'event_id', 'category', 'time', 'redacted_context_hash', 'source'}:
        raise ValueError('invalid metadata schema')
    if event['category'] != 'stream_bug_report' or not isinstance(event['event_id'], str) or not TOKEN.fullmatch(event['event_id']):
        raise ValueError('invalid event')
    if not isinstance(event['redacted_context_hash'], str) or not re.fullmatch('[a-f0-9]{64}', event['redacted_context_hash']):
        raise ValueError('invalid context hash')
    if type(event['time']) is not int or event['time'] < 0 or not isinstance(event['source'], str) or len(event['source']) > 80:
        raise ValueError('invalid metadata')
    return event


def duplicate(event, jobs, now, cooldown):
    return any(j['event']['event_id'] == event['event_id'] or
               (j['event']['redacted_context_hash'] == event['redacted_context_hash'] and now-j['created'] < cooldown)
               for j in jobs.values())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def trusted(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink(): raise ValueError('untrusted path')
    for item in [path, *path.parents]:
        st = item.stat()
        if st.st_uid != 0 or st.st_mode & 0o022: raise ValueError('policy must be root-owned and not writable by others')
    return path


def load(path, limit=65536):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit: raise ValueError('invalid file')
    return json.loads(path.read_text())


def run(argv, cwd=None, expected=(0,), timeout=120, env=None, stdin_data=None):
    """Bound process output/duration without limiting unrelated Git/SQLite files."""
    import importlib.util
    spec=importlib.util.spec_from_file_location('self_repair_bounded_process',Path(__file__).with_name('bounded_process.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code,output=module.run_bounded(argv,cwd=cwd,env=env,stdin_data=stdin_data,timeout=timeout,limit=1048576)
    if code not in expected: raise RuntimeError('command failed')
    return code,output


def sandbox(argv, candidate, timeout):
    # Fixed commands can read candidate code, not host home, credentials or runtime.
    command = ['/usr/local/bin/bwrap','--die-with-parent','--new-session','--unshare-all',
               '--ro-bind','/usr','/usr','--ro-bind','/bin','/bin']
    for lib in ('/lib','/lib64'):
        if Path(lib).exists(): command += ['--ro-bind',lib,lib]
    command += ['--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/home',
                '--ro-bind',str(candidate),'/candidate','--chdir','/candidate',
                '--clearenv','--setenv','PATH','/usr/bin:/bin','--setenv','HOME','/tmp',
                '--',*argv]
    return run(command, timeout=timeout, env={'PATH':'/usr/bin:/bin'})


def generate(policy, candidate):
    mode=policy.get('generator_mode','sandbox')
    if mode == 'sandbox':
        return sandbox(policy['generator_command'],candidate,policy.get('generator_timeout',120))[1]
    if mode != 'broker': raise ValueError('unknown generator mode')
    files={}
    total=0
    for relative in policy['allowed_paths']:
        path=Path(relative)
        if path.is_absolute() or '..' in path.parts or '.git' in path.parts: raise ValueError('invalid snapshot path')
        source=candidate/path
        if any(p.is_symlink() for p in [source,*source.parents]) or not source.is_file(): raise ValueError('invalid snapshot file')
        total+=source.stat().st_size
        if total>1048576: raise ValueError('snapshot too large')
        files[relative]=source.read_text(encoding='utf-8')
    payload=json.dumps({'repair_kind':policy['repair_kind'],'files':files},ensure_ascii=False).encode('utf-8')
    if len(payload)>1048576: raise ValueError('snapshot too large')
    # The root-owned broker is a trusted adapter, not candidate code. Only this
    # explicit mode can contact the separately configured text-only provider.
    with tempfile.TemporaryDirectory(prefix='vm-repair-broker-') as directory:
        return run(policy['generator_command'],cwd=directory,stdin_data=payload,
                   env={'PATH':'/usr/bin:/bin','HOME':directory},
                   timeout=policy.get('generator_timeout',120))[1]


def replace_files(candidate, replacements, allowed):
    if not isinstance(replacements,dict) or not replacements: raise ValueError('no replacements')
    for relative, value in replacements.items():
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts or '.git' in path.parts or relative not in allowed or not isinstance(value,str):
            raise ValueError('replacement outside policy')
        dest = candidate/path
        if any(p.is_symlink() for p in [dest,*dest.parents]) or not dest.is_file(): raise ValueError('only regular existing files supported')
        if len(value.encode()) > 262144: raise ValueError('replacement too large')
    for relative, value in replacements.items(): (candidate/relative).write_text(value)


def publish(job, path, publisher, attach):
    try:
        url = job.get('pr_url') or publisher(job)
        job['pr_url'] = url
        save(path,job)  # Persist before ledger attachment, so attachment can be retried.
        attach(job['repair_id'],url)
        job['status'] = 'awaiting_review'
        job.pop('last_error',None)
    except Exception:
        job['last_error'] = 'pr_publication_pending'
    save(path,job)


def git(candidate,*args):
    return run(['/usr/bin/git','-c','core.hooksPath=/dev/null',*args], cwd=candidate)[1].strip()


def policy_check(policy):
    paths=policy.get('allowed_paths')
    if not isinstance(paths,list) or not paths or not all(isinstance(x,str) and x in ALLOWED_FILES for x in paths) or len(paths)!=len(set(paths)):
        raise ValueError('repair path outside approved scope')
    if policy.get('generator_mode','sandbox') not in ('sandbox','broker'): raise ValueError('invalid generator mode')
    for field in ('health_command','generator_command','test_command'):
        command = policy[field]
        if not isinstance(command,list) or not command or not all(isinstance(x,str) and '\x00' not in x for x in command) or not command[0].startswith('/'):
            raise ValueError('invalid fixed command')
        trusted(command[0])
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', policy['github_repo']): raise ValueError('invalid repository')
    if not re.fullmatch(r'[a-z0-9_-]{1,48}',policy['repair_kind']): raise ValueError('invalid repair kind')
    if not isinstance(policy['allowed_paths'],list) or not policy['allowed_paths']: raise ValueError('missing allowed paths')
    source = Path(policy['source_repo'])
    if not source.is_absolute() or source.is_symlink(): raise ValueError('invalid source repo')


def publish_pr(policy, job):
    gh = '/usr/bin/gh'
    # Recover after a create succeeded but the response/process was lost.
    result = run([gh,'pr','list','--repo',policy['github_repo'],'--head',job['branch'],
                  '--state','all','--json','url','--limit','2'])[1]
    found = json.loads(result)
    if found: return found[0]['url']
    body = ('VM-first bounded repair. Existing hourly ChatGPT review owns approval and merge.\n\n'
            'Repair kind: '+policy['repair_kind']+'\nEvent: '+job['event']['event_id']+
            '\nCandidate: '+job['candidate_sha']+'\nRepair ledger: '+job['repair_id']+
            '\nEvidence: fixed diagnostic reproduced fault; candidate tests passed; stage health passed.\n'
            'Raw comments and diagnostic output are intentionally excluded.\n')
    return run([gh,'pr','create','--repo',policy['github_repo'],'--base','main','--head',job['branch'],
                '--title','Repair '+policy['repair_kind'],'--body',body])[1].strip()


def process(config, policy_path, policy, job, path):
    stage = [sys.executable,config['stage_helper']]
    gateway_path=config.get('gateway_config','/etc/azumag-vm-ops.json')
    candidate = Path(config['state_dir'])/'candidates'/job['event']['event_id']
    if job['status'] == 'queued':
        if not time.time()-MAX_EVENT_AGE<=job['event']['time']<=time.time()+60:
            job['status']='expired'; save(path,job); return
        # 0 means healthy, 1 means reproduced; any other result is a failure.
        code,_ = run(policy['health_command'],expected=(0,1),timeout=policy.get('health_timeout',30))
        if code == 0:
            job['status']='not_reproduced'; save(path,job); return
        source = Path(policy['source_repo'])
        git(source,'fetch','origin','main')
        gateway=load(trusted(gateway_path))
        deployed=load(Path(gateway['state'])/'current'/'docich.json')
        if deployed.get('mode')!='git' or not SHA.fullmatch(deployed.get('sha','')): raise ValueError('gateway not bootstrapped')
        entry=git(Path(gateway['repos']['docich']['production']),'ls-tree',deployed['sha'],'--',policy['projection']).split()
        if len(entry)!=4 or entry[0]!='160000' or entry[3]!=policy['projection']: raise ValueError('invalid deployed gitlink')
        base=entry[2]
        if not SHA.fullmatch(base): raise ValueError('invalid base')
        candidate.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if candidate.exists(): raise ValueError('candidate already exists; needs inspection')
        git(source,'clone','--no-local','--no-checkout',str(source),str(candidate))
        git(candidate,'remote','set-url','origin','https://github.com/'+policy['github_repo']+'.git')
        branch='codex/self-repair-'+job['event']['event_id']
        git(candidate,'checkout','-b',branch,base)
        output = generate(policy,candidate)
        response = json.loads(output)
        if set(response) != {'replacements'}: raise ValueError('invalid generator response')
        replace_files(candidate,response['replacements'],policy['allowed_paths'])
        sandbox(policy['test_command'],candidate,policy.get('test_timeout',120))
        git(candidate,'add','--',*response['replacements'])
        git(candidate,'-c','user.name=VM Repair','-c','user.email=vm-repair@localhost',
            'commit','-m','Repair '+policy['repair_kind'])
        sha=git(candidate,'rev-parse','HEAD')
        job.update(status='candidate_ready',branch=branch,base_sha=base,candidate_sha=sha)
        save(path,job)
    if job['status'] == 'candidate_ready':
        git(candidate,'push','origin',job['branch'])  # Durable remote commit before touching live.
        job['status']='pushed'; save(path,job)
    if job['status'] == 'pushed':
        result=run([*stage,'apply',gateway_path,str(policy_path),job['event']['event_id'],
                    str(candidate),job['base_sha'],job['candidate_sha']],timeout=300)[1].strip()
        staged=json.loads(result)
        if staged.get('status')!='active' or staged.get('candidate_sha')!=job['candidate_sha'] or not TOKEN.fullmatch(staged.get('id','')): raise ValueError('invalid repair id')
        job.update(status='staged',repair_id=staged['id']); save(path,job)
    if job['status'] == 'staged':
        publish(job,path,lambda j:publish_pr(policy,j),
                lambda repair,url:run([*stage,'attach-pr',gateway_path,repair,url]))


def next_event(events_dir, jobs, now, cooldown=3600):
    if type(cooldown) is not int or not 0<=cooldown<=86400: raise ValueError('invalid cooldown')
    if len(jobs)>=MAX_JOBS: return None,'job_capacity'
    paths=[]
    with os.scandir(events_dir) as entries:
        for entry in entries:
            if len(paths)>=MAX_EVENT_SCAN: return None,'event_scan_capacity'
            paths.append(Path(entry.path))
    candidates=[]
    for path in paths:
        if path.suffix!='.json' or path.stem in jobs: continue
        try:
            event=validate_event(load(path,4096))
            if path.stem!=event['event_id'] or not now-MAX_EVENT_AGE<=event['time']<=now+60: continue
        except (ValueError,OSError): continue
        if duplicate(event,jobs,now,cooldown): continue
        candidates.append(event)
    if not candidates: return None,'idle'
    return max(candidates,key=lambda e:(e['time'],e['event_id'])),'ready'


def main(config_path):
    os.umask(0o077)
    config = load(trusted(config_path))
    if config.get('enabled') is not True: return
    config['_path']=str(config_path)
    trusted(config['stage_helper'])
    policy_path=trusted(config['policy'])
    policy=load(policy_path); policy_check(policy)
    state=Path(config['state_dir'])
    if not state.is_absolute() or state.is_symlink(): raise ValueError('invalid state directory')
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    if state.stat().st_mode & 0o077: raise ValueError('state must be private')
    with (state/'worker.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return
        jobs_dir=state/'jobs'; jobs_dir.mkdir(exist_ok=True,mode=0o700)
        job_paths=[]
        with os.scandir(jobs_dir) as entries:
            for entry in entries:
                if len(job_paths)>=MAX_JOBS:
                    save(state/'dispatch_status.json',{'status':'job_capacity_exceeded'})
                    return
                job_paths.append(Path(entry.path))
        jobs={p.stem:load(p) for p in job_paths if p.suffix=='.json'}
        now=time.time()
        event,status=next_event(Path(config['events_dir']),jobs,now,policy.get('cooldown_seconds',3600))
        save(state/'dispatch_status.json',{'status':status,'time':now})
        if event is not None:
            job={'event':event,'created':now,'status':'queued'}
            jobs[event['event_id']]=job
            save(jobs_dir/(event['event_id']+'.json'),job)
        for ident,job in jobs.items():
            if job['status'] not in ('queued','candidate_ready','pushed','staged'): continue
            try: process(config,policy_path,policy,job,jobs_dir/(ident+'.json'))
            except Exception:
                job['last_error']='repair_step_failed'
                # Failed generation needs inspection; durable push/stage/publication may retry.
                if job['status']=='queued': job['status']='needs_attention'
                save(jobs_dir/(ident+'.json'),job)
            break  # One bounded job per tick; never a PR-review polling loop.

if __name__ == '__main__':
    try:
        if len(sys.argv)!=2: raise ValueError('expected config')
        main(sys.argv[1])
    except Exception:
        print('self-repair dispatch unavailable; inspect trusted configuration',file=sys.stderr)
        sys.exit(1)
