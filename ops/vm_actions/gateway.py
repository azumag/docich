#!/usr/bin/env python3
from __future__ import annotations
import configparser, fcntl, hashlib, json, os, re, shutil, stat, subprocess, sys, tempfile, uuid
from pathlib import Path, PurePosixPath

SHA_RE=re.compile(r'[0-9a-f]{40}\Z')
MAX_PAYLOAD=128*1024*1024
OPS={'upload','deploy','bootstrap','status','exec'}
TARGETS={'preview','production'}
OWNED_SUBMODULES={
    'games/soviet_now':'https://github.com/azumag/soviet_now.git',
    'games/hanjuku-sfc-speedrun':'https://github.com/azumag/hanjuku-sfc-speedrun.git',
}

def die(msg='VM operation rejected'):
    print(msg,file=sys.stderr); raise SystemExit(1)


def config_ok(path:Path):
    if os.environ.get('VMOPS_TESTING')=='1': return
    st=path.stat()
    if st.st_uid!=0 or st.st_mode & 0o022: raise ValueError('config must be root-owned and not writable')

def git(root:Path,*args:str, capture=True):
    command=['git','-C',str(root),'-c','core.hooksPath=/dev/null',*args]
    if capture:
        return subprocess.check_output(command,stderr=subprocess.DEVNULL,text=True).strip()
    subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=120)
    return ''

def submodule_gitlink_at(root:Path,commit:str,path:str):
    line=git(root,'ls-tree',commit,'--',path)
    if not line: return None
    fields=line.split()
    if len(fields)<3 or fields[0]!='160000' or fields[1]!='commit' or not SHA_RE.fullmatch(fields[2]):
        raise ValueError('owned submodule path is not a gitlink')
    return fields[2]

def submodule_gitlink(root:Path,path:str):
    return submodule_gitlink_at(root,'HEAD',path)

def submodule_url(root:Path,path:str):
    modules=root/'.gitmodules'
    if not modules.is_file(): raise ValueError('missing .gitmodules')
    parser=configparser.ConfigParser(interpolation=None)
    parser.read(modules)
    for section in parser.sections():
        if parser.get(section,'path',fallback='')==path:
            return parser.get(section,'url',fallback='')
    raise ValueError('owned submodule is missing from .gitmodules')

def owned_submodules_match(root:Path)->bool:
    for path,expected_url in OWNED_SUBMODULES.items():
        expected=submodule_gitlink(root,path)
        if expected is None: continue
        if submodule_url(root,path)!=expected_url: return False
        work=root/path
        if not work.is_dir(): return False
        try:
            if git(work,'rev-parse','HEAD')!=expected: return False
            if git(work,'status','--porcelain','--untracked-files=no')!='': return False
        except (subprocess.CalledProcessError,FileNotFoundError):
            return False
    return True

def sync_owned_submodules(root:Path):
    paths=[]
    for path,expected_url in OWNED_SUBMODULES.items():
        if submodule_gitlink(root,path) is None: continue
        if submodule_url(root,path)!=expected_url:
            raise ValueError('owned submodule URL mismatch')
        paths.append(path)
    if not paths: return
    prefix=['git','-C',str(root),'-c','core.hooksPath=/dev/null']
    if os.environ.get('VMOPS_TESTING')=='1':
        prefix += ['-c','protocol.file.allow=always']
    subprocess.run(prefix+['submodule','sync','--',*paths],stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=120)
    subprocess.run(prefix+['submodule','update','--init','--checkout','--',*paths],stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=180)
    if not owned_submodules_match(root): raise ValueError('submodule deployment verification failed')

def git_clean(root:Path)->bool:
    status=git(root,'status','--porcelain','--untracked-files=no','--ignore-submodules=all')
    return status=='' and owned_submodules_match(root)

