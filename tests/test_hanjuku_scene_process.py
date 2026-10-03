"""Scene jobs cannot outlive their owner or capture another process tree."""
from __future__ import annotations

import json
import errno
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from docich import hanjuku_scene_process as processes


HELPER = r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
from docich.hanjuku_scene_process import enable_subreaper, OwnedSceneProcess, SceneProcessUnavailable
from docich import hanjuku_scene_process as implementation

directory, mode = Path(sys.argv[1]), sys.argv[2]
heartbeat = directory / 'heartbeat'
mask_file = directory / 'child_mask.json'
stop_request = directory / 'request_stop'
child_code = ('import json,os,signal,time\nfrom pathlib import Path\n'
              'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
              'mask=signal.pthread_sigmask(signal.SIG_BLOCK,set())\n'
              'Path(' + repr(str(mask_file)) + ').write_text(json.dumps({"term_blocked":signal.SIGTERM in mask}))\n'
              'p=Path(' + repr(str(heartbeat)) + ')\n'
              'sent=False\n'
              'end=time.monotonic()+6\n'
              'with p.open("ab",buffering=0) as stream:\n'
              ' while time.monotonic()<end:\n'
              '  stream.write(b"x"); time.sleep(.02)\n')
if mode == 'cleanup_stop':
    child_code += ('  if not sent and Path(' + repr(str(stop_request)) + ').exists():\n'
                   '   time.sleep(.15); os.kill(' + str(os.getpid()) + ',signal.SIGTERM); sent=True\n')
enable_subreaper()
if mode == 'exit_status':
    job=OwnedSceneProcess([sys.executable,'-c','raise SystemExit(7)'])
    try:
        result=job.wait(timeout=1)
    finally:
        job.close()
    print(json.dumps({'result':result,'remaining_children':len(job._view.descendants())}))
    raise SystemExit(0)
if mode == 'baseline':
    unrelated = subprocess.Popen([sys.executable,'-c',child_code])
    try:
        limit=time.monotonic()+2
        while not heartbeat.exists() and time.monotonic()<limit: time.sleep(.01)
        assert heartbeat.exists()
        before=heartbeat.stat().st_size
        try:
            OwnedSceneProcess([sys.executable,'-c','raise SystemExit(99)'])
        except SceneProcessUnavailable:
            rejected=True
        else:
            rejected=False
        time.sleep(.1)
        print(json.dumps({'rejected':rejected,'unrelated_continued':heartbeat.stat().st_size>before}))
    finally:
        unrelated.kill(); unrelated.wait()
    raise SystemExit(0)

if mode == 'nested_timeout':
    launch='p=subprocess.Popen(["timeout","--kill-after=10s","10",sys.executable,"-c",'+repr(child_code)+'])'
elif mode in {'new_session', 'leader_exited'}:
    launch='p=subprocess.Popen([sys.executable,"-c",'+repr(child_code)+'],start_new_session=True)'
else:
    launch='p=subprocess.Popen([sys.executable,"-c",'+repr(child_code)+'])'
job_code='import subprocess,sys,time\n'+launch+'\n'
if mode != 'leader_exited':
    job_code+='time.sleep(8)\n'
if mode == 'launch_stop':
    def stop(*args):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    original=implementation.subprocess.Popen
    def interrupted_launch(*args,**kwargs):
        child=original(*args,**kwargs)
        limit=time.monotonic()+2
        while not heartbeat.exists() and time.monotonic()<limit: time.sleep(.01)
        assert heartbeat.exists()
        # The subprocess exists, but OwnedSceneProcess has not received it.
        os.kill(os.getpid(),signal.SIGTERM)
        return child
    implementation.subprocess.Popen=interrupted_launch
    try:
        OwnedSceneProcess([sys.executable,'-c',job_code])
    except KeyboardInterrupt:
        interrupted=True
    else:
        interrupted=False
    implementation.subprocess.Popen=original
    before=heartbeat.stat().st_size
    time.sleep(.15)
    print(json.dumps({'interrupted':interrupted,'stopped':heartbeat.stat().st_size==before,
                      'child_mask':json.loads(mask_file.read_text()),
                      'remaining_children':len(implementation._ProcView().descendants())}))
    raise SystemExit(0)

