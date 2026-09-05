#!/usr/bin/env python3
from __future__ import annotations
import fcntl, hashlib, io, json, os, re, shutil, stat, subprocess, sys, tarfile, tempfile, time, uuid
from pathlib import Path, PurePosixPath

SHA_RE=re.compile(r'[0-9a-f]{40}\Z')
MAX_PAYLOAD=128*1024*1024
MAX_FILE=32*1024*1024
MAX_FILES=20000
OPS={'upload','deploy','bootstrap','status','exec'}
TARGETS={'preview','production'}
MODES={'overlay','git'}

def die(msg='VM operation rejected'):
    print(msg,file=sys.stderr); raise SystemExit(1)

def safe_rel(name:str)->str:
    p=PurePosixPath(name)
    if p.is_absolute() or not p.parts or any(x in {'','.','..'} or x.startswith('.') for x in p.parts):
        raise ValueError('unsafe archive path')
    return p.as_posix()

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

def git_clean(root:Path)->bool:
    status=git(root,'status','--porcelain','--untracked-files=no','--ignore-submodules=all')
    return status==''

def load_config(path:Path):
    config_ok(path)
    cfg=json.loads(path.read_text())
    state=Path(cfg['state'])
    if not state.is_absolute(): raise ValueError('state must be absolute')
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    repos=cfg.get('repos',{})
    if set(repos)!={'soviet_now','docich'}: raise ValueError('unexpected repos')
    expected={'soviet_now':'overlay','docich':'git'}
    for repo,value in repos.items():
        root=Path(value['production'])
        if not root.is_absolute() or not root.is_dir() or value.get('mode')!=expected[repo]:
            raise ValueError('invalid production root/mode')
        if value['mode']=='git':
            if git(root,'rev-parse','--is-inside-work-tree')!='true': raise ValueError('git production root required')
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
def mode_for(cfg,repo): return cfg['repos'][repo]['mode']

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

def prod_path(root:Path,rel:str)->Path:
    rel=safe_rel(rel); cur=root
    for part in PurePosixPath(rel).parts:
        cur=cur/part
        if cur.exists() and cur.is_symlink(): raise ValueError('symlink in production path')
    return cur

def file_meta(path:Path):
    if not path.exists(): return None
    if path.is_symlink() or not path.is_file(): raise ValueError('managed path is not regular file')
    st=path.stat()
    return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mode':stat.S_IMODE(st.st_mode)}

def manifest(root:Path):
    result={}
    for p in sorted(root.rglob('*')):
        if p.is_symlink() or (p.exists() and not (p.is_file() or p.is_dir())): raise ValueError('unsafe release entry')
        if p.is_file():
            rel=p.relative_to(root).as_posix(); result[rel]=file_meta(p)
    if not result: raise ValueError('empty release')
    return result

def verify_prod(root:Path,files:dict):
    for rel,expected in files.items():
        if file_meta(prod_path(root,rel))!=expected: raise ValueError('VM drift detected')

def read_payload():
    data=sys.stdin.buffer.read(MAX_PAYLOAD+1)
    if not data or len(data)>MAX_PAYLOAD: raise ValueError('payload size invalid')
    return data

def upload_archive(cfg,repo,sha,data:bytes):
    dest=release_dir(cfg,repo,sha)
    if dest.exists():
        old=manifest(dest)
        with tempfile.TemporaryDirectory(prefix='vmops-verify-') as td:
            unpack_archive(data,Path(td))
            if manifest(Path(td))!=old: raise ValueError('release SHA collision')
        return {'status':'uploaded','sha':sha,'kind':'archive'}
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.incoming-',dir=dest.parent))
    try:
        unpack_archive(data,temp)
        os.rename(temp,dest)
    finally:
        if temp.exists(): shutil.rmtree(temp)
    return {'status':'uploaded','sha':sha,'kind':'archive'}

