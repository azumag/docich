"""Display of dedicated per-symbol PAPER slots (Issue #3623 §2.9, stage 2 PR-4)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import soren_corner_text as sd  # noqa: E402
from docich.soren_corner_stats import load_active_corner  # noqa: E402
from docich.trading import corner_script  # noqa: E402
from docich.trading.dashboard_snapshot import build_dashboard_snapshot  # noqa: E402
from docich.trading.experiment_control import CONTROL_FILENAME, ExperimentStep  # noqa: E402
from docich.trading.ledger import PaperLedger  # noqa: E402
from docich.trading.slot_overview import build_slot_overview  # noqa: E402
from docich.trading.slot_runtime import slot_summary  # noqa: E402
from docich.trading.status import _WORKER_SUMMARY_KEYS  # noqa: E402
from docich.trading.strategy_lab import (  # noqa: E402
    EVALUATION_FILENAME, experiment_from_mapping, save_strategy_experiment,
)
from docich.trading.symbol_slots import create_slot, slot_dir  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000.0


def _spec(experiment_id, thesis="押し目を拾う。"):
    return experiment_from_mapping({
        "experiment_id": experiment_id,
        "name": f"{experiment_id} name",
        "thesis": thesis,
        "entry_rules": [{
            "rule_id": "entry-1", "combine": "all",
            "conditions": [{"feature": "return_bps", "lookback": 3, "op": ">=", "threshold": 50}],
        }],
        "exit_rules": [{
            "rule_id": "exit-1", "combine": "all",
            "conditions": [{"feature": "hold_minutes", "op": ">=", "threshold": 360}],
        }],
    })


def _slot(target, symbol, experiment_id, *, activated_at, closed_sells=None,
          evaluation_id=None, entries=True, status="active"):
    directory = create_slot(target, symbol, now=activated_at)
    save_strategy_experiment(directory, _spec(experiment_id), activated_at=activated_at)
    (directory / CONTROL_FILENAME).write_text(json.dumps({
        "schema_version": 1, "status": status, "reason_code": "collecting",
        "entries_allowed": entries, "evaluation_status": "ok", "experiment_id": experiment_id,
        "activated_at": activated_at, "open_position_count": 0, "updated_at": NOW,
        "active_key": "x" * 64, "pending_available": False, "drain_reason_code": None,
    }), encoding="utf-8")
    if closed_sells is not None:
        (directory / EVALUATION_FILENAME).write_text(json.dumps({
            "status": "ok", "reason_code": "evaluated",
            "experiment_id": evaluation_id or experiment_id, "activated_at": activated_at,
            "closed_sells": closed_sells, "evaluated_at": NOW,
        }), encoding="utf-8")
    return directory


def _trading(tmp_path):
    target = tmp_path / "trading"
    target.mkdir(parents=True)
    return target


def test_slot_summary_counts_slots_and_symbols_left_to_the_common_slot():
    steps = {
        "ADA/JPY": ExperimentStep(None, None, True, "active", "collecting"),
        "XYM/JPY": ExperimentStep(None, None, False, "blocked", "control-error"),
    }
    summary = slot_summary(steps, ["ADA/JPY", "XYM/JPY", "BTC/JPY"])
    assert summary == {
        "slot_count": 2, "slot_active_count": 1, "slot_entries_allowed_count": 1,
        "slot_blocked_count": 1, "slot_layout_invalid": False, "common_owned_symbol_count": 1,
    }
    assert set(summary) <= _WORKER_SUMMARY_KEYS


def test_overview_lists_focus_symbols_first_and_only_trusts_the_current_activation(tmp_path):
    target = _trading(tmp_path)
    _slot(target, "ADA/JPY", "ada_1", activated_at=NOW - 86400, closed_sells=4)
    _slot(target, "XYM/JPY", "xym_1", activated_at=NOW - 2 * 86400, closed_sells=9,
          evaluation_id="xym_0")  # evaluation from a previous activation
    _slot(target, "SUI/JPY", "sui_1", activated_at=NOW - 3600, closed_sells=1, entries=False)

    overview = build_slot_overview(target, now=NOW, universe=["ADA/JPY", "XYM/JPY", "SUI/JPY", "BTC/JPY"],
                                   focus_symbols=["SUI/JPY", "ETH/JPY"])

    assert overview["slot_count"] == 3
    assert overview["entries_allowed_count"] == 2
    assert overview["common_owned_symbol_count"] == 1
    assert overview["rotation_closed_sells"] == 10 and overview["rotation_days"] == 7
    assert [item["symbol"] for item in overview["slots"]] == ["SUI/JPY", "ADA/JPY", "XYM/JPY"]
    by_symbol = {item["symbol"]: item for item in overview["slots"]}
    assert by_symbol["ADA/JPY"]["closed_sells"] == 4
    assert by_symbol["ADA/JPY"]["age_days"] == 1.0
    assert by_symbol["ADA/JPY"]["thesis"] == "押し目を拾う。"
    assert by_symbol["XYM/JPY"]["closed_sells"] is None
    assert overview["closed_sells_total"] == 5


def test_thesis_is_clipped_at_a_sentence_break():
    from docich.trading.slot_overview import _clip_sentence

    assert _clip_sentence("短い。") == "短い。"
    assert _clip_sentence("一文目。" + "あ" * 200) == "一文目。"
    assert len(_clip_sentence("あ" * 300)) == 160


def test_overview_without_slots_is_empty_and_never_raises(tmp_path):
    target = _trading(tmp_path)
    overview = build_slot_overview(target, now=NOW)
    assert overview["slot_count"] == 0 and overview["slots"] == []
    (target / "slots").write_text("not a directory", encoding="utf-8")
    assert build_slot_overview(target, now=NOW)["slot_count"] == 0


def test_dashboard_snapshot_carries_the_slot_overview(tmp_path):
    target = _trading(tmp_path)
    PaperLedger(target / "paper.sqlite3").close()
    (target / "status.json").write_text(json.dumps({
        "worker_state": "paper_worker_idle", "capital_reference": "10000",
        "deployed_reference": "0", "open_positions": {}, "recent_fills": [],
        "eligible_symbols": ["ADA/JPY"], "skipped_reason_codes": [],
        "signal_summary": {"candidate_count": 0},
    }), encoding="utf-8")
    _slot(target, "ADA/JPY", "ada_1", activated_at=NOW - 3600, closed_sells=2)
    snapshot = build_dashboard_snapshot(target, now=NOW)
    assert snapshot["slots"]["slot_count"] == 1
    assert snapshot["slots"]["common_owned_symbol_count"] == 0
    assert snapshot["slots"]["slots"][0]["experiment_id"] == "ada_1"


def test_narration_describes_slot_strategies_not_the_common_policy():
    facts = {
        "policy": {"momentum_lookback": 3, "momentum_threshold_bps": "40",
                   "mean_reversion_lookback": 20, "mean_reversion_z": "-2",
                   "max_notional_fraction": "0.1"},
        **corner_script._slot_facts({
            "slot_count": 43, "entries_allowed_count": 43, "common_owned_symbol_count": 0,
            "rotation_closed_sells": 10, "rotation_days": 7,
            "slots": [
                {"symbol": "XYM/JPY", "experiment_id": "xym_1", "name": "反発狙い",
                 "thesis": "下げた後の戻りを取る。", "closed_sells": 2, "age_days": 0.3,
                 "entries_allowed": True},
                {"symbol": "ADA/JPY", "experiment_id": None, "name": None, "thesis": ""},
            ],
        }),
    }
    assert [item["symbol"] for item in facts["slot_strategies"]] == ["XYM/JPY"]
    text = corner_script._slot_strategy_text(facts)
    assert "43銘柄" in text and "XYMの戦略は「反発狙い」" in text and "決済2件" in text
    assert "二本立て" not in text
    assert "下げた後の戻り" not in text  # the AI's working note is not read verbatim
    assert facts["slot_strategies"][0]["candidate_note"] == "下げた後の戻りを取る。"  # but the AI sees it
    assert corner_script._slot_facts({"slot_count": 0}) == {}


def _prompt_facts(with_slots):
    facts = {"policy": {"momentum_lookback": 3}, "research": {}}
    if with_slots:
        facts.update(corner_script._slot_facts({
            "slot_count": 1, "entries_allowed_count": 1, "common_owned_symbol_count": 0,
            "rotation_closed_sells": 10, "rotation_days": 7,
            "slots": [{"symbol": "APE/JPY", "experiment_id": "ape_1", "name": "急落リバウンド",
                       "thesis": "現状26決済/7勝。", "closed_sells": 1, "age_days": 0.3,
                       "entries_allowed": True}],
        }))
    return facts


def test_every_narration_prompt_carries_the_slot_rules():
    prompts = [
        corner_script.build_prompt(_prompt_facts(True)),
        corner_script.build_next_prompt(_prompt_facts(True), [], target_key="strategy"),
        corner_script.build_next_prompt(_prompt_facts(True), [], target_key="fills"),
        corner_script.build_next_prompt(_prompt_facts(True), []),
    ]
    for prompt in prompts:
        assert "【銘柄別の戦略】" in prompt
        assert "facts.policy は専用の戦略を持たない銘柄の既定値" in prompt
        assert "候補を作ったときの改善AIのメモで、今の成績ではない" in prompt
    assert "【今回の固定枠】strategy（銘柄別の戦略）" in prompts[1]


def test_narration_prompts_without_slots_are_unchanged():
    for prompt in (corner_script.build_prompt(_prompt_facts(False)),
                   corner_script.build_next_prompt(_prompt_facts(False), [], target_key="strategy")):
        assert "facts.policy は専用の戦略を持たない銘柄の既定値" not in prompt
    assert "【今回の固定枠】strategy（戦略パラメータ）" in corner_script.build_next_prompt(
        _prompt_facts(False), [], target_key="strategy")


def _paper_status(tmp_path, worker_summary):
    root = tmp_path / "state"
    (root / "trading").mkdir(parents=True)
    (root / "paper_corner.json").write_text(json.dumps({"schema_version": 1, "status": "active"}),
                                            encoding="utf-8")
    (root / "trading/status.json").write_text(json.dumps({
        "worker_state": "paper_worker_running", "capital_reference": "10000",
        "deployed_reference": "0", "open_positions": {}, "recent_fills": [],
        "snapshot_generated_at": 190, "heartbeat_at": 198,
        "worker_summary": {"cycle_index": 1, "experiment_status": "active",
                           "experiment_reason_code": "collecting",
                           "experiment_entries_allowed": True, **worker_summary},
    }), encoding="utf-8")
    return root


def test_broadcast_status_shows_slot_counts_and_hides_an_empty_common_slot(tmp_path):
    root = _paper_status(tmp_path, {
        "slot_count": 43, "slot_entries_allowed_count": 42, "slot_blocked_count": 1,
        "common_owned_symbol_count": 0, "slot_layout_invalid": False,
    })
    lines = sd.render_docich_corner_stats(load_active_corner(root, now=200))
    rendered = "\n".join(lines)
    assert "Slots: 43 on=42 blocked=1 common=0" in rendered
    assert "Experiment:" not in rendered
    for line in lines:
        assert sd.ansi_display_width(line) <= sd.W


def test_broadcast_status_keeps_the_common_experiment_while_it_owns_symbols(tmp_path):
    root = _paper_status(tmp_path, {"slot_count": 2, "slot_entries_allowed_count": 2,
                                    "slot_blocked_count": 0, "common_owned_symbol_count": 5})
    rendered = "\n".join(sd.render_docich_corner_stats(load_active_corner(root, now=200)))
    assert "Slots: 2 on=2 blocked=0 common=5" in rendered
    assert "Experiment: status=active entries=on" in rendered


def test_broadcast_status_without_slots_is_unchanged(tmp_path):
    rendered = "\n".join(sd.render_docich_corner_stats(load_active_corner(_paper_status(tmp_path, {}), now=200)))
    assert "Slots:" not in rendered
    assert "Experiment: status=active" in rendered


def _diagnostics():
    spec = importlib.util.spec_from_file_location(
        "slot_diag", ROOT / "ops" / "vm_actions" / "collect_diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_diagnostics_aggregates_slots_and_fits_the_gateway(tmp_path):
    state = tmp_path / "state"
    target = state / "trading"
    target.mkdir(parents=True)
    for index, symbol in enumerate(["ADA/JPY", "XYM/JPY", "SUI/JPY", "BAT/JPY", "APE/JPY", "BNB/JPY"]):
        _slot(target, symbol, f"e{index}", activated_at=NOW - 3600 * (index + 1),
              closed_sells=index, entries=symbol != "BAT/JPY",
              status="blocked" if symbol == "BAT/JPY" else "active")
    (target / "slots" / "junk").mkdir()
    (target / "paper_improve_slots_status.json").write_text(json.dumps({
        "schema_version": 1, "status": "improved", "targets": 43, "calls": 11,
        "queued": 0, "activated": 43, "rejected": 0, "deferred": 0,
        "started_at": NOW - 7200, "completed_at": NOW - 5700,
        "results": {"ADA/JPY": "activated"},
    }), encoding="utf-8")
    module = _diagnostics()
    slots = module._collect_paper_slots(state, NOW)
    assert slots["slot_count"] == 6 and slots["invalid_count"] == 1
    assert slots["status_counts"] == {"active": 5, "blocked": 1}
    assert slots["entries_allowed_count"] == 5
    assert slots["closed_sells_total"] == 15
    assert slots["nearest_rotation"][0]["symbol"] == "BAT/JPY"  # not trading comes first
    assert slots["nearest_rotation"][1]["symbol"] == "BNB/JPY"  # 5/10 sells
    assert len(slots["nearest_rotation"]) == 3
    assert slots["improve"]["status"] == "improved" and slots["improve"]["activated"] == 43
    assert "results" not in slots["improve"]
    raw = json.dumps(slots)
    assert "e0" not in raw and "押し目" not in raw  # no strategy identity or rule text

    gateway_spec = importlib.util.spec_from_file_location(
        "slot_diag_gateway", ROOT / "ops" / "vm_actions" / "gateway.py")
    gateway = importlib.util.module_from_spec(gateway_spec)
    gateway_spec.loader.exec_module(gateway)
    payload = {"status": "ok", "corners": {"paper_slots": slots}}
    clean = gateway._sanitize_diagnostics(payload)
    assert clean == payload
    assert len(json.dumps(slots).encode("utf-8")) < 1024  # production headroom is ~4.7 KiB


def test_diagnostics_without_slots_reports_absent(tmp_path):
    state = tmp_path / "state"
    (state / "trading").mkdir(parents=True)
    slots = _diagnostics()._collect_paper_slots(state, NOW)
    assert slots["present"] is False and slots["slot_count"] == 0
    assert slots["improve"] == {"present": False, "readable": False, "age_sec": slots["improve"]["age_sec"]}


def test_slot_dir_layout_matches_the_diagnostics_pattern(tmp_path):
    target = _trading(tmp_path)
    directory = create_slot(target, "ADA/JPY", now=NOW)
    assert directory == slot_dir(target, "ADA/JPY")
    assert _diagnostics().PAPER_SLOT_DIR_RE.fullmatch(directory.name)