def load_config(path:Path):
    config_ok(path)
    cfg=json.loads(path.read_text())
    state=Path(cfg['state'])
    if not state.is_absolute(): raise ValueError('state must be absolute')
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    repos=cfg.get('repos',{})
    if set(repos)!={'docich'}: raise ValueError('unexpected repos')
    value=repos['docich']
    root=Path(value['production'])
    if not root.is_absolute() or not root.is_dir() or value.get('mode')!='git':
        raise ValueError('invalid production root/mode')
    if git(root,'rev-parse','--is-inside-work-tree')!='true': raise ValueError('git production root required')
    projections=value.get('projections', {})
    if not isinstance(projections, dict): raise ValueError('projections must be an object')
    for sub_path,destination in projections.items():
        if sub_path not in OWNED_SUBMODULES or not isinstance(destination, str):
            raise ValueError('invalid projection mapping')
        dest=Path(destination)
        if not dest.is_absolute() or not dest.is_dir() or dest.is_symlink():
            raise ValueError('projection destination must be an existing absolute directory')
        if dest.resolve()==root.resolve() or root.resolve() in dest.resolve().parents or dest.resolve() in root.resolve().parents:
            raise ValueError('projection destination must be disjoint from docich worktree')
    return cfg

def parse_command(cfg):
    raw=os.environ.get('SSH_ORIGINAL_COMMAND','')
    parts=raw.split()
    if len(parts)!=4: raise ValueError('invalid forced command')
    op,repo,target,sha=parts
    if op not in OPS or repo not in cfg['repos'] or target not in TARGETS or not SHA_RE.fullmatch(sha):
        raise ValueError('invalid forced command')
    if op=='bootstrap' and target!='production': raise ValueError('bootstrap production only')
    return op,repo,target,sha

def state_root(cfg): return Path(cfg['state'])
def release_dir(cfg,repo,sha): return state_root(cfg)/'releases'/repo/sha
def bundle_file(cfg,repo,sha): return state_root(cfg)/'bundles'/repo/f'{sha}.bundle'
def current_file(cfg,repo): return state_root(cfg)/'current'/f'{repo}.json'

def atomic_write(path:Path,data:bytes,mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.vmops-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data); f.flush(); os.fchmod(f.fileno(),mode); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def write_json(path,obj): atomic_write(path,(json.dumps(obj,sort_keys=True)+'\n').encode())
def read_json(path): return json.loads(path.read_text()) if path.exists() else None


def _safe_projection_path(root:Path, rel:str)->Path:
    p=PurePosixPath(rel)
    if p.is_absolute() or not p.parts or any(part in {'','.','..'} for part in p.parts):
        raise ValueError('unsafe projection path')
    current=root
    if root.is_symlink(): raise ValueError('projection root is a symlink')
    for part in p.parts:
        current=current/part
        if current.exists() and current.is_symlink(): raise ValueError('symlink in projection path')
    return current


def _tree_entry(root:Path, commit:str, rel:str):
    raw=subprocess.check_output(
        ['git','-C',str(root),'-c','core.hooksPath=/dev/null','ls-tree','-z',commit,'--',rel],
        stderr=subprocess.DEVNULL,
    )
    if not raw: return None
    entries=[item for item in raw.split(b'\0') if item]
    if len(entries)!=1: raise ValueError('ambiguous projection tree entry')
    meta,raw_path=entries[0].split(b'\t',1)
    mode,kind,obj=meta.decode('ascii').split()
    path=raw_path.decode('utf-8','strict')
    if path!=rel or kind!='blob' or mode not in {'100644','100755'}:
        raise ValueError('projection supports regular tracked files only')
    return {'mode':0o755 if mode=='100755' else 0o644,'object':obj}


def _blob_bytes(root:Path, obj:str)->bytes:
    data=subprocess.check_output(
        ['git','-C',str(root),'-c','core.hooksPath=/dev/null','cat-file','blob',obj],
        stderr=subprocess.DEVNULL,
    )
    if len(data)>32*1024*1024: raise ValueError('projection file too large')
    return data


