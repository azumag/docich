"""Read the latest verified Hanjuku run for the retro-corner intro.

The gameplay policy stays on the permanent chart. This module only summarizes
the previous completed run's adjusted orders and outcomes for the next intro.
"""
from __future__ import annotations

import json
import math
import os
import stat
from pathlib import Path

from . import hanjuku_chart, hanjuku_chart_review, hanjuku_run
from .adapters.base import AdapterError
from .naming import NameValidationError, runtime_id_generation
from .retroarch_boundary import read_record

MAX_RUNTIME_SCAN = 2048
MAX_REVIEW_BYTES = 1024 * 1024
MAX_REVIEW_COUNT = 10000
MAX_SPOKEN_TARGETS = 2


def _load_review(runtime_dir: Path, runtime_id: str, generation: int) -> dict | None:
    run = read_record(runtime_dir / hanjuku_run.RUN_FILE)
    if (run.get("game") != "hanjuku-hero"
            or run.get("runtime_id") != runtime_id
            or type(run.get("generation")) is not int
            or run.get("generation") != generation):
        return None

    lease_id = run.get("lease_id")
    if (lease_id is not None
            and (not isinstance(lease_id, str) or not lease_id or len(lease_id) > 128)):
        return None
    identity = {
        "game": "hanjuku-hero",
        "runtime_id": runtime_id,
        "generation": generation,
        "lease_id": lease_id,
    }
    terminal = hanjuku_run.terminal(runtime_dir, identity)
    if terminal is None or terminal.get("terminal_reason") != "game_over":
        return None

    path = runtime_dir / hanjuku_chart_review.REVIEW_FILE
    if path.is_symlink():
        return None
    flags = (
        os.O_RDONLY
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_REVIEW_BYTES:
            return None
        raw = stream.read(MAX_REVIEW_BYTES + 1)
    if len(raw.encode("utf-8")) > MAX_REVIEW_BYTES:
        return None
    report = json.loads(raw)
    if (not isinstance(report, dict)
            or type(report.get("schema")) is not int
            or report.get("schema") != hanjuku_chart_review.SCHEMA
            or not isinstance(report.get("steps"), dict)
            or not isinstance(report.get("adjusted_orders"), dict)
            or not isinstance(report.get("proposals"), list)
            or len(report["steps"]) > MAX_REVIEW_COUNT
            or len(report["adjusted_orders"]) > MAX_REVIEW_COUNT
            or len(report["proposals"]) > MAX_REVIEW_COUNT):
        return None
    if any(report.get(key) != value for key, value in identity.items()):
        return None
    generated_at = report.get("generated_at")
    if (type(generated_at) not in (int, float)
            or not 0 <= generated_at <= 1_000_000_000_000
            or not math.isfinite(generated_at)):
        return None
    return report


def latest_completed_review(state_dir: Path) -> dict | None:
    """Return the newest identity-matched game-over review in recent runtimes."""
    root = Path(state_dir) / "runtimes"
    try:
        if root.is_symlink() or not root.is_dir():
            return None
    except OSError:
        return None

    candidates: list[tuple[int, str]] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        continue
                    generation = runtime_id_generation(entry.name)
                except (NameValidationError, OSError):
                    continue
                candidates.append((generation, entry.name))
    except OSError:
        return None

    for generation, runtime_id in sorted(candidates, reverse=True)[:MAX_RUNTIME_SCAN]:
        runtime_dir = root / runtime_id
        try:
            if runtime_dir.is_symlink():
                continue
            report = _load_review(runtime_dir, runtime_id, generation)
        except (AdapterError, OSError, RecursionError, TypeError, ValueError):
            continue
        if report is not None:
            return report
    return None


def _count(value) -> int:
    if type(value) is int and 0 <= value <= MAX_REVIEW_COUNT:
        return value
    return 0


def _known_targets() -> set[str]:
    known: set[str] = set()
    for chapter, names in hanjuku_chart.CASTLE_NAMES.items():
        known.update(names)
        known.update(hanjuku_chart.castles(chapter))
        known.update(o['target'] for o in hanjuku_chart.all_orders(chapter))
    return known


def _targets(proposals: list, kinds: set[str]) -> list[str]:
    known = _known_targets()
    result: list[str] = []
    for proposal in proposals:
        if not isinstance(proposal, dict) or proposal.get("type") not in kinds:
            continue
        order = (proposal.get("order")
                 if proposal.get("type") == "promote_adjusted_step" else proposal)
        target = order.get("target") if isinstance(order, dict) else None
        if isinstance(target, str) and target in known and target not in result:
            result.append(target)
        if len(result) >= MAX_SPOKEN_TARGETS:
            break
    return result


def _missed_adjusted_targets(steps: dict) -> list[str]:
    known = _known_targets()
    result: list[str] = []
    for row in steps.values():
        if (not isinstance(row, dict) or row.get("kind") != "adjusted"
                or row.get("captured_target") is True
                or _count(row.get("failed")) + _count(row.get("losses")) == 0):
            continue
        target = row.get("target")
        if isinstance(target, str) and target in known and target not in result:
            result.append(target)
        if len(result) >= MAX_SPOKEN_TARGETS:
            break
    return result


def describe_latest_run(state_dir: Path) -> str:
    """Build a short, factual strategy intro from the latest completed run."""
    report = latest_completed_review(state_dir)
    if report is None:
        return (
            "過去の完走レビュー記録が見つからないため、"
            "今回は第1話の基本チャートから始めます。"
        )

    steps = report.get("steps") if isinstance(report.get("steps"), dict) else {}
    wins = losses = adjusted_executed = 0
    for row in steps.values():
        if not isinstance(row, dict):
            continue
        wins += _count(row.get("wins"))
        losses += _count(row.get("losses"))
        if row.get("kind") == "adjusted" and _count(row.get("launched")) > 0:
            adjusted_executed += 1

    adjusted_orders = report.get("adjusted_orders")
    adjusted_created = (
        min(len(adjusted_orders), MAX_REVIEW_COUNT)
        if isinstance(adjusted_orders, dict) else 0
    )
    proposals = report.get("proposals") if isinstance(report.get("proposals"), list) else []
    successful_adjustments = _targets(proposals, {"promote_adjusted_step"})
    successful_interim = _targets(proposals, {"promote_interim_attack"})
    missed_adjustments = _missed_adjusted_targets(steps)
    review_targets = _targets(proposals, {"review_base_step"})

    if wins + losses:
        parts = [f"記録に残る直近の完走ランは{wins}勝{losses}敗"]
    else:
        parts = ["記録に残る直近の完走ランには勝敗記録がありません"]
    if adjusted_created:
        parts.append(
            f"調整チャート{adjusted_created}手を作成し、{adjusted_executed}手を実行"
        )
    if successful_adjustments:
        parts.append("調整チャートでは" + "・".join(successful_adjustments) + "を攻略")
    if successful_interim:
        parts.append("臨時判断では" + "・".join(successful_interim) + "を攻略")
    if missed_adjustments:
        parts.append("調整手順の見直し候補は" + "・".join(missed_adjustments))
    if review_targets:
        parts.append("基本手順の見直し候補は" + "・".join(review_targets))
    parts.append(
        "今回は前回の実績も案内に反映し、"
        "第1話の基本チャートから進めます"
    )
    return "。".join(parts) + "。"
