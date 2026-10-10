from types import SimpleNamespace
from unittest.mock import patch
from docich.flyhome import win32, runner
from docich.flyhome.mpc import MpcController

def test_exe_selector_does_not_fall_back_to_discord_title():
    discord = win32.WindowInfo(1, 'Fly Me To The Home! 並走会', 'Discord', 9, 'C:/Discord.exe', (0,0,1,1))
    with patch.object(win32, 'list_windows', return_value=[discord]):
        assert win32.find_window(('Fly Me To The Home',), ('FlyMeToTheHome.exe',)) is None

def test_reused_game_handle_rejects_input():
    backend = runner.WindowsBackend.__new__(runner.WindowsBackend)
    backend.s = SimpleNamespace(input_mode='postmessage')
    backend.hwnd = 1
    backend.info = SimpleNamespace(pid=10, exe='game.exe')
    backend.w = SimpleNamespace(list_windows=lambda: [SimpleNamespace(hwnd=1, pid=11, exe='game.exe')])
    assert not backend.ready()

def test_sparse_path_first_turn_is_slowed():
    ctl = MpcController()
    ctl.path = [(20, 20), (52, 20), (52, 100)]
    ctl._cum = [0, 32, 112]
    ctl._corners = ctl._find_corners()
    assert ctl._corners == [32]
    samples = ctl._sample_path()
    assert samples[15][4] < ctl.cfg.v_ref

def test_spike_corridor_limits_reference_speed():
    ctl = MpcController()
    ctl.path = [(20, 20), (20, 100)]
    ctl._cum = [0, 80]
    ctl.hazards = [(4, 20, 10, 100), (30, 20, 36, 100)]
    samples = ctl._sample_path()
    assert max(p[4] for p in samples) < 15


def test_pending_jet_reversal_keeps_unapplied_first_input():
    import pytest
    ctl = MpcController()
    ctl.cfg.delay_s = 0.12
    ctl._acts.extend([(9.82, 1), (9.92, 2)])
    delay = ctl._pending_inputs(10.0)
    assert delay == pytest.approx(0.12)
    assert [a for _, a in ctl._pending] == [1, 2]
    assert [dt for dt, _ in ctl._pending] == pytest.approx([0.04, 0.08])
    ctl.reset()
    assert ctl._pending is None


def test_live_level39_dark_green_wall_edge_is_solid():
    from docich.flyhome import vision
    assert vision.classify_rgb(11, 38, 0) == vision.GRASS
    assert vision.classify_rgb(10, 10, 10) == vision.DARK


def test_distance_to_sparse_path_measures_segment_not_waypoint():
    from docich.flyhome import planner
    assert planner.path_distance([(20,20), (100,20)], (60,22)) == 2


def test_live_home_target_remains_fixed_when_player_splits_roof():
    from dataclasses import replace
    from docich.flyhome.vision import Observation, Player
    from docich.flyhome.tracker import Tracker
    obs = Observation(320, 180, Player(198, 146, 0, (194, 138, 203, 153), 92),
                      (184, 154.6), (163, 133, 205, 164), [], [], False, False,
                      1, 1, bytearray(320 * 180), bytearray(320 * 180))
    tr = Tracker()
    tr.update(obs, 0)
    # Lv42実機: 屋根が隠れると中央184→172/高さ154.6→149へ誤推定し、経路が逆行していた。
    st = tr.update(replace(obs, home=(172, 149), home_bbox=(163, 133, 181, 156)), .1)
    assert st.home == (184, 154.6)
    assert st.home_bbox == (163, 133, 205, 164)
    assert tr.last_obs.home == st.home


def test_restart_can_release_jets_left_down_by_previous_controller():
    # 強制終了したcontrollerのheldは消えるが、ゲーム内のキー状態は残り得る。
    kb = win32.Keyboard.__new__(win32.Keyboard)
    kb.held = set()
    events = []
    kb._event = lambda scan, down: events.append((scan, down))
    kb.release_all((75, 77))
    assert set(events) == {(75, False), (77, False)}
    assert kb.held == set()


def test_restart_resync_rejects_reused_game_handle_before_sending_keys():
    import pytest
    backend = runner.WindowsBackend.__new__(runner.WindowsBackend)
    backend.s = SimpleNamespace(input_mode='postmessage')
    backend._jets_synced = False
    backend.hwnd = 1
    backend.info = SimpleNamespace(pid=10, exe='game.exe')
    backend.w = SimpleNamespace(list_windows=lambda: [SimpleNamespace(hwnd=1, pid=11, exe='game.exe')])
    with pytest.raises(RuntimeError, match='identity changed'):
        backend.set_jets(False, False)


def test_path_rejoin_preserves_corridor_height_before_long_horizontal_leg():
    from docich.flyhome import planner
    path = [(20, 150), (20, 50), (300, 50), (300, 150)]
    rest = planner.remaining_path(path, (30, 40))
    assert rest[:3] == [(30, 40), (30.0, 50.0), (300, 50)]
    # Lv48実機での逸脱: (30,41)→(291,57)の長い斜線に置き換えて上の棘へ寄っていた。
    assert planner.path_distance(path, rest[1]) == 0

def test_aligned_descent_does_not_return_to_old_recovery_leg():
    from docich.flyhome import planner
    from docich.flyhome.vision import Observation
    ctl = MpcController()
    grid = planner.Grid(80, 45, 4, bytearray(3600), bytearray(3600))
    obs = Observation(320, 180, None, (168, 106.6), None, [], [], False, False,
                      1, 1, bytearray(320 * 180), bytearray(320 * 180))
    # Lv48実ログ: 入口へ合流する古い斜線が、中央到着後も左への参照を返していた。
    ctl.set_world(grid, obs, [(298, 43), (288, 53), (290, 54), (294, 58), (294, 142), (168, 106.6)])
    ctl._hist.extend([(9.7, 294, 50, 0), (9.8, 294, 50, 0), (9.9, 294, 50, 0), (10, 294, 50, 0)])
    ctl._ref = [(292, 49, -20, 0)] * ctl.cfg.horizon
    ctl._descent_ready_since = 9.5
    assert ctl._precision_action(294, 50, 0, 0, 0, 0) in (0, 3)
    assert ctl._precision_debug[2] == 0
    ctl._hist.append((10.1, 294, 60, 0))
    assert ctl._precision_action(294, 60, 0, 0, 0, 0) in (0, 3)
    assert ctl._precision_debug[2] == 0
    ctl.reset()
    assert ctl._descent_commit is None
