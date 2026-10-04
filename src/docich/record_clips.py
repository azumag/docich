"""Local record confirmation and durable outbox; never calls a service API.

Only completed wrapper scores and verified Hanjuku clears are supported.
Delivery is owned by Soren's existing chat worker and Twitch credential path.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import time
import tomllib

from .game_switch import atomic_write_json

SCORE_GAMES = {"ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console"}
TITLES = {"ninvaders": "NInvaders", "nsnake": "Snake", "bastet": "Bastet",
          "moon-buggy": "Moon Buggy", "pacman4console": "Pacman",
          "hanjuku-hero": "半熟英雄"}


def enabled(g) -> bool:
    try:
        raw = tomllib.loads(Path(g.config_path).read_text()).get("record_clips", {})
        return raw.get("enabled") is True
    except (OSError, ValueError, AttributeError):
        return False


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _load(path):
    try:
        row = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if (not isinstance(row, dict) or type(row.get("schema")) is not int or row["schema"] != 1
            or type(row.get("best")) is not int or row["best"] < 0
            or not isinstance(row.get("outbox"), list)):
        raise ValueError("invalid record ledger")
    for item in row["outbox"]:
        if not isinstance(item, dict) or type(item.get("enqueued")) is not bool:
            raise ValueError("invalid record outbox")
        event = item.get("event")
        if (not isinstance(event, dict) or not isinstance(event.get("event_id"), str)
                or not re.fullmatch("[0-9a-f]{64}", event["event_id"])
                or event.get("event_kind") != "record"):
            raise ValueError("invalid record event")
    return row


def _publish(state, soren_root):
    # A sink receipt is retained independently of queue cleanup. The stable
    # filename also closes the crash-after-enqueue/before-local-ACK window.
    queue = Path(soren_root) / "tmp/clip_queue"
    for item in state["outbox"]:
        if item["enqueued"]:
            continue
        event = item["event"]
        receipt = queue / "receipts" / (event["event_id"] + ".json")
        target = queue / ("record_" + event["event_id"] + ".json")
        if not receipt.exists() and not target.exists():
            atomic_write_json(target, event)
        item["enqueued"] = True
    # Only undelivered events need repeated local publication. Best is never
    # pruned/reset; equal/older records cannot produce another event.
    state["outbox"] = [e for e in state["outbox"] if not e["enqueued"]] + [
        e for e in state["outbox"] if e["enqueued"]][-32:]


def confirm(state_dir, soren_root, *, game, metric, value, run_id, sequence,
            baseline=None, now=None):
    """Confirm a strict maximum once; baseline=None makes first value a seed.

    This registry deliberately has no implicit time/depth/clear conversion.
    A new lower-is-better metric must have its own explicit implementation.
    """
    if ((game not in SCORE_GAMES or metric != "score")
            and (game != "hanjuku-hero" or metric != "cleared")):
        raise ValueError("unsupported record metric")
    if (type(value) is not int or value < 0
            or metric == "cleared" and value > 12
            or baseline is not None and (type(baseline) is not int or baseline < 0)
            or not isinstance(run_id, str) or not 1 <= len(run_id) <= 256
            or not isinstance(sequence, str) or not 1 <= len(sequence) <= 128):
        raise ValueError("invalid record evidence")
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError("invalid record clock")
    path = Path(state_dir) / "records" / f"{game}_{metric}.json"
    with _lock(path.with_suffix(".lock")) as acquired:
        if not acquired:
            return {"status": "busy"}
        state = _load(path)
        if state is None:
            state = {"schema": 1, "best": value if baseline is None else baseline,
                     "outbox": []}
        elif baseline is not None:
            state["best"] = max(state["best"], baseline)
        previous = state["best"]
        changed = value > previous
        if changed:
            event_id = hashlib.sha256(json.dumps(
                [game, metric, run_id, sequence, value], separators=(",", ":")
            ).encode()).hexdigest()
            label = f"{value}話突破" if metric == "cleared" else f"score={value}"
            event = {"schema": 1, "event_id": event_id, "event_kind": "record",
                     "event_msg": f"🏆 {TITLES[game]} 新記録: {label}（前記録 {previous}）",
                     "game_id": "", "delay": 0, "created_at": now,
                     "record": {"game": game, "metric": metric, "value": value,
                                "previous": previous}}
            state["best"] = value
            state["outbox"].append({"event": event, "enqueued": False})
        # Record confirmation is fsynced before publication. A queue failure
        # cannot discard best; the next local call retries the same event.
        atomic_write_json(path, state)
        try:
            _publish(state, soren_root)
            atomic_write_json(path, state)
        except OSError:
            return {"status": "delivery_pending", "best": state["best"]}
        return {"status": "confirmed" if changed else "baseline", "best": state["best"]}


def seed_score(state_dir, soren_root, game, scorelog, run_id):
    """Import existing completed live scores, without replaying old clips."""
    best = None
    try:
        with Path(scorelog).open() as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    score = row.get("score")
                    if (row.get("game") == game and type(score) is int and score >= 0
                            and row.get("source") in {"wrapper", "live", "agent"}
                            and not row.get("experiment_id") and not row.get("ab_experiment_id")):
                        best = max(best if best is not None else score, score)
                except (ValueError, AttributeError):
                    continue
    except FileNotFoundError:
        pass
    if best is not None:
        return confirm(state_dir, soren_root, game=game, metric="score", value=best,
                       run_id=run_id, sequence="seed", baseline=best)
    return {"status": "unseeded"}


def consider_hanjuku(g, identity):
    """Observe already-durable progress outside the input gate; no policy edits."""
    if not enabled(g):
        return
    try:
        from . import hanjuku_progress as progress, hanjuku_predictions
        from .naming import runtime_directory
        from .trading.soren_output import resolve_soren_root
        identity = progress.checked_identity(identity)
        runtime = runtime_directory(g.state_dir, identity["runtime_id"])
        row = progress.read(runtime, identity)
        if not row or row["ambiguous"]:
            return
        _, seed, _ = hanjuku_predictions.config(g)
        # Retain the previously verified production best on rollout, even if
        # the prediction feature is currently disabled.
        prediction_path = Path(g.state_dir) / hanjuku_predictions.FILE
        ledger_path = Path(g.state_dir) / "records/hanjuku-hero_cleared.json"
        if prediction_path.exists() and not ledger_path.exists():
            prior = hanjuku_predictions._load(Path(g.state_dir), seed)
            seed = max(seed, prior["best_cleared"])
        return confirm(g.state_dir, resolve_soren_root(g), game="hanjuku-hero",
                       metric="cleared", value=row["cleared"], baseline=seed,
                       run_id=json.dumps(identity, sort_keys=True), sequence=str(row["cleared"]))
    except Exception:
        # Record I/O must never stop a game or alter the strategy's decision.
        return {"status": "invalid_evidence_or_io"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("seed-score", "score"))
    parser.add_argument("--game", choices=sorted(SCORE_GAMES), required=True)
    parser.add_argument("--scorelog")
    parser.add_argument("--value", type=int)
    parser.add_argument("--sequence", default="seed")
    args = parser.parse_args()
    # Only the adapter's explicit opt-in launches this side channel. Test/eval
    # wrappers and existing smoke harnesses cannot write production queues.
    if os.environ.get("DOCICH_RECORD_CLIPS") != "1" or os.environ.get("EXPLORE_MODE") == "1":
        return
    try:
        directory = os.environ["DOCICH_STATE_DIR"]
        soren = os.environ["DOCICH_CLIP_SOREN_ROOT"]
        run_id = os.environ["DOCICH_CLIP_RUN_ID"]
        if args.command == "seed-score":
            seed_score(directory, soren, args.game, args.scorelog, run_id)
        else:
            confirm(directory, soren, game=args.game, metric="score", value=args.value,
                    run_id=run_id, sequence=args.sequence)
    except Exception:
        pass


if __name__ == "__main__":
    main()
