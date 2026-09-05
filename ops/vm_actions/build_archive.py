#!/usr/bin/env python3
import io, os, subprocess, sys, tarfile
from pathlib import Path, PurePosixPath

MAX_TOTAL=128*1024*1024
MAX_FILE=32*1024*1024
COMMON_BLOCKED={'.github','ops','logs','tmp','data','node_modules','__pycache__'}
REPO_BLOCKED={
  'soviet_now': {'strategies'},
  'docich': {'run-soren-live','games/roms','games/soviet_now','games/hanjuku-sfc-speedrun'},
}

def git(repo,*args):
    return subprocess.check_output(['git','-C',str(repo),'-c','core.hooksPath=/dev/null',*args],stderr=subprocess.DEVNULL)

def excluded(path,repo_name):
    p=PurePosixPath(path)
    parts=p.parts
    if not parts or any(part in {'','.', '..'} or part.startswith('.') for part in parts):
        return True
    if parts[0] in COMMON_BLOCKED:
        return True
    for item in REPO_BLOCKED.get(repo_name,set()):
        item_parts=PurePosixPath(item).parts
        if parts[:len(item_parts)]==item_parts:
            return True
    return False

def main():
    if len(sys.argv)!=5:
        raise SystemExit('usage: build_archive.py CHECKOUT SHA OUTPUT REPO_NAME')
    repo=Path(sys.argv[1]); sha=sys.argv[2]; out=Path(sys.argv[3]); repo_name=sys.argv[4]
    if repo_name not in REPO_BLOCKED:
        raise SystemExit('unsupported repository')
    head=git(repo,'rev-parse','HEAD').decode().strip()
    if head!=sha or len(sha)!=40 or any(c not in '0123456789abcdef' for c in sha):
        raise SystemExit('checkout SHA mismatch')
    entries=git(repo,'ls-tree','-rz','-r','--full-tree',sha).split(b'\0')
    total=count=0
    fd=os.open(out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as raw, tarfile.open(fileobj=raw,mode='w') as tf:
        for entry in entries:
            if not entry: continue
            meta,raw_path=entry.split(b'\t',1)
            mode,kind,blob=meta.decode().split()
            path=raw_path.decode('utf-8','strict')
            if excluded(path,repo_name): continue
            if mode not in {'100644','100755'} or kind!='blob':
                continue
            size=int(git(repo,'cat-file','-s',blob))
            if size>MAX_FILE: raise SystemExit(f'file too large: {path}')
            total+=size; count+=1
            if total>MAX_TOTAL or count>20000: raise SystemExit('archive limit exceeded')
            content=git(repo,'cat-file','blob',blob)
            info=tarfile.TarInfo(path)
            info.size=len(content); info.mode=0o755 if mode=='100755' else 0o644
            info.uid=info.gid=0; info.uname=info.gname=''; info.mtime=0
            tf.addfile(info,io.BytesIO(content))
    if count==0:
        out.unlink(missing_ok=True); raise SystemExit('empty archive')

if __name__=='__main__': main()
