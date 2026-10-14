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
    backend.w = SimpleNamespace(window_identity=lambda hwnd: (11, 'game.exe'))
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
    backend.w = SimpleNamespace(window_identity=lambda hwnd: (11, 'game.exe'))
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


def test_low_corridor_brakes_before_body_reaches_bottom_edge():
    ctl = _actual_mpc()
    ctl._hist.append((10, 170, 161, 0))
    ctl._ref = [(170, 162, 0, 0)] * ctl.cfg.horizon
    # Lv48の家の下: y158の参照へ降りる途中でも、入力遅れ用の下端余白を残す。
    assert ctl._precision_action(170, 161, 0, 0, 0, 0) == 3


def test_terrain_above_full_body_does_not_disable_rotation_under_house():
    ctl = MpcController(height=200)
    ctl.solid = bytearray(320 * 200)
    for y in range(144, 149):
        ctl.solid[y * 320 + 140:y * 320 + 195] = bytes([1]) * 55
    ctl._hist.append((10, 170, 159, .4))
    ctl._ref = [(150, 158, -8, 0)] * ctl.cfg.horizon
    # Lv48の家の下: 本体の頭はy152。旧y-14プローブだけが地形へ入り噴射を止めた。
    assert ctl._precision_action(170, 159, -20, 5, .4, 0) == 2
    # 実際に頭が地形へ接する高さでは、先に噴射を切り離れる。
    assert ctl._precision_action(170, 150, -20, 5, 0, 0) == 0


def test_real_spike_red_ring_is_not_removed_as_nearby_jet_flame():
    from docich.flyhome import vision
    from docich.flyhome.image import Image
    from docich.flyhome.settings import Settings
    img = Image.blank(320, 180)
    for y in range(80, 94):
        for x in range(110, 118):
            img.put(x, y, (255, 127, 0))
    for y in range(80, 90):
        for x in range(99, 109):
            if x in (99, 108) or y in (80, 89):
                img.put(x, y, (217, 0, 0))
    for y in range(95, 99):
        for x in range(110, 112):
            img.put(x, y, (251, 0, 2))
    obs = vision.analyze(img, Settings())
    assert obs.player is not None
    assert (99, 80, 109, 90) in obs.hazards
    assert (110, 95, 112, 99) not in obs.hazards


def test_bottom_braking_also_stops_residual_rotation():
    ctl = _actual_mpc()
    ctl._hist.append((10, 170, 161, .3))
    ctl._ref = [(170, 162, 0, 0)] * ctl.cfg.horizon
    assert ctl._precision_action(170, 161, 0, 0, .3, .7) == 2


def test_upward_coast_does_not_suppress_urgent_rotation_braking():
    ctl = _actual_mpc()
    ctl._hist.append((10, 100, 52, .3))
    ctl._ref = [(100, 52, 0, 0)] * ctl.cfg.horizon
    assert ctl._precision_action(100, 52, 0, -16, .3, .7) == 2
    assert ctl._precision_action(100, 52, 0, -16, .05, .1) == 0


def test_descent_entry_waits_for_upright_still_pose():
    ctl = _actual_mpc()
    ctl.path = [(294, 58), (294, 142), (168, 106.6)]
    ctl._hist.extend([(9.7,294,50,.3),(9.8,294,50,.3),(9.9,294,50,.3),(10,294,50,.3)])
    ctl._ref = [(294, 65, 0, 12)] * ctl.cfg.horizon
    ctl._descent_ready_since = 9.5
    ctl._precision_action(294, 50, 0, 0, .3, .4)
    assert ctl._descent_commit is None
    assert ctl._precision_debug[7] is True


def _actual_mpc(**kwargs):
    from docich.flyhome.control import Physics
    return MpcController(Physics(gravity=86, thrust=211, single_thrust_ratio=.89,
                                 spin=5.29, spin_mode="accel", angular_damping=.17,
                                 torque_sign=1, drag=.55), **kwargs)


def test_rejoin_from_inflated_terrain_keeps_clear_corridor_but_rejects_new_obstacles():
    from docich.flyhome import planner
    g = planner.Grid(4, 4, 1, bytearray(16), bytearray(16))
    g.blocked[g.idx(0, 0)] = 1
    path = [(1.5, .5), (2.5, .5), (2.5, 2.5)]
    assert planner.forward_path_clear(g, path, (.5, .5))
    g.blocked[g.idx(2, 1)] = 1
    assert not planner.forward_path_clear(g, path, (.5, .5))
    g.blocked[g.idx(2, 1)] = 0
    g.blocked[g.idx(1, 0)] = 1
    assert not planner.forward_path_clear(g, path, (.5, .5))


