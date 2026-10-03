"""Fly Me To The Home! ランナー (docich.flyhome) のオフライン検証。

Windows API とゲーム本体は使わず、ゲームと同じ配色で描くシミュレータを相手に
認識 → 追跡 → 計画 → 制御 → 入力 の一気通貫と、各部品の単体挙動を確かめる。
"""

import json
import math
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.flyhome import calibrate, cli, planner, runner, sim, steam, tracker, vision, win32  # noqa: E402
from docich.flyhome.control import JetController, Physics  # noqa: E402
from docich.flyhome.image import Image, decode_png, encode_png, game_area  # noqa: E402
from docich.flyhome.settings import Settings, load  # noqa: E402
from docich.flyhome.timeline import Timeline  # noqa: E402


class TestImage(unittest.TestCase):
    def test_png_roundtrip(self):
        img = Image.blank(7, 5, (10, 20, 30))
        img.put(3, 2, (255, 127, 0))
        back = decode_png(encode_png(img))
        self.assertEqual((back.width, back.height), (7, 5))
        self.assertEqual(back.data, img.data)

    def test_game_area_letterbox_and_pillarbox(self):
        # Steam のスクリーンショット: 1728x1080 の中央 1728x972 が 16:9 の描画領域
        self.assertEqual(game_area(1728, 1080), (0, 54, 1728, 1026))
        self.assertEqual(game_area(2560, 1080), (320, 0, 2240, 1080))
        self.assertEqual(game_area(1920, 1080), (0, 0, 1920, 1080))

    def test_from_bgra_and_sample(self):
        buf = bytes([30, 20, 10, 255]) * 4  # 2x2 BGRA
        img = Image.from_bgra(2, 2, buf)
        self.assertEqual(img.get(1, 1), (10, 20, 30))
        big = img.scaled(4)
        self.assertEqual(big.sample(2, 2).data, img.data)


class TestVision(unittest.TestCase):
    def render(self, body=None, world=None):
        world = world or sim.World.basic()
        body = body or sim.Body(*world.start)
        return world, body, sim.render(world, body)

    def test_classify_measured_colors(self):
        self.assertEqual(vision.classify_rgb(255, 127, 0), vision.ORANGE)
        self.assertEqual(vision.classify_rgb(217, 0, 0), vision.RED)
        self.assertEqual(vision.classify_rgb(128, 0, 0), vision.ROOF)
        self.assertEqual(vision.classify_rgb(32, 129, 0), vision.GRASS)
        self.assertEqual(vision.classify_rgb(128, 64, 0), vision.DIRT)
        self.assertEqual(vision.classify_rgb(110, 145, 213), vision.OTHER)  # 空
        self.assertEqual(vision.classify_rgb(142, 108, 63), vision.OTHER)  # 夕焼けの背景

    def test_player_home_hud(self):
        world, body, img = self.render()
        obs = vision.analyze(img)
        self.assertIsNotNone(obs.player)
        self.assertAlmostEqual(obs.player.x, world.start[0], delta=1.5)
        self.assertAlmostEqual(obs.player.y, world.start[1], delta=1.5)
        self.assertAlmostEqual(obs.player.angle, 0.0, delta=0.2)
        hx0, hy0, hx1, _ = world.home
        self.assertIsNotNone(obs.home)
        self.assertTrue(hx0 <= obs.home[0] <= hx1)
        self.assertEqual((obs.hud_left, obs.hud_right), (False, False))

    def test_rotated_player_angle_and_flames(self):
        world = sim.World.basic()
        for deg in (-60, -30, 30, 60, 90):
            body = sim.Body(150, 80, angle=math.radians(deg), left=True, right=False)
            obs = vision.analyze(sim.render(world, body))
            self.assertIsNotNone(obs.player, deg)
            err = tracker.wrap(obs.player.angle - math.radians(deg))
            self.assertLess(abs(err), math.radians(15), deg)
            self.assertTrue(obs.player.flame_left, deg)
            self.assertFalse(obs.player.flame_right, deg)
            self.assertEqual((obs.hud_left, obs.hud_right), (True, False))

    def test_hazards_and_coin_not_player(self):
        world = sim.World.basic()
        world.hazards.append((150, 20, 166, 36))
        img = sim.render(world, sim.Body(*world.start))
        # コイン: 白縁付きのオレンジ
        img.fill_rect(100, 40, 111, 53, (255, 255, 255))
        img.fill_rect(101, 41, 110, 52, (255, 124, 5))
        obs = vision.analyze(img)
        self.assertEqual(len(obs.hazards), 1)
        self.assertEqual(len(obs.coins), 1)
        self.assertAlmostEqual(obs.player.x, world.start[0], delta=1.5)

    def test_explosion_fragments_are_not_player(self):
        world = sim.World.basic()
        body = sim.Body(*world.start)
        body.outcome = "dead"
        img = sim.render(world, body)
        for i in range(6):
            img.fill_rect(150 + i * 5, 60 + (i % 2) * 4, 153 + i * 5, 63 + (i % 2) * 4, (255, 127, 0))
        self.assertIsNone(vision.analyze(img).player)

    def test_to_native_from_upscaled_letterboxed_capture(self):
        world, body, img = self.render()
        up = img.scaled(4)  # 1280x720
        boxed = Image.blank(1280, 800)
        for y in range(720):
            o = ((y + 40) * 1280) * 3
            boxed.data[o : o + 1280 * 3] = up.data[y * 1280 * 3 : (y + 1) * 1280 * 3]
        native = vision.to_native(boxed)
        self.assertEqual(native.data, img.data)