if mode == 'initial_capture_failure':
    original=implementation._ProcView.descendants
    calls=[0]
    def fail_initial(view):
        calls[0]+=1
        if calls[0]==2:
            limit=time.monotonic()+2
            while not heartbeat.exists() and time.monotonic()<limit: time.sleep(.01)
            assert heartbeat.exists()
            raise SceneProcessUnavailable('fixture initial capture failure')
        return original(view)
    implementation._ProcView.descendants=fail_initial
    try:
        OwnedSceneProcess([sys.executable,'-c',job_code])
    except SceneProcessUnavailable as error:
        rejected=str(error)=='fixture initial capture failure'
    else:
        rejected=False
    implementation._ProcView.descendants=original
    before=heartbeat.stat().st_size
    time.sleep(.15)
    print(json.dumps({'rejected':rejected,'stopped':heartbeat.stat().st_size==before,
                      'remaining_children':len(implementation._ProcView().descendants())}))
    raise SystemExit(0)

job=OwnedSceneProcess([sys.executable,'-c',job_code])
try:
    limit=time.monotonic()+2
    while not heartbeat.exists() and time.monotonic()<limit: time.sleep(.01)
    assert heartbeat.exists(), 'owned fixture did not start'
    timed_out=False
    try:
        result=job.wait(timeout=.1)
    except subprocess.TimeoutExpired:
        timed_out=True
        result=None
    original=job._view.descendants
    if mode == 'capture_failure':
        def failed_capture():
            raise SceneProcessUnavailable('fixture capture failure')
        job._view.descendants=failed_capture
    if mode == 'cleanup_stop':
        def stop(*args):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM,stop)
        stop_request.touch()
    started=time.monotonic()
    cleanup_unavailable=False
    cleanup_interrupted=False
    try:
        job.close()
    except SceneProcessUnavailable:
        cleanup_unavailable=True
    except KeyboardInterrupt:
        cleanup_interrupted=True
    elapsed=time.monotonic()-started
    job._view.descendants=original
    before=heartbeat.stat().st_size
    time.sleep(.15)
    print(json.dumps({'timed_out':timed_out,'result':result,'stopped':heartbeat.stat().st_size==before,
                      'elapsed':elapsed,'remaining_children':len(job._view.descendants()),
                      'cleanup_unavailable':cleanup_unavailable,'cleanup_interrupted':cleanup_interrupted}))
finally:
    job.close()
