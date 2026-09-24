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


def _manager(g, now, coord, **kwargs):
    return ManualPaperCornerManager(
        g, clock=lambda: now[0],
        sleep=lambda s: now.__setitem__(0, now[0] + s),
        overlay=lambda g, p: None, speech=lambda g, t, **k: None, coordinator=coord,
        **kwargs)


def test_manual_start_narrates_and_restores(tmp_path):
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
    assert state['end_reason'] == 'eight-slots-drained'
    assert state['previous_game'] == 'sorengame'
    assert ('switch', PAPER_VIEW_NAME) in coord.calls
    assert ('switch', 'sorengame') in coord.calls
    # Separate state/lock: the daily slot is untouched.
    assert mgr.path.name == 'paper_corner_manual.json'
    assert not (g.state_dir / 'paper_corner.json').exists()


def test_manual_start_after_failed_but_restored_run_starts_fresh(tmp_path):
    from docich.adapters.program import PAPER_VIEW_NAME
    g = setup(tmp_path)
    now = [datetime(2026, 9, 11, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='sorengame')
    mgr = _manager(g, now, coord)
    # The stale failed run already handed the display back to sorengame, so a
    # fresh operator start must actually start instead of no-oping on restore.
    mgr._active_game = lambda: coord.active_game
    mgr.save({'status': 'failed', 'date': '2026-09-11', 'previous_game': 'sorengame'})

    assert mgr.start() == 'completed'

    saved = json.loads(mgr.path.read_text())
    assert saved['previous_game'] == 'sorengame'
    assert ('switch', PAPER_VIEW_NAME) in coord.calls


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
        g, clock=lambda: now[0],
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


def test_manual_run_advertises_paper_flag_during_active_window(tmp_path):
    """The soviet_now radio gate flag must exist while a manual run is active.

    ``_run_locked`` refreshes the flag right after the switch commits and again
    on every narrated segment, then clears it on restore. This test observes the
    flag from inside the manager's own overlay callback, the same vantage point
    a concurrently-polling shell script would have during a real run.
    """
    from docich.adapters.program import PAPER_VIEW_NAME

    g = setup(tmp_path)
    now = [datetime(2026, 9, 11, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='sorengame')
    flag = tmp_path / 'soren' / 'tmp' / '.paper_corner_active'
    seen_active = []

    def _overlay(g, payload):
        seen_active.append(flag.exists())

    mgr = ManualPaperCornerManager(
        g, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
        overlay=_overlay, speech=lambda g, t, **k: None, coordinator=coord)
    seen = {'n': 0}

    def _active():
        seen['n'] += 1
        return 'sorengame' if seen['n'] == 1 else 'paper-view'

    mgr._active_game = _active
    assert mgr.start() == 'completed'
    assert ('switch', PAPER_VIEW_NAME) in coord.calls
    # The first two overlays (switch notice, opening) precede the active flag;
    # every narration-loop overlay must see the flag present.
    assert len(seen_active) >= 3, 'the corner must have narrated at least once'
    assert all(seen_active[2:]), 'flag must be present while the corner narrates'
    assert not flag.exists(), 'flag must be cleared once the corner restores'


def test_manual_run_delivers_opening_and_all_eight_fallback_segments(tmp_path):
    """A no-AI manual run still reads every finite deterministic segment."""
    g = setup(tmp_path)
    now = [datetime(2026, 9, 11, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    coord = FakeCoordinator(active='sorengame')
    voice = []
    mgr = ManualPaperCornerManager(
        g, clock=lambda: now[0],
        sleep=lambda s: now.__setitem__(0, now[0] + s),
        overlay=lambda g, p: None,
        speech=lambda g, t, **k: voice.append(k.get('event_id')),
        coordinator=coord)
    seen = {'n': 0}

    def _active():
        seen['n'] += 1
        return 'sorengame' if seen['n'] == 1 else 'paper-view'

    mgr._active_game = _active
    assert mgr.start() == 'completed'
    delivered = {str(e).split(':')[-1] for e in voice if ':script:' in str(e)}
    assert delivered == {str(index) for index in range(1, 9)}
    assert any(str(e).endswith(':opening') for e in voice)


def test_manual_runs_get_distinct_delivery_scopes(tmp_path):
    g = setup(tmp_path)
    now = [datetime(2026, 9, 11, 3, 0, tzinfo=ZoneInfo('Asia/Tokyo')).timestamp()]
    first = _manager(g, now, FakeCoordinator(active='sorengame'))
    second = _manager(g, list(now), FakeCoordinator(active='sorengame'))
    assert first.delivery_scope.startswith('paper-corner-manual-')
    assert first.delivery_scope != second.delivery_scope
