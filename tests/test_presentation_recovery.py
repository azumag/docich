"""Ownership transfer, races and cleanup; these doubles are not native proof."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from docich import presentation_recovery as mod
from ops.vm_actions.tests.test_collect_diagnostics import load_collector


def write(path, value):
    path.write_text(json.dumps(value))


class FakeEffects:
    def __init__(self, root):
        self.root = root
        self.identity = dict(game='hanjuku-hero', runtime_id='g7-abcdef12', generation=7, lease_id='lease')
        self.runtime = root/'run-soren-live/runtimes/g7-abcdef12'
        self.runtime.mkdir(parents=True)
        (root/'config').mkdir()
        (root/'config/docich.soren-live.toml').write_text(
            '[display]\nnumber=99\nviewport_x=0\nviewport_y=90\nviewport_width=960\nviewport_height=540\n')
        write(root/'run-soren-live/game_switch.json', dict(phase='ready', active=self.identity))
        write(self.runtime/'hanjuku_run.json', dict(self.identity, terminal_reason=None))
        self.native = dict(status='presentation_failed', display=':1', window='11', width=299, height=224,
                           groups=[200, 300, 400])
        write(self.runtime/'presentation.json', self.native)
        def proc(pid, executable, parent=100, group=None):
            return dict(pid=pid, executable=executable, parent=parent, group=group or pid,
                        uid=os.getuid(), start=str(pid*100))
        self.processes = {100: proc(100, 'python3', 1), 200: proc(200, 'Xvfb'),
                          300: proc(300, 'dbus-run-session'), 301: proc(301, 'retroarch', 300, 300),
                          500: proc(500, 'ffplay', 1), os.getpid(): proc(os.getpid(), 'python3', 1)}
        self.members = {200: [200], 300: [300, 301], 400: [], 500: [500]}
        self.output = ['22']
        self.geometries = {(':1', '11'): (dict(X=0, Y=0, WIDTH=299, HEIGHT=224), 301),
                           (':99', '22'): (dict(X=239, Y=90, WIDTH=721, HEIGHT=540), 500)}
        self.tmux = SimpleNamespace(window_target_exists=lambda _: False, kill_window_owned=lambda *a: None)
        self.terminated = []
        self.spawned = []
        self.argv_override = None

    def process(self, pid):
        return copy.deepcopy(self.processes.get(pid))

    def group_members(self, group):
        return self.members.get(group, [])

    def argv(self, pid):
        if pid == os.getpid():
            return ['python3','-m','docich.presentation_recovery','guard',self.identity['runtime_id'],'100','10000']
        if self.argv_override is not None:
            return self.argv_override
        return mod.projection_argv(dict(identity=self.identity, native=self.native,
            projection=dict(viewport=[0,90,960,540], content=[239,0,721,540])))

    def owner(self, identity, role, name, *, pid=None):
        assert identity == self.identity
        assert role=='game' and name=='game-g7'
        return self.process(100 if pid is None else pid)

    def window(self, display, window):
        return self.geometries[(display, window)]

    def windows(self, display, title):
        assert display == ':99' and title == 'docich-present-g7-abcdef12'
        return self.output

    def launch_player(self,claim):
        self.processes[501]=dict(pid=501,executable='ffplay',parent=os.getpid(),group=501,
                                uid=os.getuid(),start='50100')
        self.members[501]=[501];self.output.append('23')
        self.geometries[(':99','23')]=(dict(X=239,Y=90,WIDTH=721,HEIGHT=540),501)
        return SimpleNamespace(pid=501,poll=lambda:None if 501 in self.processes else 0,
                               wait=lambda timeout=None:0)

    def wait_displayed(self,claim,child,deadline):
        self.displayed=True
        return '23',dict(matched_fraction=1.0,native_rgb_sha256='a'*64,projection_rgb_sha256='a'*64)

    def spawn(self, claim):
        self.spawned.append(copy.deepcopy(claim))
        child=self.launch_player(claim)
        window,frame=self.wait_displayed(claim,child,0)
        self.terminate(claim)
        guard=self.owner(self.identity,'game','game-g7',pid=os.getpid())
        updated=dict(claim,player=self.process(child.pid),output_window=window,original_player=claim['player'],
                     guard=guard,first_frame=frame)
        ready=dict(claim['native'],status='ready',projection=claim['projection'],
                   recovery=dict(identity=claim['identity'],player=updated['player'],guard=guard))
        write(self.runtime/'presentation.json',ready)
        write(self.runtime/mod.RECEIPT,dict(updated,status='ready'))

    def terminate(self, claim):
        mod.verify_player(claim, self)
        self.terminated.append(claim['player']['pid'])
        self.processes.pop(claim['player']['pid'])
        self.members[claim['player']['group']] = []
        self.output=[wid for wid in self.output if self.geometries[(':99',wid)][1]!=claim['player']['pid']]


@pytest.fixture
def effects(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, 'measured_contain_size', lambda *args: (721,540))
    return FakeEffects(tmp_path)


def test_adopt_keeps_native_groups_game_and_existing_capture(effects):
    before = copy.deepcopy(effects.processes)
    before_bytes=(effects.runtime/'presentation.json').read_bytes()
    assert mod.adopt(effects)['status'] == 'recovered'
    assert len(effects.spawned) == 1 and effects.terminated==[500]
    assert all(effects.processes[p]==before[p] for p in [100,200,300,301])
    assert (effects.runtime/'presentation_before_recovery.json').read_bytes()==before_bytes
    state, _ = mod.read_record(effects.runtime/'presentation.json')
    assert state['groups'] == [200,300,400]  # never pretend the orphan was an original child
    assert state['recovery']['player']['pid'] == 501
    assert mod.adopt(effects)['status'] == 'already_recovered'
    assert len(effects.spawned) == 1


@pytest.mark.parametrize('fault', ['old_player', 'unknown_group', 'duplicate_window', 'wrong_source',
    'wrong_geometry', 'wrong_pid', 'pid_reused', 'foreign_uid', 'foreign_parent', 'terminal', 'foreign_run',
    'not_failed', 'symlink', 'extra_argv', 'foreign_native_window'])
def test_invalid_claim_never_spawns_or_changes_native(effects, fault):
    path = effects.runtime/'presentation.json'
    if fault == 'old_player': effects.members[400] = [400]
    if fault == 'unknown_group': effects.members[500] = [500,501]
    if fault == 'duplicate_window': effects.output.append('23')
    if fault == 'wrong_source': effects.argv_override = effects.argv(500); effects.argv_override[17] = ':2'
    if fault == 'extra_argv': effects.argv_override = effects.argv(500)+['-loop','0']
    if fault == 'wrong_geometry': effects.geometries[(':99','22')][0]['X'] = 0
    if fault == 'wrong_pid': effects.geometries[(':99','22')] = (effects.geometries[(':99','22')][0],501)
    if fault == 'pid_reused': effects.processes[500]['executable'] = 'python3'
    if fault == 'foreign_uid': effects.processes[500]['uid'] = os.getuid()+1
    if fault == 'foreign_parent': effects.processes[200]['parent'] = 999
    if fault == 'foreign_native_window': effects.processes[301]['group'] = 999
    if fault == 'terminal': write(effects.runtime/'hanjuku_run.json', dict(effects.identity,terminal_reason='game_over'))
    if fault == 'foreign_run': write(effects.runtime/'hanjuku_run.json',dict(effects.identity,generation=8))
    if fault == 'not_failed': write(path,dict(effects.native,status='ready'))
    if fault == 'symlink':
        other=path.with_name('foreign.json');path.rename(other);path.symlink_to(other)
    before = path.read_bytes()
    with pytest.raises(mod.RecoveryRefused): mod.adopt(effects)
    assert not effects.spawned and not effects.terminated
    assert path.read_bytes() == before
    assert not (effects.runtime/mod.RECEIPT).exists()


def test_saved_claim_detects_process_or_lease_change(effects):
    claim = mod.prepare(effects)
    effects.processes[500]['start'] = 'new-birth'
    with pytest.raises(mod.RecoveryRefused): mod.verify_live(claim,effects)
    effects.processes[500] = copy.deepcopy(claim['player'])
    write(effects.root/'run-soren-live/game_switch.json',dict(phase='ready',active=dict(effects.identity,lease_id='new')))
    with pytest.raises(mod.RecoveryRefused): mod.verify_live(claim,effects)
    assert not effects.terminated


def test_saved_player_fingerprint_cannot_override_capture_validation(effects):
    claim=mod.prepare(effects);claim['player_argv_sha256']='0'*64
    with pytest.raises(mod.RecoveryRefused):mod.verify_live(claim,effects)
    assert effects.terminated==[]


def test_guard_terminates_only_adopted_player_on_owned_stop(effects, monkeypatch):
    claim = mod.prepare(effects)
    write(effects.runtime/mod.RECEIPT,claim)
    handlers = {}
    monkeypatch.setattr(mod,'LinuxEffects',lambda root: effects)
    monkeypatch.setattr(mod.signal,'signal',lambda sig, handler: handlers.update({sig:handler}))
    def stop_after_ready(_):
        receipt,_=mod.read_record(effects.runtime/mod.RECEIPT)
        assert receipt['status']=='ready'
        handlers[mod.signal.SIGTERM](None,None)
    monkeypatch.setattr(mod.time,'sleep',stop_after_ready)
    mod.supervise(effects.root,effects.identity['runtime_id'],100,'10000')
    assert effects.terminated == [500,501]
    assert all(effects.processes.get(pid) for pid in (100,200,300,301))
    assert mod.read_record(effects.runtime/mod.RECEIPT)[0]['status']=='stopped'
    assert mod.read_record(effects.runtime/'presentation.json')[0] == effects.native


def test_guard_rejects_stale_saved_native_record_without_killing_player(effects, monkeypatch):
    claim=mod.prepare(effects);write(effects.runtime/mod.RECEIPT,claim)
    write(effects.runtime/'presentation.json',dict(effects.native,status='stopped'))
    monkeypatch.setattr(mod,'LinuxEffects',lambda root: effects)
    monkeypatch.setattr(mod.signal,'signal',lambda *args:None)
    monkeypatch.setattr(mod.time,'sleep',lambda _:effects.processes.pop(100,None))
    mod.supervise(effects.root,effects.identity['runtime_id'],100,'10000')
    assert effects.terminated == [500]
    assert mod.read_record(effects.runtime/'presentation.json')[0]['status']=='stopped'


def test_guard_rejects_corrupt_saved_native_payload_even_with_valid_digest(effects, monkeypatch):
    claim=mod.prepare(effects)
    claim['native']=dict(claim['native'],groups=[200,300,900])
    write(effects.runtime/mod.RECEIPT,claim)
    monkeypatch.setattr(mod,'LinuxEffects',lambda root:effects)
    monkeypatch.setattr(mod.signal,'signal',lambda *args:None)
    monkeypatch.setattr(mod.time,'sleep',lambda _:effects.processes.pop(100,None))
    mod.supervise(effects.root,effects.identity['runtime_id'],100,'10000')
    assert effects.terminated==[500]
    assert mod.read_record(effects.runtime/'presentation.json')[0]==effects.native


def test_repeated_operator_rejects_changed_ready_native_source(effects):
    mod.adopt(effects)
    path=effects.runtime/'presentation.json';state,_=mod.read_record(path)
    write(path,dict(state,display=':2'))
    with pytest.raises(mod.RecoveryRefused):mod.adopt(effects)
    assert len(effects.spawned)==1 and effects.terminated==[500]


@pytest.mark.parametrize('generation',[True,False,0,'7',8])
def test_identity_requires_exact_integer_generation(effects,generation):
    with pytest.raises(mod.RecoveryRefused):mod.runtime_identity(dict(effects.identity,generation=generation))


def test_pidfd_reuse_refuses_signal(effects,monkeypatch):
    claim=mod.prepare(effects);signals=[];closed=[]
    backend=mod.LinuxEffects(effects.root)
    backend.process=effects.process;backend.group_members=effects.group_members;backend.argv=effects.argv
    def pin(_): effects.processes[500]['start']='reused';return 99
    monkeypatch.setattr(mod.os,'pidfd_open',pin,raising=False)
    monkeypatch.setattr(mod.signal,'pidfd_send_signal',lambda *args:signals.append(args),raising=False)
    monkeypatch.setattr(mod.os,'close',lambda fd:closed.append(fd))
    with pytest.raises(mod.RecoveryRefused):backend.terminate(claim)
    assert signals==[] and closed==[99]


def test_busy_lifecycle_never_prepares_or_adopts(effects):
    held=mod.GameSwitchLock(effects.root/'run-soren-live').acquire(exclusive=True)
    try:
        with pytest.raises(Exception):mod.adopt(effects)
        assert effects.spawned==[] and effects.terminated==[]
        assert not (effects.runtime/mod.RECEIPT).exists()
    finally:held.release()


@pytest.mark.parametrize('fault',[None,'guard_birth','player_birth','guard_argv','player_argv','lease','presentation'])
def test_diagnostics_are_current_bounded_and_read_only(effects,monkeypatch,fault):
    mod.adopt(effects)
    collector=load_collector()
    claim,_=mod.read_record(effects.runtime/mod.RECEIPT)
    real_read_text=Path.read_text;real_read_bytes=Path.read_bytes
    guard=claim['guard'];player=claim['player']
    def stat_text(path,*args,**kwargs):
        if str(path)==f"/proc/{guard['pid']}/stat":
            start='reused' if fault=='guard_birth' else guard['start']
            return f"{guard['pid']} (python3) S "+' '.join(['0']*18+[start])
        if str(path)==f"/proc/{player['pid']}/stat":
            start='reused' if fault=='player_birth' else player['start']
            return f"{player['pid']} (ffplay) S "+' '.join(['0']*18+[start])
        return real_read_text(path,*args,**kwargs)
    def argv_bytes(path,*args,**kwargs):
        if str(path)==f"/proc/{guard['pid']}/cmdline":
            argv=['python3','-m','docich.presentation_recovery','guard',effects.identity['runtime_id'],'100','10000']
            if fault=='guard_argv':argv[4]='g8-abcdef12'
            return b'\0'.join(x.encode() for x in argv)+b'\0'
        if str(path)==f"/proc/{player['pid']}/cmdline":
            argv=mod.projection_argv(claim)
            if fault=='player_argv':argv+=['-loop','0']
            return b'\0'.join(x.encode() for x in argv)+b'\0'
        return real_read_bytes(path,*args,**kwargs)
    if fault=='lease':
        write(effects.root/'run-soren-live/game_switch.json',dict(phase='ready',active=dict(effects.identity,lease_id='new')))
    if fault=='presentation':
        state,_=mod.read_record(effects.runtime/'presentation.json');write(effects.runtime/'presentation.json',dict(state,status='stopped'))
    claim['DO-NOT-PUBLISH']='secret fixture text';write(effects.runtime/mod.RECEIPT,claim)
    before={p:p.read_bytes() for p in effects.root.rglob('*.json')}
    monkeypatch.setattr(Path,'read_text',stat_text)
    monkeypatch.setattr(Path,'read_bytes',argv_bytes)
    result=collector._collect_hanjuku_projection_recovery(effects.root/'run-soren-live')
    assert result['status']==('healthy' if fault is None else 'unverified')
    assert set(result)=={'status','present','identity_matches','guard_alive','player_alive'}
    assert 'DO-NOT-PUBLISH' not in json.dumps(result) and effects.identity['runtime_id'] not in json.dumps(result)
    assert {p:p.read_bytes() for p in before}==before


def test_projection_manifest_does_not_add_shared_worker_or_queue():
    from ops.vm_actions.runtime_registry import HANJUKU_PROJECTION_RECOVERY as registry,worker_names
    assert registry['module']=='docich.presentation_recovery' and registry['role']=='game'
    assert registry['receipt_file']==mod.RECEIPT and registry['queue_lane'] is None
    assert 'presentation_recovery' not in worker_names()


def test_first_frame_rejects_blank_fade_and_unrelated_pixels():
    source=bytes(range(32))*3
    assert mod.frame_match(source,source)['matched_fraction']==1
    assert mod.frame_match(bytes(len(source)),bytes(len(source))) is None
    assert mod.frame_match(bytes([1])*len(source),bytes([1])*len(source)) is None
    assert mod.frame_match(source,bytes([255])*len(source)) is None
    assert mod.frame_match(source,source[:-1]) is None


def test_failed_first_display_keeps_old_projection_and_guard_until_game_ends(effects,monkeypatch):
    claim=mod.prepare(effects);write(effects.runtime/mod.RECEIPT,claim)
    handlers={};checks=[]
    monkeypatch.setattr(mod,'LinuxEffects',lambda root:effects)
    monkeypatch.setattr(mod.signal,'signal',lambda sig,handler:handlers.update({sig:handler}))
    def no_frame(*args,**kwargs):raise mod.RecoveryRefused('no decoded pixels')
    effects.wait_displayed=no_frame
    def end_after_holding(_):
        checks.append(True)
        assert effects.processes.get(500) is not None  # healthy manual mirror retained
        assert 501 not in effects.processes          # failed new child reclaimed
        assert mod.read_record(effects.runtime/mod.RECEIPT)[0]['status']=='failed'
        effects.processes.pop(100)
    monkeypatch.setattr(mod.time,'sleep',end_after_holding)
    mod.supervise(effects.root,effects.identity['runtime_id'],100,'10000')
    assert checks and effects.terminated==[501,500]
    assert mod.read_record(effects.runtime/'presentation.json')[0]==effects.native


def test_guard_is_split_into_existing_game_window_with_owned_role(monkeypatch,tmp_path):
    backend=mod.LinuxEffects(tmp_path);captured=[]
    backend.owner=lambda *args,**kwargs:dict(pid=100,start='10000')
    backend.tmux._checked=lambda args,operation:(captured.append(args) or SimpleNamespace(stdout='%9\n'))
    claim=dict(identity=dict(game='hanjuku-hero',runtime_id='g7-abcdef12',generation=7,lease_id='lease'),
               game_owner=dict(pid=100,start='10000'))
    backend.spawn(claim)
    args=captured[0]
    assert args[0]=='split-window' and args[args.index('-t')+1]=='docich:game-g7'
    assert 'DOCICH_TMUX_ROLE=game' in args
    assert 'DOCICH_TMUX_RUNTIME_ID=g7-abcdef12' in args
    assert 'guard g7-abcdef12 100 10000' in args[-1]


@pytest.mark.skipif(__import__('sys').platform!='linux',reason='Linux physical process tree')
def test_legacy_tree_cleanup_reaps_child_mirror_and_preserves_shared_process(tmp_path):
    import subprocess,sys,time
    from docich.process_tree import terminate_process_tree,is_running
    child_file=tmp_path/'child.pid'
    source="import subprocess,time,pathlib; p=subprocess.Popen(['sleep','60'],start_new_session=True); pathlib.Path(%r).write_text(str(p.pid)); time.sleep(60)"%str(child_file)
    guardian=subprocess.Popen([sys.executable,'-c',source],start_new_session=True)
    game=subprocess.Popen(['sleep','60'],start_new_session=True)
    shared=subprocess.Popen(['sleep','60'],start_new_session=True)
    owned=[guardian.pid,game.pid];child=None
    try:
        deadline=time.monotonic()+5
        while not child_file.exists() and time.monotonic()<deadline:time.sleep(.02)
        assert child_file.exists();child=int(child_file.read_text());owned.append(child)
        # This is the same existing physical-tree function used by old tmux
        # cleanup. No newly hotloaded adapter method is needed.
        result=terminate_process_tree([game.pid,guardian.pid])
        assert not result.remaining and not is_running(child)
        assert shared.poll() is None
        guardian.wait(timeout=3);game.wait(timeout=3)
    finally:
        terminate_process_tree(owned+[shared.pid])
        for process in (guardian,game,shared):process.wait(timeout=3)
