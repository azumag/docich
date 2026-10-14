#!/usr/bin/env python3
"""Owner-only fixed three-file deployment, paused, locked, backed up."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

spec=importlib.util.spec_from_file_location('policy_installer',Path(__file__).with_name('apply_founding_policy_20260907.py'))
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
ROOT=Path('/home/ubuntu/soren')
SOURCE=Path('/home/ubuntu/docich/games/soviet_now')
SOURCE_SHA='fea026ddfb285eee603acd5f330f286624754c8d'
PATHS=('strategy/improve_command.py','strategy/ai.sh','eloop_improve.sh')
PAUSE_OWNER='tmp/state/step5-founding-20260906T201809Z'

def manifest():
    return json.loads(Path(__file__).with_name('improve_command_20260907.json').read_text())

def preflight(root):
    policy.require_idle(root)
    raw,_=policy.read(policy.safe_path(root,'tmp/state/improve_state.json'))
    if int(json.loads(raw).get('pid') or 0):raise ValueError('nonzero improve pid')
    raw,_=policy.read(policy.safe_path(root,'tmp/state/improve_daemon.paused'))
    if raw is None or raw.decode().strip()!=PAUSE_OWNER:raise ValueError('expected operator pause missing')

def validate_payload(doc,payload):
    if doc.get('source_sha')!=SOURCE_SHA or tuple(doc.get('files',{}))!=PATHS or set(payload)!=set(PATHS):
        raise ValueError('unexpected deployment scope')
    for rel,v in doc['files'].items():
        if policy.digest(payload[rel])!=v['new'] or v['mode']!=(0o644 if rel.endswith('.py') else 0o755):
            raise ValueError('source hash/mode mismatch')
    compile(payload[PATHS[0]].decode(),PATHS[0],'exec')
    for rel in PATHS[1:]:subprocess.run(['bash','-n'],input=payload[rel],check=True,timeout=15)

def main():
    if len(sys.argv)!=1:raise ValueError('no arguments accepted')
    preflight(ROOT);policy.require_runtime_protocol(ROOT)
    doc=manifest()
    prefix=['git','-C',str(SOURCE),'-c','core.hooksPath=/dev/null']
    subprocess.run(prefix+['fetch','--no-tags','https://github.com/azumag/soviet_now.git',SOURCE_SHA],check=True,timeout=120)
    payload={p:subprocess.check_output(prefix+['show',SOURCE_SHA+':'+p],timeout=30) for p in PATHS}
    validate_payload(doc,payload)
    with policy.improvement_quiescence(ROOT):
        preflight(ROOT);policy.require_runtime_protocol(ROOT)
        print(policy.apply(ROOT,doc['files'],payload))

if __name__=='__main__':main()
