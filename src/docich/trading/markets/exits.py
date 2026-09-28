"""Quote-driven PAPER exits; trigger levels are never assumed fill prices.

The caller validates quotes and commits this state with the fill in one SQLite
transaction. No timers, broker requests, bar highs/lows, or external I/O live here.
"""
from __future__ import annotations

from decimal import Decimal

D = Decimal
REASONS = frozenset({"session_end", "risk_stop", "stop_loss", "trailing_stop",
                     "take_profit", "max_hold", "signal_reverse"})


def _number(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("invalid exit number")
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("non-finite exit number")
    return result


def _initial_state(pos: dict, *, from_entry: bool) -> dict:
    return {"peak_price": pos["mark"],
            "stop_price": str(_number(pos["entry"]) * (1 - pos["side"] * D(pos["stop_bps"]) / 10000)),
            "armed": False, "requested_reason": None,
            "max_net_pnl_jpy": "0", "tracking_from_entry": from_entry}


def initialize_exit(pos: dict, policy) -> None:
    """Freeze the exit contract on a newly filled position, never on a proposal."""
    pos.update(exit_mode=policy.exit_mode, trail_activation_bps=policy.trail_activation_bps,
               trail_distance_bps=policy.trail_distance_bps)
    pos["exit_state"] = _initial_state(pos, from_entry=True)


def observe_exit(pos: dict, *, fee_bps: Decimal, slippage_bps: Decimal) -> None:
    """Advance extrema and the stop only on the caller's validated NEW quote."""
    mode = pos.get("exit_mode", "fixed")
    if mode not in {"fixed", "trailing"}:
        raise ValueError("unknown persisted exit mode")
    if "exit_state" not in pos:
        if mode == "trailing":
            raise ValueError("trailing state missing; cannot reset a protected stop")
        # Legacy positions keep their fixed exits. Never invent pre-upgrade MFE.
        pos["exit_state"] = _initial_state(pos, from_entry=False)
    state = pos["exit_state"]
    if (type(state["armed"]) is not bool or type(state["tracking_from_entry"]) is not bool
            or state["requested_reason"] not in REASONS | {None}):
        raise ValueError("invalid persisted exit state")
    px, entry, qty = (_number(pos[key]) for key in ("mark", "entry", "qty"))
    peak, stop = _number(state["peak_price"]), _number(state["stop_price"])
    if min(px, entry, qty, peak, stop) <= 0 or pos["side"] not in (-1, 1):
        raise ValueError("invalid persisted exit prices")
    side = pos["side"]
    peak = max(peak, px) if side == 1 else min(peak, px)
    state["peak_price"] = str(peak)
    if mode == "trailing":
        activation, distance = pos["trail_activation_bps"], pos["trail_distance_bps"]
        if (type(activation) is not int or type(distance) is not int
                or not 5 <= distance <= 300 or not distance < activation <= 600):
            raise ValueError("invalid persisted trail policy")
        if (peak / entry - 1) * side * 10000 >= activation:
            state["armed"] = True
        if state["armed"]:
            proposed = peak * (1 - side * D(distance) / 10000)
            stop = max(stop, proposed) if side == 1 else min(stop, proposed)
            state["stop_price"] = str(stop)
    # This uses precisely the close-fill cost model, including accrued financing.
    exit_px = px * (1 - side * slippage_bps / 10000)
    net = ((exit_px - entry) * qty * side - qty * exit_px * fee_bps / 10000
           - _number(pos["entry_fee"]) - _number(pos["financing"]))
    state["max_net_pnl_jpy"] = str(max(D(0), _number(state["max_net_pnl_jpy"]), net))


def exit_reason(pos: dict, *, now: float, direction: int,
                force_flat: bool, risk_stopped: bool) -> str | None:
    """Latch an exit intent; insufficient liquidity must not cancel a trigger."""
    state = pos["exit_state"]
    px, entry = _number(pos["mark"]), _number(pos["entry"])
    side = pos["side"]
    signed_return = (px / entry - 1) * side * 10000
    reason = state["requested_reason"]
    if force_flat:
        reason = "session_end"
    elif risk_stopped:
        reason = "risk_stop"
    elif reason is None:
        if signed_return <= -pos["stop_bps"]:
            reason = "stop_loss"
        elif (pos.get("exit_mode", "fixed") == "trailing" and state["armed"]
              and (px - _number(state["stop_price"])) * side <= 0):
            reason = "trailing_stop"
        elif pos.get("exit_mode", "fixed") == "fixed" and signed_return >= pos["take_bps"]:
            reason = "take_profit"
        elif now - pos["opened_at"] >= pos["max_hold_s"]:
            reason = "max_hold"
        elif direction and direction != side:
            reason = "signal_reverse"
    if reason is not None:
        if state["requested_reason"] is None:
            state["trigger_quote_ts"] = pos["mark_ts"]
            state["trigger_stop_price"] = state["stop_price"]
        state["requested_reason"] = reason
    return reason


def fill_exit_details(pos: dict, net_pnl: Decimal) -> dict:
    state = pos["exit_state"]
    peak = _number(state["max_net_pnl_jpy"])
    return {"exit_mode": pos.get("exit_mode", "fixed"),
            "max_net_pnl_jpy": str(peak),
            "peak_to_exit_giveback_jpy": str(max(D(0), peak - net_pnl)),
            "mfe_tracking_from_entry": state["tracking_from_entry"],
            "trigger_quote_ts": state["trigger_quote_ts"],
            "trigger_stop_price": state["trigger_stop_price"]}


def record_exit_stats(account: dict, fill: dict) -> None:
    """Incremental Decimal aggregates, avoiding a full fill scan on every tick."""
    stats = account.setdefault("exit_stats", {"closed_trades": 0, "wins": 0,
        "net_pnl_jpy": "0", "peak_to_exit_giveback_jpy": "0", "mfe_complete_trades": 0})
    stats["closed_trades"] += 1
    stats["wins"] += int(_number(fill["net_pnl_jpy"]) > 0)
    stats["mfe_complete_trades"] += int(fill["mfe_tracking_from_entry"])
    for key in ("net_pnl_jpy", "peak_to_exit_giveback_jpy"):
        stats[key] = str(_number(stats[key]) + _number(fill[key]))