def test_head_contact_waits_even_when_horizontal_reference_is_close():
    ctl = _actual_mpc()
    ctl.solid = bytearray(320 * 180)
    for y in range(65, 68):
        ctl.solid[y * 320 + 10:y * 320 + 40] = bytes([1]) * 30
    ctl._hist.append((10, 24, 75, 0))
    ctl._ref = [(25, 83, 8, 0)] * ctl.cfg.horizon
    ctl._om_obs = 2
    assert ctl._precision_action(24, 75, 0, 0, 0, 0) == 0
    assert ctl._om_obs == 2  # 天井の近接判定で残っている回転を捨てない。


def test_actual_floor_uses_configured_foot_to_reset_phantom_rotation():
    ctl = _actual_mpc()
    ctl.solid = bytearray(320 * 180)
    for y in range(100, 106):
        ctl.solid[y * 320 + 10:y * 320 + 90] = bytes([1]) * 80
    ctl._acts.append((9.8, 1))
    st = SimpleNamespace(t=10, x=23.924, y=91.337, angle=0)
    for t in (10, 10.033, 10.066):
        st.t = t
        result = ctl._estimate(st)
    assert result[-1] == 0
    ctl.reset()
    ctl.solid = bytearray(320 * 180)
    ctl._acts.append((9.8, 1))
    for t in (10, 10.033, 10.066):
        st.t = t
        result = ctl._estimate(st)
    assert result[-1] > 0


def test_actual_flower_stems_are_not_terrain_but_floor_and_ceiling_are():
    from docich.flyhome import vision
    w, h = 40, 50
    labs = bytearray(w * h)
    for y in range(2, 10):
        labs[y * w: (y + 1) * w] = bytes([vision.GRASS]) * w
    for y in range(40, 50):
        labs[y * w: (y + 1) * w] = bytes([vision.DIRT if y > 44 else vision.GRASS]) * w
    for y in range(28, 40):
        labs[y * w + 20] = vision.GRASS
    for x in range(18, 23):
        labs[28 * w + x] = vision.GRASS
    result = vision._terrain_mask(labs, w, h)
    assert not any(result[y * w + 20] for y in range(28, 39))
    assert all(result[9 * w + x] for x in range(w))
    assert all(result[40 * w + x] for x in range(w))
    assert all(result[y * w + 20] for y in range(40, 50))


def test_thin_dirt_edge_remains_real_terrain():
    from docich.flyhome import vision
    labs = bytearray(12 * 12)
    for y in range(12):
        labs[y * 12 + 6] = vision.DIRT
    result = vision._terrain_mask(labs, 12, 12)
    assert all(result[y * 12 + 6] for y in range(12))
    assert sum(result) == 12


def test_near_ceiling_brakes_observed_remaining_rotation():
    ctl = _actual_mpc()
    ctl.solid = bytearray(320 * 180)
    for y in range(65, 68):
        ctl.solid[y * 320 + 10:y * 320 + 40] = bytes([1]) * 30
    ctl._hist.append((10, 24, 75, 0))
    ctl._ref = [(25, 83, 8, 0)] * ctl.cfg.horizon
    ctl._om_obs = 2
    assert ctl._precision_action(24, 75, 0, 0, 0, 2) == 2
    assert ctl._om_obs == 2


def test_matching_predicted_stop_angle_still_requires_counter_torque():
    ctl = _actual_mpc()
    ctl._hist.append((10, 24, 80, -.1))
    ctl._ref = [(24, 80, 5.3, 0)] * ctl.cfg.horizon
    assert ctl._precision_action(24, 80, 0, 0, -.1, 1) == 2
    assert abs(ctl._precision_debug[6]) < .02


def test_direct_window_identity_avoids_enumeration_and_checks_live_exe():
    from ctypes import wintypes
    def get_pid(hwnd, output):
        output._obj.value = 10
        return 1
    api = SimpleNamespace(IsWindow=lambda hwnd: True, IsWindowVisible=lambda hwnd: True,
                          GetWindowThreadProcessId=get_pid)
    with patch.object(win32, '_require'), patch.object(win32, 'user32', api, create=True), \
         patch.object(win32, 'wt', wintypes, create=True), \
         patch.object(win32, '_exe_of', return_value='game.exe'), \
         patch.object(win32, 'list_windows', side_effect=AssertionError('enumeration is slow')):
        assert win32.window_identity(1) == (10, 'game.exe')
    api.IsWindow = lambda hwnd: False
    with patch.object(win32, '_require'), patch.object(win32, 'user32', api, create=True), \
         patch.object(win32, 'wt', wintypes, create=True):
        assert win32.window_identity(1) is None


