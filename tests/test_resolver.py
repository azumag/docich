import sys
import json
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.adapters.base import AdapterError  # noqa: E402
from docich.agent import brains  # noqa: E402
from docich.resolver import resolver_policy, strategy_path  # noqa: E402
from docich.resolver import robots  # noqa: E402

# 実機（VM の docich-game-g16:robots ペイン）から capture-pane した実際の盤面
REAL_PANE = """\
+-----------------------------------------------------------+ Directions:
|                                                           |
|               +                                 +         | y k u
|                                                           |  \\|/
|                                                           | h- -l
|                                                           |  /|\\
|                                                           | b j n
|                                            +  +           |
|                                                           | Commands:
|                                                           |
|  +                                                        | w:  wait for end
|                                                           | t:  teleport
|                 +                                         | q:  quit
|            +                                              | ^L: redraw screen
|                                                           |
|                   +              +               @        |
|                                                           | +:  robot
|                                                           | *:  junk heap
|                                                           | @:  you
|                                                           |
|                 +                                         |
+-----------------------------------------------------------+ Score: 0
"""


def _pane(rows: list[str], extra: str = "") -> str:
    """Build pane text: bordered board rows + right panel, like the real pane."""
    width = max([len(r) for r in rows] + [5])
    body = [r.ljust(width) for r in rows]
    while len(body) < 3:
        body.append(" " * width)
    lines = ["+" + "-" * width + "+ Directions:"]
    for r in body:
        lines.append("|" + r + "| " + extra)
    lines.append("+" + "-" * width + "+ Score: 0")
    return "\n".join(lines)


