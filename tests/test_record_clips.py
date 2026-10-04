"""Synthetic completed records only: no running games, tokens or service API."""
from dataclasses import replace
import fcntl
import json
from pathlib import Path
import re
import subprocess
import os

import pytest

from docich import record_clips as clips, hanjuku_progress as progress
from docich.config import load_global, load_game
from docich.game_switch import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = dict(game="hanjuku-hero", runtime_id="g1-abcdef", generation=1, lease_id="test")


def queue(root):
    return list((root / "soren/tmp/clip_queue").glob("record_*.json"))


def confirm(root, value, **kwargs):
    return clips.confirm(root / "state", root / "soren", game=kwargs.pop("game", "bastet"),
                         metric=kwargs.pop("metric", "score"), value=value,
                         run_id=kwargs.pop("run_id", "test-run"),
                         sequence=kwargs.pop("sequence", str(value)), now=kwargs.pop("now", 100), **kwargs)


@pytest.mark.parametrize("game", sorted(clips.SCORE_GAMES))
def test_seed_tie_reset_restart_and_strict_new_score(tmp_path, game):
    assert confirm(tmp_path, 0, game=game)["status"] == "baseline"
    assert confirm(tmp_path, 10, game=game)["status"] == "confirmed"
    assert confirm(tmp_path, 10, game=game, run_id="restart")["status"] == "baseline"
    assert confirm(tmp_path, 0, game=game, run_id="new-match")["best"] == 10
    assert len(queue(tmp_path)) == 1
    confirm(tmp_path, 11, game=game)
    assert len(queue(tmp_path)) == 2
    assert all(game not in p.name and "test-run" not in p.read_text() for p in queue(tmp_path))


def test_first_positive_value_is_baseline_without_historical_evidence(tmp_path):
    assert confirm(tmp_path, 123)["status"] == "baseline"
    assert not queue(tmp_path)


def test_rollout_seeds_all_history_excluding_evaluation_and_ab(tmp_path):
    log = tmp_path / "old.jsonl"
    rows = [dict(game="bastet", score=40, source="wrapper"),
            dict(game="bastet", score=10, source="wrapper"),
            dict(game="bastet", score=9999, source="evaluation"),
            dict(game="bastet", score=8888, source="wrapper", ab_experiment_id="test"),
            dict(game="nsnake", score=7000, source="wrapper")]
    log.write_text("\n".join(map(json.dumps, rows)) + "\nmalformed\n")
    clips.seed_score(tmp_path / "state", tmp_path / "soren", "bastet", log, "test")
    assert not queue(tmp_path)
    assert confirm(tmp_path, 39)["best"] == 40
    confirm(tmp_path, 41)
    assert json.loads(queue(tmp_path)[0].read_text())["record"]["previous"] == 40


def test_durable_record_survives_failed_queue_and_retries_same_event(tmp_path, monkeypatch):
    confirm(tmp_path, 10)
    publish = clips._publish
    monkeypatch.setattr(clips, "_publish", lambda *args: (_ for _ in ()).throw(OSError()))
    assert confirm(tmp_path, 11)["status"] == "delivery_pending"
    assert not queue(tmp_path)
    monkeypatch.setattr(clips, "_publish", publish)
    assert confirm(tmp_path, 0)["best"] == 11
    assert len(queue(tmp_path)) == 1
    assert json.loads(queue(tmp_path)[0].read_text())["record"]["value"] == 11


def test_crash_after_enqueue_local_ack_does_not_republish_completed_receipt(tmp_path):
    confirm(tmp_path, 10)
    confirm(tmp_path, 11)
    event_file = queue(tmp_path)[0]
    event = json.loads(event_file.read_text())
    ledger = tmp_path / "state/records/bastet_score.json"
    state = json.loads(ledger.read_text())
    state["outbox"][0]["enqueued"] = False
    atomic_write_json(ledger, state)
    receipt = tmp_path / "soren/tmp/clip_queue/receipts" / (event["event_id"] + ".json")
    receipt.parent.mkdir()
    receipt.write_text('{"phase":"ready"}')
    event_file.unlink()
    confirm(tmp_path, 11)
    assert not queue(tmp_path)