def test_same_pid_with_unexpected_exe_still_rejects_input():
    backend = runner.WindowsBackend.__new__(runner.WindowsBackend)
    backend.s = SimpleNamespace(input_mode='postmessage')
    backend.hwnd = 1
    backend.info = SimpleNamespace(pid=10, exe='game.exe')
    backend.w = SimpleNamespace(window_identity=lambda hwnd: (10, 'other.exe'))
    assert not backend.ready()


def test_actual_level15_low_terrain_tunnel_needs_sideways_sliding():
    ctl = _actual_mpc()
    ctl.solid = bytearray(320 * 180)
    for xx in range(194, 251):
        for yy in range(132):
            ctl.solid[yy * 320 + xx] = 1
        for yy in range(148, 180):
            ctl.solid[yy * 320 + xx] = 1
    ctl._hist.append((10, 170, 139, 0))
    ctl._ref = [(185, 141, 8, 0)] * ctl.cfg.horizon
    assert ctl._low_terrain_tunnel(170, 139)
    assert ctl._low_terrain_tunnel(210, 144)
    ctl.hazards = [(220, 132, 224, 135)]
    assert not ctl._low_terrain_tunnel(210, 144)
    ctl.hazards = []
    ctl.solid = bytearray(320 * 180)
    assert not ctl._low_terrain_tunnel(170, 139)


def test_low_terrain_tunnel_is_detected_towards_left_as_well():
    ctl = _actual_mpc()
    ctl.solid = bytearray(320 * 180)
    for yy in list(range(132)) + list(range(148, 180)):
        ctl.solid[yy * 320 + 150:yy * 320 + 180] = bytes([1]) * 30
    ctl._hist.append((10, 200, 139, 0))
    ctl._ref = [(185, 141, -8, 0)] * ctl.cfg.horizon
    assert ctl._low_terrain_tunnel(200, 139)


def test_actual_level16_wide_spike_lane_can_descend_without_exact_stillness():
    ctl = _actual_mpc()
    ctl.path = [(150, 62), (150, 126), (152, 154.6)]
    ctl._hist.append((10, 154, 54, .2))
    ctl._ref = [(150, 65, 0, 12)] * ctl.cfg.horizon
    ctl.hazards = [(115, 70, 123, 153), (179, 70, 187, 153)]
    ctl._descent_ready_since = 9.7
    ctl._precision_action(154, 54, 8, 0, .2, .6)
    assert ctl._descent_commit == (150, 74)


def test_narrow_spike_lane_keeps_strict_descent_alignment():
    ctl = _actual_mpc()
    ctl.path = [(150, 62), (150, 126), (152, 154.6)]
    ctl._hist.append((10, 154, 54, .2))
    ctl._ref = [(150, 65, 0, 12)] * ctl.cfg.horizon
    ctl.hazards = [(131, 70, 139, 153), (161, 70, 169, 153)]
    ctl._descent_ready_since = 9.7
    ctl._precision_action(154, 54, 8, 0, .2, .6)
    assert ctl._descent_commit is None
    assert ctl._precision_debug[7]


def test_actual_level16_home_text_at_feet_does_not_reverse_head():
    from docich.flyhome import vision
    rows = ['..OOOO....', '..OOOOOO..', '.OOOOOOO..', 'OOOOOWOWO.',
            '.OOWOWOWO.', '.OOWOOOOO.', '.OOOWWWWO.', '.OOOOOOOO.',
            '.OOOOOOOOO', '..OOOOOOOO', '..OOOOOOOO', '..OOOOOOOO',
            '..OOO..OOO', 'WWWOOW.OWW']
    labels = bytearray(30 * 30)
    body = []
    for y, row in enumerate(rows, 5):
        for x, value in enumerate(row, 5):
            if value == 'O':
                labels[y * 30 + x] = vision.ORANGE
                body.append((x, y))
            elif value == 'W':
                labels[y * 30 + x] = vision.WHITE
    blob = vision.Blob(vision.ORANGE, body)
    angle = vision._orientation(blob, labels, 30, 30, -.14)
    assert abs(angle) < .35  # 実機は直立。旧判定は2.972rad。
    rotated = bytearray(30 * 30)
    for y in range(30):
        for x in range(30):
            rotated[x * 30 + 29 - y] = labels[y * 30 + x]
    blob = vision.Blob(vision.ORANGE, [(29 - y, x) for x, y in body])
    import math
    angle = vision._orientation(blob, rotated, 30, 30, math.pi / 2)
    assert abs(angle - math.pi / 2) < .35