class ParseTest(unittest.TestCase):
    def test_parse_real_pane(self):
        board = robots.parse_board(REAL_PANE)
        self.assertIsNotNone(board)
        self.assertEqual(len(board.robots), 10)
        # expected coords computed from the fixture itself (robust to retyping)
        lines = REAL_PANE.splitlines()
        at_line = next(l for l in lines if "@" in l)
        expected = (at_line.index("@") - 1, next(i for i, l in enumerate(lines) if "@" in l) - 1)
        self.assertEqual(board.player, expected)
        self.assertEqual(board.score, 0)
        self.assertFalse(board.game_over)

    def test_non_board_returns_none(self):
        self.assertIsNone(robots.parse_board("ffmpeg log\nnative=1204x724 output=960x540\n"))

    def test_game_over_and_score_helpers(self):
        self.assertTrue(robots.game_over("Somebody got you.  Another game? (y or n)"))
        self.assertEqual(robots.score_from_text("Score: 123"), 123)
        self.assertIsNone(robots.score_from_text("no score here"))


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.decide = resolver_policy("robots")

    def test_game_over_precedence_over_unparseable(self):
        # transition frames drop the '@' but the prompt must still be answered
        text = "Teleport!\nSomebody got you.  Another game? (y or n)"
        self.assertEqual(self.decide(text, None), ["y"])

    def test_game_over_restarts(self):
        text = _pane(["       @  "], extra="Another game? (y or n)")
        self.assertEqual(self.decide(text, None), ["y"])

    def test_no_robots_waits(self):
        self.assertEqual(self.decide(_pane(["      @      "]), None), ["."])

    def test_unparseable_does_nothing(self):
        self.assertEqual(self.decide("hello world", None), [])

    def test_never_moves_into_robot(self):
        # robot immediately to the right of the player: 'l' would be suicide
        rows = ["       ", "     @+  "]
        key = self.decide(_pane(rows), None)[0]
        self.assertNotEqual(key, "l")

    def test_never_waits_when_adjacent_robot_closes_in(self):
        # robot adjacent right: waiting lets it step onto the player
        rows = ["       ", "     @+  "]
        self.assertNotEqual(self.decide(_pane(rows), None)[0], robots.WAIT_KEY)

    def test_trapped_teleports(self):
        rows = ["+++", "+@+", "+++"]
        self.assertEqual(self.decide(_pane(rows), None), ["t"])

    def test_wait_scores_collision(self):
        # two robots one cell apart converge onto the same cell while we wait
        rows = [
            "    ",
            "    +",
            "@   +",
            "    ",
        ]
        key = self.decide(_pane(rows), None)[0]
        board = robots.parse_board(_pane(rows))
        dx, dy = (0, 0) if key == robots.WAIT_KEY else robots.DIRECTIONS[key]
        target = (board.player[0] + dx, board.player[1] + dy)
        _, killed, hits = robots._simulate(board.robots, set(board.junk), target)
        self.assertEqual(hits, 0)
        self.assertEqual(killed, 2)

    def test_lure_bonus_steers_toward_junk_path(self):
        # robot 5 cells right, junk on its pursuit line: a huge w_lure must
        # make the player hold the line (wait/h/l all keep the robot's path
        # through the junk); any off-line move would lose the lure bonus
        rows = ["              ", "     @   * +  ", "              "]
        key = self.decide(_pane(rows), {"w_lure": 1000.0})[0]
        self.assertIn(key, (robots.WAIT_KEY, "h", "l"))

    def test_endgame_approach_walks_to_kill_spot(self):
        # one robot, junk on its pursuit line: with distance terms disabled a
        # huge w_approach must walk the player toward the beyond-junk cell
        rows = ["                     ", "     @     *    +    ", "                     "]
        key = self.decide(
            _pane(rows), {"w_dist": 0.0, "w_lure": 0.0, "w_approach": 100.0}
        )[0]
        self.assertEqual(key, "l")

    def test_endgame_wait_commits_on_kill_spot(self):
        # player already on the kill spot (robot 3 away, junk between):
        # waiting must win so the robot steps onto the junk next turn
        rows = ["                  ", "                 @* +", "                  "]
        self.assertEqual(self.decide(_pane(rows), None)[0], robots.WAIT_KEY)

    def test_junk_adjacency_bias(self):
        # a huge w_junk must make the chosen cell junk-adjacent (lure stance)
        rows = [
            "  * *         ",
            "   @        + ",
            "              ",
        ]
        key = self.decide(_pane(rows), {"w_junk": 100000.0})[0]
        board = robots.parse_board(_pane(rows))
        junk = set(board.junk)
        dx, dy = (0, 0) if key == robots.WAIT_KEY else robots.DIRECTIONS[key]
        target = (board.player[0] + dx, board.player[1] + dy)
        jadj = sum(
            1
            for ddx, ddy in robots.DIRECTIONS.values()
            if (target[0] + ddx, target[1] + ddy) in junk
        )
        self.assertGreaterEqual(jadj, 1)

    def test_strategy_none_uses_defaults(self):
        # same collision board with w_wait disabled: wait is no longer forced
        rows = ["    ", "    +", "@   +", "    "]
        key = self.decide(_pane(rows), {"w_wait": -1000.0})[0]
        self.assertNotEqual(key, robots.WAIT_KEY)


class ResolverBrainTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.g = config.load_global(self.repo_root)
        self.game = config.GameConfig(
            name="robots",
            title="Robots",
            adapter="cli",
            raw={},
            agent=config.GameAgentConfig(brain="resolver"),
            path=self.repo_root / "config" / "games" / "robots.toml",
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_build_brain_resolver(self):
        brain = brains.build_brain(self.g, self.game)
        self.assertIsInstance(brain, brains.ResolverBrain)

    def test_brain_holds_restart_while_draining(self):
        import json

        brain = brains.build_brain(self.g, self.game)
        s_file = strategy_path(self.g.state_dir, "robots")
        s_file.parent.mkdir(parents=True, exist_ok=True)
        (Path(self.g.state_dir) / "game_switch.json").write_text(
            json.dumps({"phase": "draining"}), encoding="utf-8"
        )

        class Obs:
            adapter = "cli"
            text = _pane(["      @      "], extra="Another game? (y or n)")

        # The game-over prompt belongs to the boundary waiter while
        # draining: the brain must not consume it with 'y'.
        self.assertEqual(brain.decide(Obs()), [])

    def test_brain_allows_y_movement_while_draining(self):
        import json

        brain = brains.build_brain(self.g, self.game)
        brain.policy = lambda text, strategy: ["y"]
        state_file = Path(self.g.state_dir) / "game_switch.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"phase": "draining"}), encoding="utf-8")

        class Obs:
            adapter = "cli"
            text = _pane(["  @       +  "])

        # BSD robots uses y for a normal up-left move too.  Only a y produced
        # for the actual end-of-match prompt may be held during draining.
        self.assertEqual([a.text for a in brain.decide(Obs())], ["y"])

    def test_brain_restarts_when_not_draining(self):
        import json

        brain = brains.build_brain(self.g, self.game)

        class Obs:
            adapter = "cli"
            text = _pane(["      @      "], extra="Another game? (y or n)")

        # No coordinator state (or a settled phase): restart as usual.
        self.assertEqual([a.text for a in brain.decide(Obs())], ["y"])
        state_file = Path(self.g.state_dir) / "game_switch.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"phase": "ready"}), encoding="utf-8")
        self.assertEqual([a.text for a in brain.decide(Obs())], ["y"])

    def test_brain_reads_strategy_file_and_hot_reloads(self):
        brain = brains.build_brain(self.g, self.game)
        s_file = strategy_path(self.g.state_dir, "robots")
        # far robot: waiting is safe; default weights may prefer fleeing
        far_rows = ["         ", "    @   +", "         "]

        class Obs:
            adapter = "cli"
            text = _pane(far_rows)

        before = [a.text for a in brain.decide(Obs())]
        self.assertEqual(len(before), 1)

        s_file.parent.mkdir(parents=True, exist_ok=True)
        s_file.write_text('{"w_wait": 10000.0}\n', encoding="utf-8")
        time.sleep(0.01)  # mtime 解像度より十分大きい
        # a huge wait bonus must flip the (safe) choice to waiting
        self.assertEqual([a.text for a in brain.decide(Obs())], ["."])
        self.assertNotEqual(before, ["."])

    def test_brain_ignores_corrupt_strategy_file(self):
        brain = brains.build_brain(self.g, self.game)
        s_file = strategy_path(self.g.state_dir, "robots")
        s_file.parent.mkdir(parents=True, exist_ok=True)
        s_file.write_text("{not json", encoding="utf-8")

        class Obs:
            adapter = "cli"
            text = _pane(["      @      "])  # no robots: default policy waits

        self.assertEqual([a.text for a in brain.decide(Obs())], ["."])

    def test_unknown_game_raises(self):
        with self.assertRaises(AdapterError):
            resolver_policy("unknown_game")


class GnurobotsResolverTest(unittest.TestCase):
    def test_render_substitutes_all_weights(self):
        from docich.resolver.gnurobots import render

        text = render(None)
        self.assertNotIn("@", text)
        self.assertIn("(define food-threshold 400)", text)
        self.assertIn("(define move-budget 12)", text)
        custom = render({"food_urgency": 250.4, "wander_turn_one_in": 9.0})
        self.assertIn("(define food-threshold 250)", custom)
        self.assertIn("(define wander-turn-one-in 9)", custom)
        self.assertIn("(define move-budget 12)", custom)

    def test_parse_statistics(self):
        from docich.resolver.gnurobots import parse_statistics

        stats = parse_statistics(
            "-----------------------STATISTICS-----------------------\n"
            "Shields: 100\nEnergy: 512\nScore: 640\n"
        )
        self.assertEqual(stats, {"score": 640, "energy": 512, "shields": 100})
        self.assertIsNone(parse_statistics("no stats here")["score"])

    def test_read_strategy_for_game_uses_game_defaults(self):
        import tempfile

        from docich.resolver.improve import read_strategy_for_game

        with tempfile.TemporaryDirectory() as td:
            st = read_strategy_for_game("gnurobots", Path(td) / "missing.json")
            self.assertEqual(st["food_urgency"], 400.0)
            self.assertNotIn("w_collision", st)  # robots既定は混入しない
            st2 = read_strategy_for_game("robots", Path(td) / "missing.json")
            self.assertIn("w_collision", st2)


class PerturbTest(unittest.TestCase):
    def test_perturb_changes_one_numeric_weight(self):
        from docich.resolver.improve import perturb

        rng = __import__("random").Random(7)
        base = dict(robots.DEFAULT_STRATEGY)
        out = perturb(base, rng)
        changed = [k for k in base if base[k] != out[k]]
        self.assertEqual(len(changed), 1)
        self.assertIn(changed[0], {k for k, v in base.items() if isinstance(v, (int, float)) and not isinstance(v, bool)})

    def test_perturb_keeps_bool_flags(self):
        from docich.resolver.improve import perturb

        rng = __import__("random").Random(1)
        base = dict(robots.DEFAULT_STRATEGY)
        self.assertEqual(base["teleport_when_trapped"], perturb(base, rng)["teleport_when_trapped"])


class ImprovementLifecycleFenceTest(unittest.TestCase):
    def test_only_one_restart_claims_stale_marker(self):
        from docich.resolver import improve
        from docich.resolver.runner import EvaluationCleanupError

        with tempfile.TemporaryDirectory() as td:
            marker=Path(td)/"resolver"/"active"/"robots.json";marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"pid":999999,"game":"robots","generation":1,"lease_id":"old","sessions":[]}))
            code=("import os,sys,time;from pathlib import Path;"
                  "from docich.resolver.improve import _claim_activity_marker;"
                  "p=Path(sys.argv[1]);_claim_activity_marker(p,{'pid':os.getpid(),'game':'robots','generation':2,'lease_id':'winner','sessions':[]});time.sleep(10)")
            child=subprocess.Popen([sys.executable,"-c",code,str(marker)],env={**__import__("os").environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1]/"src")})
            try:
                for _ in range(100):
                    if json.loads(marker.read_text()).get("lease_id")=="winner": break
                    time.sleep(0.02)
                with self.assertRaises(EvaluationCleanupError):
                    improve._claim_activity_marker(marker,{"pid":__import__("os").getpid(),"game":"robots","generation":2,"lease_id":"loser","sessions":[]})
                self.assertEqual(json.loads(marker.read_text())["lease_id"],"winner")
            finally:
                child.terminate();child.wait(timeout=5)

    def test_restart_does_not_overwrite_orphaned_session_marker(self):
        from docich.resolver import improve
        from docich.resolver.runner import EvaluationCleanupError

        with tempfile.TemporaryDirectory() as td:
            marker=Path(td)/"resolver"/"active"/"robots.json"
            marker.parent.mkdir(parents=True)
            previous={"pid":999999,"game":"robots","generation":1,"lease_id":"old","sessions":["evalr-999999-1"]}
            marker.write_text(__import__("json").dumps(previous))
            running=SimpleNamespace(returncode=0,stderr="",stdout="")
            with mock.patch.object(improve.subprocess,"run",return_value=running):
                with self.assertRaises(EvaluationCleanupError):
                    improve._claim_activity_marker(marker,{"pid":1,"game":"robots","generation":2,"lease_id":"new","sessions":[]})
            self.assertEqual(__import__("json").loads(marker.read_text()),previous)

    def test_runner_persists_session_intent_before_spawn(self):
        from docich.resolver import runner

        completed=SimpleNamespace(returncode=0,stderr="",stdout="")
        with mock.patch.object(runner,"_tmux",return_value=completed) as tmux:
            with self.assertRaisesRegex(OSError,"marker"):
                runner.run_match(["game"],80,24,lambda _text:[],session_hook=lambda *_: (_ for _ in ()).throw(OSError("marker")))
        self.assertFalse(any(call.args[0][0]=="new-session" for call in tmux.call_args_list))

    def test_tmux_observation_error_is_not_absence(self):
        from docich.resolver import runner

        with self.assertRaises(runner.EvaluationCleanupError):
            runner._session_absent(SimpleNamespace(returncode=1,stderr="failed to connect to server"))

    def test_daemon_retains_marker_after_cleanup_failure(self):
        from docich.resolver import improve
        from docich.resolver.runner import EvaluationCleanupError

        with tempfile.TemporaryDirectory() as td:
            g=SimpleNamespace(state_dir=Path(td))
            with mock.patch.object(improve,"active_runtime_identity",return_value=("robots",1,"lease")), \
                 mock.patch.object(improve,"improve_once",side_effect=EvaluationCleanupError("session remains")):
                improve.run_daemon(g,"robots",cycle_s=60,matches=1,candidates=1,margin_pct=1)
            marker=Path(td)/"resolver"/"active"/"robots.json"
            self.assertTrue(marker.exists())
            self.assertIn("cleanup_error",marker.read_text())

    def test_draining_runtime_is_not_active(self):
        from docich.resolver import improve

        store=SimpleNamespace(canonical=SimpleNamespace(load=lambda: ({"phase":"draining","active":{"game":"robots","generation":4,"lease_id":"lease"}},False)))
        with mock.patch("docich.game_switch.GameSwitchStore",return_value=store):
            self.assertIsNone(improve.active_runtime_identity(SimpleNamespace(state_dir="unused")))

    def test_ready_runtime_identity_includes_generation_and_lease(self):
        from docich.resolver import improve

        store=SimpleNamespace(canonical=SimpleNamespace(load=lambda: ({"phase":"ready","active":{"game":"robots","generation":4,"lease_id":"lease"}},False)))
        with mock.patch("docich.game_switch.GameSwitchStore",return_value=store):
            self.assertEqual(improve.active_runtime_identity(SimpleNamespace(state_dir="unused")),("robots",4,"lease"))

    def test_improve_once_refuses_stale_runtime_before_evaluation(self):
        from docich.resolver import improve

        g=SimpleNamespace(state_dir=Path(tempfile.mkdtemp()))
        with mock.patch.object(improve,"active_runtime_identity",return_value=("robots",5,"new")), \
             mock.patch.object(improve,"evaluate") as evaluate:
            with self.assertRaises(improve.ImprovementLeaseLost):
                improve.improve_once(g,"robots",expected_identity=("robots",4,"old"))
        evaluate.assert_not_called()

    def test_runner_kills_evaluation_session_when_lease_is_lost(self):
        from docich.resolver import improve, runner

        def command(args):
            return SimpleNamespace(returncode=1 if args[0]=="has-session" else 0,stderr="can't find session" if args[0]=="has-session" else "",stdout="")
        with mock.patch.object(runner,"_tmux",side_effect=command) as tmux:
            with self.assertRaises(improve.ImprovementLeaseLost):
                runner.run_match(["game"],80,24,lambda _text:[],interval_s=0,guard=lambda: (_ for _ in ()).throw(improve.ImprovementLeaseLost("changed")))
        kills=[call for call in tmux.call_args_list if call.args[0][:2]==["kill-session","-t"]]
        self.assertEqual(len(kills),2)

    def test_runner_reports_evaluation_session_cleanup_failure(self):
        from docich.resolver import runner

        completed=SimpleNamespace(returncode=0,stderr="",stdout="")
        with mock.patch.object(runner,"_tmux",return_value=completed):
            with self.assertRaisesRegex(RuntimeError,"停止に失敗"):
                runner.run_match(["game"],80,24,lambda _text:[],interval_s=0,guard=lambda: (_ for _ in ()).throw(RuntimeError("stop")))


if __name__ == "__main__":
    unittest.main()