class TestTracker(unittest.TestCase):
    def test_dead_and_cleared(self):
        world = sim.World.basic()
        tr = tracker.Tracker()
        body = sim.Body(*world.start)
        st = tr.update(vision.analyze(sim.render(world, body)), 0.0)
        self.assertEqual((st.phase, st.attempt), (tracker.PLAYING, 1))
        body.outcome = "dead"
        for i in range(4):
            st = tr.update(vision.analyze(sim.render(world, body)), 0.1 * (i + 1))
        self.assertEqual(st.phase, tracker.DEAD)
        # 再挑戦 → 家の中で消える = 帰宅
        hx0, hy0, hx1, hy1 = world.home
        body = sim.Body((hx0 + hx1) / 2, hy0 - 4)
        st = tr.update(vision.analyze(sim.render(world, body)), 1.0)
        self.assertEqual((st.phase, st.attempt), (tracker.PLAYING, 2))
        body.outcome = "cleared"
        for i in range(4):
            st = tr.update(vision.analyze(sim.render(world, body)), 1.1 + 0.1 * i)
        self.assertEqual(st.phase, tracker.CLEARED)

    def test_velocity_estimate(self):
        world = sim.World.basic()
        tr = tracker.Tracker(vel_alpha=1.0)
        tr.update(vision.analyze(sim.render(world, sim.Body(100, 80))), 0.0)
        st = tr.update(vision.analyze(sim.render(world, sim.Body(103, 77))), 0.1)
        self.assertAlmostEqual(st.vx, 30, delta=12)
        self.assertAlmostEqual(st.vy, -30, delta=12)


class TestPlanner(unittest.TestCase):
    def test_path_goes_over_wall_and_avoids_hazard(self):
        world = sim.World.basic()
        world.solids.append((150, 80, 170, 180))
        world.hazards.append((190, 10, 206, 26))
        obs = vision.analyze(sim.render(world, sim.Body(*world.start)))
        grid = planner.build_grid(obs)
        path = planner.astar(grid, world.start, obs.home)
        self.assertIsNotNone(path)
        self.assertEqual(path[-1], obs.home)
        for a, b in zip(path, path[1:]):
            for i in range(21):
                x = a[0] + (b[0] - a[0]) * i / 20
                y = a[1] + (b[1] - a[1]) * i / 20
                self.assertFalse(150 - 2 <= x <= 170 + 2 and y >= 80 - 2, (x, y))
                self.assertFalse(190 - 4 <= x <= 206 + 4 and 10 - 4 <= y <= 26 + 4, (x, y))

    def test_lookahead_skips_passed_points(self):
        path = [(0, 0), (10, 0), (20, 0), (100, 0)]
        self.assertEqual(planner.lookahead(path, (19, 0)), (100, 0))
        self.assertEqual(planner.lookahead(path, (0, 0), reach=5), (10, 0))


