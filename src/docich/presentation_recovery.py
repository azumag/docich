"""Adopt one verified Hanjuku mirror without restarting its native game.

The old presenter keeps owning Xvfb/RetroArch. A runtime-owned game pane
supervises a replacement child ffplay, and publishes its separate ownership
receipt. No process is inferred to be ours from its window title alone.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import tomllib

from .game_switch import GameSwitchLock, atomic_write_json
from .presentation import contain_filter, measured_contain_size
from .tmux import SESSION, Tmux, TmuxOwnership, ownership_environment

ROOT = Path('/home/ubuntu/docich')
RECEIPT = 'presentation_recovery.json'
MAX_RECORD = 262144


class RecoveryRefused(RuntimeError):
    pass


def read_record(path, *, include_bytes=False):
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    file = None
    try:
        parts = Path(path).absolute().parts[1:]
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        file = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(file)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_RECORD:
            raise RecoveryRefused('record unavailable')
        raw = os.read(file, MAX_RECORD+1)
        if len(raw) > MAX_RECORD:
            raise RecoveryRefused('record grew')
    except OSError as exc:
        raise RecoveryRefused('record unavailable') from exc
    finally:
        if file is not None:
            os.close(file)
        os.close(directory)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise RecoveryRefused('record encoding') from exc
    if not isinstance(value, dict):
        raise RecoveryRefused('record type')
    digest = hashlib.sha256(raw).hexdigest()
    return (value, digest, raw) if include_bytes else (value, digest)


def runtime_identity(active):
    if not isinstance(active, dict) or active.get('game') != 'hanjuku-hero':
        raise RecoveryRefused('inactive game')
    rid, generation, lease = (active.get(k) for k in ('runtime_id', 'generation', 'lease_id'))
    if (not isinstance(rid, str) or not re.fullmatch(r'g[1-9][0-9]*-[a-f0-9]{8}', rid)
            or type(generation) is not int or generation != int(rid.split('-')[0][1:])
            or not isinstance(lease, str) or not 1 <= len(lease) <= 128):
        raise RecoveryRefused('runtime identity')
    return dict(game='hanjuku-hero', runtime_id=rid, generation=generation, lease_id=lease)


def guard_name(generation):
    return f'projection-g{generation}'


def projection_filter(claim):
    projection = claim['projection']
    vx, vy, vw, vh = projection['viewport']
    cx, _, cw, _ = projection['content']
    video_filter = contain_filter(vw, vh, nearest=True, align='right')
    video_filter += f',crop={cw}:{vh}:{cx}:0:exact=1'
    return video_filter


def projection_argv(claim):
    state, projection = claim['native'], claim['projection']
    vx, vy, vw, vh = projection['viewport']
    cx, _, cw, _ = projection['content']
    return ['ffplay', '-loglevel', 'warning', '-nostats', '-an', '-sn',
            '-f', 'x11grab', '-framerate', '15', '-draw_mouse', '0',
            '-window_id', state['window'], '-video_size', f"{state['width']}x{state['height']}",
            '-i', state['display'], '-vf', projection_filter(claim), '-noborder',
            '-window_title', f"docich-present-{claim['identity']['runtime_id']}",
            '-left', str(vx+cx), '-top', str(vy), '-x', str(cw), '-y', str(vh)]


class LinuxEffects:
    """Fixed X11/process probes; no environment, output destination or secrets."""
    def __init__(self, root=ROOT):
        self.root = root
        self.tmux = Tmux()

    def process(self, pid):
        if type(pid) is not int or pid < 2:
            raise RecoveryRefused('pid type')
        try:
            base = Path('/proc') / str(pid)
            raw = (base/'stat').read_text()
            fields = raw[raw.rfind(')')+2:].split()
            if fields[0] == 'Z':
                return None
            uid = int(re.search(r'^Uid:\s+(\d+)', (base/'status').read_text(), re.M)[1])
            return dict(pid=pid, start=fields[19], parent=int(fields[1]),
                        group=int(fields[2]), uid=uid,
                        executable=(base/'exe').resolve().name)
        except FileNotFoundError:
            return None

    def group_members(self, group):
        found = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                raw = (entry/'stat').read_text()
            except FileNotFoundError:
                continue
            fields = raw[raw.rfind(')')+2:].split()
            if fields[0] != 'Z' and int(fields[2]) == group:
                found.append(int(entry.name))
        return sorted(found)

    def argv(self, pid):
        raw = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
        if len(raw) > 8192:
            raise RecoveryRefused('argv size')
        return [word.decode() for word in raw.split(b'\0') if word]

    def x11(self, display, command):
        return subprocess.run(command, env=dict(os.environ, DISPLAY=display),
                              capture_output=True, text=True, timeout=3)

    def windows(self, display, title):
        r = self.x11(display, ['xdotool', 'search', '--onlyvisible', '--name', '^'+title+'$'])
        if r.returncode not in (0, 1):
            raise RecoveryRefused('window search failed')
        return r.stdout.split()

    def window(self, display, window):
        r = self.x11(display, ['xdotool', 'getwindowgeometry', '--shell', window])
        if r.returncode:
            raise RecoveryRefused('window geometry')
        geometry = {k: int(v) for k, v in re.findall(r'^(X|Y|WIDTH|HEIGHT)=(\d+)$', r.stdout, re.M)}
        r = self.x11(display, ['xprop', '-id', window, '_NET_WM_PID'])
        match = re.fullmatch(r'_NET_WM_PID\(CARDINAL\) = (\d+)\s*', r.stdout)
        if r.returncode or not match:
            raise RecoveryRefused('window pid')
        return geometry, int(match[1])

    def owner(self, identity, role, name, *, pid=None):
        expected = TmuxOwnership(identity['runtime_id'], identity['generation'], role)
        target = f'{SESSION}:{name}'
        if self.tmux.read_window_ownership(target) != expected:
            raise RecoveryRefused('tmux ownership')
        states = self.tmux.pane_states_checked(target)
        selected = [row for row in states if pid is None or row.pid == pid]
        if len(selected) != 1 or selected[0].dead:
            raise RecoveryRefused('tmux pane')
        p = self.process(selected[0].pid)
        if not p or p['uid'] != os.getuid():
            raise RecoveryRefused('owner process')
        return p

    def spawn(self, claim):
        i = claim['identity']
        target = f"{SESSION}:game-g{i['generation']}"
        # A second pane in the existing game window is part of the old
        # coordinator's physical descendant sweep, even without code hotload.
        self.owner(i, 'game', f"game-g{i['generation']}", pid=claim['game_owner']['pid'])
        command = [sys.executable, '-m', 'docich.presentation_recovery', 'guard', i['runtime_id'],
                   str(claim['game_owner']['pid']), claim['game_owner']['start']]
        import shlex
        args = ['split-window', '-d', '-P', '-F', '#{pane_id}', '-t', target, '-c', str(self.root)]
        env = {**ownership_environment(TmuxOwnership(i['runtime_id'], i['generation'], 'game')),
               'PYTHONPATH': str(self.root/'src')}
        for key, value in env.items():
            args += ['-e', f'{key}={value}']
        args.append('exec '+shlex.join(command))
        result = self.tmux._checked(args, 'projection guard pane creation')
        if not re.fullmatch(r'%[0-9]+', result.stdout.strip()):
            raise RecoveryRefused('guard pane response')

    def launch_player(self, claim):
        from .presentation import SDL_SOFTWARE_RENDER_ENV
        return subprocess.Popen(projection_argv(claim), start_new_session=True,
            env=dict(os.environ, DISPLAY=claim['output_display'], **SDL_SOFTWARE_RENDER_ENV),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def capture_rgb(self, claim, *, native=False, window=None):
        state = claim['native']; projection = claim['projection']
        cw, vh = projection['content'][2], projection['viewport'][3]
        display = state['display'] if native else claim['output_display']
        wid = state['window'] if native else window
        width, height = (state['width'], state['height']) if native else (cw, vh)
        command = ['ffmpeg','-v','error','-threads','1','-f','x11grab','-window_id',wid,
                   '-video_size',f'{width}x{height}','-i',display,'-frames:v','1']
        if native:
            command += ['-vf', projection_filter(claim)]
        command += ['-pix_fmt','rgb24','-f','rawvideo','pipe:1']
        result = subprocess.run(command, capture_output=True, timeout=3)
        if result.returncode or len(result.stdout) != cw*vh*3:
            raise RecoveryRefused('projection frame unavailable')
        return result.stdout

    def wait_displayed(self, claim, child, *, deadline):
        while time.monotonic() < deadline and child.poll() is None:
            verify_source(claim, self)
            title = f"docich-present-{claim['identity']['runtime_id']}"
            windows = self.windows(claim['output_display'], title)
            found=[]
            for wid in windows:
                geometry,pid=self.window(claim['output_display'],wid)
                if pid==child.pid:found.append((wid,geometry))
                elif wid!=claim['output_window'] or pid!=claim['player']['pid']:
                    raise RecoveryRefused('foreign projection during replacement')
            if len(found)==1:
                wid,geometry=found[0]
                vx,vy,_,vh=claim['projection']['viewport'];cx,_,cw,_=claim['projection']['content']
                if geometry!=dict(X=vx+cx,Y=vy,WIDTH=cw,HEIGHT=vh):
                    raise RecoveryRefused('replacement geometry')
                # Capture the raised replacement itself and compare to an
                # independent native capture through the exact same filter.
                if self.x11(claim['output_display'],['xdotool','windowraise',wid]).returncode:
                    raise RecoveryRefused('replacement raise failed')
                source=self.capture_rgb(claim,native=True)
                output=self.capture_rgb(claim,window=wid)
                match=frame_match(source,output)
                if match is not None:
                    return wid,match
            time.sleep(.05)
        raise RecoveryRefused('replacement first frame not verified')

    def terminate(self, claim):
        """Pin one process with a pidfd; never signal a reused PID/group."""
        p = claim['player']
        if self.process(p['pid']) is None:
            if self.group_members(p['group']):
                raise RecoveryRefused('orphan group')
            return
        try:
            fd = os.pidfd_open(p['pid'])
        except ProcessLookupError:
            if self.process(p['pid']) is None and not self.group_members(p['group']):
                return
            raise RecoveryRefused('player changed while pinning')
        try:
            verify_player(claim, self)
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            end = time.monotonic() + 3
            while self.process(p['pid']) and time.monotonic() < end:
                time.sleep(.05)
            if self.process(p['pid']):
                verify_player(claim, self)
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            end = time.monotonic() + 2
            while self.group_members(p['group']) and time.monotonic() < end:
                time.sleep(.05)
            if self.group_members(p['group']):
                raise RecoveryRefused('group shutdown unconfirmed')
        finally:
            os.close(fd)


def verify_player(claim, effects):
    p = claim['player']
    if (effects.process(p['pid']) != p or p['uid'] != os.getuid()
            or p['group'] != p['pid'] or p['executable'] != 'ffplay'
            or effects.group_members(p['group']) != [p['pid']]):
        raise RecoveryRefused('player identity')
    argv = effects.argv(p['pid'])
    expected = projection_argv(claim)
    if not argv or Path(argv[0]).name != expected[0] or argv[1:] != expected[1:]:
        raise RecoveryRefused('player capture')
    if claim.get('player_argv_sha256') != hashlib.sha256(json.dumps(expected).encode()).hexdigest():
        raise RecoveryRefused('saved player capture fingerprint')


def verify_guard(claim, guard, effects):
    if (effects.process(guard['pid']) != guard or guard['uid'] != os.getuid()
            or effects.argv(guard['pid'])[1:] != ['-m', 'docich.presentation_recovery',
                                                'guard', claim['identity']['runtime_id'],
                                                str(claim['game_owner']['pid']), claim['game_owner']['start']]):
        raise RecoveryRefused('guard command changed')


def verify_source(claim, effects):
    runtime_identity(claim['identity'])
    switch,_=read_record(effects.root/'run-soren-live/game_switch.json')
    if runtime_identity(switch.get('active'))!=claim['identity']:
        raise RecoveryRefused('runtime changed')
    owner=effects.owner(claim['identity'],'game',f"game-g{claim['identity']['generation']}",
                        pid=claim['game_owner']['pid'])
    if owner!=claim['game_owner']:raise RecoveryRefused('game process changed')
    for source in claim['sources']:
        if effects.process(source['pid'])!=source or source['parent']!=owner['pid']:
            raise RecoveryRefused('native source changed')
    geometry,pid=effects.window(claim['native']['display'],claim['native']['window'])
    source=effects.process(pid)
    if (geometry.get('WIDTH')!=claim['native']['width'] or geometry.get('HEIGHT')!=claim['native']['height']
            or not source or source['group']!=claim['sources'][1]['pid'] or source['uid']!=os.getuid()):
        raise RecoveryRefused('native window changed')


def verify_live(claim,effects):
    verify_source(claim,effects)
    verify_player(claim,effects)
    p=claim['projection'];vx,vy,_,vh=p['viewport'];cx,_,cw,_=p['content']
    windows=effects.windows(claim['output_display'],f"docich-present-{claim['identity']['runtime_id']}")
    if windows!=[claim['output_window']]:raise RecoveryRefused('duplicate projection')
    geometry,pid=effects.window(claim['output_display'],claim['output_window'])
    if geometry!=dict(X=vx+cx,Y=vy,WIDTH=cw,HEIGHT=vh) or pid!=claim['player']['pid']:
        raise RecoveryRefused('projection window changed')


def frame_match(source,output):
    # Not an input/decision receipt. Pixel tolerance accounts for live animation
    # between the two captures; blank/fade frames cannot attest first display.
    if not source or len(source)!=len(output) or len(source)%3:
        return None
    if sum(v>16 for v in source)<len(source)*.05 or len(set(source))<8:
        return None
    fraction=sum(abs(a-b)<=3 for a,b in zip(source,output))/len(source)
    if fraction<.99:return None
    return dict(native_rgb_sha256=hashlib.sha256(source).hexdigest(),
                projection_rgb_sha256=hashlib.sha256(output).hexdigest(),matched_fraction=fraction)


def prepare(effects):
    path = effects.root/'config/docich.soren-live.toml'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RECORD:
        raise RecoveryRefused('layout unavailable')
    display_config = tomllib.loads(path.read_text()).get('display', {})
    viewport = [display_config.get(k) for k in ('viewport_x', 'viewport_y', 'viewport_width', 'viewport_height')]
    number = display_config.get('number')
    if (type(number) is not int or not 0 <= number <= 999
            or any(type(v) is not int for v in viewport)
            or min(viewport[:2]) < 0 or min(viewport[2:]) <= 0):
        raise RecoveryRefused('layout type')
    output_display = f':{number}'
    state_dir = effects.root/'run-soren-live'
    switch, _ = read_record(state_dir/'game_switch.json')
    if switch.get('phase') != 'ready':
        raise RecoveryRefused('not ready')
    identity = runtime_identity(switch.get('active'))
    runtime = state_dir/'runtimes'/identity['runtime_id']
    if runtime.is_symlink() or not runtime.is_dir():
        raise RecoveryRefused('runtime directory')
    path = runtime/'presentation.json'
    state, digest = read_record(path)
    run, _ = read_record(runtime/'hanjuku_run.json')
    if (runtime_identity(run) != identity
            or run.get('terminal_reason') or run.get('terminal_candidate')):
        raise RecoveryRefused('terminal or foreign native game')
    if state.get('status') != 'presentation_failed':
        raise RecoveryRefused('not a failed projection')
    groups = state.get('groups')
    if (not isinstance(groups, list) or len(groups) != 3 or any(type(p) is not int or p < 2 for p in groups)
            or len(set(groups)) != 3 or effects.group_members(groups[2])):
        raise RecoveryRefused('old player still owned')
    display, window = state.get('display'), state.get('window')
    if (not isinstance(display, str) or not re.fullmatch(r':[0-9]+', display) or display == output_display
            or not isinstance(window, str) or not window.isdecimal()
            or any(type(state.get(k)) is not int or not 0 < state[k] <= cap
                   for k, cap in [('width', 4096), ('height', 2160)])):
        raise RecoveryRefused('native dimensions')
    sources = [effects.process(pid) for pid in groups[:2]]
    if (any(p is None or p['uid'] != os.getuid() or p['group'] != p['pid'] for p in sources)
            or sources[0]['executable'] != 'Xvfb' or sources[1]['executable'] != 'dbus-run-session'):
        raise RecoveryRefused('native ownership')
    cw, ch = measured_contain_size(state['width'], state['height'], viewport[2], viewport[3])
    gap = viewport[2] - cw
    if gap < 180:
        raise RecoveryRefused('unsupported projection')
    windows = effects.windows(output_display, f"docich-present-{identity['runtime_id']}")
    if len(windows) != 1:
        raise RecoveryRefused('projection ambiguity')
    _, pid = effects.window(output_display, windows[0])
    player = effects.process(pid)
    if not player:
        raise RecoveryRefused('no live player')
    claim = dict(schema=1, status='prepared', identity=identity, native=state,
                 native_sha256=digest, sources=sources, player=player,
                 game_owner=effects.owner(identity, 'game', f"game-g{identity['generation']}"),
                 output_display=output_display, output_window=windows[0],
                 projection=dict(align='right', viewport=viewport, content=[gap, (viewport[3]-ch)//2, cw, ch]))
    claim['player_argv_sha256'] = hashlib.sha256(json.dumps(projection_argv(claim)).encode()).hexdigest()
    verify_live(claim, effects)
    if read_record(path)[1] != digest:
        raise RecoveryRefused('presentation changed')
    return claim


def adopt(effects):
    # Serialize the transfer with normal drain/switch/cleanup. Gameplay and its
    # input gate keep running; a busy lifecycle is refused instead of interrupted.
    with GameSwitchLock(effects.root/'run-soren-live').acquire(exclusive=True, blocking=False):
        return _adopt_locked(effects)


def write_receipt(runtime, initial, value):
    current,_=read_record(runtime/RECEIPT)
    for key,expected in initial.items():
        if key=='status':continue
        actual=(current.get('original_player',current.get('player')) if key=='player' else
                current.get('original_output_window',current.get('output_window')) if key=='output_window' else
                current.get(key))
        if actual!=expected:raise RecoveryRefused('recovery receipt superseded')
    atomic_write_json(runtime/RECEIPT,value)


def _adopt_locked(effects):
    switch, _ = read_record(effects.root/'run-soren-live/game_switch.json')
    identity = runtime_identity(switch.get('active'))
    existing = effects.root/'run-soren-live/runtimes'/identity['runtime_id']/RECEIPT
    if existing.exists() or existing.is_symlink():
        claim, _ = read_record(existing)
        if claim.get('status') != 'ready' or claim.get('identity') != identity:
            raise RecoveryRefused('existing recovery owner')
        verify_live(claim, effects)
        if effects.owner(identity, 'game', f"game-g{identity['generation']}", pid=claim['guard']['pid']) != claim.get('guard'):
            raise RecoveryRefused('recovery guard changed')
        verify_guard(claim, claim['guard'], effects)
        state, _ = read_record(existing.with_name('presentation.json'))
        expected = dict(claim['native'], status='ready', projection=claim['projection'],
                        recovery=dict(identity=identity, player=claim['player'], guard=claim['guard']))
        if state != expected:
            raise RecoveryRefused('recovery presentation changed')
        return {'status': 'already_recovered', 'game_restarted': False, 'projection_restarted': True}
    claim = prepare(effects)
    runtime = effects.root/'run-soren-live/runtimes'/claim['identity']['runtime_id']
    receipt = runtime/RECEIPT
    # An existing receipt/window is an ownership claim, never an overwrite target.
    if receipt.exists() or receipt.is_symlink():
        raise RecoveryRefused('existing recovery owner')
    backup = runtime/'presentation_before_recovery.json'
    if backup.exists() or backup.is_symlink():
        raise RecoveryRefused('existing recovery backup')
    verify_live(claim, effects)
    _, digest, raw = read_record(runtime/'presentation.json', include_bytes=True)
    if digest != claim['native_sha256']:
        raise RecoveryRefused('presentation changed before claim')
    # Both creates are exclusive. Concurrent operators cannot overwrite a claim.
    with backup.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(claim, stream)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    effects.spawn(claim)
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        status, _ = read_record(receipt)
        if status.get('status') == 'ready':
            verify_live(status, effects)
            if status.get('original_player') != claim['player']:
                raise RecoveryRefused('replacement owner receipt')
            return {'status': 'recovered', 'game_restarted': False, 'projection_restarted': True}
        if status.get('status') in ('failed', 'cleanup_failed', 'stopped'):
            break
        time.sleep(.05)
    # An observation deadline is not evidence that the runtime-owned guard
    # stopped. Leave its receipt/handle intact for a verified follow-up.
    return {'status': 'pending_guard', 'game_restarted': False, 'projection_restarted': None}


def supervise(root,runtime_id,anchor_pid,anchor_start):
    effects=LinuxEffects(root);runtime=root/'run-soren-live/runtimes'/runtime_id
    stopped=False;child=None;child_claim=None;replacement=None;claim=None;ready=None;failure=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,stop)
    def anchor_alive():
        try:p=effects.process(anchor_pid)
        except Exception:return True  # unknown must not manufacture a dead pane
        return p is not None and p['start']==anchor_start and p['uid']==os.getuid()
    try:
        claim,_=read_record(runtime/RECEIPT)
        if (claim.get('status')!='prepared' or claim['identity']['runtime_id']!=runtime_id
                or claim['game_owner']['pid']!=anchor_pid or claim['game_owner']['start']!=anchor_start):
            raise RecoveryRefused('guard receipt')
        guard=effects.owner(claim['identity'],'game',f"game-g{claim['identity']['generation']}",pid=os.getpid())
        if guard['pid']!=os.getpid():raise RecoveryRefused('guard process')
        verify_guard(claim,guard,effects)
        verify_live(claim,effects)
        path=runtime/'presentation.json';native,digest=read_record(path)
        if digest!=claim['native_sha256'] or native!=claim['native']:
            raise RecoveryRefused('native record changed')
        child=effects.launch_player(claim)
        child_info=effects.process(child.pid)
        if not child_info or child_info['parent']!=guard['pid']:
            raise RecoveryRefused('replacement child birth')
        child_claim=dict(claim,player=child_info)
        verify_player(child_claim,effects)
        deadline=time.monotonic()+8
        window,first_frame=effects.wait_displayed(claim,child,deadline=deadline)
        player=effects.process(child.pid)
        replacement=dict(claim,player=player,output_window=window,original_player=claim['player'],
                         original_output_window=claim['output_window'],guard=guard,first_frame=first_frame)
        if not player or player['parent']!=guard['pid']:
            raise RecoveryRefused('replacement is not a guard child')
        verify_player(replacement,effects)
        verify_source(claim,effects)
        if read_record(path)[1]!=claim['native_sha256']:
            raise RecoveryRefused('presenter changed before replacement')
        # Until a displayed child is verified the healthy manual mirror stays.
        effects.terminate(claim)
        verify_live(replacement,effects)
        ready=dict(claim['native'],status='ready',projection=claim['projection'],
                   recovery=dict(identity=claim['identity'],player=player,guard=guard))
        atomic_write_json(path,ready)
        write_receipt(runtime,claim,dict(replacement,status='ready'))
        while not stopped and anchor_alive():
            try:
                verify_live(replacement,effects)
                if read_record(path)[0]!=ready:raise RecoveryRefused('presenter superseded')
            except Exception:
                failure=True
                current,_=read_record(path)
                if current==ready:
                    atomic_write_json(path,dict(claim['native'],status='presentation_failed'))
                write_receipt(runtime,claim,dict(replacement,status='failed'))
                break
            time.sleep(1)
    except Exception:
        failure=True
        if claim is not None:
            try:write_receipt(runtime,claim,dict(replacement or claim,status='failed'))
            except RecoveryRefused:pass
    finally:
        # A projection failure must not create a dead game pane while the
        # native game still lives: old adapters classify any dead pane as game
        # failure. On legacy cleanup the child is also in the physical tree.
        if child is not None and child.poll() is None and child_claim is not None:
            try:effects.terminate(child_claim)
            except Exception:failure=True
        if child is not None:
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:failure=True
        while not stopped and anchor_alive():time.sleep(1)
        if claim is not None:
            # Failed startup leaves the known manual mirror untouched while
            # the game lives. Only after the anchor ends may it be reclaimed.
            if stopped or not anchor_alive():
                try:effects.terminate(claim)
                except Exception:failure=True
            remaining=(effects.process(claim['player']['pid']) is not None
                       or (child is not None and child.poll() is None))
            status='cleanup_failed' if remaining else 'stopped'
            if ready is not None:
                current,_=read_record(runtime/'presentation.json')
                if current==ready:
                    atomic_write_json(runtime/'presentation.json',dict(claim['native'],status='presentation_failed'))
            write_receipt(runtime,claim,dict(replacement or claim,status=status))


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if (len(argv)!=4 or argv[0]!='guard' or not re.fullmatch(r'g[1-9][0-9]*-[a-f0-9]{8}',argv[1])
            or not argv[2].isdecimal() or int(argv[2])<2 or not argv[3].isdecimal()):return 64
    try:
        supervise(ROOT,argv[1],int(argv[2]),argv[3]);return 0
    except Exception:
        print('Hanjuku projection recovery guard ended unverified',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