def test_short_interval_updates_have_distinct_events_and_game_bests(tmp_path):
    confirm(tmp_path, 1)
    for value in (2, 3, 4):
        confirm(tmp_path, value, sequence="same-match")
    confirm(tmp_path, 0, game="nsnake")
    confirm(tmp_path, 1, game="nsnake")
    assert len(queue(tmp_path)) == 4
    assert len({json.loads(p.read_text())["event_id"] for p in queue(tmp_path)}) == 4


@pytest.mark.parametrize("metric", ["time", "depth", "clear", "rank", "turns"])
def test_unapproved_direction_or_metric_fails_closed(tmp_path, metric):
    with pytest.raises(ValueError):
        confirm(tmp_path, 5, metric=metric)
    assert not queue(tmp_path)


@pytest.mark.parametrize("value", [True, -1, 1.2, "3", None])
def test_invalid_scores_do_not_fabricate_new_records(tmp_path, value):
    with pytest.raises(ValueError):
        confirm(tmp_path, value)
    assert not queue(tmp_path)


def test_busy_lock_never_blocks_game(tmp_path):
    lock = tmp_path / "state/records/bastet_score.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        assert confirm(tmp_path, 10) == {"status": "busy"}


def test_busy_completed_record_survives_history_seed_on_restart(tmp_path):
    confirm(tmp_path, 10)
    lock = tmp_path / "state/records/bastet_score.lock"
    log = tmp_path / "scores.jsonl"
    log.write_text(json.dumps(dict(game="bastet", source="wrapper", score=11)) + "\n")
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        assert confirm(tmp_path, 11)["status"] == "busy"
        assert not queue(tmp_path)
    clips.seed_score(tmp_path / "state", tmp_path / "soren", "bastet", log, "restart")
    assert len(queue(tmp_path)) == 1
    event = json.loads(queue(tmp_path)[0].read_text())
    assert event["record"] == dict(game="bastet", metric="score", value=11, previous=10)
    assert event["created_at"] == 100  # deferred evidence must not become fresh
    clips.seed_score(tmp_path / "state", tmp_path / "soren", "bastet", log, "restart-again")
    assert len(queue(tmp_path)) == 1


def test_busy_replay_retains_original_time_and_distinct_updates(tmp_path):
    confirm(tmp_path, 10)
    lock = tmp_path / "state/records/bastet_score.lock"
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        confirm(tmp_path, 11)
        confirm(tmp_path, 11, now=200)
        confirm(tmp_path, 12, now=101)
        pending = list((tmp_path / "state/records/pending/bastet_score").glob("*.json"))
        assert len(pending) == 2
        assert all("test-run" not in p.read_text() for p in pending)
    confirm(tmp_path, 0, now=201)
    events = sorted((json.loads(p.read_text()) for p in queue(tmp_path)), key=lambda e: e["record"]["value"])
    assert [(e["record"]["value"], e["record"]["previous"], e["created_at"]) for e in events] == [
        (11, 10, 100), (12, 11, 101)]
    assert not list((tmp_path / "state/records/pending/bastet_score").glob("*.json"))


def test_busy_first_values_still_seed_once_then_confirm(tmp_path):
    lock = tmp_path / "state/records/bastet_score.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        confirm(tmp_path, 10)
        confirm(tmp_path, 11)
    assert confirm(tmp_path, 0, now=101)["best"] == 11
    assert len(queue(tmp_path)) == 1
    assert json.loads(queue(tmp_path)[0].read_text())["record"]["previous"] == 10


def test_crash_after_ledger_commit_before_pending_cleanup_is_idempotent(tmp_path, monkeypatch):
    confirm(tmp_path, 10)
    unlink = Path.unlink

    def crash_on_pending(path, *args, **kwargs):
        if path.parent.name == "bastet_score" and path.suffix == ".json":
            raise OSError("synthetic crash before cleanup")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", crash_on_pending)
    with pytest.raises(OSError, match="synthetic crash"):
        confirm(tmp_path, 11)
    assert json.loads((tmp_path / "state/records/bastet_score.json").read_text())["best"] == 11
    assert not queue(tmp_path)
    monkeypatch.setattr(Path, "unlink", unlink)
    confirm(tmp_path, 11, now=200)
    assert len(queue(tmp_path)) == 1
    assert json.loads(queue(tmp_path)[0].read_text())["created_at"] == 100


