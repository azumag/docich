#!/usr/bin/env python3
"""First bounded policy: event-overlay generator syntax, not arbitrary UX faults."""
import ast
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def valid_html(text):
    return all(s in text for s in ('<html','id="work-indicator"','id="toasts"','</html>'))


def health(root,wait_seconds=15):
    source=root/'generate_event_overlay.py'
    if source.is_symlink() or not source.is_file(): return 2
    try: ast.parse(source.read_text())
    except SyntaxError: return 1
    except Exception: return 2
    output=root/'tmp/state/event_overlay.html'
    deadline=time.monotonic()+wait_seconds
    while True:
        try:
            if (output.is_file() and not output.is_symlink() and output.stat().st_size<2*1024*1024
                and output.stat().st_mtime>=source.stat().st_mtime
                and 0<=time.time()-output.stat().st_mtime<=30 and valid_html(output.read_text())):
                return 0
        except (OSError,UnicodeError): pass
        if time.monotonic()>=deadline: return 2
        time.sleep(.25)


def candidate(root):
    # This mode is invoked only INSIDE the no-network, read-only candidate sandbox.
    source=root/'generate_event_overlay.py'
    ast.parse(source.read_text())
    with tempfile.TemporaryDirectory() as d:
        tmp=Path(d);events=tmp/'events.jsonl';output=tmp/'overlay.html';work=tmp/'work.json'
        events.write_text(json.dumps({'ts':int(time.time()),'category':'worker','title':'Probe','body':'Probe'})+'\n')
        work.write_text('{}')
        result=subprocess.run(['/usr/bin/python3',str(source),str(events),str(output),'10','18',str(work)],
                              cwd=tmp,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        if result.returncode or not output.exists() or not valid_html(output.read_text()): return 1
    return 0

if __name__=='__main__':
    try:
        if sys.argv[1:]==['health']:result=health(Path('/home/ubuntu/soren'))
        elif sys.argv[1:]==['candidate']:result=candidate(Path.cwd())
        else:result=2
    except Exception:result=2
    raise SystemExit(result)
