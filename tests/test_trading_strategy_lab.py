from __future__ import annotations

import json
import stat
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.trading.events import build_fill_event  # noqa: E402
from docich.trading.market_data import MarketFrame  # noqa: E402
from docich.trading.models import PaperFill  # noqa: E402
from docich.trading.paper_improve import run_paper_improve  # noqa: E402
from docich.trading.presentation import render_notification  # noqa: E402
from docich.trading.strategies import (  # noqa: E402
    scan_exit_opportunities,
    scan_opportunities,
)
from docich.trading.strategy_lab import (  # noqa: E402
    StrategyExperiment,
    evaluate_experiment,
    experiment_from_mapping,
    load_strategy_experiment,
    persist_evaluation,
    save_strategy_experiment,
)
from docich.trading.strategy_runtime import set_active_experiment  # noqa: E402

D = Decimal
NOW = 1_800_000_000.0


def frame(symbol: str, prices: list[object]) -> MarketFrame:
    count = len(prices)
    return MarketFrame(
        symbol=symbol,
        timeframe_seconds=300,
        timestamps=tuple(NOW - (count - index - 1) * 300 for index in range(count)),
        closes=tuple(D(str(value)) for value in prices),
        volumes=tuple(D("1") for _ in prices),
    )


def paper_fill(opportunity, *, price: str = "101.5") -> PaperFill:
    return PaperFill(
        fill_id=f"paper:{opportunity.opportunity_id}",
        opportunity_id=opportunity.opportunity_id,
        strategy_id=opportunity.strategy_id,
        symbol=opportunity.symbol,
        side=opportunity.side,
        quote="JPY",
        amount=D("1"),
        price=D(price),
        quote_notional=D(price),
        reference_notional=D(price),
        reason_code=opportunity.reason_code,
        filled_at=NOW,
    )


def experiment_payload(experiment_id: str = "lab-a") -> dict:
    return {
        "experiment_id": experiment_id,
        "name": "複合モメンタム実験",
        "thesis": "値動きとRSIを組み合わせ、単純な閾値より誤発注を減らせるか検証する。",
        "entry_rules": [
            {
                "rule_id": "entry-1",
                "combine": "all",
                "max_notional_fraction": 0.10,
                "conditions": [
                    {"feature": "return_bps", "lookback": 3, "op": ">=", "threshold": 100},
                    {"feature": "rsi", "lookback": 3, "op": ">=", "threshold": 60},
                ],
            }
        ],
        "exit_rules": [
            {
                "rule_id": "exit-1",
                "combine": "any",
                "conditions": [
                    {"feature": "pnl_bps", "op": ">=", "threshold": 150},
                    {"feature": "hold_minutes", "op": ">=", "threshold": 90},
                ],
            }
        ],
        "max_pair_correlation": 0.70,
    }


def _global(root: Path):
    cfg = root / "docich.toml"
    cfg.write_text(
        '[paths]\nstate_dir = "run"\n[trading]\npaper_worker_enabled = true\n',
        encoding="utf-8",
    )
    return config.load_global(root, config_path=cfg)


def _trading_dir(g) -> Path:
    target = g.state_dir / "trading"
    target.mkdir(parents=True, exist_ok=True)
    (target / "status.json").write_text(
        json.dumps({
            "worker_state": "paper_worker_idle",
            "capital_reference": "10000",
            "deployed_reference": "0",
            "open_positions": {},
            "recent_fills": [],
            "skipped_reason_codes": [],
            "signal_summary": {"candidate_count": 0},
        }),
        encoding="utf-8",
    )
    return target


def test_take_profit_brief_says_observed_move_and_threshold():
    set_active_experiment(None)
    frames = {"BTC/JPY": frame("BTC/JPY", [100] * 9 + [101.5])}
    exits = scan_exit_opportunities(
        frames, {"BTC/JPY": (D("1"), D("100"), NOW - 600)}, now=NOW
    )
    assert len(exits) == 1 and exits[0].reason_code == "take_profit"
    event = build_fill_event(paper_fill(exits[0]))
    event["realized_pnl_reference"] = "1.5"
    rendered = render_notification(event, mode="compact")
    assert "平均取得価格から1.5%上昇" in rendered.speech_text
    assert "利確基準1%以上" in rendered.speech_text
    assert "BTC/JPYを売り、損益プラス1.5円です。" in rendered.speech_text
    assert "利確条件を検出" not in rendered.speech_text


def test_stop_loss_and_max_hold_briefs_say_actual_conditions():
    set_active_experiment(None)
    loss_frame = {"BTC/JPY": frame("BTC/JPY", [100] * 9 + [96.5])}
    loss = scan_exit_opportunities(
        loss_frame, {"BTC/JPY": (D("1"), D("100"), NOW - 600)}, now=NOW
    )[0]
    loss_event = build_fill_event(paper_fill(loss, price="96.5"))
    loss_event["realized_pnl_reference"] = "-3.5"
    loss_text = render_notification(loss_event, mode="detailed").speech_text
    assert "平均取得価格から3.5%下落" in loss_text
    assert "損切り基準3%以上" in loss_text

    set_active_experiment(None)
    flat_frame = {"BTC/JPY": frame("BTC/JPY", [100] * 10)}
    timed = scan_exit_opportunities(
        flat_frame, {"BTC/JPY": (D("1"), D("100"), NOW - 7 * 3600)}, now=NOW
    )[0]
    timed_event = build_fill_event(paper_fill(timed, price="100"))
    timed_event["realized_pnl_reference"] = "0"
    timed_text = render_notification(timed_event, mode="compact").speech_text
    assert "保有7時間" in timed_text
    assert "最大保有6時間を超過" in timed_text