def test_measured_input_delay_can_be_loaded_for_live_mpc(tmp_path):
    from docich.flyhome.settings import load, Settings
    cfg = tmp_path / 'flyhome.toml'
    cfg.write_text('mpc_delay_s = 0.04', encoding='utf8')
    assert load(cfg).mpc_delay_s == .04
    assert Settings().mpc_delay_s == .12


def test_invalid_live_input_delays_are_rejected():
    import pytest
    from docich.flyhome.settings import Settings
    for value in (-.01, .51, float('inf'), float('nan'), '0.04'):
        with pytest.raises(ValueError):
            Settings(mpc_delay_s=value)


def test_actual_level23_ascent_stops_before_rotation_runs_out_of_headroom():
    ctl = _actual_mpc()
    ctl._hist.append((10, 127.924, 23.337, 0))
    ctl._ref = [(129.2, 30, 8, 5.33)] * ctl.cfg.horizon
    # 実フレームの上昇速度: 姿勢制動の片足噴射を足さず自由落下へ。
    assert ctl._precision_action(127.924, 23.337, 21.779, -20.037, 0, -1.344) == 0
    # 十分な高さでは残った回転の制動を続ける。
    ctl._hist.clear()
    ctl._hist.append((10, 127.924, 60, 0))
    ctl._ref = [(129.2, 66, 8, 5.33)] * ctl.cfg.horizon
    assert ctl._precision_action(127.924, 60, 21.779, -20.037, 0, -1.344) == 1


def test_actual_level23_home_progress_wait_survives_edge_and_speed_quantization():
    from docich.flyhome.tracker import State, PLAYING
    ctl = _actual_mpc()
    ctl.cfg.takeoff_s = 0
    ctl._attempt = 1
    ctl.solid = bytearray(320 * 180)
    for y in range(164, 180):
        for x in range(147, 189):
            ctl.solid[y * 320 + x] = 1
    ctl._estimate = lambda st: (st.x, st.y, st.vx, st.vy, st.angle, st.omega)
    ctl._ref = [(168, 154.6, 0, 0)] * ctl.cfg.horizon
    ctl._precision_action = lambda *args: 2
    st = State(10, PLAYING, 1, 183.924, 153.337, -.0, .347, 0, 0, (168, 154.6), (147, 133, 189, 164))
    ctl._hist.extend((t, st.x, st.y, st.angle) for t in (9.76, 9.84, 9.92, 10))
    assert ctl.decide(st, st.home) == (False, False)
    assert ctl._home_settling
    # 実機では演出中に端へ約2pxずれ、差分速度が13px/sになった。
    st.t, st.x, st.vx, st.vy, st.angle = 10.3, 185.444, 13, -7.642, .311
    assert ctl.decide(st, st.home) == (False, False)
    # 家を離れたら追従へ戻す。試行のリセットでも待機を持ち越さない。
    st.t, st.x = 10.6, 190
    assert ctl.decide(st, st.home) == (False, True)
    assert not ctl._home_settling
    ctl._home_settling = True
    ctl.reset()
    assert not ctl._home_settling


def test_actual_level33_open_descent_does_not_require_two_spike_columns():
    ctl = _actual_mpc()
    ctl.path = [(46, 46), (46, 90), (58, 102)]
    ctl._hist.append((10, 49.924, 33.337, 0))
    ctl._ref = [(46.2, 38.7, 0, 12)] * ctl.cfg.horizon
    ctl.hazards = [(195, 87, 205, 97), (259, 87, 269, 97)]
    ctl._descent_ready_since = 9.7
    ctl._precision_action(49.924, 33.337, -.588, -2.023, 0, .391)
    assert ctl._descent_commit == (46, 58)
    # 片側だけの近い棘でも厳密条件を保つ。通路上に棘があれば余白0。
    for rect in ((59, 50, 69, 90), (41, 50, 51, 90)):
        ctl._descent_commit = None
        ctl._descent_ready_since = 9.7
        ctl.hazards = [rect]
        ctl._precision_action(49.924, 33.337, -.588, -2.023, 0, .391)
        assert ctl._descent_commit is None
        assert ctl._precision_debug[7]


def test_vertical_descent_preserves_three_identical_position_observations():
    ctl = _actual_mpc()
    ctl._hist.extend([(9.88, 151, 60, 0), (9.92, 150, 60, 0),
                      (9.96, 150, 60, 0), (10, 150, 60, 0)])
    ctl._ref = [(150, 65, 0, 12)] * ctl.cfg.horizon
    assert ctl._precision_action(150, 60, 0, 0, 0, 0) == 0
    assert ctl._precision_debug[0] == 0
    assert ctl._precision_debug[4] == 0
