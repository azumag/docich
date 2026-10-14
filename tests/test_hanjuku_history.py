import json

from docich import hanjuku_chart, hanjuku_chart_review
from docich.retro_corner import describe_strategy_change


def _write_completed_review(state_dir, generation, *, terminal_reason="game_over",
                            report_identity=None):
    runtime_id = f"g{generation}-abcdef"
    runtime_dir = state_dir / "runtimes" / runtime_id
    runtime_dir.mkdir(parents=True)
    identity = {
        "game": "hanjuku-hero",
        "runtime_id": runtime_id,
        "generation": generation,
        "lease_id": f"lease-{generation}",
    }
    (runtime_dir / "hanjuku_run.json").write_text(json.dumps({
        **identity,
        "terminal_reason": terminal_reason,
        "frame_sha256": "a" * 64,
        "name_entered": True,
        "gameplay_seen": True,
        "phase": "title",
        "title_count": 3,
        "observed_monotonic": 10.0,
        "title_since": 8.0,
    }), encoding="utf-8")

    success_target = hanjuku_chart.orders(1)[0]["target"]
    missed_target = hanjuku_chart.orders(1)[1]["target"]
    base_target = hanjuku_chart.orders(1)[2]["target"]
    report = {
        "schema": hanjuku_chart_review.SCHEMA,
        "generated_at": 1_800_000_000.0,
        **(identity if report_identity is None else report_identity),
        "steps": {
            "base-1": {
                "kind": "base", "wins": 1, "losses": 2, "launched": 1,
                "target": base_target, "captured_target": False,
            },
            "adjusted-success": {
                "kind": "adjusted", "wins": 2, "losses": 0, "launched": 1,
                "target": success_target, "captured_target": True,
            },
            "adjusted-missed": {
                "kind": "adjusted", "wins": 0, "losses": 1, "launched": 0,
                "failed": 0, "target": missed_target, "captured_target": False,
            },
        },
        "adjusted_orders": {"a": {}, "b": {}},
        "proposals": [
            {"type": "promote_adjusted_step", "order": {"target": success_target}},
            {"type": "promote_interim_attack", "target": success_target},
            {"type": "review_base_step", "target": base_target},
        ],
    }
    (runtime_dir / hanjuku_chart_review.REVIEW_FILE).write_text(
        json.dumps(report), encoding="utf-8"
    )
    return runtime_dir, report


def test_hanjuku_start_announcement_summarizes_latest_verified_run(tmp_path):
    _write_completed_review(tmp_path, 5)
    # A newer run that ended by stalling must not replace the last game-over review.
    _write_completed_review(tmp_path, 6, terminal_reason="screen_stalled")

    message = describe_strategy_change(tmp_path, "hanjuku-hero")

    assert "直近の完走ランは3勝3敗" in message
    assert "調整チャート2手を作成し、1手を実行" in message
    assert "調整チャートでは" + hanjuku_chart.orders(1)[0]["target"] + "を攻略" in message
    assert "臨時判断では" + hanjuku_chart.orders(1)[0]["target"] + "を攻略" in message
    assert "調整手順の見直し候補は" + hanjuku_chart.orders(1)[1]["target"] in message
    assert "基本手順の見直し候補は" + hanjuku_chart.orders(1)[2]["target"] in message
    assert "今回は前回の実績も案内に反映" in message
    assert "比較に使える同じゲームの過去戦略記録が見つからない" not in message


def test_hanjuku_announcement_rejects_review_with_mismatched_run_identity(tmp_path):
    runtime_dir, report = _write_completed_review(tmp_path, 5)
    report["lease_id"] = "different-lease"
    (runtime_dir / hanjuku_chart_review.REVIEW_FILE).write_text(
        json.dumps(report), encoding="utf-8"
    )

    message = describe_strategy_change(tmp_path, "hanjuku-hero")

    assert message == (
        "過去の完走レビュー記録が見つからないため、"
        "今回は第1話の基本チャートから始めます。"
    )
