import json
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]

from docich.soren_corner_stats import load_active_corner
from docich import soren_corner_text as sd


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _epoch(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class DocichCornerStatsTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def _state(self, name: str, **extra) -> None:
        value = {"schema_version": 1, "status": "active", **extra}
        _write_json(self.root / name, value)

    def test_idle_corner_keeps_normal_mode_and_does_not_read_soren_history(self):
        (self.root / "score_history.txt").write_text("999999\n", encoding="utf-8")
        self._state("retro_corner.json", status="completed", game="robots")
        self.assertIsNone(load_active_corner(self.root))

    def test_retro_history_keeps_same_game_across_visits_and_counts_session(self):
        started = "2026-09-21T00:00:00+00:00"
        self._state(
            "retro_corner.json",
            game="ninvaders",
            started_at=started,
            target_matches=3, rotation_runtime_id="g1",
        )
        _write_json(self.root / "game_switch.json", {"phase":"ready", "active":{"game":"ninvaders","runtime_id":"g1"}})
        log = self.root / "scores/ninvaders.jsonl"
        log.parent.mkdir(parents=True)
        rows = [
            {"ts": _epoch("2026-09-20T23:59:00"), "game": "ninvaders", "score": 100},
            {"ts": _epoch("2026-09-21T00:01:00"), "game": "ninvaders", "score": 10},
            {"ts": _epoch("2026-09-21T00:02:00"), "game": "ninvaders", "score": 20},
            {"ts": _epoch("2026-09-21T00:03:00"), "game": "ninvaders", "score": 30},
        ]
        log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        _write_json(
            self.root / "resolver/unused.json",
            {"not": "used"},
        )
        improve = self.root / "resolver/improve_log.jsonl"
        improve.parent.mkdir(parents=True, exist_ok=True)
        improve.write_text(
            json.dumps(
                {
                    "game": "ninvaders",
                    "promoted": True,
                    "best_strategy_key": "not-used-directly",
                    "trials": [{"strategy": {"mode": "safe"}, "mean_score": 20}],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        corner = load_active_corner(self.root)
        self.assertEqual(corner["kind"], "retro")
        self.assertEqual([row["score"] for row in corner["scores"]], [100, 10, 20, 30])
        self.assertEqual(corner["session_matches"], 3)
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("Last8: 100 10 20 30", rendered)
        self.assertIn("this corner matches 3/3", rendered)
        self.assertIn("mean=40 median=25", rendered)
        self.assertIn("Strategy: #1", rendered)
        self.assertNotIn("999999", rendered)

    def test_paper_corner_exposes_rich_allowlisted_status_not_game_score(self):
        self._state("paper_corner.json")
        _write_json(
            self.root / "trading/status.json",
            {
                "worker_state": "paper_worker_running",
                "capital_reference": "10000",
                "deployed_reference": "3000",
                "open_positions": {"btc_jpy": "0.001"},
                "recent_fills": [
                    {"symbol": "btc_jpy", "side": "buy", "quote_notional": "3000"},
                    {"symbol": "btc_jpy", "side": "sell", "quote_notional": "3100"},
                ],
                "snapshot_generated_at": 190,
                "heartbeat_at": 198,
                "market_count": 4,
                "eligible_symbols": ["btc_jpy", "eth_jpy", "sol_jpy", "xrp_jpy"],
                "signal_summary": {
                    "candidate_count": 3,
                    "selected_count": 1,
                    "rejected_count": 2,
                    "strategy_ids": ["momentum-v1", "relative-value-v1"],
                    "candidate_reason_codes": ["momentum_breakout"],
                    "candidate_symbols": ["btc_jpy", "eth_jpy", "sol_jpy"],
                    "selected_symbols": ["btc_jpy"],
                    "private_secret": "DO_NOT_SHOW",
                },
                "skipped_decisions": [
                    {"symbol": "eth_jpy", "side": "buy", "reason_code": "capital_limit"}
                ],
                "worker_summary": {
                    "cycle_index": 42,
                    "last_success_at": 189,
                    "next_cycle_at": 205,
                    "frame_error_count": 1,
                    "arbitrage_candidate_count": 2,
                    "new_fill_count": 1,
                    "new_settlement_count": 3,
                    "experiment_status": "active",
                    "experiment_reason_code": "shadow",
                    "experiment_entries_allowed": False,
                    "error_codes": ["frame_fetch_error"],
                    "detail": "DO_NOT_SHOW",
                },
                "market_freshness": {
                    "btc_jpy": {"quality": "fresh", "reason_code": "ok"},
                    "eth_jpy": {"quality": "stale", "reason_code": "stale_data",
                                "raw": "DO_NOT_SHOW"},
                },
                "coverage": {
                    "attempted": 2,
                    "total": 2,
                    "budget_exceeded": False,
                    "carried_symbols": [],
                },
                "performance_summary": {
                    "as_of": 199,
                    "complete": True,
                    "position_count": 1,
                    "priced_positions": 1,
                    "valued_positions": 1,
                    "realized_total_jpy": "120.5",
                    "today_realized_pnl_jpy": "20",
                    "unrealized_pnl_jpy": "-5.5",
                    "cumulative_pnl_jpy": "115",
                    "equity_jpy": "10115",
                    "positions": [{"avg_cost": "DO_NOT_SHOW"}],
                },
                "unknown_private_field": "DO_NOT_SHOW",
            },
        )
        corner = load_active_corner(self.root, now=200)
        lines = sd.render_docich_corner_stats(corner)
        rendered = "\n".join(lines)
        self.assertIn("SOREN/CORNER: PAPER / PAPER /", rendered)
        self.assertIn("Funds: capital=10000 free=7000", rendered)
        self.assertIn("Deployed: 3000", rendered)
        self.assertIn("P/L: cum=+115 unreal=-5.5", rendered)
        self.assertIn("Realized: today=+20 all=+120.5", rendered)
        self.assertIn("Equity: 10115 / valued=1/1 / age=1s", rendered)
        self.assertIn("Signals: candidate=3 selected=1 rejected=2", rendered)
        self.assertIn("btc_jpy -> SELECTED", rendered)
        self.assertIn("eth_jpy -> REJECTED:capital_limit", rendered)
        self.assertIn("sol_jpy -> candidate", rendered)
        self.assertIn("xrp_jpy -> no signal", rendered)
        self.assertIn("Strategy: momentum-v1 / relative-value-v1", rendered)
        self.assertIn("Rejected: eth_jpy:capital_limit", rendered)
        self.assertIn("Worker: running cycle=42", rendered)
        self.assertIn("Age: data=10s hb=2s next=5s", rendered)
        self.assertIn("Markets: fresh=1/2 frame_err=1", rendered)
        self.assertIn("Cycle: arb=2 fills=1 settlements=3", rendered)
        self.assertIn("Experiment: status=active entries=off reason=shadow", rendered)
        self.assertIn("Market issue: eth_jpy:stale_data", rendered)
        self.assertIn("Errors: frame_fetch_error", rendered)
        self.assertIn("btc_jpy SELL 3100", rendered)
        self.assertIn("btc_jpy BUY 3000", rendered)
        self.assertNotIn("Score Timeline", rendered)
        self.assertNotIn("DO_NOT_SHOW", rendered)
        for line in lines:
            self.assertLessEqual(sd.ansi_display_width(line), sd.W)

    def test_paper_corner_keeps_legacy_minimal_status_compatible(self):
        self._state("paper_corner.json")
        _write_json(
            self.root / "trading/status.json",
            {
                "worker_state": "running",
                "capital_reference": "10000",
                "deployed_reference": "3000",
                "open_positions": {},
                "recent_fills": [],
            },
        )
        rendered = "\n".join(sd.render_docich_corner_stats(load_active_corner(self.root, now=200)))
        self.assertIn("Funds: capital=10000 free=7000", rendered)
        self.assertIn("Deployed: 3000", rendered)
        self.assertIn("Signals: candidate=-- selected=-- rejected=--", rendered)
        self.assertIn("(no fills yet)", rendered)

    def test_nethack_corner_uses_run_history(self):
        self._state(
            "nethack_corner.json",
            game="nethack",
            run_id="00000000-0000-4000-8000-000000000001",
            run_status="active",
            run_score=42,
            run_turns=120,
            run_max_depth=4,
        )
        _write_json(self.root / "nethack/current.json", {"schema_version": 1, "run_id": "00000000-0000-4000-8000-000000000001"})
        _write_json(self.root / "nethack/runs/00000000-0000-4000-8000-000000000001.json", {"run_id": "00000000-0000-4000-8000-000000000001", "expedition": 7, "status": "active", "score": 42, "turns": 120, "max_depth": 4})
        _write_json(self.root / "nethack/runs/old.json", {"score": 12, "status": "dead", "last_finished_at": "2026-09-20T00:00:00+00:00"})
        corner = load_active_corner(self.root)
        self.assertEqual(corner["scores"], [12])
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("expedition 7", rendered)
        self.assertIn("score=42", rendered)

    def test_soren91_reads_confirmed_rank_history(self):
        self._state("soren91_corner.json", game="soren91", started_at=200)
        for i, rank in enumerate([12, 4, 1, None, -1, 92, True]):
            _write_json(self.root / f"soren91/tmp/summaries/game_{i:04}.json",
                        {"rank": rank, "timestamp": 100 + i * 100})
        corner = load_active_corner(self.root, soren_root=self.root)
        self.assertEqual([r["score"] for r in corner["scores"]], [12, 4, 1])
        self.assertEqual(corner["session_matches"], 2)
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("Rank Timeline", rendered)
        self.assertNotIn("Rank Distribution", rendered)
        self.assertIn("best=1", rendered)
        self.assertIn("Recent30=", rendered)
        self.assertIn("wins=1 / lower rank is better", rendered)
        plot, colors = sd._render_timeline_grid([50, 1], 5, 3, 1, 50, lower_is_better=True)
        self.assertEqual(plot[0][-1], "*")
        self.assertEqual(colors[0][-1], sd.gradient_color(50, 1, 50))

    def test_jev_reads_own_reports_not_mixed_soren_history(self):
        self._state("jev_corner.json", game="sorengame", policy="jev", player_generation=3)
        (self.root / "score_history.txt").write_text("999999\n")
        _write_json(self.root / "tmp/jev_player/runs/run-1/report.json",
                    {"status": "completed", "finished_at": 100, "summary": {"score": 80}})
        corner = load_active_corner(self.root, soren_root=self.root)
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("policy=jev generation=3", rendered)
        self.assertIn("1 reports / best=80", rendered)
        self.assertIn("Recent30=80", rendered)
        self.assertIn("may include interrupted runs", rendered)
        self.assertNotIn("Score Distribution", rendered)
        self.assertNotIn("999999", rendered)

    def test_compact_corner_rank_trend_treats_lower_as_better(self):
        scores = ([10] * 30) + ([5] * 30)
        rendered = "\n".join(sd._corner_score_panels(scores, rank=True, compact=True))
        self.assertIn("Trend: -5.0 vs previous 30 / better", rendered)
        self.assertIn("Rank Timeline", rendered)
        self.assertNotIn("Rank Distribution", rendered)

    def test_multiple_active_corners_fail_closed(self):
        self._state("retro_corner.json", game="robots")
        self._state("paper_corner.json")
        corner = load_active_corner(self.root)
        self.assertEqual(corner["kind"], "conflict")
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("STATE CONFLICT", rendered)
        self.assertNotIn("Score Distribution", rendered)

    def test_retro_filters_other_games_and_bad_rows_but_keeps_long_history(self):
        self._state("retro_corner.json", game="robots", started_at=200)
        log = self.root / "scores/robots.jsonl"
        log.parent.mkdir()
        rows = [{"game": "robots", "score": i, "ts": i} for i in range(250)]
        rows += [{"game": "other", "score": 999999, "ts": 300},
                 {"game": "robots", "score": True}, {"game": "robots", "score": "NaN"}]
        log.write_text("\n".join(json.dumps(row) for row in rows) + "\n{partial")
        corner = load_active_corner(self.root)
        self.assertEqual(len(corner["scores"]), 250)
        self.assertEqual(corner["session_matches"], 50)
        text = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("250 results / best=249", text)
        self.assertIn("mean=124.5 median=124.5", text)
        self.assertIn("(last 100 games; old -> now)", text)
        self.assertNotIn("999999", text)

    def test_nethack_completed_current_run_is_counted_once(self):
        run_id = "00000000-0000-4000-8000-000000000001"
        self._state("nethack_corner.json", run_id=run_id)
        _write_json(self.root / "nethack/current.json", {"schema_version": 1, "run_id": run_id})
        _write_json(self.root / f"nethack/runs/{run_id}.json",
                    {"run_id": run_id, "expedition": 7, "status": "dead", "score": 42,
                     "last_finished_at": 200})
        _write_json(self.root / "nethack/runs/older.json",
                    {"status": "ascended", "score": 100, "last_finished_at": 100})
        _write_json(self.root / "nethack/runs/suspended.json",
                    {"status": "suspended", "score": 123456})
        corner = load_active_corner(self.root)
        self.assertEqual(corner["scores"], [100, 42])
        self.assertEqual(corner["run"]["expedition"], 7)

    def test_nethack_does_not_read_a_different_current_run(self):
        self._state("nethack_corner.json", run_id="expected", run_score=10)
        _write_json(self.root / "nethack/current.json", {"run_id": "../../unrelated"})
        self.assertEqual(load_active_corner(self.root)["run"]["score"], 10)

    def test_distribution_covers_small_large_negative_and_constant_scores(self):
        for scores in ([0, 1, 2, 3, 4, 5], [100000, 200000], [-10, -5, 0], [7, 7]):
            with self.subTest(scores=scores):
                lines = sd.render_corner_distribution(scores)
                counts = [int(re.sub(r"\x1b\[[0-9;]*m", "", line).split()[-1]) for line in lines[1:]]
                self.assertEqual(sum(counts), len(scores))
                self.assertLessEqual(max(sd.ansi_display_width(line) for line in lines), sd.W)
        small = "\n".join(sd.render_corner_distribution([0, 1, 2, 3, 4, 5]))
        self.assertNotIn("500", small)

    def test_one_or_two_results_are_visible_and_empty_is_explicit(self):
        for scores in ([1], [1, 2]):
            text = "\n".join(sd._corner_score_panels(scores))
            self.assertIn("Score Timeline", text)
            self.assertNotIn("not enough data", text)
            self.assertIn("*", text)
        self.assertIn("no completed results", "\n".join(sd._corner_score_panels([])))




    def test_bad_active_schema_fails_closed(self):
        _write_json(self.root / "retro_corner.json", {"schema_version": 99, "status": "active", "game": "robots"})
        corner = load_active_corner(self.root)
        self.assertEqual(corner["kind"], "invalid")
        self.assertIn("STATE INVALID", "\n".join(sd.render_docich_corner_stats(corner)))

    def test_retro_render_stays_within_dashboard_width(self):
        self._state("retro_corner.json", game="moon-buggy", target_matches=3)
        log = self.root / "scores/moon-buggy.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(
            "\n".join(json.dumps({"ts": 10 + i, "game": "moon-buggy", "score": i * 100}) for i in range(1, 8)),
            encoding="utf-8",
        )
        corner = load_active_corner(self.root)
        for line in sd.render_docich_corner_stats(corner):
            self.assertLessEqual(sd.ansi_display_width(line), sd.W)



if __name__ == "__main__":
    unittest.main()

class HanjukuStatusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.identity = dict(game='hanjuku-hero', runtime_id='g1-abcdef12', generation=1, lease_id='lease')
        self.runtime = self.root / 'runtimes' / self.identity['runtime_id']
        self.state = dict(schema_version=1, status='active', game='hanjuku-hero', bot_identity=self.identity)
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        import time
        _write_json(self.root / 'retro_corner.json', self.state)
        _write_json(self.root / 'game_switch.json', dict(phase='ready', active=self.identity))
        _write_json(self.runtime / 'hanjuku_run.json', dict(self.identity, observed_at=time.time(), phase='field', actions_sent=7))
        _write_json(self.runtime / 'hanjuku_bot.json', dict(decision_trace=self.identity, screen_kind='world_map', policy=dict(chapter=2, gold=123, captured=['城'], active='F3', stats=dict(wins=2, losses=1))))

    def snapshot(self):
        return load_active_corner(self.root)

    def test_measured_gap_is_fenced_and_observation_deadlines_are_preserved(self):
        import time
        now=time.time()
        projection=dict(align='right',viewport=[0,90,960,540],content=[239,0,721,540])
        _write_json(self.runtime / 'presentation.json', dict(status='ready', projection=projection))
        bot=json.loads((self.runtime / 'hanjuku_bot.json').read_text())
        names=['長い城の名前'+str(i) for i in range(10)]
        bot['policy'].update(captured=names, tick=100,
            garrison={'アルマムーン':['長い将軍の名前です','ゼウス'], '古い城':['どうし']},
            garrison_observed_at={'アルマムーン':now-5,'古い城':now-31},
            active='J3',
            orders={'J3':'launched'},
            launched_orders={'J3': {'step':'J3','general':'どうし','source':'アルマムーン',
                                     'target':'ナキューメラ','purpose':'attack',
                                     'note':'ナキューメラの敵を討つ'},
                             'J4': {'note':'ジュメルズを厚くする'}},
            sorties={'J4': {'general':'パプリカ','source':'カルーン','target':'ジュメルズ',
                            'status':'en_route','tick':90},
                     'J5': {'general':'キッシュ','source':'アルマムーン','planned_target':'ジュメルズ',
                            'status':'overdue','tick':95}},
            battle=dict(
                enemy='シェーブル', ally='どうし', enemy_hp=55, ally_hp=70,
                hp_observed_at=now-3, enemy_soldiers=4, ally_soldiers=7,
                card_soldiers_current=True, side='attack', castle='ナキューメラ',
                step='J3', strategy_variant='retry_with_opening_cards',
                deviation_reason='敵の卵使用を避けるため、致死確認できる切り札を優先',
                planned_cards=['ゼンマイン','クースカン'],
                cards_used=['ゼンマイン'], cards_selected=['クースカン'],
                card_flow={'card':'クースカン','stage':'menu'},
                okunote_egg_preempt={'stage':'selected','exhausted':False},
                egg_battle=False))
        _write_json(self.runtime / 'hanjuku_bot.json',bot)
        value=self.snapshot()['hanjuku']; gap=value['gap']
        self.assertEqual((gap['left'],gap['width'],gap['side']),(0,239,'left'))
        self.assertEqual(gap['captured_names'],names)
        self.assertEqual([g['castle'] for g in gap['garrison']],['アルマムーン'])
        self.assertAlmostEqual(gap['garrison'][0]['until'],now+25)
        self.assertAlmostEqual(gap['hp']['until'],now+7)
        battle=gap['battle']
        self.assertEqual((battle['enemy_soldiers'],battle['ally_soldiers']),(4,7))
        self.assertEqual((battle['side'],battle['castle'],battle['step']),
                         ('attack','ナキューメラ','J3'))
        self.assertEqual(battle['planned_cards'],['ゼンマイン','クースカン'])
        self.assertEqual(battle['used_cards'],['ゼンマイン'])
        self.assertEqual((battle['card'],battle['card_stage']),('クースカン','menu'))
        self.assertEqual(battle['egg_guard_stage'],'selected')
        self.assertEqual(gap['active_order'], {
            'step':'J3','general':'どうし','source':'アルマムーン',
            'target':'ナキューメラ','purpose':'attack','status':'launched',
            'note':'ナキューメラの敵を討つ'})
        self.assertEqual(value['active_order'], gap['active_order'])
        self.assertEqual(gap['moves'][0]['general'], 'パプリカ')
        self.assertEqual((gap['moves'][0]['source'], gap['moves'][0]['target']),
                         ('カルーン', 'ジュメルズ'))
        self.assertEqual(gap['moves'][0]['status'], 'en_route')
        self.assertEqual(gap['march_count'], 2)
        self.assertEqual(gap['moves'][1]['status'], 'tracking_unknown')
        self.assertEqual((gap['moves'][1]['source'], gap['moves'][1]['target']),
                         ('アルマムーン', 'ジュメルズ'))
        text='\n'.join(sd.fit_dashboard_lines(sd.render_hanjuku_status(value),width=20))
        self.assertIn('余白出撃: パプリカ / カルーン→ジュメルズ / 行軍中', text)
        self.assertIn('余白進軍数: 2', text)
        self.assertIn('余白出撃: キッシュ / アルマムーン→ジュメルズ / 到着未確認（追跡期限超過）', text)
        self.assertIn(' / '.join(names),text)
        self.assertIn('長い将軍の名前です',text)
        self.assertIn('余白交戦兵数:',text)
        self.assertIn('敵 4 / 我 7',text)
        self.assertIn('攻撃 ナキューメラ / 段階 J3 / 方針 retry_with_opening_cards',text)
        self.assertIn('予定 ゼンマイン・クースカン / 使用 ゼンマイン',text)
        self.assertIn('現在 クースカン(menu)',text)
        self.assertIn('敵の卵使用を避けるため、致死確認できる切り札を優先',text)
        self.assertIn('奥の手選択済み',text)
        self.assertIn('余白出撃意図: どうし / アルマムーン→ナキューメラ / 攻撃 / 状態 launched / 段階 J3 / 理由 ナキューメラの敵を討つ',text)
        self.assertIn('出撃意図: どうし / アルマムーン→ナキューメラ / 攻撃 / 状態 launched / 段階 J3 / 理由 ナキューメラの敵を討つ',text)
        self.assertIn('余白出撃: パプリカ / カルーン→ジュメルズ / 行軍中 / 段階 J4 / 理由 ジュメルズを厚くする', text)
        # Projection failure, mismatched geometry, narrow gap and old generations
        # must never reserve an area over a different or unmeasured game plane.
        for changed in [dict(status='presentation_failed',projection=projection),
                        dict(status='ready',projection=dict(projection,content=[0,0,900,540])),
                        dict(status='ready',projection=dict(projection,viewport=[0,0,960,540])),
                        dict(status='ready',projection=dict(projection,content=[0,0,721,540]))]:
            _write_json(self.runtime / 'presentation.json',changed)
            self.assertIsNone(self.snapshot()['hanjuku']['gap'])

    def test_every_action_plan_and_reason_reaches_news_including_page_turns(self):
        import time
        now = time.time()
        projection = dict(align='right', viewport=[0,90,960,540], content=[239,0,721,540])
        _write_json(self.runtime / 'presentation.json', dict(status='ready', projection=projection))
        prefix = self.identity['runtime_id'] + ':1:'
        rows = []
        def plan(n, screen, actions, identity=None):
            rows.append(dict(self.identity if identity is None else identity,
                             event='action_plan', at=now, decision_id=prefix+str(n),
                             screen_kind=screen, planned_actions=[
                                 dict(type='pad', buttons=[button], hold_ms=100)
                                 for button in actions]))
        def decision(n, kind, reason):
            rows.append(dict(self.identity, event='decision', at=now,
                             decision_id=prefix+str(n), decision=kind,
                             reason=reason))
        plan(1, 'month_menu', ['down', 'a'])
        decision(1, 'month_plan', '今月は兵士が不足しているので補充を選んだ')
        plan(2, 'general_list', ['r', 'down'])
        decision(2, 'general_page', '次のページに未確認の将軍がいるため一覧を送る')
        plan(3, 'battle_menu', ['a'])
        decision(3, 'battle_card', '敵の卵使用前に切り札で攻める')
        plan(4, 'general_list', ['down'])  # No separate decision emitted
        plan(5, 'battle_menu', [])
        decision(5, 'situation_held', '選択肢が不明なので入力を保留する')
        wrong = dict(self.identity, lease_id='different')
        plan(6, 'month_menu', ['a'], identity=wrong)
        journal = self.runtime / 'hanjuku_decisions.jsonl'
        journal.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows)+'\n',
                           encoding='utf-8')
        snapshot = self.snapshot()['hanjuku']
        decisions = snapshot['gap']['decisions']
        self.assertEqual(len(decisions), 5)
        self.assertEqual([row['actions'] for row in decisions],
                         [['down', 'a'], ['r', 'down'], ['a'], ['down'], []])
        self.assertIn('兵士が不足', decisions[0]['reasons'][0])
        self.assertIn('次のページ', decisions[1]['reasons'][0])
        self.assertIn('敵の卵使用前', decisions[2]['reasons'][0])
        self.assertEqual(decisions[3]['reasons'], [])
        self.assertIn('保留', decisions[4]['reasons'][0])
        feed = '\n'.join(sd.fit_dashboard_lines(sd.render_hanjuku_status(snapshot), width=20))
        displayed = [json.loads(line.split('余白判断: ',1)[1])
                     for line in feed.splitlines() if '余白判断: ' in line]
        self.assertEqual(len(displayed), 5)
        self.assertEqual(displayed[1]['id'], prefix+'2')
        self.assertNotIn('…', displayed[2]['reasons'][0])

    def test_live_procedure_stages_use_actual_policy_sessions(self):
        import time
        now = time.time()
        projection = dict(align='right', viewport=[0, 90, 960, 540],
                          content=[239, 0, 721, 540])
        _write_json(self.runtime / 'presentation.json',
                    dict(status='ready', projection=projection))
        bot = json.loads((self.runtime / 'hanjuku_bot.json').read_text())
        policy = bot['policy']
        policy.update(
            active='J3', month_sub={'kind': 'recruit', 'stage': 'recovering'},
            post_month_status={'stage': 'await_map'},
            house={'phase': 'open_roster', 'general': 'ゼウス'},
            flow={'recall': {'stage': 'to_camp', 'general': 'どうし'}})
        _write_json(self.runtime / 'hanjuku_bot.json', bot)
        snapshot = self.snapshot()['hanjuku']
        flows = snapshot['gap']['flows']
        self.assertEqual([row['key'] for row in flows],
                         ['month_sub', 'post_month', 'house', 'recall'])
        text = '\n'.join(sd.render_hanjuku_status(snapshot))
        self.assertIn('余白進行: month_sub / 将軍募集 / recovering / recruit', text)
        self.assertIn('余白進行: recall / 帰還 / to_camp / どうし', text)

        # Long but bounded flow descriptions must survive snapshot -> status feed
        # unchanged; the news panel wraps them instead of appending an ellipsis.
        long_detail = '所在地を確認してから出撃準備を進める' * 12
        policy['house']['general'] = long_detail
        _write_json(self.runtime / 'hanjuku_bot.json', bot)
        long_snapshot = self.snapshot()['hanjuku']
        self.assertEqual(long_snapshot['gap']['flows'][2]['detail'], long_detail)
        long_feed = '\n'.join(sd.render_hanjuku_status(long_snapshot))
        self.assertIn('余白進行: house / 城・将軍の確認 / open_roster / ' + long_detail, long_feed)
        self.assertNotIn('…', next(line for line in long_feed.splitlines()
                                   if '余白進行: house' in line))

        # A chart step alone never proves a departure.  A matching persisted
        # attempt proves only a procedure, not a successful sortie.
        policy.pop('month_sub')
        policy.pop('post_month_status')
        policy.pop('house')
        policy['flow'] = {'sortie_attempt': {'step': 'J3', 'target_seen': True}}
        policy['battle'] = {'hp_observed_at': now - 2, 'castle': 'ナキューメラ',
                            'card_flow': {'stage': 'menu', 'card': 'ゼンマイン'}}
        _write_json(self.runtime / 'hanjuku_bot.json', bot)
        flows = self.snapshot()['hanjuku']['gap']['flows']
        self.assertEqual({r['key']: r['stage'] for r in flows},
                         {'sortie': 'target_seen', 'battle': 'menu'})
        policy['flow']['sortie_attempt']['step'] = 'J30'
        policy['battle']['hp_observed_at'] = now - 50
        _write_json(self.runtime / 'hanjuku_bot.json', bot)
        self.assertEqual(self.snapshot()['hanjuku']['gap']['flows'], [])

    def test_gap_hp_and_rosters_hide_missing_future_and_expired_observations(self):
        import time
        now=time.time()
        _write_json(self.runtime / 'presentation.json',dict(status='ready',projection=
            dict(align='right',viewport=[0,90,960,540],content=[239,0,721,540])))
        for stamp in [None, True, now+2, now-31]:
            bot=json.loads((self.runtime / 'hanjuku_bot.json').read_text())
            bot['policy'].update(garrison={'城':['将軍']},garrison_observed_at={'城':stamp},
                battle=dict(enemy='敵',ally='我',enemy_hp=10,ally_hp=20,hp_observed_at=stamp))
            _write_json(self.runtime / 'hanjuku_bot.json',bot)
            gap=self.snapshot()['hanjuku']['gap']
            self.assertEqual(gap['garrison'],[])
            self.assertIsNone(gap['hp'])
            self.assertIsNone(gap['battle'])

    def test_cached_observations_render_without_score_inference(self):
        corner = self.snapshot()
        self.assertEqual(corner['hanjuku']['availability'], 'fresh')
        rendered = '\n'.join(sd.render_docich_corner_stats(corner))
        self.assertIn('第2話', rendered)
        self.assertIn('123G', rendered)
        self.assertIn('現在の城数ではない', rendered)
        self.assertIn('計画段階: F3（完了未確認）', rendered)
        self.assertIn('実入力: 7回', rendered)
        # No live battle record in this fixture: say so instead of inventing HP.
        self.assertIn('交戦HP: 戦闘記録なし', rendered)
        self.assertNotIn('将軍HP', rendered)

    def test_month_and_pending_plan_are_observations_not_roster_counts(self):
        path = self.runtime / 'hanjuku_bot.json'
        data = json.loads(path.read_text())
        data['policy'].update(month='1-11', house={'phase': 'castle_verify'},
                              house_eggs={'old_general': {'hp': 1}})
        _write_json(path, data)
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['year'], result['month']), (1, 11))
        self.assertEqual(result['pending_plan'], 'repair')
        self.assertNotIn('general_count', result)
        rendered = '\n'.join(sd.render_hanjuku_status(result))
        self.assertIn('1年11月（最終観測）', rendered)
        self.assertIn('保留計画: repair', rendered)

    def test_invalid_month_stays_unknown(self):
        path = self.runtime / 'hanjuku_bot.json'
        for month in ('1-13', '0-2', True, '1-2\n', '10000-2'):
            data = json.loads(path.read_text())
            data['policy']['month'] = month
            _write_json(path, data)
            result = self.snapshot()['hanjuku']
            self.assertIsNone(result['month'])
            self.assertIsNone(result['year'])

    def test_previous_generation_is_unavailable(self):
        changed = dict(self.identity, generation=2, runtime_id='g2-abcdef12')
        _write_json(self.root / 'game_switch.json', dict(phase='ready', active=changed))
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')

    def test_stale_and_future_observation_are_not_live(self):
        import time
        for stamp in (time.time() - 60, time.time() + 60, True, float('nan')):
            with self.subTest(stamp=stamp):
                _write_json(self.runtime / 'hanjuku_run.json', dict(self.identity, observed_at=stamp))
                self.assertEqual(self.snapshot()['hanjuku']['availability'], 'stale')

    def test_terminal_malformed_and_symlink_fail_closed(self):
        _write_json(self.runtime / 'hanjuku_run.json', dict(self.identity, terminal_reason='game_over'))
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')
        self.write()
        target = self.runtime / 'hanjuku_bot.json'
        target.write_text('x' * 262145)
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')
        target.unlink()
        target.symlink_to(self.root / 'retro_corner.json')
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')

    def test_missing_numbers_are_unknown_not_zero(self):
        _write_json(self.runtime / 'hanjuku_bot.json', dict(decision_trace=self.identity, policy=dict(chapter=True, gold=-1)))
        result = self.snapshot()['hanjuku']
        self.assertIsNone(result['chapter'])
        self.assertIsNone(result['gold'])
        text = '\n'.join(sd.render_hanjuku_status(result))
        self.assertIn('所持金 不明G', text)
        self.assertNotIn('0勝', text)

    def test_transition_and_identity_mismatch_fail_closed(self):
        _write_json(self.root / 'game_switch.json', dict(phase='switching', active=self.identity))
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')
        self.write()
        _write_json(self.runtime / 'hanjuku_bot.json', dict(decision_trace=dict(self.identity, lease_id='old')))
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')

    def test_renderer_strips_control_characters_and_bounds_lines(self):
        text = sd.fit_dashboard_lines(sd.render_hanjuku_status(dict(availability='fresh', screen='\x1b[31mBAD\n' * 100)))
        self.assertNotIn('\x1b', '\n'.join(text))
        self.assertTrue(all(sd.ansi_display_width(line) <= sd.W for line in text))

    def test_no_escape_fragment_survives_any_card_field(self):
        # isprintable() rejects ESC but keeps the printable "[31m" behind it, so
        # a per-character check would put a broken escape on the card. Cover
        # every field the renderer reads, including the nested name rows.
        esc = '\x1b[31m'
        value = dict(
            availability='fresh', chapter=2, gold=1,
            variant=esc + 'EVIL\n' * 50, chart_step='X' * 200,
            screen=esc + 'S\n', phase=esc + 'P\n', pending_plan=esc + 'N\n',
            captured_names=[esc + '[2J', 'Y' * 90, 'ok'],
            lost_names=[esc + '[9mZ'],
            garrison=[{'castle': esc + 'X', 'generals': ['\x07bell', esc + 'OK']}],
            marching=[{'general': esc + 'A', 'target': esc + '[2JB'}],
            eggs=[{'general': esc + 'E', 'uses': 2}],
            enemy=esc + 'EN', ally=esc + 'AL', enemy_hp=5, ally_hp=6,
            gap={'left':0,'width':239,'side':'left','until':9999999999.0,
                 'captured_names':[],'garrison':[],'marching':[],'hp':None,
                 'battle':{'until':9999999999.0,'side':'attack','castle':esc+'CASTLE',
                           'step':esc+'STEP','variant':esc+'VAR',
                           'deviation':esc+'DECISION\n'*10,
                           'planned_cards':[esc+'CARD'],'used_cards':[],'selected_cards':[],
                           'card':esc+'NOW','card_stage':esc+'menu',
                           'egg_battle':True,'egg_guard_stage':'selected',
                           'egg_guard_exhausted':False,
                           'enemy_soldiers':4,'ally_soldiers':7}})
        text = '\n'.join(sd.fit_dashboard_lines(sd.render_hanjuku_status(value)))
        # '\n' is the card's own line separator, so check per line instead.
        for fragment in ('\x1b', '\x07', '\r', '[31m', '[2J', '[1m', '[9m'):
            self.assertNotIn(fragment, text, f'{fragment!r} leaked onto the card')
        machine_prefixes = (
            '  投影余白:', '  余白占領記録:', '  余白駐留:', '  余白行軍:', '  余白交戦HP:',
            '  余白交戦兵数:', '  余白戦闘計画:', '  余白切り札:', '  余白戦闘判断:', '  余白卵対策:',
        )
        for line in text.split('\n'):
            self.assertNotIn('\n', line)
            if not line.startswith(machine_prefixes):
                self.assertTrue(sd.ansi_display_width(line) <= sd.W)

    def test_snapshot_cleaner_drops_whole_escape_runs(self):
        from docich.soren_corner_stats import _hanjuku_clean, _hanjuku_names
        self.assertEqual(_hanjuku_clean('\x1b[31mEVIL\n'), 'EVIL')
        self.assertEqual(_hanjuku_clean('\x1b]0;title\x07X'), 'X')
        self.assertEqual(_hanjuku_clean('\x07\x08\x00only'), 'only')
        self.assertEqual(_hanjuku_names(['\x1b[2J', 'plain', 'plain', '\x07b']), ['plain', 'b'])

    def _policy(self, **extra):
        data = json.loads((self.runtime / 'hanjuku_bot.json').read_text())
        data['policy'].update(extra)
        _write_json(self.runtime / 'hanjuku_bot.json', data)
        return data

    def test_country_power_income_and_wages_survive_until_the_next_read(self):
        self._policy(chapter=5, month='46-5', hanjuku_level=17, hanjuku_value=9999,
                     hanjuku_level_seen={'scope': [5, '46-5'], 'tick': 500},
                     country_income={'scope': [5, '46-5'], 'tick': 500, 'income': 300},
                     country_wages={'scope': [5, '46-5'], 'tick': 500, 'wages': 150})
        value = self.snapshot()['hanjuku']
        self.assertEqual((value['hanjuku_level'], value['hanjuku_value'], value['income'],
                          value['wages']), (17, 9999, 300, 150))
        text = '\n'.join(sd.render_hanjuku_status(value))
        self.assertIn('半熟レベル: 17 / 半熟値: 9999', text)
        self.assertIn('現在収入: 300G', text)
        self.assertIn('総賃金: 150G', text)
        # Owner 2026-11-04: a month rollover before the page is re-read keeps
        # the last observation. It must not overwrite the four values with 不明.
        self._policy(month='46-6')
        value = self.snapshot()['hanjuku']
        self.assertEqual((value['hanjuku_level'], value['hanjuku_value'], value['income'],
                          value['wages']), (17, 9999, 300, 150))
        text = '\n'.join(sd.render_hanjuku_status(value))
        self.assertIn('半熟レベル: 17 / 半熟値: 9999', text)
        self.assertIn('現在収入: 300G', text)
        self.assertIn('総賃金: 150G', text)
        # The next successful page read rewrites all four.
        self._policy(month='46-6', hanjuku_level=18, hanjuku_value=12000,
                     hanjuku_level_seen={'scope': [5, '46-6'], 'tick': 700},
                     country_income={'scope': [5, '46-6'], 'tick': 700, 'income': 350},
                     country_wages={'scope': [5, '46-6'], 'tick': 700, 'wages': 160})
        value = self.snapshot()['hanjuku']
        self.assertEqual((value['hanjuku_level'], value['hanjuku_value'], value['income'],
                          value['wages']), (18, 12000, 350, 160))
        text = '\n'.join(sd.render_hanjuku_status(value))
        self.assertIn('現在収入: 350G', text)
        self.assertIn('総賃金: 160G', text)

    def test_right_status_shows_generals_cards_and_shared_egg_drop_arithmetic(self):
        import time
        now = time.time()
        plan = dict(
            enemy='キッシュ', ally='ココット',
            enemy_profile=dict(name='キッシュ', max_hp=26, combat=5, counter=50,
                               thought=2, egg_type='スーパー', egg_bonus='0',
                               aptitude='洞窟', debut=1, personality='身勝手',
                               hobby='カルトクイズ', has_egg=True),
            ally_profile=dict(name='ココット', max_hp=24, combat=8, counter=75,
                              thought=3, egg_type='カラフル', egg_bonus='3',
                              aptitude='平地', debut=1, personality='泣き虫',
                              hobby='じょうば', has_egg=True),
            enemy_max_hp=26, ally_max_hp=24, max_hp_sum=50, remainder=2, threshold=3,
            has_egg=True, threat=True, egg_state='available', excluded=None,
            verdict='droppable', dropper='ブラッキー', dropper_charted=False,
            cards=[
                dict(name='ダイチスイム', id=1, egg_drop=1, drops=False, stock=2,
                     carried=2, available=2, usable=True, unavailable=None),
                dict(name='ブラッキー', id=2, egg_drop=3, drops=True, stock=1,
                     carried=1, available=1, usable=True, unavailable=None),
                dict(name='イッテツーン', id=0, egg_drop=8, drops=True, stock=1,
                     carried=0, available=0, usable=False, unavailable='not_carried'),
            ], cards_total=3)
        self._policy(battle=dict(enemy='キッシュ', ally='ココット', enemy_hp=20,
                                 ally_hp=24, hp_observed_at=now - 1, egg_plan=plan))
        value = self.snapshot()['hanjuku']
        duel = value['duel']
        self.assertEqual((duel['enemy'], duel['ally']), ('キッシュ', 'ココット'))
        self.assertEqual((duel['max_hp_sum'], duel['remainder']), (50, 2))
        self.assertEqual([card['name'] for card in duel['cards']],
                         ['ダイチスイム', 'ブラッキー', 'イッテツーン'])
        text = '\n'.join(sd.render_hanjuku_status(value))
        self.assertIn('敵将軍: キッシュ / 最大HP 26 / 戦闘 5 / 対抗 50%（召喚時）'
                      ' / 思考パターン 2 / エッグ スーパー / 卵補正 0 / 得意 洞窟'
                      ' / 初登場 1話 / 性格 身勝手 / 趣味 カルトクイズ', text)
        self.assertIn('味方将軍: ココット / 最大HP 24 / 戦闘 8 / 対抗 75%（召喚時）'
                      ' / 思考パターン 3 / エッグ カラフル / 卵補正 3 / 得意 平地'
                      ' / 初登場 1話 / 性格 泣き虫 / 趣味 じょうば', text)
        self.assertIn('卵落: 敵最大HP 26 + 味方最大HP 24 = 50 / 余り 2 / 条件 卵落>2'
                      ' / 予測 成立（ブラッキー 卵落3） / 敵卵 保有（静的能力）'
                      ' / 実際の落下 未確認', text)
        self.assertIn('切り札: ブラッキー / ID 2 / 卵落 3 / 在庫 1 / 携行 1'
                      ' / 使用可 / 予測 成立（3>2）', text)
        self.assertIn('切り札: ダイチスイム / ID 1 / 卵落 1 / 在庫 2 / 携行 2'
                      ' / 使用可（残2） / 予測 不成立（1>2）', text)
        self.assertIn('切り札: イッテツーン / ID 0 / 卵落 8 / 在庫 1 / 携行 0'
                      ' / 使用不可（未携行） / 予測 成立（8>2）', text)

    def test_right_status_drops_old_generals_cards_and_plan_on_change_or_stale(self):
        import time
        now = time.time()
        plan = dict(enemy='キッシュ', ally='ココット', enemy_max_hp=26,
                    ally_max_hp=24, max_hp_sum=50, remainder=2, egg_state='available',
                    verdict='not_droppable', cards=[dict(name='ブラッキー', id=2,
                    egg_drop=3, drops=True, stock=1, carried=1, available=1,
                    usable=True, unavailable=None)])
        battle = dict(enemy='キッシュ', ally='ココット', enemy_hp=20, ally_hp=24,
                      hp_observed_at=now - 1, egg_plan=plan)
        self._policy(battle=battle)
        self.assertIsNotNone(self.snapshot()['hanjuku']['duel'])
        # 相手変更: plan が旧将軍のままなら新しい戦闘の行に混ぜない。
        self._policy(battle=dict(battle, enemy='ガルバンゾー'))
        value = self.snapshot()['hanjuku']
        self.assertIsNone(value['duel'])
        text = '\n'.join(sd.render_hanjuku_status(value))
        self.assertNotIn('敵将軍:', text)
        self.assertNotIn('切り札:', text)
        # 観測が古い: 10秒の戦闘パネル境界を超えたら将軍・札・計算を出さない。
        self._policy(battle=dict(battle, hp_observed_at=now - 11))
        self.assertIsNone(self.snapshot()['hanjuku']['duel'])

    def test_right_status_reports_a_chart_owned_dropper_as_withheld_not_absent(self):
        import time
        now = time.time()
        plan = dict(enemy='ガルバンゾー', ally='どうし', enemy_max_hp=30,
                    ally_max_hp=20, max_hp_sum=50, remainder=2, threshold=3,
                    has_egg=True, threat=True, egg_state='available', excluded=None,
                    verdict='not_droppable', dropper=None, egg_drop_free=False,
                    cards=[dict(name='フットバース', id=3, egg_drop=5, drops=True,
                                stock=1, carried=1, available=1, usable=True,
                                unavailable=None)])
        self._policy(battle=dict(enemy='ガルバンゾー', ally='どうし', enemy_hp=30,
                                 ally_hp=20, hp_observed_at=now - 1, egg_plan=plan))
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertIn('予測 保留（チャート指定札のため開幕は使わない）', text)
        self.assertNotIn('携行札に卵落札なし', text)
        self.assertRegex(text, r'切り札: フットバース / ID 3 / 卵落 5 .*予測 成立（5>2）')

    def test_right_status_never_fills_unknown_max_hp_or_card_values(self):
        import time
        now = time.time()
        plan = dict(enemy='未知将軍', ally='ココット', enemy_max_hp=None,
                    ally_max_hp=24, max_hp_sum=None, remainder=None, threshold=None,
                    excluded='enemy_unknown', egg_state='unknown', verdict='unknown',
                    enemy_profile=None, ally_profile=None,
                    cards=[dict(name='きらきら', id=None, egg_drop=None, drops=None,
                                stock=1, carried=0, available=0, usable=False,
                                unavailable='not_carried')])
        self._policy(battle=dict(enemy='未知将軍', ally='ココット', enemy_hp=20,
                                 ally_hp=24, hp_observed_at=now - 1, egg_plan=plan))
        value = self.snapshot()['hanjuku']
        self.assertEqual(value['duel']['max_hp_sum'], None)
        text = '\n'.join(sd.render_hanjuku_status(value))
        self.assertIn('敵将軍: 判定不能（将軍または最大HP不明）', text)
        self.assertIn('卵落: 判定不能（敵将軍の最大HP不明）', text)
        self.assertIn('切り札: きらきら / ID 不明 / 卵落 不明 / 在庫 1 / 携行 0'
                      ' / 使用不可（未携行） / 予測 判定不能', text)
        self.assertNotIn('最大HP 0', text)

    def test_right_status_keeps_seasonal_conditions_and_long_text(self):
        import time
        now = time.time()
        plan = dict(enemy='アルファルファ', ally='しゅじんこう', enemy_max_hp=38,
                    ally_max_hp=90, max_hp_sum=128, remainder=0, threshold=1,
                    egg_state='available', threat=True, verdict='droppable',
                    dropper='イッテツーン', excluded=None, has_egg=True,
                    enemy_profile=dict(name='アルファルファ', max_hp=38, combat=5,
                                       counter=50, thought=2, egg_type='スーパー',
                                       egg_bonus='春+1', aptitude='森', debut=1,
                                       personality='和風', hobby='あいきどう'),
                    ally_profile=dict(name='しゅじんこう', max_hp=90, combat=14,
                                      counter=50, thought=2, egg_type='エラベル',
                                      egg_bonus='0', aptitude=None, debut=1,
                                      personality='甘えんぼ', hobby='つまみぐい'),
                    cards=[dict(name='イッテツーン', id=0, egg_drop=8, drops=True,
                                stock=2, carried=1, available=1, usable=True)])
        self._policy(battle=dict(enemy='アルファルファ', ally='しゅじんこう',
                                 enemy_hp=10, ally_hp=80, hp_observed_at=now - 1,
                                 egg_plan=plan))
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertIn('卵補正 春+1', text)
        self.assertIn('得意 森', text)
        # 得意なし ('－') は空文字で届く。不明とは書かず、条件を失わない。
        self.assertIn('味方将軍: しゅじんこう', text)
        self.assertIn('得意 －', text)

    def test_observed_battle_hp_replaces_the_permanent_unknown_placeholder(self):
        self._policy(battle={'enemy': ' dragon', 'ally': 'ゼウス', 'enemy_hp': 31, 'ally_hp': 44})
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertIn('交戦HP: 敵 31（dragon） / 我 44（ゼウス）', text)
        # The old card ended on a permanent "将軍HP・卵状態: 未確認" line.
        self.assertNotIn('将軍HP', text)
        self.assertNotIn('交戦HP: 戦闘記録なし', text)

    def test_partial_battle_record_never_prints_half_measured_hp(self):
        cases = [
            {'enemy': ' dragon', 'ally_hp': 44},
            {'enemy': 'dragon', 'enemy_hp': 31, 'ally_hp': 44},
            {'ally': 'ゼウス', 'enemy_hp': 31, 'ally_hp': 44},
        ]
        for battle in cases:
            with self.subTest(battle=battle):
                self._policy(battle=battle)
                text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
                self.assertIn('交戦HP: 戦闘記録なし', text)
                self.assertNotIn('交戦HP: 敵', text)

    def test_roster_line_names_observed_castles_and_lost_ones(self):
        self._policy(captured=['カストーラ', '-travel'], lost=['ジョンリギ'], home_lost=False)
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertIn('失った城: ジョンリギ', text)
        self.assertNotIn('本拠: 失陥', text)
        self._policy(home_lost=True)
        self.assertIn('本拠: 失陥（記録）', '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku'])))

    def test_marching_sortie_expires_with_the_policy_busy_window(self):
        self._policy(tick=5000, sorties={'J3': {'general': 'どうし', 'target': 'ナキューメラ',
                                                 'status': 'en_route', 'tick': 4990}})
        result = self.snapshot()['hanjuku']
        self.assertEqual(result['marching'], [{'general': 'どうし', 'target': 'ナキューメラ'}])
        self.assertIn('行軍中: どうし→ナキューメラ', '\n'.join(sd.render_hanjuku_status(result)))
        # One tick past the policy's own 400-observation busy window: no longer
        # marching, so it must not be displayed as one.
        self._policy(tick=5000 + 400, sorties={'J3': {'general': 'どうし', 'target': 'ナキューメラ',
                                                      'status': 'en_route', 'tick': 4990}})
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertNotIn('行軍中', text)

    def test_settled_and_unread_sorties_are_not_shown_as_marching(self):
        self._policy(tick=5000, sorties={
            'J3': {'general': 'ゼウス', 'status': 'arrived', 'tick': 4999},
            'J4': {'general': 'ユイートル', 'target': 'X', 'status': 'launched_unconfirmed', 'tick': True},
            'J5': {'target': 'Y', 'status': 'en_route', 'tick': 4999},
            'J6': {'general': 'future', 'target': 'Z', 'status': 'en_route', 'tick': 5001},
        })
        self.assertEqual(self.snapshot()['hanjuku']['marching'], [])

    def test_marching_without_a_confirmed_target_says_so_instead_of_inventing_one(self):
        self._policy(tick=5000, sorties={'J4': {'general': 'ヴィーナス', 'status': 'en_route', 'tick': 4999}})
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertIn('行軍中: ヴィーナス→未確定', text)

    def test_active_chart_order_is_a_plan_until_a_launch_is_recorded(self):
        order = {'step': 'J3', 'general': 'どうし', 'source': 'アルマムーン',
                 'target': 'ナキューメラ', 'purpose': 'attack', 'note': '敵城攻略'}
        self._policy(active='J3', chart_plan={'orders': [order]}, orders={'J3': 'pending'})
        result = self.snapshot()['hanjuku']
        self.assertEqual(result['active_order']['status'], 'pending')
        self.assertEqual(result['active_order']['target'], 'ナキューメラ')
        self.assertIn('状態 pending / 段階 J3 / 理由 敵城攻略',
                      '\n'.join(sd.render_hanjuku_status(result)))
        # A stage ID alone cannot imply a planned sortie.
        self._policy(chart_plan={'orders': []}, orders={})
        self.assertIsNone(self.snapshot()['hanjuku']['active_order'])
        # A failed order must not survive merely because it remains in the chart.
        self._policy(chart_plan={'orders': [order]}, orders={'J3': 'failed'})
        self.assertIsNone(self.snapshot()['hanjuku']['active_order'])
        # A launched status without a persisted launch snapshot is not proof.
        self._policy(orders={'J3': 'launched'})
        self.assertIsNone(self.snapshot()['hanjuku']['active_order'])
        self._policy(launched_orders={'J3': order})
        self.assertEqual(self.snapshot()['hanjuku']['active_order']['status'], 'launched')
        self._policy(orders={'J3': 'launched_unconfirmed'})
        self.assertEqual(self.snapshot()['hanjuku']['active_order']['status'], 'launched_unconfirmed')

    def test_inactive_launched_order_is_not_shown_as_current_intent(self):
        self._policy(
            active='J3',
            orders={'J3': 'failed'},
            launched_orders={'J3': {'step': 'J3', 'general': 'どうし',
                                     'source': 'アルマムーン', 'target': 'ナキューメラ',
                                     'purpose': 'attack'}},
        )
        result = self.snapshot()['hanjuku']
        self.assertIsNone(result['active_order'])
        self.assertNotIn('出撃意図:', '\n'.join(sd.render_hanjuku_status(result)))

    def test_garrison_and_eggs_are_bounded_observations(self):
        self._policy(garrison={'アルマムーン': ['ゼウス', 'ユイートル'], '空': []},
                     egg_uses={'ゼウス': 1, '不正': 'x', '範囲外': 9})
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertIn('駐留: アルマムーン=ゼウス/ユイートル', text)
        self.assertIn('卵: ゼウス 1回', text)
        self.assertNotIn('範囲外', text)
        self.assertNotIn('不正', text)

    def _roster(self, names=None, **extra):
        names = list(names if names is not None else ['どうし', 'ゼウス', 'ユイートル'])
        receipt = dict(chapter=2, month='5-3', tick=100, complete=True,
                       identity=dict(self.identity), names=names)
        receipt.update(extra)
        self._policy(tick=120, month='5-3', recruit_roster=receipt)
        return receipt

    def test_complete_roster_counts_generals_and_partial_pages_do_not(self):
        self._roster()
        result = self.snapshot()['hanjuku']
        self.assertEqual(result['generals'], 3)
        self.assertIn('将軍数: 3人（完全名簿）', '\n'.join(sd.render_hanjuku_status(result)))
        # A partial page, another run/chapter or a malformed receipt is not a
        # count: the card says 不明 instead of the page size.
        for extra in [dict(complete=False), dict(complete='true'),
                      dict(chapter=1), dict(chapter=None),
                      dict(identity={**self.identity, 'lease_id': 'other'}),
                      dict(identity=None), dict(tick=121), dict(tick=True),
                      dict(tick='100'), dict(names=['どうし', 'どうし']),
                      dict(names=['']), dict(names=['将軍' * 40]),
                      dict(names=[f'将軍{i}' for i in range(33)]),
                      dict(names=['どうし', 7])]:
            with self.subTest(extra=extra):
                self._roster(**extra)
                result = self.snapshot()['hanjuku']
                self.assertIsNone(result['generals'])
                text = '\n'.join(sd.render_hanjuku_status(result))
                self.assertIn('将軍数: 不明', text)
                self.assertNotIn('完全名簿', text)
        # A complete last-month receipt is the 前月 fallback, not 不明.
        self._roster(month='5-2')
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['generals'], result['generals_previous']), (3, True))
        self.assertIn('将軍数: 3人（前月の完全名簿）',
                      '\n'.join(sd.render_hanjuku_status(result)))

    def test_completed_status_survey_supplies_the_count_while_a_page_is_partial(self):
        names = [f'将軍{i}' for i in range(12)]
        receipt = dict(chapter=2, month='5-3', tick=100, complete=True,
                       identity=dict(self.identity), names=names)
        self._policy(tick=120, month='5-3',
                     recruit_roster=dict(chapter=2, month='5-3', tick=118, complete=False,
                                         identity=dict(self.identity), names=names[:2]),
                     roster_survey=dict(schema=1, identity=dict(self.identity), scope=[2, '5-3'],
                                        receipt=dict(receipt), statuses={}, dirty={}))
        self.assertEqual(self.snapshot()['hanjuku']['generals'], 12)
        # A survey of another month or another run cannot supply it either.
        for scope, receipt_identity in [([2, '5-2'], dict(self.identity)),
                                        ([2, '5-3'], {**self.identity, 'runtime_id': 'g2-abcdef12'})]:
            with self.subTest(scope=scope, identity=receipt_identity):
                self._policy(tick=120, month='5-3',
                             recruit_roster=dict(chapter=2, month='5-3', tick=118,
                                                 complete=False, identity=dict(self.identity),
                                                 names=names[:2]),
                             roster_survey=dict(schema=1, identity=dict(self.identity), scope=scope,
                                                receipt=dict(receipt, identity=receipt_identity),
                                                statuses={}, dirty={}))
                self.assertIsNone(self.snapshot()['hanjuku']['generals'])

    def _last_complete(self, **extra):
        record = dict(schema=1, identity=dict(self.identity), chapter=2, month='5-3',
                      tick=100, complete=True, names=[f'将軍{i}' for i in range(12)])
        record.update(extra)
        self._policy(tick=200, month='5-4', roster_last_complete=record,
                     roster_pending_joins=None)
        return record

    def test_previous_month_complete_roster_is_kept_and_labelled(self):
        self._last_complete()
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['generals'], result['generals_previous']), (12, True))
        self.assertIn('将軍数: 12人（前月の完全名簿）',
                      '\n'.join(sd.render_hanjuku_status(result)))

    def test_current_month_complete_roster_is_not_labelled_previous(self):
        self._last_complete(month='5-4')
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['generals'], result['generals_previous']), (12, False))
        text = '\n'.join(sd.render_hanjuku_status(result))
        self.assertIn('将軍数: 12人（完全名簿）', text)
        self.assertNotIn('前月の完全名簿', text)

    def test_current_complete_receipt_wins_over_the_kept_copy(self):
        self._last_complete()
        self._policy(recruit_roster=dict(chapter=2, month='5-4', tick=150, complete=True,
                                         identity=dict(self.identity),
                                         names=['どうし', 'ゼウス', 'ヴィーナス', 'ココット', 'バジル']))
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['generals'], result['generals_previous']), (5, False))

    def test_older_other_chapter_or_malformed_kept_copy_stays_unknown(self):
        for extra in [dict(month='5-2'), dict(month='5-12'), dict(chapter=1), dict(complete=False),
                      dict(identity={**self.identity, 'generation': 9}),
                      dict(identity=None), dict(tick=True), dict(tick=201),
                      dict(names=[]), dict(names=['どうし', 'どうし']),
                      dict(names=['将軍' * 40]), dict(names=['どうし', 7]),
                      dict(month='13-1'), dict(month=None)]:
            with self.subTest(extra=extra):
                self._last_complete(**extra)
                result = self.snapshot()['hanjuku']
                self.assertIsNone(result['generals'])
                self.assertFalse(result['generals_previous'])
                text = '\n'.join(sd.render_hanjuku_status(result))
                self.assertIn('将軍数: 不明', text)
                self.assertNotIn('完全名簿', text)

    def test_year_boundary_previous_month_is_accepted(self):
        self._last_complete(month='5-12')
        self._policy(month='6-1')
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['generals'], result['generals_previous']), (12, True))

    def test_confirmed_joins_are_added_to_the_kept_count(self):
        self._last_complete(month='5-4', names=[f'将軍{i}' for i in range(18)])
        self._policy(roster_pending_joins=['ココット', 'ゼウス'])
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['generals'], result['generals_previous'],
                          result['generals_roster'], result['generals_joins']), (20, False, 18, 2))
        self.assertIn('将軍数: 20人（名簿18人＋加入2名）',
                      '\n'.join(sd.render_hanjuku_status(result)))

    def test_confirmed_joins_are_added_to_the_previous_month_count(self):
        self._last_complete()
        self._policy(roster_pending_joins=['ココット'])
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['generals'], result['generals_previous'],
                          result['generals_roster'], result['generals_joins']), (13, True, 12, 1))
        self.assertIn('将軍数: 13人（前月12人＋加入1名）',
                      '\n'.join(sd.render_hanjuku_status(result)))

    def test_pending_joins_are_bounded_deduplicated_and_never_double_counted(self):
        self._last_complete()
        self._policy(roster_pending_joins=['', '将軍0', 'ココット', 'ココット', 7,
                                          '将軍' * 40, 'ゼウス'])
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['generals'], result['generals_joins']), (14, 2))
        # A current live receipt already contains the joins: do not add again.
        self._policy(recruit_roster=dict(chapter=2, month='5-4', tick=150, complete=True,
                                         identity=dict(self.identity),
                                         names=['どうし', 'ゼウス', 'ヴィーナス']))
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['generals'], result['generals_joins']), (3, 0))
        # Pending joins without any complete list cannot invent a count.
        self._policy(recruit_roster=None, roster_last_complete=None,
                     roster_pending_joins=['ココット'])
        result = self.snapshot()['hanjuku']
        self.assertIsNone(result['generals'])
        self.assertIn('将軍数: 不明', '\n'.join(sd.render_hanjuku_status(result)))

    def test_gap_moves_keep_stale_as_unconfirmed_and_skip_settled_sorties(self):
        import time
        now = time.time()
        _write_json(self.runtime / 'presentation.json', dict(
            status='ready', projection=dict(align='right', viewport=[0,90,960,540], content=[239,0,721,540])))
        self._policy(tick=5000, month='5-3',
                     sorties={
                         'A': {'general': 'どうし', 'planned_target': 'ナキューメラ',
                               'status': 'launched_unconfirmed', 'tick': 4990},
                         'B': {'general': '古い', 'source': '古', 'target': '先',
                               'status': 'en_route', 'tick': 4000},
                         'C': {'general': '到着', 'source': 'カ', 'target': 'ス',
                               'status': 'arrived', 'tick': 4999},
                         'D': {'source': 'カ', 'target': 'ス', 'status': 'en_route', 'tick': 4999},
                         'E': {'general': '不明先', 'source': 'カ', 'status': 'en_route', 'tick': 4999},
                     },
                     launched_orders={'A': {'source': 'アルマムーン', 'target': 'ナキューメラ',
                                            'purpose': 'attack'}})
        moves = self.snapshot()['hanjuku']['gap']['moves']
        self.assertEqual([m['general'] for m in moves], ['どうし', '古い', '不明先'])
        self.assertEqual(moves[1]['status'], 'tracking_unknown')
        self.assertEqual((moves[0]['source'], moves[0]['target']), ('アルマムーン', 'ナキューメラ'))
        self.assertEqual((moves[0]['status'], moves[0]['purpose']), ('launched_unconfirmed', 'attack'))
        # No source/target at all is not a route the chart can draw.
        self.assertEqual(moves[2]['source'], 'カ')
        self.assertEqual(moves[2]['target'], '')

    def _ready_gap(self):
        _write_json(self.runtime / 'presentation.json', dict(
            status='ready', projection=dict(align='right', viewport=[0,90,960,540],
                                            content=[239,0,721,540])))

    def test_house_repair_sortie_names_the_house_and_its_purpose(self):
        # g1139 2026-11-06 11:45 JST: purpose='house' with target=None showed
        # "カルーン→未確認" and "行動内容未取得" on the left chart.
        self._ready_gap()
        self._policy(tick=5000, month='46-9',
                     sorties={'HOUSE_OUT:1087': {'general': 'ヴィーナス', 'purpose': 'house',
                                                 'source': 'カルーン', 'status': 'en_route',
                                                 'target': None, 'tick': 4990}})
        result = self.snapshot()['hanjuku']
        moves = result['gap']['moves']
        self.assertEqual([(m['general'], m['source'], m['target']) for m in moves],
                         [('ヴィーナス', 'カルーン', 'あたしの家')])
        self.assertEqual((moves[0]['purpose'], result['gap']['march_count']), ('house', 1))
        self.assertEqual((result['active_order']['target'], result['active_order']['purpose'],
                          result['active_order']['status']),
                         ('あたしの家', '卵修理', 'launched_unconfirmed'))
        text = '\n'.join(sd.render_hanjuku_status(result))
        self.assertIn('カルーン→あたしの家', text)
        self.assertIn('種別 house', text)
        self.assertIn('卵修理', text)

    def test_roof_grounded_recall_route_is_displayed_but_unverified_origin_is_not(self):
        self._ready_gap()
        self._policy(tick=5000, month='46-9',
                     flow={'recall': {'stage': 'await_dispatch', 'origin_source': 'roofs',
                                      'origin': [440, 392], 'goal': 'アルマムーン'}})
        result = self.snapshot()['hanjuku']
        self.assertEqual(result['gap']['recall_route'], {
            'origin': [440, 392], 'target': 'アルマムーン', 'stage': 'await_dispatch'})
        text = '\n'.join(sd.render_hanjuku_status(result))
        self.assertIn('余白帰還: {"origin":[440,392],"target":"アルマムーン",', text)
        self._policy(flow={'recall': {'stage': 'await_dispatch', 'origin_source': 'guess',
                                       'origin': [440, 392], 'goal': 'アルマムーン'}})
        self.assertIsNone(self.snapshot()['hanjuku']['gap']['recall_route'])
        self._policy(flow={'recall': {'stage': 'await_dispatch', 'origin_source': 'roofs',
                                       'origin': [440, 392], 'goal': None}})
        self.assertIsNone(self.snapshot()['hanjuku']['gap']['recall_route'])

    def test_settled_or_stale_house_sortie_is_not_a_current_action(self):
        self._ready_gap()
        self._policy(tick=5000, month='46-9',
                     sorties={'HOUSE_OUT:1087': {'general': 'ヴィーナス', 'purpose': 'house',
                                                 'source': 'カルーン', 'status': 'closed',
                                                 'target': None, 'tick': 4990},
                              'HOUSE_OUT:1000': {'general': '古い', 'purpose': 'house',
                                                 'source': 'カルーン', 'status': 'en_route',
                                                 'target': None, 'tick': 4000}})
        result = self.snapshot()['hanjuku']
        self.assertEqual([(m['general'], m['target'], m['status'])
                          for m in result['gap']['moves']],
                         [('古い', 'あたしの家', 'tracking_unknown')])
        self.assertIsNone(result['active_order'])

    def test_non_house_sortie_without_a_target_stays_unconfirmed(self):
        self._ready_gap()
        self._policy(tick=5000, month='46-9',
                     sorties={'A': {'general': '不明先', 'source': 'カ',
                                    'status': 'en_route', 'tick': 4999}})
        result = self.snapshot()['hanjuku']
        self.assertEqual([(m['general'], m['target']) for m in result['gap']['moves']],
                         [('不明先', '')])
        self.assertIsNone(result['active_order'])

    def test_unread_values_stay_unknown_rather_than_zero(self):
        result = self.snapshot()['hanjuku']
        text = '\n'.join(sd.render_hanjuku_status(result))
        self.assertIn('兵力: 不明名', text)
        self.assertIn('停滞 不明秒', text)
        self.assertIn('戦闘: 開始 不明 / 終了 不明', text)
        self.assertNotIn('開始 0', text)
        self.assertNotIn('兵力: 0名', text)
        self.assertIn('出撃: 成立 不明 / 失敗 不明', text)
        self.assertNotIn('出撃: 成立 0 / 失敗 0', text)

    def test_hanjuku_corner_omits_permanently_empty_score_and_ranking_lines(self):
        rendered = sd.render_docich_corner_stats(self.snapshot())
        text = '\n'.join(rendered)
        self.assertNotIn('no completed results', text)
        self.assertNotIn('no corner ranking data', text)
        # The retro card header and the Hanjuku card itself stay in place.
        self.assertIn('SOREN/CORNER: RETRO / hanjuku-hero', text)
        self.assertIn('半熟英雄 / 最終観測・記録', text)

    def test_other_retro_game_without_scores_keeps_empty_score_panel(self):
        state = dict(self.state, game='ninvaders', bot_identity=None)
        _write_json(self.root / 'retro_corner.json', state)
        text = '\n'.join(sd.render_docich_corner_stats(load_active_corner(self.root)))
        self.assertIn('Stats: no completed results yet', text)
        self.assertIn('Strategy: no corner ranking data', text)

    def test_other_retro_games_keep_their_score_and_ranking_lines(self):
        state = dict(self.state, game='ninvaders', bot_identity=None)
        _write_json(self.root / 'retro_corner.json', state)
        log = self.root / 'scores/ninvaders.jsonl'
        log.parent.mkdir(parents=True)
        log.write_text('\n'.join(json.dumps({'ts': f'2026-10-01T0{i}:00:00+00:00',
                                            'game': 'ninvaders', 'score': 100 + i})
                                for i in range(4)), encoding='utf-8')
        text = '\n'.join(sd.render_docich_corner_stats(load_active_corner(self.root)))
        self.assertIn('Stats: 4 results', text)
        self.assertIn('Strategy: no corner ranking data', text)