def test_strategy_experiment_is_private_and_drives_entry_and_exit(tmp_path):
    spec = experiment_from_mapping(experiment_payload())
    path = save_strategy_experiment(tmp_path, spec, activated_at=NOW - 3600)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_strategy_experiment(tmp_path)
    assert loaded is not None
    assert loaded.experiment_id == "lab-a"
    assert loaded.max_pair_correlation == D("0.70")

    set_active_experiment(loaded)
    rising = {"BTC/JPY": frame("BTC/JPY", [100, 100, 100, 100, 101, 102, 103])}
    entries = scan_opportunities(rising, now=NOW)
    assert len(entries) == 1
    assert entries[0].reason_code == "paper_lab_entry"
    entry_event = build_fill_event(paper_fill(entries[0], price="103"))
    entry_text = render_notification(entry_event, mode="compact").speech_text
    assert "直近3本" in entry_text
    assert "RSI3" in entry_text
    assert "BTC/JPYを買い。" in entry_text

    set_active_experiment(loaded)
    exits = scan_exit_opportunities(
        rising, {"BTC/JPY": (D("1"), D("100"), NOW - 30 * 60)}, now=NOW
    )
    assert len(exits) == 1
    assert exits[0].reason_code == "paper_lab_exit"
    exit_event = build_fill_event(paper_fill(exits[0], price="103"))
    exit_event["realized_pnl_reference"] = "3"
    exit_text = render_notification(exit_event, mode="compact").speech_text
    assert "平均取得価格から3%上昇" in exit_text
    assert "BTC/JPYを売り、損益プラス3円です。" in exit_text


def test_paper_improve_prefers_strategy_experiment_and_legacy_still_works(tmp_path):
    g = _global(tmp_path)
    trading_dir = _trading_dir(g)
    response = json.dumps({"strategy_experiment": experiment_payload("lab-new")})
    result = run_paper_improve(
        g, trading_dir=trading_dir, agents="opencode:x", llm=lambda prompt: response, now=NOW
    )
    assert result["status"] == "improved"
    assert result["kind"] == "strategy-experiment"
    assert result["activated"] is True
    loaded = load_strategy_experiment(trading_dir)
    assert loaded is not None and loaded.experiment_id == "lab-new"

    legacy = json.dumps({
        "momentum_lookback": 8,
        "momentum_threshold_bps": 250,
        "mean_reversion_lookback": 12,
        "mean_reversion_z": -2.0,
        "max_notional_fraction": 0.2,
    })
    other = _global(tmp_path / "legacy")
    legacy_dir = _trading_dir(other)
    old = run_paper_improve(
        other, trading_dir=legacy_dir, agents="opencode:x", llm=lambda prompt: legacy, now=NOW
    )
    assert old["status"] == "improved"
    assert old["kind"] == "legacy-policy"


def test_fresh_active_experiment_keeps_next_candidate_pending(tmp_path):
    g = _global(tmp_path)
    trading_dir = _trading_dir(g)
    active = experiment_from_mapping(experiment_payload("active"))
    save_strategy_experiment(trading_dir, active, activated_at=NOW - 60)
    response = json.dumps({"strategy_experiment": experiment_payload("next")})
    result = run_paper_improve(
        g, trading_dir=trading_dir, agents="opencode:x", llm=lambda prompt: response, now=NOW
    )
    assert result["status"] == "improved"
    assert result["pending"] is True
    assert result["activated"] is False
    assert load_strategy_experiment(trading_dir).experiment_id == "active"
    pending = json.loads((trading_dir / "paper_strategy_pending.json").read_text(encoding="utf-8"))
    assert pending["experiment_id"] == "next"


def test_promotion_candidate_is_evidence_only_and_private(tmp_path):
    spec = experiment_from_mapping(experiment_payload())
    evaluation = {
        "schema_version": 1,
        "experiment_id": "lab-a",
        "closed_sells": 25,
        "wins": 16,
        "realized_pnl_jpy": "1000",
        "profit_factor": "1.5",
        "max_realized_drawdown_pct": "4",
        "promotion_ready": True,
    }
    persist_evaluation(tmp_path, evaluation, spec)
    path = tmp_path / "paper_strategy_promotion_candidate.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "candidate"
    assert "自動反映は行わない" in payload["note"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_evaluate_experiment_with_no_closed_sells_is_not_promotable(tmp_path):
    spec = StrategyExperiment(**{
        **experiment_from_mapping(experiment_payload()).__dict__,
        "activated_at": NOW - 3600,
    })
    evaluation = evaluate_experiment(tmp_path, spec, capital_jpy="10000")
    assert evaluation["closed_sells"] == 0
    assert evaluation["promotion_ready"] is False
