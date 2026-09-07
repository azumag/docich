"""Bound only captured streams; never cap a child's database or Git file writes."""
import os
import selectors
import signal
import subprocess
import tempfile
import time


def run_bounded(argv, cwd=None, env=None, stdin_data=None, timeout=120, limit=1048576):
    with tempfile.TemporaryFile() as inp, selectors.DefaultSelector() as selector:
        if stdin_data is not None:
            if len(stdin_data)>limit: raise ValueError('input too large')
            inp.write(stdin_data);inp.seek(0)
        proc=subprocess.Popen(argv,cwd=cwd,env=env,stdin=inp,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        output=bytearray();counts={};deadline=time.monotonic()+timeout
        for stream in (proc.stdout,proc.stderr):
            os.set_blocking(stream.fileno(),False)
            selector.register(stream,selectors.EVENT_READ);counts[stream]=0
        killed=False
        try:
            while selector.get_map():
                if proc.poll() is not None and not killed:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    killed=True
                if time.monotonic()>=deadline: raise TimeoutError('command timed out')
                for key,_ in selector.select(min(.05,max(0,deadline-time.monotonic()))):
                    data=os.read(key.fileobj.fileno(),65536)
                    if not data:
                        selector.unregister(key.fileobj);continue
                    counts[key.fileobj]+=len(data)
                    if counts[key.fileobj]>limit: raise ValueError('command output too large')
                    if key.fileobj is proc.stdout: output.extend(data)
            proc.wait(timeout=max(.01,deadline-time.monotonic()))
            return proc.returncode,output.decode('utf-8','strict')
        finally:
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            proc.wait()
            proc.stdout.close();proc.stderr.close()