'''


def helper(tmp_path, mode):
    result = subprocess.run(
        [sys.executable, '-c', HELPER, str(tmp_path), mode],
        env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
        capture_output=True, text=True, timeout=8,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('mode', ['same_group', 'nested_timeout', 'new_session', 'leader_exited'])
def test_dedicated_owner_stops_detached_and_adopted_children(tmp_path, mode):
    result = helper(tmp_path, mode)
    assert result['stopped'] is True
    assert result['remaining_children'] == 0
    assert result['elapsed'] < 3
    assert result['cleanup_unavailable'] is False
    assert result['timed_out'] is (mode != 'leader_exited')
    if mode == 'leader_exited':
        assert result['result'] == 0


def test_existing_child_is_not_adopted_or_signalled(tmp_path):
    result = helper(tmp_path, 'baseline')
    assert result == {'rejected': True, 'unrelated_continued': True}


def test_nonzero_leader_exit_status_is_preserved(tmp_path):
    assert helper(tmp_path, 'exit_status') == {'result': 7, 'remaining_children': 0}


def test_initial_capture_failure_still_cleans_the_launched_tree(tmp_path):
    result = helper(tmp_path, 'initial_capture_failure')
    assert result == {'rejected': True, 'stopped': True, 'remaining_children': 0}


def test_capture_failure_keeps_cleanup_of_bound_children(tmp_path):
    result = helper(tmp_path, 'capture_failure')
    assert result['cleanup_unavailable'] is True
    assert result['stopped'] is True
    assert result['remaining_children'] == 0
    assert result['elapsed'] < 3


def test_stop_during_launch_is_delivered_after_child_cleanup(tmp_path):
    assert helper(tmp_path, 'launch_stop') == {
        'interrupted': True, 'stopped': True, 'remaining_children': 0,
        'child_mask': {'term_blocked': False},
    }


def test_stop_during_cleanup_is_deferred_until_owned_children_stop(tmp_path):
    result = helper(tmp_path, 'cleanup_stop')
    assert result['cleanup_interrupted'] is True
    assert result['cleanup_unavailable'] is False
    assert result['stopped'] is True
    assert result['remaining_children'] == 0
    assert result['elapsed'] < 3


def test_shared_process_cannot_launch_a_scene_job(monkeypatch):
    monkeypatch.setattr(processes, '_ENABLED_OWNER', None)
    monkeypatch.setattr(processes.subprocess, 'Popen', lambda *a, **k: pytest.fail('shared process launched child'))
    with pytest.raises(processes.SceneProcessUnavailable, match='dedicated scene worker required'):
        processes.OwnedSceneProcess(['never-run'])


def test_reused_pid_is_never_signalled(monkeypatch):
    owner = object.__new__(processes.OwnedSceneProcess)
    identity = processes._Identity(proc_pid=101, pid=5, start_ticks=1000)
    owner._owned = {}
    owner._leader_fd = None
    owner._leader_identity = None
    owner._view = type('View', (), {'matches': lambda self, value: False,
                                   'descendants': lambda self: [identity]})()
    closed = []
    monkeypatch.setattr(processes.os, 'pidfd_open', lambda pid: 123)
    monkeypatch.setattr(processes.os, 'close', lambda fd: closed.append(fd))
    monkeypatch.setattr(processes.signal, 'pidfd_send_signal', lambda *a: pytest.fail('reused PID signalled'))
    owner._capture()
    owner._signal(processes.signal.SIGKILL)
    assert owner._owned == {}
    assert closed == [123]


def test_exit_race_does_not_abandon_other_bound_children(monkeypatch):
    owner = object.__new__(processes.OwnedSceneProcess)
    owner._owned = {processes._Identity(101, 5, 1000): 123,
                    processes._Identity(102, 6, 1001): 124}
    owner._leader_fd = None
    owner._view = type('View', (), {'matches': lambda *a: pytest.fail('cleanup reread process identity')})()
    sent = []
    monkeypatch.setattr(processes, '_exited', lambda fd: False)
    def send(fd, sig):
        if fd == 123:
            raise ProcessLookupError
        sent.append((fd, sig))
    monkeypatch.setattr(processes.signal, 'pidfd_send_signal', send)
    _, succeeded = owner._signal(processes.signal.SIGKILL)
    assert succeeded is True
    assert sent == [(124, processes.signal.SIGKILL)]


def test_invalid_ancestor_does_not_adopt_reused_pid_children(tmp_path):
    def write_stat(path, pid, ppid, start):
        fields = ['0'] * 20
        fields[0], fields[1], fields[19] = 'S', str(ppid), str(start)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'{pid} (fixture (name)) ' + ' '.join(fields))
    write_stat(tmp_path / 'self/stat', 100, 1, 10)
    for pid, ppid in [(100, 1), (200, 100), (300, 200), (400, 300)]:
        write_stat(tmp_path / str(pid) / 'stat', pid, ppid, 10 if pid == 100 else pid)
    view = object.__new__(processes._ProcView)
    view.root = tmp_path
    view.owner = processes._Stat(100, 1, 10, 'S')
    def identity(entry):
        if entry.proc_pid == 300:
            raise ProcessLookupError
        return processes._Identity(entry.proc_pid, entry.proc_pid, entry.start_ticks)
    view.identity = identity
    assert [item.pid for item in view.descendants()] == [200]


def test_unreadable_process_is_not_assumed_to_be_unrelated(tmp_path, monkeypatch):
    for pid in (100, 200):
        (tmp_path / str(pid)).mkdir()
    (tmp_path / 'self').mkdir()
    fields = ['0'] * 20
    fields[0], fields[1], fields[19] = 'S', '1', '10'
    (tmp_path / 'self/stat').write_text('100 (fixture) ' + ' '.join(fields))
    view = object.__new__(processes._ProcView)
    view.root = tmp_path
    view.owner = processes._Stat(100, 1, 10, 'S')
    def read(pid):
        if pid == 200:
            raise PermissionError('fixture process stat denied')
        return view.owner
    view._read_stat = read
    with pytest.raises(processes.SceneProcessUnavailable, match='process tree unavailable'):
        view.descendants()


@pytest.mark.parametrize('unsupported', ['signal', 'waitid'])
def test_unusable_pidfd_syscalls_reject_before_subreaper_or_launch(monkeypatch, unsupported):
    monkeypatch.setattr(processes.os, 'pidfd_open', lambda pid: 123)
    monkeypatch.setattr(processes.os, 'close', lambda fd: None)
    monkeypatch.setattr(processes, '_prctl', lambda *a: pytest.fail('unsupported containment was enabled'))
    def fail(*a):
        raise OSError(errno.ENOSYS if unsupported == 'signal' else errno.EINVAL, 'fixture')
    monkeypatch.setattr(processes.signal, 'pidfd_send_signal', fail if unsupported == 'signal' else lambda *a: None)
    monkeypatch.setattr(processes.os, 'waitid', fail)
    with pytest.raises(processes.SceneProcessUnavailable, match='containment unavailable'):
        processes.enable_subreaper()
