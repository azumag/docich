import json
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.config import load_global
from docich.paper_corner_manual import ManualPaperCornerManager


def setup(tmp_path):
    cfg = tmp_path / 'config.toml'
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
    return load_global(tmp_path, cfg)


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


def _manager(g, now, coord, duration=3):
    return ManualPaperCornerManager(
        g, duration_minutes=duration, clock=lambda: now[0],
        sleep=lambda s: now.__setitem__(0, now[0] + s),
        overlay=lambda g, p: None, speech=lambda g, t, **k: None, coordinator=coord)


def test_manual_start_runs_full_duration_and_restores(tmp_path):
    from docich.adapters.program import PAPER_VIEW_NAME
    g = setup(tmp_path)
    now = [datetime(2026, 9, 11, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='sorengame')
    mgr = _manager(g, now, coord)
    seen = {'n': 0}

    def _active():
        seen['n'] += 1
        return 'sorengame' if seen['n'] == 1 else 'paper-view'

    mgr._active_game = _active
    assert mgr.start() == 'completed'
    state = json.loads(mgr.path.read_text())
    assert state['completed_at'] - state['started_at'] == 180
    assert state['previous_game'] == 'sorengame'
    assert ('switch', PAPER_VIEW_NAME) in coord.calls
    assert ('switch', 'sorengame') in coord.calls
    # Separate state/lock: the daily slot is untouched.
    assert mgr.path.name == 'paper_corner_manual.json'
    assert not (g.state_dir / 'paper_corner.json').exists()


def test_manual_stop_is_noop_when_inactive(tmp_path):
    g = setup(tmp_path)
    now = [datetime(2026, 9, 11, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='sorengame')
    mgr = _manager(g, now, coord)
    assert mgr.stop() == 'not-active'


def test_manual_stop_restores_an_active_view(tmp_path):
    g = setup(tmp_path)
    now = [datetime(2026, 9, 11, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='paper-view')
    mgr = _manager(g, now, coord)
    started = now[0]
    mgr.save({'status': 'active', 'date': '2026-09-11', 'game': 'paper-view',
              'previous_game': 'sorengame', 'started_at': started, 'ends_at': started + 1800})
    mgr._active_game = lambda: 'paper-view'
    assert mgr.stop() == 'completed'
    assert ('switch', 'sorengame') in coord.calls


def test_manual_start_is_single_flight(tmp_path):
    import fcntl
    g = setup(tmp_path)
    now = [datetime(2026, 9, 11, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='sorengame')
    mgr = _manager(g, now, coord)
    mgr.tick_guard_path.parent.mkdir(parents=True, exist_ok=True)
    held = mgr.tick_guard_path.open('a+')
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert mgr.start() == 'already-running'
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()


def test_manual_run_uses_its_own_delivery_scope(tmp_path):
    from docich.adapters.program import PAPER_VIEW_NAME
    g = setup(tmp_path)
    now = [datetime(2026, 9, 11, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='sorengame')
    voice = []
    mgr = ManualPaperCornerManager(
        g, duration_minutes=3, clock=lambda: now[0],
        sleep=lambda s: now.__setitem__(0, now[0] + s),
        overlay=lambda g, p: None, speech=lambda g, t, **k: voice.append(k),
        coordinator=coord)
    seen = {'n': 0}

    def _active():
        seen['n'] += 1
        return 'sorengame' if seen['n'] == 1 else 'paper-view'

    mgr._active_game = _active
    assert mgr.start() == 'completed'
    assert ('switch', PAPER_VIEW_NAME) in coord.calls
    ids = [str(kw.get('event_id')) for kw in voice]
    assert ids, 'manual narration must be spoken'
    # A manual run must use its own namespace and must never consume the daily
    # once-per-day delivery keys.
    assert all(i.startswith('paper-corner-manual-') for i in ids)
    assert not any(i.startswith('paper-corner:') for i in ids)


def test_manual_runs_get_distinct_delivery_scopes(tmp_path):
    g = setup(tmp_path)
    now = [datetime(2026, 9, 11, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    first = _manager(g, now, FakeCoordinator(active='sorengame'))
    second = _manager(g, list(now), FakeCoordinator(active='sorengame'))
    assert first.delivery_scope.startswith('paper-corner-manual-')
    assert first.delivery_scope != second.delivery_scope