def upload_bundle(cfg,repo,sha,data:bytes):
    root=Path(cfg['repos'][repo]['production'])
    dest=bundle_file(cfg,repo,sha)
    if dest.exists():
        if hashlib.sha256(dest.read_bytes()).digest()!=hashlib.sha256(data).digest():
            raise ValueError('bundle SHA collision')
        return {'status':'uploaded','sha':sha,'kind':'bundle'}
    atomic_write(dest,data,0o600)
    try:
        heads=subprocess.check_output(['git','bundle','list-heads',str(dest)],stderr=subprocess.DEVNULL,text=True,timeout=30)
        advertised={line.split()[0] for line in heads.splitlines() if line.split()}
        if sha not in advertised: raise ValueError('bundle does not advertise requested SHA')
        subprocess.run(['git','-C',str(root),'bundle','verify',str(dest)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=60)
    except Exception:
        dest.unlink(missing_ok=True); raise
    return {'status':'uploaded','sha':sha,'kind':'bundle'}

def upload(cfg,repo,target,sha):
    data=read_payload()
    if target=='production' and mode_for(cfg,repo)=='git':
        return upload_bundle(cfg,repo,sha,data)
    return upload_archive(cfg,repo,sha,data)

def unpack_archive(data:bytes,dest:Path):
    count=total=0
    try:
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:') as tf:
            for m in tf.getmembers():
                name=safe_rel(m.name)
                if not m.isfile() or m.islnk() or m.issym(): raise ValueError('archive must contain regular files only')
                if m.size<0 or m.size>MAX_FILE: raise ValueError('file too large')
                count+=1; total+=m.size
                if count>MAX_FILES or total>MAX_PAYLOAD: raise ValueError('archive limits exceeded')
                src=tf.extractfile(m); content=src.read(MAX_FILE+1) if src else b''
                if len(content)!=m.size: raise ValueError('truncated file')
                out=dest/name; out.parent.mkdir(parents=True,exist_ok=True)
                atomic_write(out,content,0o755 if m.mode & 0o111 else 0o644)
    except tarfile.TarError as e: raise ValueError('invalid tar') from e
    if count==0: raise ValueError('empty archive')

def bootstrap_overlay(cfg,repo,sha):
    rel=release_dir(cfg,repo,sha); new=manifest(rel)
    root=Path(cfg['repos'][repo]['production']); state=current_file(cfg,repo)
    if state.exists(): raise ValueError('baseline already recorded')
    baseline={}
    for path in new:
        meta=file_meta(prod_path(root,path))
        if meta is not None: baseline[path]=meta
    write_json(state,{'mode':'overlay','sha':None,'files':baseline,'previous_backup':None})
    return {'status':'bootstrapped','sha':sha,'tracked':len(baseline)}

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
    return bootstrap_git(cfg,repo,sha) if mode_for(cfg,repo)=='git' else bootstrap_overlay(cfg,repo,sha)

def copy_release_to_prod(release:Path,root:Path,new:dict,old:dict):
    for path in sorted(set(old)-set(new),reverse=True):
        p=prod_path(root,path)
        if p.exists(): p.unlink()
    for path,meta in new.items():
        src=release/path; dst=prod_path(root,path)
        if path not in old and dst.exists(): raise ValueError('unmanaged VM file collision')
        atomic_write(dst,src.read_bytes(),meta['mode'])

def restore_backup(root:Path,backup:Path,old:dict,new:dict):
    for path in sorted(set(new)-set(old),reverse=True):
        p=prod_path(root,path)
        if p.exists(): p.unlink()
    for path,meta in old.items():
        src=backup/path; atomic_write(prod_path(root,path),src.read_bytes(),meta['mode'])

def deploy_overlay(cfg,repo,sha):
    release=release_dir(cfg,repo,sha); new=manifest(release)
    root=Path(cfg['repos'][repo]['production']); state_path=current_file(cfg,repo); state=read_json(state_path)
    if state is None or state.get('mode')!='overlay': raise ValueError('bootstrap required')
    old=state['files']; verify_prod(root,old)
    for path in set(new)-set(old):
        if prod_path(root,path).exists(): raise ValueError('unmanaged VM file collision')
    backup_id=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    backup=state_root(cfg)/'backups'/repo/backup_id; backup.mkdir(parents=True,exist_ok=False)
    for path,meta in old.items():
        src=prod_path(root,path); atomic_write(backup/path,src.read_bytes(),meta['mode'])
    write_json(backup/'state.json',state)
    try:
        copy_release_to_prod(release,root,new,old); verify_prod(root,new)
        write_json(state_path,{'mode':'overlay','sha':sha,'files':new,'previous_backup':backup_id})
    except Exception:
        restore_backup(root,backup,old,new); verify_prod(root,old); raise
    return {'status':'deployed','sha':sha,'files':len(new)}

def deploy_git(cfg,repo,sha):
    root=Path(cfg['repos'][repo]['production']); state_path=current_file(cfg,repo); state=read_json(state_path)
    bundle=bundle_file(cfg,repo,sha)
    if state is None or state.get('mode')!='git': raise ValueError('bootstrap required')
    old=state.get('sha')
    if not SHA_RE.fullmatch(old or '') or git(root,'rev-parse','HEAD')!=old or not git_clean(root):
        raise ValueError('tracked VM drift detected')
    if not bundle.is_file(): raise ValueError('bundle missing')
    try:
        subprocess.run(['git','-C',str(root),'-c','core.hooksPath=/dev/null','fetch','--no-tags','--force',str(bundle),'HEAD'],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=120)
        if git(root,'cat-file','-t',sha)!='commit': raise ValueError('requested object is not commit')
        subprocess.run(['git','-C',str(root),'-c','core.hooksPath=/dev/null','reset','--hard',sha],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=120)
        if git(root,'rev-parse','HEAD')!=sha or not git_clean(root): raise ValueError('git deployment verification failed')
        write_json(state_path,{'mode':'git','sha':sha,'previous_head':old})
    except Exception:
        subprocess.run(['git','-C',str(root),'-c','core.hooksPath=/dev/null','reset','--hard',old],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False,timeout=120)
        raise
    return {'status':'deployed','sha':sha}

def deploy_prod(cfg,repo,sha):
    return deploy_git(cfg,repo,sha) if mode_for(cfg,repo)=='git' else deploy_overlay(cfg,repo,sha)

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
            shutil.copytree(release,work/'repo',dirs_exist_ok=True,symlinks=False)
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
    if target=='preview': return {'status':'ready' if release_dir(cfg,repo,sha).is_dir() else 'missing','sha':sha}
    cur=read_json(current_file(cfg,repo)); mode=mode_for(cfg,repo)
    if cur is None: return {'status':'bootstrap_required','sha':None}
    if mode=='git':
        root=Path(cfg['repos'][repo]['production']); head=git(root,'rev-parse','HEAD')
        clean=git_clean(root); expected=cur.get('sha')
        return {'status':'configured' if clean and head==expected else 'drift','sha':head}
    try: verify_prod(Path(cfg['repos'][repo]['production']),cur['files']); status='configured'
    except ValueError: status='drift'
    return {'status':status,'sha':cur.get('sha')}

def main():
    if len(sys.argv)!=2: die()
    cfg=load_config(Path(sys.argv[1])); op,repo,target,sha=parse_command(cfg)
    lock=state_root(cfg)/'vm-operations.lock'; lock.parent.mkdir(parents=True,exist_ok=True)
    with open(lock,'a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        if op=='upload': result=upload(cfg,repo,target,sha)
        elif op=='bootstrap': result=bootstrap(cfg,repo,sha)
        elif op=='deploy':
            if target=='preview':
                if not release_dir(cfg,repo,sha).is_dir(): raise ValueError('preview not uploaded')
                result={'status':'staged','sha':sha}
            else: result=deploy_prod(cfg,repo,sha)
        elif op=='exec': result=execute(cfg,repo,target,sha)
        else: result=status_result(cfg,repo,target,sha)
    print(json.dumps(result,separators=(',',':')))
    if result.get('exit_code',0): raise SystemExit(result['exit_code'])

if __name__=='__main__':
    try: main()
    except SystemExit: raise
    except Exception: die()