class ConsoleProgressTest(unittest.TestCase):
    setUp = DocichCornerStatsTest.setUp
    tearDown = DocichCornerStatsTest.tearDown
    _state = DocichCornerStatsTest._state

    def console(self, *, status="active", game="nsnake", start=100, deadline=500, **extra):
        self._state("retro_corner.json", status=status, game=game, started_at=start,
                    ends_at=deadline, target_matches=3, rotation_runtime_id="g7", **extra)
        _write_json(self.root / "game_switch.json", {"phase": "ready", "active":
                    {"game": game, "runtime_id": "g7"}})
        log = self.root / f"scores/{game}.jsonl"
        log.parent.mkdir(exist_ok=True)
        log.write_text("\n".join(json.dumps(row) for row in [
            {"game": game, "score": 900, "ts": 90},
            {"game": game, "score": 10, "ts": 110},
            {"game": game, "score": 30, "ts": 160},
            {"game": game, "score": 999, "ts": 300},
            {"game": "other", "score": 888, "ts": 150}]))
        return load_active_corner(self.root, now=200)

    def test_session_excludes_previous_future_and_other_game_results(self):
        for game in ["ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console"]:
            with self.subTest(game=game):
                c = self.console(game=game)
                p = c["console"]
                self.assertNotIn(999, [r["score"] for r in c["scores"]])
                self.assertEqual((p["session_count"], p["session_best"], p["session_mean"]), (2, 30, 20))
                self.assertEqual((p["remaining"], p["remaining_seconds"]), (1, 300))
                self.assertEqual(p["latest"], {"score": 30, "at": 160, "age": 40})
                text = "\n".join(sd.render_console_progress(p))
                self.assertIn("Session: n=2 / best=30 / mean=20", text)
                self.assertIn("1 matches left / limit 5:00", text)
                self.assertIn("40s ago", text)
                self.assertIn("live score unobserved", text)
                self.assertNotIn("999", text)
                self.assertNotIn("900", text)
                for line in sd.render_console_progress(p):
                    self.assertLessEqual(sd.ansi_display_width(line), sd.W)
                self.assertEqual(sd.fit_dashboard_lines(sd.render_console_progress(p)),
                                 sd.render_console_progress(p))

    def test_absent_invalid_and_undated_history_are_unknown_not_zero(self):
        self.console()
        path = self.root / "scores/nsnake.jsonl"
        for content in [None, "{partial", json.dumps({"game": "nsnake", "score": 42})]:
            with self.subTest(content=content):
                if content is None: path.unlink()
                else: path.write_text(content)
                p = load_active_corner(self.root, now=200)["console"]
                self.assertIsNone(p["session_count"])
                self.assertIsNone(p["remaining"])
                self.assertIsNone(p["latest"])
                self.assertIn("history unavailable", "\n".join(sd.render_console_progress(p)))
        path.write_text("")
        self.assertEqual(load_active_corner(self.root, now=200)["console"]["session_count"], 0)

    def test_unidentified_history_rows_make_session_unknown(self):
        self.console()
        path = self.root / "scores/nsnake.jsonl"
        valid = {"game": "nsnake", "score": 10, "ts": 110}
        for invalid in [None, [], {}, {"score": 42, "ts": 150},
                        {"game": None}, {"game": ""}, {"game": 7}]:
            for rows in [[invalid], [valid, invalid]]:
                with self.subTest(rows=rows):
                    path.write_text("\n".join(json.dumps(row) for row in rows))
                    p = load_active_corner(self.root, now=200)["console"]
                    self.assertEqual(p["history_status"], "partial")
                    self.assertIsNone(p["session_count"])
                    self.assertIsNone(p["remaining"])
                    self.assertIsNone(p["latest"])
                    text = "\n".join(sd.render_console_progress(p))
                    self.assertIn("history unavailable", text)
                    self.assertNotIn("n=0", text)
                    self.assertNotIn("matches left", text)

    def test_identified_other_game_rows_do_not_make_history_partial(self):
        self.console()
        path = self.root / "scores/nsnake.jsonl"
        other = {"game": "bastet", "score": 42, "ts": 150}
        valid = {"game": "nsnake", "score": 10, "ts": 110}
        for rows, count in [([other], 0), ([valid, other], 1)]:
            with self.subTest(rows=rows):
                path.write_text("\n".join(json.dumps(row) for row in rows))
                p = load_active_corner(self.root, now=200)["console"]
                self.assertEqual(p["history_status"], "readable")
                self.assertEqual(p["session_count"], count)
                self.assertEqual(p["remaining"], 3 - count)

    def test_old_same_game_runtime_and_switching_never_claim_current_plan(self):
        self.console()
        for canonical in [{"phase": "ready", "active": {"game": "nsnake", "runtime_id": "g8"}},
                          {"phase": "draining", "active": {"game": "nsnake", "runtime_id": "g7"}},
                          {"phase": "ready", "active": {"game": "bastet", "runtime_id": "g7"}}, {}]:
            _write_json(self.root / "game_switch.json", canonical)
            p = load_active_corner(self.root, now=200)["console"]
            self.assertEqual(p["next"], "unverified")
            if canonical.get("phase") != "draining":
                self.assertIsNone(p["session_count"])
                self.assertIsNone(p["latest"])
            self.assertNotIn("matches left", "\n".join(sd.render_console_progress(p)))

    def test_target_time_and_transition_plans_are_distinct(self):
        for status, start, end, expected in [
                ("starting", 100, 500, "switch-wait"), ("restoring", 100, 500, "restore-wait"),
                ("active", 100, 150, "deadline-passed"), ("active", 50, 500, "target-recorded")]:
            c = self.console(status=status, start=start, deadline=end)
            self.assertEqual(c["console"]["next"], expected)
            if status == "starting": self.assertIsNone(c["console"]["session_count"])
        c = self.console(start=300)
        self.assertIsNone(c["console"]["session_count"])
        self.assertIsNone(c["console"]["remaining"])

    def test_reasons_are_allowlisted_and_other_corners_are_unchanged(self):
        c = self.console(status="restoring", end_reason="manual_saved_stop", last_error="private detail")
        self.assertIn("Reason: saved stop (record)", "\n".join(sd.render_console_progress(c["console"])))
        self.assertNotIn("private", "\n".join(sd.render_docich_corner_stats(c)))
        c = self.console(end_reason=["arbitrary"], last_error_code="sentinel-secret")
        self.assertIsNone(c["console"]["reason"])
        c = self.console(game="robots")
        self.assertNotIn("console", c)

    def test_every_reason_keeps_live_score_unobserved_in_the_fitted_feed(self):
        for reason in ["game_over", "screen_stalled", "manual_saved_stop", "manual_forced_stop",
                       "switch-terminal-before-corner-active", "recovery_required", "quiesce_failed",
                       "readiness_timeout", "deadline_exceeded"]:
            with self.subTest(reason=reason):
                c = self.console(status="restoring", end_reason=reason, last_error_code=reason)
                lines = sd.render_console_progress(c["console"])
                self.assertEqual(len(lines), 4)
                self.assertIn("live score unobserved", lines[3])
                self.assertLessEqual(sd.ansi_display_width(lines[3]), sd.W)
                self.assertEqual(sd.fit_dashboard_lines(lines), lines)