class TestControllerInSim(unittest.TestCase):
    """仮説の物理パラメータを振っても、経路追従で帰宅できること。"""

    def fly(self, physics: Physics, wall: bool) -> str:
        world = sim.World.basic()
        if wall:
            world.solids.append((150, 80, 170, 180))
            world.hazards.append((190, 10, 206, 26))
        obs = vision.analyze(sim.render(world, sim.Body(*world.start)))
        path = planner.astar(planner.build_grid(obs), world.start, obs.home)
        ctl = JetController(physics)
        tick = [0]

        def decide(b):
            tick[0] += 1
            st = tracker.State(tick[0] / 30, tracker.PLAYING, 1, b.x, b.y, b.vx, b.vy, b.angle, b.omega, obs.home, obs.home_bbox)
            return ctl.decide(st, planner.lookahead(path, (b.x, b.y)))

        outcome, _ = sim.run_controller(world, decide, physics=physics)
        return outcome

    def test_variants(self):
        variants = [
            {},
            {"torque_sign": -1},
            {"spin_mode": "accel", "spin": 25},
            {"spin": 3},
            {"single_thrust_ratio": 0.0},
            {"drag": 0.0},
            {"thrust": 400},
        ]
        for kw in variants:
            for wall in (False, True):
                with self.subTest(physics=kw, wall=wall):
                    self.assertEqual(self.fly(Physics(**kw), wall), "cleared")