def _live_meta(path:Path):
    if not path.exists(): return None
    if path.is_symlink() or not path.is_file(): raise ValueError('projection path is not a regular file')
    data=path.read_bytes()
    return {'sha256':hashlib.sha256(data).hexdigest(),'mode':stat.S_IMODE(path.stat().st_mode)}


def _expected_meta(root:Path, entry):
    if entry is None: return None
    data=_blob_bytes(root,entry['object'])
    return {'sha256':hashlib.sha256(data).hexdigest(),'mode':entry['mode']}


def _changed_paths(root:Path, old_sha:str, new_sha:str)->list[str]:
    raw=subprocess.check_output(
        ['git','-C',str(root),'-c','core.hooksPath=/dev/null','diff','--name-only','-z','--no-renames',old_sha,new_sha,'--'],
        stderr=subprocess.DEVNULL,
    )
    return [item.decode('utf-8','strict') for item in raw.split(b'\0') if item]


def _plan_projection(subrepo:Path, destination:Path, old_sha:str, new_sha:str):
    changes=[]
    for rel in _changed_paths(subrepo,old_sha,new_sha):
        live=_safe_projection_path(destination,rel)
        old_entry=_tree_entry(subrepo,old_sha,rel)
        new_entry=_tree_entry(subrepo,new_sha,rel)
        old_meta=_expected_meta(subrepo,old_entry)
        if _live_meta(live)!=old_meta:
            raise ValueError(f'projection drift detected: {rel}')
        old_data=_blob_bytes(subrepo,old_entry['object']) if old_entry else None
        new_data=_blob_bytes(subrepo,new_entry['object']) if new_entry else None
        changes.append({'path':live,'old_data':old_data,'old_mode':old_entry['mode'] if old_entry else None,
                        'new_data':new_data,'new_mode':new_entry['mode'] if new_entry else None})
    return changes


def _apply_projection(changes):
    for change in changes:
        path=change['path']
        if change['new_data'] is None:
            if path.exists(): path.unlink()
        else:
            atomic_write(path,change['new_data'],change['new_mode'])


def _rollback_projection(changes):
    for change in reversed(changes):
        path=change['path']
        if change['old_data'] is None:
            if path.exists(): path.unlink()
        else:
            atomic_write(path,change['old_data'],change['old_mode'])


def _plan_projections(cfg,repo,root:Path,old_parent:str,new_parent:str):
    plans=[]
    mappings=cfg['repos'][repo].get('projections', {})
    for sub_path,destination in mappings.items():
        old_sub=submodule_gitlink_at(root,old_parent,sub_path)
        new_sub=submodule_gitlink_at(root,new_parent,sub_path)
        if old_sub==new_sub: continue
        if old_sub is None or new_sub is None: raise ValueError('projected submodule must exist in both parent commits')
        subrepo=root/sub_path
        if git(subrepo,'rev-parse','HEAD')!=new_sub: raise ValueError('projected submodule is not at new gitlink')
        plans.append(_plan_projection(subrepo,Path(destination),old_sub,new_sub))
    return plans


def read_payload():
    data=sys.stdin.buffer.read(MAX_PAYLOAD+1)
    if not data or len(data)>MAX_PAYLOAD: raise ValueError('payload size invalid')
    return data