def test_hanjuku_verified_chapter_evidence_and_previous_best(tmp_path, monkeypatch):
    g = replace(load_global(ROOT, ROOT / "config/docich.soren-live.toml"), state_dir=tmp_path / "state")
    monkeypatch.setattr("docich.trading.soren_output.resolve_soren_root", lambda _: tmp_path / "soren")
    runtime = g.state_dir / "runtimes" / IDENTITY["runtime_id"]
    runtime.mkdir(parents=True)
    progress.record(runtime, IDENTITY, {"chapter": 2, "chapter_evidence": "header"}, [], "a" * 64)
    clips.consider_hanjuku(g, IDENTITY)
    assert not queue(tmp_path)  # chapter 2 is only one clear, matching seed=1
    progress.record(runtime, IDENTITY, {"chapter": 3, "chapter_evidence": "header"}, [], "b" * 64)
    clips.consider_hanjuku(g, IDENTITY)
    clips.consider_hanjuku(g, IDENTITY)
    assert len(queue(tmp_path)) == 1
    assert json.loads(queue(tmp_path)[0].read_text())["record"] == dict(
        game="hanjuku-hero", metric="cleared", value=2, previous=1)
    progress.record(runtime, IDENTITY, {"chapter": 4, "chapter_evidence": "header"},
                    [{"decision": "new_game_detected"}], "c" * 64)
    clips.consider_hanjuku(g, IDENTITY)
    assert len(queue(tmp_path)) == 1  # ambiguous reset is never a new clear


@pytest.mark.parametrize("game", sorted(clips.SCORE_GAMES))
@pytest.mark.parametrize("busy", [False, True])
def test_real_wrapper_record_hook_reuses_history_without_network(tmp_path, game, busy):
    # Execute the real record function only, never its driver/game lifecycle.
    text = (ROOT / f"games/cli-wrappers/{game}_docich.sh").read_text()
    function = re.search(r"(?ms)^record_score\(\) \{.*?^\}", text).group()
    log = tmp_path / "scores.jsonl"
    log.write_text(json.dumps(dict(game=game, source="wrapper", score=10)) + "\n")
    script = f'''SCRIPT_DIR='{ROOT}/games/cli-wrappers'
. "$SCRIPT_DIR/_run_with_driver.sh"
SCORELOG='{log}'
AB_STATE=''
matches=1
{function}
docich_wrapper_seed_clips {game}
record_score '{'Score 11' if game == 'nsnake' else '11'}'
'''
    env = {**os.environ, "DOCICH_STATE_DIR": str(tmp_path / "state"),
           "DOCICH_RECORD_CLIPS": "1", "DOCICH_CLIP_SOREN_ROOT": str(tmp_path / "soren"),
           "DOCICH_CLIP_RUN_ID": "synthetic-run"}
    lock = tmp_path / f"state/records/{game}_score.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("a") as handle:
        if busy:
            confirm(tmp_path, 10, game=game)
            fcntl.flock(handle, fcntl.LOCK_EX)
        result = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    if busy:
        assert not queue(tmp_path)
        clips.seed_score(tmp_path / "state", tmp_path / "soren", game, log, "restart")
    assert len(queue(tmp_path)) == 1
    assert json.loads(queue(tmp_path)[0].read_text())["record"]["previous"] == 10


def test_adapter_only_enables_live_config_and_excludes_ab(tmp_path, monkeypatch):
    from docich.adapters import cli_game
    g = replace(load_global(ROOT, ROOT / "config/docich.soren-live.toml"), state_dir=tmp_path)
    monkeypatch.setattr("docich.trading.soren_output.resolve_soren_root", lambda _: tmp_path / "soren")
    game = load_game(g, "nsnake")
    command = cli_game._game_launch_command(g, game, ["sh", "wrapper"])
    assert "DOCICH_RECORD_CLIPS=1" in command
    monkeypatch.setenv("DOCICH_MOON_BUGGY_AB_STATE", "test-ab")
    assert "DOCICH_RECORD_CLIPS=1" not in cli_game._game_launch_command(g, game, ["sh", "wrapper"])
