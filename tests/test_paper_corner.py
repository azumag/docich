import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.config import load_global
from docich.paper_corner import PaperCornerManager


def setup(tmp_path):
    cfg=tmp_path/'config.toml'
    cfg.write_text(f'''[paths]
state_dir = "run"
[trading]
paper_worker_enabled = true
notifications_enabled = true
notification_speech_enabled = true
[webui]
soren_root = "{tmp_path}/soren"
[paper_corner]
enabled = true
start_hour = 22
duration_minutes = 30
''')
    return load_global(tmp_path,cfg)


class FakeResult:
    def __init__(self, status='succeeded', detail=None, error_code=None):
        self.status = status
        self.detail = detail
        self.error_code = error_code


class FakeCoordinator:
    def __init__(self, active=None):
        self.calls = []
        self.active_game = active

    def start(self, game):
        self.calls.append(('start', game))
        self.active_game = game
        return FakeResult()

    def switch(self, game):
        self.calls.append(('switch', game))
        self.active_game = game
        return FakeResult()

    def stop(self):
        self.calls.append(('stop', None))
        self.active_game = None
        return FakeResult()


def manager(g, **kwargs):
    kwargs.setdefault('coordinator', FakeCoordinator())
    return PaperCornerManager(g, **kwargs)


def test_delayed_boundary_runs_full_duration_and_does_not_repeat(tmp_path):
    g=setup(tmp_path)
    now=[datetime(2026,9,8,22,tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    due=now[0]; output=[]; voice=[]
    state=tmp_path/'soren/tmp/state'
    state.mkdir(parents=True, exist_ok=True)
    # An in-flight prediction is what the boundary must confirm.
    (state/'prediction_worker.pid').write_text(str(os.getpid()))
    (state/'current_prediction.json').write_text(json.dumps({'status':'ACTIVE'}))
    def sleep(seconds):
        if now[0] == due:
            now[0]+=35*60
            (state/'corner_boundary_prediction.json').write_text(json.dumps({'completed_at':now[0]}))
        else: now[0]+=seconds
    mgr=manager(g,clock=lambda:now[0],sleep=sleep,overlay=lambda g,p:output.append(p),speech=lambda g,t,**kw:voice.append(kw))
    assert mgr.tick() == 'completed'
    state=json.loads(mgr.path.read_text())
    assert state['started_at']==due+2100
    assert state['completed_at']-state['started_at']==1800
    # opening + 4 narration segments + 6 x 5-minute deliveries + end
    assert len(output)==len(voice)==12
    assert all(f'script:{i}' in state['reports'] for i in range(1, 5))
    assert all(p['body'] for p in output)
    assert '切り替えました' in state['reports']['opening']['text']
    assert json.loads(mgr.presentation.read_text())['mode']=='compact'
    assert mgr.tick()=='not-due'


def test_stale_waiting_request_is_not_fired_late(tmp_path):
    g=setup(tmp_path)
    now=[datetime(2026,9,9,0,30,tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord=FakeCoordinator(active='sorengame')
    mgr=manager(g,clock=lambda:now[0],sleep=lambda s:None,
                overlay=lambda g,p:None,speech=lambda g,t,**k:None,coordinator=coord)
    mgr.save({'status':'waiting','date':'2026-09-08','requested_at':now[0]-86400})
    assert mgr.tick()=='expired'
    assert coord.calls==[]
    assert json.loads(mgr.path.read_text())['status']=='idle'


def test_switch_notice_announced_when_displacing_game(tmp_path):
    g=setup(tmp_path)
    now=[datetime(2026,9,8,22,tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    due=now[0]
    coord=FakeCoordinator(active='sorengame')

    def sleep(seconds):
        if now[0] == due:
            now[0]+=35*60
            state=tmp_path/'soren/tmp/state'
            state.mkdir(parents=True, exist_ok=True)
            (state/'corner_boundary_prediction.json').write_text(json.dumps({'completed_at':now[0]}))
        else:
            now[0]+=seconds

    mgr=manager(g,clock=lambda:now[0],sleep=sleep,
                overlay=lambda g,p:None,speech=lambda g,t,**kw:None,
                coordinator=coord)
    seen={'n':0}

    def _active():
        seen['n']+=1
        return 'sorengame' if seen['n'] == 1 else 'paper-view'

    mgr._active_game=_active
    assert mgr.tick() == 'completed'
    state=json.loads(mgr.path.read_text())
    assert '試合終了後に画面を切り替えます' in state['reports']['switch-notice']['text']
    assert '切り替えました' in state['reports']['opening']['text']


def test_commit_verification_fails_when_old_game_remains(tmp_path):
    g=setup(tmp_path)
    now=[datetime(2026,9,8,22,tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord=FakeCoordinator(active='sorengame')
    mgr=manager(g,clock=lambda:now[0],sleep=lambda s:now.__setitem__(0,now[0]+s),
                overlay=lambda g,p:None,speech=lambda g,t,**kw:None,
                coordinator=coord)
    # Coordinator claims success but the old game is still canonical active.
    mgr._active_game=lambda:'sorengame'
    import pytest
    from docich.paper_corner import PaperCornerError
    mgr.save({'status':'starting','date':'2026-09-08','previous_game':'sorengame',
              'requested_at':now[0]})
    with pytest.raises(PaperCornerError):
        mgr._tick_locked(None,datetime.fromtimestamp(now[0],tz=ZoneInfo('Asia/Tokyo')))


def test_restart_retries_only_failed_sink_and_preserves_deadline(tmp_path):
    g=setup(tmp_path)
    now=[datetime(2026,9,8,22,tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    overlay=[]; speech=[]
    mgr=manager(g,clock=lambda:now[0],sleep=lambda t:now.__setitem__(0,now[0]+t),overlay=lambda g,p:overlay.append(p),speech=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('sink unavailable')))
    state={'status':'active','date':'2026-09-08','started_at':now[0],'ends_at':now[0]+1800}
    mgr.save(state)
    import pytest
    with pytest.raises(RuntimeError):mgr.tick()
    assert len(overlay)==1
    mgr.speech=lambda *a,**k:speech.append(k)
    now[0]+=10
    mgr.tick()
    assert len(overlay)==7 and len(speech)==7
    assert json.loads(mgr.path.read_text())['ends_at']==state['ends_at']


def test_another_crashed_active_corner_blocks_new_start(tmp_path):
    import pytest
    from docich.corner_boundary import program_lock
    from docich.game_switch import atomic_write_json
    g=setup(tmp_path)
    other=g.state_dir/'retro_corner.json'
    other.parent.mkdir(parents=True)
    atomic_write_json(other,{'status':'active'})
    with program_lock(g,other):pass
    with pytest.raises(RuntimeError):
        with program_lock(g,g.state_dir/'paper_corner.json'):pass
    atomic_write_json(other,{'status':'completed'})
    with program_lock(g,g.state_dir/'paper_corner.json'):pass


def test_view_switch_and_restore_cycle(tmp_path):
    from docich.adapters.program import PAPER_VIEW_NAME
    g = setup(tmp_path)
    now = [datetime(2026, 9, 8, 22, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    due = now[0]
    coord = FakeCoordinator(active='sorengame')

    def sleep(seconds):
        if now[0] == due:
            now[0] += 35 * 60
            state = tmp_path / 'soren/tmp/state'
            state.mkdir(parents=True, exist_ok=True)
            (state / 'corner_boundary_prediction.json').write_text(json.dumps({'completed_at': now[0]}))
        else:
            now[0] += seconds

    mgr = manager(g, clock=lambda: now[0], sleep=sleep,
                  overlay=lambda g, p: None, speech=lambda g, t, **kw: None,
                  coordinator=coord)
    seen = {'n': 0}

    def _active():
        # starting sees sorengame; the restore phase sees the live view.
        seen['n'] += 1
        return 'sorengame' if seen['n'] == 1 else 'paper-view'

    mgr._active_game = _active
    assert mgr.tick() == 'completed'
    state = json.loads(mgr.path.read_text())
    assert state['previous_game'] == 'sorengame'
    assert ('switch', PAPER_VIEW_NAME) in coord.calls
    assert ('switch', 'sorengame') in coord.calls
    assert coord.calls.index(('switch', PAPER_VIEW_NAME)) < coord.calls.index(('switch', 'sorengame'))


def test_view_is_not_a_game_definition(tmp_path):
    # The program view must never resolve from the games catalog.
    from docich.adapters import make_coordinator_adapter
    from docich.game_switch import RuntimeSpec
    from pathlib import Path as _Path
    import uuid
    g = setup(tmp_path)
    spec = RuntimeSpec(game='paper-view', adapter='program', generation=1,
                       runtime_id='g1-x', lease_id=str(uuid.uuid4()),
                       runtime_dir=_Path(tmp_path) / 'run' / 'runtimes' / 'g1-x',
                       game_window='game-g1', agent_window='agent-g1',
                       adapter_session='docich-game-g1')
    try:
        make_coordinator_adapter(g, spec)
    except Exception as exc:
        assert '見つかりません' in str(exc)
    else:
        raise AssertionError('paper-view must not resolve as a catalog game')


def test_restore_failure_is_failed_not_completed(tmp_path):
    from docich.paper_corner import PaperCornerError
    g = setup(tmp_path)
    now = [datetime(2026, 9, 8, 22, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]

    class FailingRestore(FakeCoordinator):
        def switch(self, game):
            self.calls.append(('switch', game))
            if game == 'sorengame':
                from test_paper_corner import FakeResult as _R
                return _R(status='failed', detail='boom')
            self.active_game = game
            return FakeResult()

    coord = FailingRestore(active='sorengame')
    mgr = manager(g, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
                  overlay=lambda g, p: None, speech=lambda g, t, **kw: None,
                  coordinator=coord)
    started = now[0]
    mgr.save({'status': 'active', 'date': '2026-09-08', 'game': 'paper-view',
              'previous_game': 'sorengame',
              'started_at': started, 'ends_at': started + 1800})
    assert mgr.tick() == 'failed'
    state = json.loads(mgr.path.read_text())
    assert state['status'] == 'failed'
    assert 'boom' in (state.get('last_error') or '')


def test_starting_reentry_keeps_original_return_target(tmp_path):
    g = setup(tmp_path)
    now = [datetime(2026, 9, 8, 22, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='sorengame')
    mgr = manager(g, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
                  overlay=lambda g, p: None, speech=lambda g, t, **kw: None,
                  coordinator=coord)
    mgr.save({'status': 'starting', 'date': '2026-09-08', 'previous_game': 'sorengame',
              'requested_at': now[0]})
    mgr._active_game = lambda: 'paper-view'
    mgr._tick_locked(None, datetime.fromtimestamp(now[0], tz=ZoneInfo('Asia/Tokyo')))
    assert json.loads(mgr.path.read_text())['previous_game'] == 'sorengame'


def test_restore_skips_live_game_without_view_evidence(tmp_path):
    g = setup(tmp_path)
    now = [datetime(2026, 9, 8, 22, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='sorengame')
    mgr = manager(g, clock=lambda: now[0], sleep=lambda s: None,
                  overlay=lambda g, p: None, speech=lambda g, t, **kw: None,
                  coordinator=coord)
    mgr._active_game = lambda: 'sorengame'
    # Legacy active state with no previous_game: view never started.
    mgr.save({'status': 'active', 'date': '2026-09-08',
              'started_at': now[0] - 3600, 'ends_at': now[0] - 1800})
    assert mgr._restore_locked(json.loads(mgr.path.read_text())) == 'completed'
    assert coord.calls == []
    assert json.loads(mgr.path.read_text())['status'] == 'completed'


def test_improve_job_spawns_after_restore_when_configured(tmp_path):
    g = setup(tmp_path)
    cfg = tmp_path / 'config.toml'
    cfg.write_text(cfg.read_text().replace(
        '[paper_corner]\nenabled = true',
        '[paper_corner]\nimprove_agents = "opencode:x"\nenabled = true'))
    g = load_global(tmp_path, cfg)
    now = [datetime(2026, 9, 8, 22, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    spawned = []
    mgr = manager(g, clock=lambda: now[0], sleep=lambda s: None,
                  overlay=lambda g, p: None, speech=lambda g, t, **kw: None,
                  coordinator=FakeCoordinator(active='paper-view'),
                  spawn=lambda argv, log: spawned.append((argv, log)))
    mgr._active_game = lambda: 'paper-view'
    mgr.save({'status': 'failed', 'date': '2026-09-08', 'previous_game': 'sorengame',
              'started_at': now[0] - 3600, 'ends_at': now[0] - 1800})
    assert mgr.tick() == 'completed'
    assert len(spawned) == 1
    argv, log_path = spawned[0]
    assert 'paper-improve' in argv and '--date' in argv and '2026-09-08' in argv
    assert str(log_path).endswith('paper-corner-improve-2026-09-08.log')
    state = json.loads(mgr.path.read_text())
    assert state['improve_job']['spawned'] is True


def test_improve_job_not_spawned_when_unconfigured(tmp_path):
    g = setup(tmp_path)
    now = [datetime(2026, 9, 8, 22, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    spawned = []
    mgr = manager(g, clock=lambda: now[0], sleep=lambda s: None,
                  overlay=lambda g, p: None, speech=lambda g, t, **kw: None,
                  coordinator=FakeCoordinator(active='paper-view'),
                  spawn=lambda argv, log: spawned.append((argv, log)))
    mgr._active_game = lambda: 'paper-view'
    mgr.save({'status': 'failed', 'date': '2026-09-08', 'previous_game': 'sorengame',
              'started_at': now[0] - 3600, 'ends_at': now[0] - 1800})
    assert mgr.tick() == 'completed'
    assert spawned == []
    assert 'improve_job' not in json.loads(mgr.path.read_text())


def test_restore_skips_operator_moved_on(tmp_path):
    g = setup(tmp_path)
    now = [datetime(2026, 9, 8, 22, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='nethack')
    mgr = manager(g, clock=lambda: now[0], sleep=lambda s: None,
                  overlay=lambda g, p: None, speech=lambda g, t, **kw: None,
                  coordinator=coord)
    mgr._active_game = lambda: 'nethack'
    mgr.save({'status': 'failed', 'date': '2026-09-08', 'previous_game': 'sorengame',
              'started_at': now[0] - 3600, 'ends_at': now[0] - 1800})
    assert mgr.tick() == 'completed'
    assert coord.calls == []
    assert json.loads(mgr.path.read_text())['status'] == 'completed'


def test_failed_restore_retries_next_tick(tmp_path):
    g = setup(tmp_path)
    now = [datetime(2026, 9, 8, 22, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]

    class Flaky(FakeCoordinator):
        def __init__(self):
            super().__init__(active='paper-view')
            self.attempts = 0

        def switch(self, game):
            self.calls.append(('switch', game))
            self.attempts += 1
            if self.attempts == 1:
                return FakeResult(status='failed', detail='boom')
            self.active_game = game
            return FakeResult()

    coord = Flaky()
    mgr = manager(g, clock=lambda: now[0], sleep=lambda s: None,
                  overlay=lambda g, p: None, speech=lambda g, t, **kw: None,
                  coordinator=coord)
    mgr._active_game = lambda: coord.active_game
    mgr.save({'status': 'failed', 'date': '2026-09-08', 'previous_game': 'sorengame',
              'started_at': now[0] - 3600, 'ends_at': now[0] - 1800})
    assert mgr.tick() == 'failed'
    assert mgr.tick() == 'completed'
    assert coord.calls.count(('switch', 'sorengame')) == 2


class FakeTmux:
    def __init__(self, session='docich', windows=None, dead=None, fail=None):
        from docich.tmux import PaneState
        self.session = session
        self.windows = dict(windows or {})
        self.dead = set(dead or [])
        self.fail = fail
        self.calls = []
        self.PaneState = PaneState

    def _maybe_fail(self, what):
        if self.fail == what or self.fail == 'all':
            raise RuntimeError(f'fake tmux {what} failure')

    def ensure_session(self):
        self.calls.append(('ensure_session',))
        self._maybe_fail('ensure_session')

    def has_window(self, name):
        self.calls.append(('has_window', name))
        self._maybe_fail('has_window')
        return name in self.windows

    def pane_states_checked(self, target):
        self.calls.append(('pane_states_checked', target))
        self._maybe_fail('pane_states_checked')
        name = target.split(':')[-1]
        if name not in self.windows:
            raise RuntimeError('no such window')
        return [self.PaneState(dead=(name in self.dead), pid=1234)]

    def kill_window(self, name):
        self.calls.append(('kill_window', name))
        self._maybe_fail('kill_window')
        self.windows.pop(name, None)
        self.dead.discard(name)

    def new_window(self, name, cmd, env=None):
        self.calls.append(('new_window', name, list(cmd)))
        self._maybe_fail('new_window')
        self.windows[name] = list(cmd)


def test_ensure_trading_window_disabled_without_worker(tmp_path):
    from docich.paper_corner import ensure_trading_window
    g = setup(tmp_path)
    g.trading.paper_worker_enabled = False
    fake = FakeTmux()
    assert ensure_trading_window(g, tmux=fake) == 'disabled'
    assert fake.calls == []


def test_ensure_trading_window_ok_when_live(tmp_path):
    from docich.paper_corner import ensure_trading_window
    g = setup(tmp_path)
    fake = FakeTmux(windows={'trading': ['old']})
    assert ensure_trading_window(g, tmux=fake) == 'ok'
    assert [c[0] for c in fake.calls] == ['ensure_session', 'has_window', 'pane_states_checked']


def test_ensure_trading_window_creates_when_missing(tmp_path):
    from docich.paper_corner import ensure_trading_window
    g = setup(tmp_path)
    fake = FakeTmux(windows={'bash': []})
    assert ensure_trading_window(g, tmux=fake) == 'created'
    created = [c for c in fake.calls if c[0] == 'new_window']
    assert len(created) == 1 and created[0][1] == 'trading'
    argv = created[0][2]
    assert argv[0].endswith('/bin/docich')
    assert argv[1:3] == ['--config', str(g.config_path)]
    assert argv[3:] == ['run', 'trading']


def test_ensure_trading_window_recreates_dead_pane(tmp_path):
    from docich.paper_corner import ensure_trading_window
    g = setup(tmp_path)
    fake = FakeTmux(windows={'trading': ['stale']}, dead={'trading'})
    assert ensure_trading_window(g, tmux=fake) == 'recreated'
    assert ('kill_window', 'trading') in fake.calls
    assert any(c[0] == 'new_window' and c[1] == 'trading' for c in fake.calls)


def test_ensure_trading_window_never_raises(tmp_path):
    from docich.paper_corner import ensure_trading_window
    g = setup(tmp_path)
    assert ensure_trading_window(g, tmux=FakeTmux(fail='all')).startswith('unavailable:')
    assert ensure_trading_window(g, tmux=FakeTmux(windows={}), ).startswith(('created', 'unavailable'))
    assert ensure_trading_window(object()) == 'disabled'