def upload_bundle(cfg,repo,sha,data:bytes):
    root=Path(cfg['repos'][repo]['production'])
    dest=bundle_file(cfg,repo,sha)
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp_name=tempfile.mkstemp(prefix='.incoming-bundle-',dir=dest.parent)
    tmp=Path(tmp_name)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data); f.flush(); os.fchmod(f.fileno(),0o600); os.fsync(f.fileno())
        heads=subprocess.check_output(['git','bundle','list-heads',str(tmp)],stderr=subprocess.DEVNULL,text=True,timeout=30)
        advertised={line.split()[0] for line in heads.splitlines() if line.split()}
        if sha not in advertised: raise ValueError('bundle does not advertise requested SHA')
        subprocess.run(['git','-C',str(root),'bundle','verify',str(tmp)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=60)
        os.replace(tmp,dest)
    finally:
        if tmp.exists(): tmp.unlink()
    return {'status':'uploaded','sha':sha,'kind':'bundle'}

def upload(cfg,repo,target,sha):
    data=read_payload()
    return upload_bundle(cfg,repo,sha,data)


def bootstrap_git(cfg,repo,sha):
    root=Path(cfg['repos'][repo]['production']); state=current_file(cfg,repo)
    if state.exists(): raise ValueError('baseline already recorded')
    if not bundle_file(cfg,repo,sha).is_file(): raise ValueError('bundle missing')
    if not git_clean(root): raise ValueError('tracked VM drift detected')
    head=git(root,'rev-parse','HEAD')
    if not SHA_RE.fullmatch(head): raise ValueError('invalid current HEAD')
    write_json(state,{'mode':'git','sha':head,'previous_head':None})
    return {'status':'bootstrapped','sha':head}

def bootstrap(cfg,repo,sha):
    return bootstrap_git(cfg,repo,sha)



def deploy_preview(cfg,repo,sha):
    bundle=bundle_file(cfg,repo,sha); dest=release_dir(cfg,repo,sha)
    if not bundle.is_file(): raise ValueError('bundle missing')
    if dest.exists():
        if git(dest,'rev-parse','HEAD')!=sha or not git_clean(dest):
            raise ValueError('preview drift detected')
        return {'status':'staged','sha':sha}
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.incoming-preview-',dir=dest.parent))
    checkout=temp/'repo'
    try:
        subprocess.run(['git','clone','--quiet','--no-checkout',str(bundle),str(checkout)],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                       check=True,timeout=120)
        subprocess.run(['git','-C',str(checkout),'-c','core.hooksPath=/dev/null','checkout','--detach','--quiet',sha],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                       check=True,timeout=120)
        sync_owned_submodules(checkout)
        if git(checkout,'rev-parse','HEAD')!=sha or not git_clean(checkout):
            raise ValueError('preview deployment verification failed')
        os.rename(checkout,dest)
    finally:
        if temp.exists(): shutil.rmtree(temp,ignore_errors=True)
    return {'status':'staged','sha':sha}

def deploy_git(cfg,repo,sha):
    root=Path(cfg['repos'][repo]['production']); state_path=current_file(cfg,repo); state=read_json(state_path)
    bundle=bundle_file(cfg,repo,sha)
    if state is None or state.get('mode')!='git': raise ValueError('bootstrap required')
    old=state.get('sha')
    if not SHA_RE.fullmatch(old or '') or git(root,'rev-parse','HEAD')!=old or not git_clean(root):
        raise ValueError('tracked VM drift detected')
    if not bundle.is_file(): raise ValueError('bundle missing')
    projection_plans=[]
    applied=[]
    try:
        subprocess.run(['git','-C',str(root),'-c','core.hooksPath=/dev/null','fetch','--no-tags','--force',str(bundle),'HEAD'],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=120)
        if git(root,'cat-file','-t',sha)!='commit': raise ValueError('requested object is not commit')
        subprocess.run(['git','-C',str(root),'-c','core.hooksPath=/dev/null','reset','--hard',sha],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=120)
        sync_owned_submodules(root)
        projection_plans=_plan_projections(cfg,repo,root,old,sha)
        for changes in projection_plans:
            applied.append(changes)
            _apply_projection(changes)
        if git(root,'rev-parse','HEAD')!=sha or not git_clean(root): raise ValueError('git deployment verification failed')
        write_json(state_path,{'mode':'git','sha':sha,'previous_head':old})
    except Exception:
        for changes in reversed(applied):
            _rollback_projection(changes)
        subprocess.run(['git','-C',str(root),'-c','core.hooksPath=/dev/null','reset','--hard',old],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=120)
        sync_owned_submodules(root)
        raise
    return {'status':'deployed','sha':sha}

def deploy_prod(cfg,repo,sha):
    return deploy_git(cfg,repo,sha)

def sandbox_argv(work:Path):
    if os.environ.get('VMOPS_TESTING')=='1':
        return ['/bin/bash','--noprofile','--norc','-euo','pipefail','-s'], work
    if shutil.which('bwrap') is None: raise ValueError('bubblewrap is required for preview exec')
    args=['bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv']
    for directory in ('/usr','/bin','/lib','/lib64','/sbin'):
        if Path(directory).exists(): args += ['--ro-bind',directory,directory]
    args += ['--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/work','--bind',str(work),'/work','--chdir','/work',
             '--setenv','PATH','/usr/local/bin:/usr/bin:/bin','--setenv','HOME','/tmp','--setenv','LANG','C.UTF-8','--',
             '/bin/bash','--noprofile','--norc','-euo','pipefail','-s']
    return args, Path('/')

def execute(cfg,repo,target,sha):
    command=sys.stdin.buffer.read(16385)
    if not command or len(command)>16384 or b'\0' in command: raise ValueError('invalid command')
    if target=='preview':
        release=release_dir(cfg,repo,sha)
        if not release.is_dir(): raise ValueError('preview not uploaded')
        work=Path(tempfile.mkdtemp(prefix='exec-',dir=state_root(cfg)))
        try:
            shutil.copytree(release,work/'repo',dirs_exist_ok=True,symlinks=True)
            argv,cwd=sandbox_argv(work/'repo')
            p=subprocess.run(argv,input=command,cwd=cwd,env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8'},timeout=900)
            return {'status':'executed','sha':sha,'exit_code':p.returncode}
        finally:
            shutil.rmtree(work,ignore_errors=True)
    cwd=Path(cfg['repos'][repo]['production'])
    logs=state_root(cfg)/'logs'; logs.mkdir(parents=True,exist_ok=True)
    opid=uuid.uuid4().hex; log=logs/f'{opid}.log'; fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as out:
        p=subprocess.run(['/bin/bash','--noprofile','--norc','-euo','pipefail','-s'],input=command,cwd=cwd,stdout=out,stderr=subprocess.STDOUT,
                         env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(cwd.parent),'LANG':'C.UTF-8'},timeout=900)
    return {'status':'executed','sha':sha,'exit_code':p.returncode,'output':'withheld','operation_id':opid}

def status_result(cfg,repo,target,sha):
    if target=='preview':
        release=release_dir(cfg,repo,sha)
        if not release.is_dir(): return {'status':'missing','sha':sha}
        try: ready=git(release,'rev-parse','HEAD')==sha and git_clean(release)
        except (ValueError,subprocess.CalledProcessError,FileNotFoundError): ready=False
        return {'status':'ready' if ready else 'drift','sha':sha}
    cur=read_json(current_file(cfg,repo))
    if cur is None: return {'status':'bootstrap_required','sha':None}
    root=Path(cfg['repos'][repo]['production']); head=git(root,'rev-parse','HEAD')
    clean=git_clean(root); expected=cur.get('sha')
    return {'status':'configured' if clean and head==expected else 'drift','sha':head}

def main():
    if len(sys.argv)!=2: die()
    cfg=load_config(Path(sys.argv[1])); op,repo,target,sha=parse_command(cfg)
    lock=state_root(cfg)/'vm-operations.lock'; lock.parent.mkdir(parents=True,exist_ok=True)
    with open(lock,'a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        if op=='upload': result=upload(cfg,repo,target,sha)
        elif op=='bootstrap': result=bootstrap(cfg,repo,sha)
        elif op=='deploy':
            result=deploy_preview(cfg,repo,sha) if target=='preview' else deploy_prod(cfg,repo,sha)
        elif op=='exec': result=execute(cfg,repo,target,sha)
        else: result=status_result(cfg,repo,target,sha)
    print(json.dumps(result,separators=(',',':')))
    if result.get('exit_code',0): raise SystemExit(result['exit_code'])

if __name__=='__main__':
    try: main()
    except SystemExit: raise
    except Exception: die()