class TestRunnerWithSim(unittest.TestCase):
    def settings(self, tmp):
        return replace(Settings(), run_dir=Path(tmp), physics_file=Path(tmp) / "physics.json")

    def test_play_clears_and_saves_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.settings(tmp)
            world = sim.World.basic()
            world.solids.append((150, 80, 170, 180))
            b = runner.SimBackend(world)
            results = runner.play(b, s, attempts=3, max_seconds=30, log_root=Path(tmp) / "log")
            self.assertEqual(results[-1]["outcome"], "cleared")
            tl_files = list((Path(tmp) / "log").glob("timeline-*.json"))
            self.assertEqual(len(tl_files), 1)
            frames = (Path(tmp) / "log" / "frames.jsonl").read_text().splitlines()
            self.assertTrue(any(json.loads(f)["left"] for f in frames))

            # 保存したタイムラインを再生すると同じく帰宅する (シミュレータは決定的)
            row = runner.replay(runner.SimBackend(world), s, Timeline.load(tl_files[0]))
            self.assertEqual(row["outcome"], "cleared")

    def test_retry_after_death(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.settings(tmp)
            b = runner.SimBackend()
            # 推力が弱すぎて飛べない物理で制御器を動かす → 試行打ち切りで死亡 → Enter で再挑戦
            b.physics = Physics(thrust=100)
            results = runner.play(b, s, physics=Physics(), attempts=2, max_seconds=40, attempt_timeout_s=3, log_root=None)
            self.assertEqual([r["outcome"] for r in results], ["dead", "dead"])
            self.assertIn("enter", b.taps)

    def test_probe_and_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.settings(tmp)
            world = sim.World(solids=[(0, 170, 320, 180)], hazards=[], home=(300, 0, 316, 10), start=(160.0, 163.0))
            truth = Physics(gravity=300, thrust=600, spin=4.0, torque_sign=-1, drag=0.0)
            rows = runner.probe(runner.SimBackend(world, truth), s)
            phys, report = calibrate.fit(rows)
            self.assertEqual(phys.torque_sign, -1, report)
            self.assertAlmostEqual(phys.gravity, 300, delta=45)
            self.assertAlmostEqual(phys.thrust, 600, delta=90)
            self.assertAlmostEqual(phys.spin, 4.0, delta=1.0)


class TestCalibrateExact(unittest.TestCase):
    def test_fit_recovers_rate_model(self):
        truth = Physics(gravity=300, thrust=650, spin=4, torque_sign=-1, single_thrust_ratio=0.3, drag=0.0)
        world = sim.World(solids=[], hazards=[], home=(0, 0, 1, 1), width=10000, height=10000)
        b = sim.Body(160, 500)
        rows, t = [], 0.0
        for keys, dur in [((1, 1), 0.4), ((0, 0), 0.4), ((1, 0), 0.2), ((1, 1), 0.3), ((0, 0), 0.3), ((0, 1), 0.25), ((1, 1), 0.3), ((0, 0), 0.3)]:
            for _ in range(int(dur * 30)):
                for _ in range(4):
                    sim.step(world, b, bool(keys[0]), bool(keys[1]), 1 / 120, truth)
                t += 1 / 30
                rows.append({"t": t, "phase": "playing", "x": b.x, "y": b.y, "angle": b.angle, "left": keys[0], "right": keys[1]})
        phys, _ = calibrate.fit(rows)
        self.assertAlmostEqual(phys.gravity, 300, delta=1)
        self.assertAlmostEqual(phys.thrust, 650, delta=1)
        self.assertAlmostEqual(phys.spin, 4, delta=0.05)
        self.assertEqual(phys.torque_sign, -1)
        self.assertEqual(phys.spin_mode, "rate")
        self.assertAlmostEqual(phys.single_thrust_ratio, 0.3, delta=0.02)


class TestTimeline(unittest.TestCase):
    def test_add_dedup_and_state(self):
        tl = Timeline(level=3)
        tl.add(0, False, False)  # 最初の無入力は捨てる
        tl.add(100, True, True)
        tl.add(150, True, True)
        tl.add(400, True, False)
        self.assertEqual(tl.events, [(100, True, True), (400, True, False)])
        self.assertEqual(tl.state_at(50), (False, False))
        self.assertEqual(tl.state_at(200), (True, True))
        self.assertEqual(tl.state_at(500), (True, False))
        back = Timeline.from_json(json.loads(json.dumps(tl.to_json())))
        self.assertEqual(back.events, tl.events)
        with self.assertRaises(ValueError):
            tl.add(10, False, False)


class TestSteam(unittest.TestCase):
    def test_library_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Steam"
            lib2 = Path(tmp) / "Lib2"
            (root / "steamapps").mkdir(parents=True)
            (lib2 / "steamapps" / "common" / "Fly Me To The Home").mkdir(parents=True)
            (lib2 / "steamapps" / "common" / "Fly Me To The Home" / "FlyMeToTheHome.exe").write_bytes(b"")
            lib2_escaped = str(lib2).replace("\\", "\\\\")
            (root / "steamapps" / "libraryfolders.vdf").write_text(
                '"libraryfolders"\n{\n "0"\n {\n  "path" "%s"\n }\n "1"\n {\n  "path" "%s"\n  "apps" { "2076670" "123" }\n }\n}\n'
                % (str(root).replace("\\", "\\\\"), lib2_escaped)
            )
            (lib2 / "steamapps" / "appmanifest_2076670.acf").write_text(
                '"AppState"\n{\n "appid" "2076670"\n "name" "Fly Me To The Home!"\n "StateFlags" "4"\n "installdir" "Fly Me To The Home"\n "buildid" "42"\n}\n'
            )
            app = steam.find_app(2076670, root)
            self.assertIsNotNone(app)
            self.assertTrue(app.fully_installed)
            self.assertEqual(app.name, "Fly Me To The Home!")
            self.assertEqual([p.name for p in app.exes], ["FlyMeToTheHome.exe"])
            self.assertIsNone(steam.find_app(2919910, root))


class TestSettingsAndCli(unittest.TestCase):
    def test_repo_config_loads(self):
        s = load()
        self.assertEqual(s.app_id, 2076670)
        self.assertEqual(s.demo_app_id, 2919910)
        self.assertEqual((s.native_width, s.native_height), (320, 180))

    def test_unknown_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.toml"
            p.write_text("tick_hz = 20\nnope = 1\n")
            with self.assertRaises(ValueError):
                load(p)
            p.write_text('tick_hz = 20\nwindow_titles = ["Foo"]\n[keys]\nleft = 30\n')
            s = load(p)
            self.assertEqual((s.tick_hz, s.window_titles, s.keys.left), (20, ("Foo",), 30))

    def test_win32_guard_off_windows(self):
        if win32.IS_WINDOWS:
            self.skipTest("Windows では実 API が呼べる")
        with self.assertRaises(RuntimeError):
            win32.list_windows()

    def test_cli_analyze_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = sim.World.basic()
            img = sim.render(world, sim.Body(*world.start)).scaled(4)
            png = Path(tmp) / "shot.png"
            png.write_bytes(encode_png(img))
            self.assertEqual(cli.main(["analyze", str(png)]), 0)
            self.assertTrue((Path(tmp) / "shot.annot.png").is_file())

    def test_cli_parser_has_all_commands(self):
        ap = cli.build_parser()
        for cmd in ("doctor", "launch", "windows", "shot", "analyze", "watch", "keytest", "probe", "fit", "play", "record", "replay"):
            ap.parse_args([cmd] + (["x.png"] if cmd == "analyze" else ["t.json"] if cmd == "replay" else ["f.jsonl"] if cmd == "fit" else []))


if __name__ == "__main__":
    unittest.main()
