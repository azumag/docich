"""Shadow judge for PAPER -> LIVE promotion of per-symbol slot strategies.

Issue #3623 §2.6 / §2.8 (rule v3).  This module only *judges and reports*:
it reads the private PAPER ledger through ``cell_metrics`` (opened ``mode=ro``),
never writes, never touches the worker, the slots or any LIVE state, and never
raises on bad input.  A "promotable" cell here changes nothing; LIVE does not
exist yet.

Rule v3 = the e-process of rule v2 (frozen 2026-11-08, Issue #3623 §2.8)
with the same constants except THRESHOLD, extended to the cells of per-symbol slot experiments
(``<base>_<yymmdd>``, see ``symbol_slots`` / ``paper_improve_slots``):

* cell = (slot experiment id, symbol); unit = JST day block of the
  fee-corrected mean net return per closed sell (``cell_metrics``);
* H0: the cell's mean net return <= DELTA (0.002, a 20bps round-trip stress);
  Y = block - DELTA;
* E_0 = 1, E_t = E_(t-1) * (1 + lam_t * Y_t), with lam_t decided from the blocks
  before t only: mu = sum(Y_i, i<t) / t, var = (PRIOR_SD^2 + sum((Y_i-mu)^2)) / t,
  lam_t = clip(mu / (var + mu^2), 0, 0.9 / LOSS_CAP);
* a block with Y_t < -LOSS_CAP sets E to 0 for good;
* promotable on the first day with E >= THRESHOLD and at least MIN_BLOCKS blocks.

Differences from v2 that matter (all deliberate and frozen with v3):

* only blocks of days on or after ``FIRST_BLOCK_DAY`` count (the slots started
  2026-11-16 and rule v3 was frozen that day, so whole days after it are used);
* THRESHOLD is 5, not 430 (owner decision 2026-11-16).  A slot experiment lives
  at most 7 days, so a cell has at most 7 day blocks; with 430 even an
  OAS/JPY-sized effect (+1.9% per day) never promoted within 7 days
  (simulated, daily sd 0.5%).  With 5 an effect of +1.9% per day promotes
  ~100% of the time, +1% per day ~60%, +0.5% per day ~1%, and break-even
  cells (<= 0.2% per day) ~0% (also with heavy tails).  Theory only
  guarantees that a cell at or below the null passes with probability <= 1/5,
  and over time many cells are tested, so some false promotions are accepted:
  LIVE holds about 10,000 JPY.  The simulation assumes independent day blocks
  and a daily sd of 0.5%; real symbols ranged 0.4-1.2% (not verified further).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable, Mapping

from .cell_metrics import aggregate_cell_metrics

RULE_VERSION = "v3"
DELTA = 0.002
LOSS_CAP = 0.02
PRIOR_SD = 0.01
MAX_LAMBDA = 0.9 / LOSS_CAP
THRESHOLD = 5.0
MIN_BLOCKS = 5
FIRST_BLOCK_DAY = "2026-11-17"  # JST; first whole day after the rule was frozen
MAX_LISTED = 10

_SLOT_EXPERIMENT_RE = re.compile(r"^([a-z0-9]+)_\d{6}$")


def e_process(blocks: Iterable[tuple[str, float]]) -> dict[str, object]:
    """Run the rule-v2/v3 e-process over ``(day, mean_net_return)`` blocks."""
    ordered = sorted(blocks)
    ys: list[float] = []
    e_value = 1.0
    promoted_day: str | None = None
    ruined = False
    for index, (day, value) in enumerate(ordered):
        y = float(value) - DELTA
        if index == 0:
            lam = 0.0
        else:
            t = float(index + 1)
            mu = sum(ys) / t
            var = (PRIOR_SD ** 2 + sum((past - mu) ** 2 for past in ys)) / t
            lam = max(0.0, min(MAX_LAMBDA, mu / (var + mu * mu))) if (var + mu * mu) > 0 else 0.0
        ys.append(y)
        if ruined:
            continue
        if y < -LOSS_CAP:
            e_value, ruined = 0.0, True
            continue
        e_value *= 1.0 + lam * y
        if promoted_day is None and index + 1 >= MIN_BLOCKS and e_value >= THRESHOLD:
            promoted_day = day
    return {"blocks": len(ordered), "e_value": e_value, "ruined": ruined,
            "promoted_day": promoted_day}


def _slot_cells(cells: object) -> list[Mapping[str, object]]:
    if not isinstance(cells, list):
        return []
    selected = []
    for cell in cells:
        if not isinstance(cell, Mapping):
            continue
        match = _SLOT_EXPERIMENT_RE.match(str(cell.get("experiment_id", "")))
        symbol = str(cell.get("symbol", ""))
        if match and symbol.split("/")[0].lower() == match.group(1):
            selected.append(cell)
    return selected


def _blocks(cell: Mapping[str, object]) -> list[tuple[str, float]]:
    raw = cell.get("return_blocks")
    blocks = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        day = str(item.get("day", ""))
        try:
            value = float(str(item.get("mean_return_corrected")))
        except ValueError:
            continue
        if day >= FIRST_BLOCK_DAY and value == value and abs(value) != float("inf"):
            blocks.append((day, value))
    return blocks


def build_promotion_shadow(trading_dir) -> dict[str, object]:
    """Judge every slot-experiment cell; report only, no side effects."""
    base: dict[str, object] = {
        "rule_version": RULE_VERSION, "first_block_day": FIRST_BLOCK_DAY,
        "threshold": THRESHOLD, "min_blocks": MIN_BLOCKS,
        "status": "unavailable", "cells_evaluated": 0, "cells_with_min_blocks": 0,
        "ruined_count": 0, "max_e_value": None, "promotable_count": 0, "promotable": [],
    }
    try:
        result = aggregate_cell_metrics(Path(trading_dir) / "paper.sqlite3")
        if result.get("cells") is None:
            return {**base, "status": "unavailable",
                    "reason": str(result.get("reason_code", "ledger_unavailable"))[:60]}
        judged = []
        for cell in _slot_cells(result["cells"]):
            blocks = _blocks(cell)
            if not blocks:
                continue
            outcome = e_process(blocks)
            judged.append((cell, outcome))
    except Exception:
        return base
    promotable = sorted(
        (item for item in judged if item[1]["promoted_day"] is not None),
        key=lambda item: -float(item[1]["e_value"]),
    )
    return {
        **base,
        "status": str(result.get("status", "ok")),
        "cells_evaluated": len(judged),
        "cells_with_min_blocks": sum(1 for _, o in judged if o["blocks"] >= MIN_BLOCKS),
        "ruined_count": sum(1 for _, o in judged if o["ruined"]),
        "max_e_value": (max(float(o["e_value"]) for _, o in judged) if judged else None),
        "promotable_count": len(promotable),
        "promotable": [
            {"symbol": str(cell["symbol"]), "experiment_id": str(cell["experiment_id"]),
             "blocks": o["blocks"], "e_value": round(float(o["e_value"]), 1),
             "promoted_day": o["promoted_day"], "closed_sells": cell.get("closed_sells")}
            for cell, o in promotable[:MAX_LISTED]
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PAPER promotion shadow judge (read-only)")
    parser.add_argument("--state-dir", required=True, help="the trading directory itself")
    args = parser.parse_args(argv)
    print(json.dumps(build_promotion_shadow(args.state_dir), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
