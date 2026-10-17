"""Read-only scorelog projection backing the #2392 acceptance check.

The wrapper regression (a lingering game-over pane re-recorded every poll) is
only visible in ``<state_dir>/scores/<game>.jsonl``.  These tests pin the
bounded projection that lets the owner-only diagnostics show it without
exporting the raw log.
"""
import importlib.util
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
DIAG_PATH = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
spec = importlib.util.spec_from_file_location("collect_diagnostics_retro_scores_test", DIAG_PATH)
collect_diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect_diagnostics)


def _write_scores(state_dir, game, rows):
    scores_dir = state_dir / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    (scores_dir / f"{game}.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_retro_scores_reports_records_and_longest_duplicate_run(tmp_path):
    state_dir = tmp_path / "state"
    _write_scores(state_dir, "bastet", [
        {"ts": 100, "game": "bastet", "score": 0, "source": "wrapper"},
        {"ts": 101, "game": "bastet", "score": 300, "source": "wrapper",
         "clip_path": "/home/ubuntu/secret-clip.mp4", "sha256": "do-not-project"},
        *[{"ts": 102 + i, "game": "bastet", "score": 300, "source": "wrapper"}
          for i in range(6)],
        {"ts": 200, "game": "bastet", "score": 100, "source": "agent"},
    ])

    result = collect_diagnostics._collect_retro_scores(state_dir, now=int(time.time()))
    bastet = result["games"]["bastet"]

    assert result["present"] is True
    assert result["game_count"] == len(collect_diagnostics.RETRO_SCORELOG_GAMES)
    assert bastet["present"] is True
    assert bastet["readable"] is True
    assert bastet["truncated"] is False
    assert bastet["records"] == 9
    assert bastet["invalid"] == 0
    # The seven consecutive 300s are the regression signature; the initial 0
    # score stays a recorded match (one record for a zero-score match).
    assert bastet["max_same_score_run"] == 7
    assert bastet["sources"] == {
        "agent": 1, "live": 0, "wrapper": 8, "other": 0,
    }
    assert bastet["age_sec"] >= 0
    assert bastet["last"] == [
        {"ts": 104, "score": 300, "source": "wrapper"},
        {"ts": 105, "score": 300, "source": "wrapper"},
        {"ts": 106, "score": 300, "source": "wrapper"},
        {"ts": 107, "score": 300, "source": "wrapper"},
        {"ts": 200, "score": 100, "source": "agent"},
    ]
    text = json.dumps(result)
    assert "do-not-project" not in text
    assert "secret-clip" not in text


def test_retro_scores_counts_invalid_rows_without_echoing_them(tmp_path):
    state_dir = tmp_path / "state"
    scores_dir = state_dir / "scores"
    scores_dir.mkdir(parents=True)
    (scores_dir / "pacman4console.jsonl").write_text(
        "\n".join([
            "not json at all",
            json.dumps([1, 2, 3]),
            json.dumps({"ts": 1, "score": "523", "source": "wrapper"}),
            json.dumps({"ts": 2, "score": -1, "source": "wrapper"}),
            json.dumps({"ts": 3, "score": True, "source": "wrapper"}),
            json.dumps({"ts": 4, "score": 523, "source": "unknown-producer"}),
            json.dumps({"ts": 5, "score": 523, "source": "wrapper"}),
        ]) + "\n",
        encoding="utf-8",
    )

    entry = collect_diagnostics._collect_retro_scores(state_dir, now=int(time.time()))["games"]["pacman4console"]

    assert entry["records"] == 2
    assert entry["invalid"] == 5
    assert entry["sources"] == {"agent": 0, "live": 0, "wrapper": 1, "other": 1}
    assert entry["max_same_score_run"] == 2
    assert entry["last"] == [
        {"ts": 4, "score": 523, "source": "other"},
        {"ts": 5, "score": 523, "source": "wrapper"},
    ]


def test_retro_scores_bounds_the_scanned_tail(tmp_path):
    state_dir = tmp_path / "state"
    scores_dir = state_dir / "scores"
    scores_dir.mkdir(parents=True)
    rows = ["x" * 200 for _ in range(600)]
    rows.append(json.dumps({"ts": 9, "score": 7, "source": "wrapper"}))
    (scores_dir / "nsnake.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    entry = collect_diagnostics._collect_retro_scores(state_dir, now=int(time.time()))["games"]["nsnake"]

    assert entry["truncated"] is True
    assert entry["records"] == 1
    assert entry["last"] == [{"ts": 9, "score": 7, "source": "wrapper"}]


def test_retro_scores_ignores_missing_and_symlinked_logs(tmp_path):
    state_dir = tmp_path / "state"
    scores_dir = state_dir / "scores"
    scores_dir.mkdir(parents=True)
    (scores_dir / "bastet.jsonl").symlink_to(tmp_path / "outside.jsonl")

    result = collect_diagnostics._collect_retro_scores(state_dir, now=int(time.time()))

    assert result["present"] is False
    assert set(result["games"]) == set(collect_diagnostics.RETRO_SCORELOG_GAMES)
    for entry in result["games"].values():
        assert entry == {
            "present": False,
            "readable": False,
            "records": 0,
            "invalid": 0,
            "truncated": False,
            "sources": {"agent": 0, "live": 0, "wrapper": 0, "other": 0},
            "max_same_score_run": 0,
            "last": [],
            "age_sec": -1,
        }


def test_retro_scores_is_wired_into_the_corners_payload(tmp_path):
    soren = tmp_path / "soren"
    state_dir = tmp_path / "state"
    _write_scores(state_dir, "pacman4console", [
        {"ts": 1, "game": "pacman4console", "score": 253, "source": "wrapper"},
    ])

    payload = collect_diagnostics._collect_programs(state_dir, soren, now=int(time.time()))

    assert payload["retro_scores"]["games"]["pacman4console"]["last"] == [
        {"ts": 1, "score": 253, "source": "wrapper"},
    ]


def test_retro_scores_survives_the_soft_budget():
    projection = {"present": True, "game_count": 1,
                  "games": {"bastet": {"present": True, "records": 3,
                                       "max_same_score_run": 3}}}
    payload = {
        "corners": {"retro_scores": projection},
        "nethack_history": {"daily": {"records": []}, "completed_runs": {"records": []}},
        "ai": {"recent_events": ["x" * collect_diagnostics.MAX_JSON_BYTES],
               "anomalous_components": {}},
        "workers": {"details": {}},
        "soren91_drop_profile": {"profileStatus": "missing"},
    }

    text = collect_diagnostics._diagnostics_budget(payload)

    assert payload["corners"]["retro_scores"] == projection
    assert len(text.encode("utf-8")) <= collect_diagnostics.MAX_JSON_BYTES


def test_retro_scores_is_omitted_only_at_the_hard_ceiling(monkeypatch):
    monkeypatch.setattr(collect_diagnostics, "MAX_JSON_BYTES", 600)
    monkeypatch.setattr(collect_diagnostics, "HARD_JSON_BYTES", 500)
    payload = {
        "corners": {"retro_scores": {"present": True,
                                     "games": {"bastet": {"records": 9}}}},
        # Never reduced by the budget chain, so the hard ceiling is reached.
        "encoder": {"frames_sent": "y" * 2000},
        "nethack_history": {"daily": {"records": []}, "completed_runs": {"records": []}},
        "ai": {"recent_events": [], "anomalous_components": {}},
        "workers": {"details": {}},
        "soren91_drop_profile": {"profileStatus": "missing"},
    }

    collect_diagnostics._diagnostics_budget(payload)

    assert payload["corners"]["retro_scores"] == {"status": "output_omitted"}


def test_retro_scores_survives_when_dropping_decision_plans_restores_the_ceiling(monkeypatch):
    """The score projection is dropped only if the envelope is still over the
    hard ceiling after the older detail reduction, never on a stale size."""
    projection = {"present": True, "game_count": 1,
                  "games": {"bastet": {"present": True, "records": 3}}}
    payload = {
        "corners": {
            "retro_scores": projection,
            "retro_corner": {"decision_plans": {"status": "ok", "plans": ["p" * 400]}},
        },
        # Never reduced by the budget chain, so the ceiling is reached.
        "encoder": {"frames_sent": "y" * 2000},
        "nethack_history": {"daily": {"records": []}, "completed_runs": {"records": []}},
        # Already in its reduced shape, so the soft-budget pass cannot change
        # the serialized size between the two measurements below.
        "ai": {"recent_events": [], "recent_events_omitted": True,
               "anomalous_components": {}},
        "workers": {"details": {}},
        "soren91_drop_profile": {"profileStatus": "missing"},
    }
    with_plans = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    reduced = json.loads(with_plans)
    reduced["corners"]["retro_corner"]["decision_plans"] = {"status": "output_omitted"}
    without_plans = json.dumps(reduced, sort_keys=True, ensure_ascii=False)
    assert len(with_plans.encode("utf-8")) > len(without_plans.encode("utf-8"))
    # Over the ceiling with decision_plans, under it once that one field yields.
    monkeypatch.setattr(collect_diagnostics, "MAX_JSON_BYTES", len(without_plans.encode("utf-8")))
    monkeypatch.setattr(collect_diagnostics, "HARD_JSON_BYTES", len(without_plans.encode("utf-8")))

    text = collect_diagnostics._diagnostics_budget(payload)

    assert payload["corners"]["retro_corner"]["decision_plans"] == {"status": "output_omitted"}
    assert payload["corners"]["retro_scores"] == projection
    assert len(text.encode("utf-8")) <= collect_diagnostics.HARD_JSON_BYTES


def test_retro_scores_survives_the_real_hard_ceiling_boundary():
    """Reproduces the reviewed boundary with the shipped 36/56 KiB thresholds:
    the envelope is over the hard ceiling only while ``decision_plans`` is
    present, and the reduction alone restores it."""
    projection = {"present": True, "game_count": 1,
                  "games": {"bastet": {"present": True, "records": 3}}}

    def build(pad_len):
        return {
            "corners": {
                "retro_scores": projection,
                "retro_corner": {"decision_plans": {"status": "ok",
                                                    "plans": ["p" * 400]}},
            },
            "encoder": {"frames_sent": "y" * pad_len},
            "nethack_history": {"daily": {"records": []},
                                "completed_runs": {"records": []}},
            "ai": {"recent_events": [], "recent_events_omitted": True,
                   "anomalous_components": {}},
            "workers": {"details": {}},
            "soren91_drop_profile": {"profileStatus": "missing"},
        }

    hard = collect_diagnostics.HARD_JSON_BYTES
    probe = json.loads(json.dumps(build(0), sort_keys=True, ensure_ascii=False))
    probe["corners"]["retro_corner"]["decision_plans"] = {"status": "output_omitted"}
    base_without_plans = len(
        json.dumps(probe, sort_keys=True, ensure_ascii=False).encode("utf-8")
    )
    # One byte under the ceiling once decision_plans yields, over it while the
    # field is still present.
    payload = build(hard - base_without_plans - 1)
    with_plans = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    reduced = json.loads(with_plans)
    reduced["corners"]["retro_corner"]["decision_plans"] = {"status": "output_omitted"}
    without_plans = json.dumps(reduced, sort_keys=True, ensure_ascii=False)
    assert len(without_plans.encode("utf-8")) <= hard
    assert len(with_plans.encode("utf-8")) > hard

    text = collect_diagnostics._diagnostics_budget(payload)

    assert payload["corners"]["retro_corner"]["decision_plans"] == {"status": "output_omitted"}
    assert payload["corners"]["retro_scores"] == projection
    assert len(text.encode("utf-8")) <= hard


def test_retro_scores_scans_a_production_sized_log_in_full(tmp_path):
    """The window must cover a real log so the acceptance delta is exact.

    pacman4console's live log was 453 lines (2026-10-17); a 400-line cap would
    have saturated it and made the per-corner delta unreadable.
    """
    state_dir = tmp_path / "state"
    _write_scores(state_dir, "pacman4console", [
        {"ts": 1700000000 + i, "game": "pacman4console",
         "score": 250 + (i % 3), "source": "wrapper"}
        for i in range(450)
    ])

    entry = collect_diagnostics._collect_retro_scores(
        state_dir, now=int(time.time()))["games"]["pacman4console"]

    assert entry["truncated"] is False
    assert entry["records"] == 450
    assert entry["invalid"] == 0
    assert [row["score"] for row in entry["last"]] == [251, 252, 250, 251, 252]
    assert entry["last"][0]["ts"] == 1700000445
