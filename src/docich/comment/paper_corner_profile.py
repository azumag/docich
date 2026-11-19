"""Comment prompt profile for the PAPER crypto corner screen.

docich owns the game-specific comment prompt slots (see :mod:`game_profile`).
While the main screen is ``paper-view``, Soren's comment worker otherwise keeps
the Soviet game persona and score memo, so a viewer's question such as
``お金増えた？`` cannot be answered from the corner's own status.  This module
turns the published PAPER status into measured facts and a reply rule: answer
with the corner's P/L and say explicitly that the trading is PAPER (virtual
funds, simulated fills).

The reader is read-only and bounded.  Missing, incomplete, malformed or stale
data is stated as such; no amount is guessed or carried over from the Soviet
game.
"""
from __future__ import annotations

import json
import math
import os
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Mapping

try:  # Python 3.9+; the launcher runs in the same runtime as docich
    from zoneinfo import ZoneInfo

    _JST = ZoneInfo("Asia/Tokyo")
except Exception:  # pragma: no cover - only a broken tz database reaches this
    _JST = timezone(timedelta(hours=9))

PAPER_VIEW = "paper-view"
BLURB = "PAPER暗号資産コーナー。仮想資金の模擬売買で損益を検証している(実取引なし)"

# The paper worker publishes every 60s.  Three missed cycles means the numbers
# may no longer be the current state, so they are shown as reference values.
STALE_AFTER_S = 180
_STATUS_LIMIT = 256 * 1024

# The shell keeps at most 1500 characters per profile string.
FIELD_LIMIT = 1500

GAME_RULES_EN = (
    "【Current Corner Rules (PAPER crypto trading)】\n"
    "This is not a game. It is the PAPER crypto-trading corner: simulated trading with "
    "virtual funds. No real money, no exchange account, and no real order is used; fills "
    "and P/L are computed locally from public prices.\n"
    "Screen labels: 資金 (capital), 投入 (deployed), 累積損益 (cumulative P/L, valuation "
    "included), 本日損益 (today's realized P/L), 含み損益 (unrealized P/L), 保有 "
    "(positions), 直近約定 (recent fills), BOTの判断 (decision summary).\n"
    "When asked about money, profit, or losses, use the current-corner memo's numbers, "
    "state whether the cumulative P/L is up or down, and always say this is PAPER trading "
    "with virtual funds, not real income. Do not apply the Soviet game's rules "
    "(countries, merging, score)."
)

GAME_RULES_JA = (
    "\t【現在のコーナーの基本ルール（PAPER暗号資産取引）】\n"
    "\t※ ゲームではない。仮想資金で暗号資産の売買判断を検証するPAPER取引のコーナー。"
    "実際のお金・口座・発注は使わず、約定と損益は公開価格からローカルで模擬計算している\n"
    "\t- 画面の主な表示: 資金、投入、累積損益、本日損益(確定分)、含み損益、保有、直近約定、BOTの判断\n"
    "\t- お金・儲け・損益を聞かれたら、現在のコーナーのメモの数字で、累積損益がプラスかマイナスかを答える\n"
    "\t- 必ずPAPER取引(仮想資金)であり実収入ではないと伝える。実取引の利益のように話さない\n"
    "\t- ソ連ゲームの国・併合・得点のルールで説明しない"
)

UI_MEMO = (
    "【いまの画面: PAPER暗号資産コーナー】\n"
    "画面上部に「PAPER / 模擬」と資金・投入。カードに累積損益・本日損益(確定分)・"
    "含み損益、保有銘柄、直近約定、BOTの判断が出る。"
)

OPS_LINES = (
    "- このコーナーはPAPER暗号資産コーナー。仮想資金の模擬売買で、実際のお金は増減しない",
    "- ソ連ゲームの戦略改善・メリケンAI・予想の話は、このコーナーの話ではない",
)


# No PAPER amount is legitimately this large; a corrupted status must not turn
# into a giant claim (Decimal.quantize also raises beyond the context precision).
_MAX_MONEY = Decimal("1e15")


def _decimal(value: object) -> Decimal | None:
    """A finite Decimal from a JSON number/string, else None. bool is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and abs(number) <= _MAX_MONEY else None


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _timestamp(value: object) -> float | None:
    """A plausible Unix timestamp (0..2100); absurd values are treated as absent."""
    number = _finite(value)
    if number is None or not 0 <= number <= 4_102_444_800:
        return None
    return number


def _rounded_yen(value: Decimal) -> Decimal | None:
    try:
        return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _yen(value: Decimal | None, *, signed: bool = True) -> str:
    """Nearest-yen text; a signed value never renders '+0円' or '-0円'."""
    if value is None:
        return "不明"
    rounded = _rounded_yen(value)
    if rounded is None:
        return "不明"
    if signed:
        if rounded > 0:
            return f"+{int(rounded):,}円"
        if rounded < 0:
            return f"-{abs(int(rounded)):,}円"
        return "0円"
    return f"{int(rounded):,}円"


def _age_text(seconds: float | None) -> str:
    if seconds is None:
        return "不明"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}秒前"
    if seconds < 3600:
        return f"{seconds // 60}分前"
    if seconds < 86400:
        return f"{seconds // 3600}時間前"
    return f"{seconds // 86400}日前"


def _jst_text(timestamp: float | None) -> str:
    if timestamp is None:
        return "不明"
    try:
        return datetime.fromtimestamp(timestamp, _JST).strftime("%m/%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "不明"


def _read_status(state_dir: Path | None) -> dict | None:
    """Read ``<state_dir>/trading/status.json`` without following links.

    Only a regular file within the size bound is accepted.  Every failure is
    "unavailable": the caller states that instead of inventing numbers.
    """
    if state_dir is None:
        return None
    path = Path(state_dir) / "trading" / "status.json"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _STATUS_LIMIT:
                return None
            raw = stream.read(_STATUS_LIMIT + 1)
    except OSError:
        return None
    if len(raw) > _STATUS_LIMIT:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, UnicodeError):
        return None
    if not isinstance(value, dict) or value.get("mode") != "paper":
        return None
    return value


def _performance(status: Mapping[str, object]) -> Mapping[str, object]:
    value = status.get("performance_summary")
    return value if isinstance(value, Mapping) else {}


def _position_count(status: Mapping[str, object]) -> int | None:
    positions = status.get("open_positions")
    return len(positions) if isinstance(positions, Mapping) else None


def _market_count(status: Mapping[str, object]) -> int | None:
    value = status.get("market_count")
    return value if type(value) is int and value >= 0 else None


def _worker_label(status: Mapping[str, object]) -> str:
    return {
        "paper_worker_running": "稼働中",
        "paper_worker_degraded": "一部不調",
        "paper_worker_idle": "待機中",
    }.get(str(status.get("worker_state") or ""), "不明")


def _unavailable() -> str:
    return "\n".join((
        "現在のメイン画面: PAPER暗号資産コーナー(paper-view)。"
        "実際のお金は使わないPAPER取引で、仮想資金による暗号資産の模擬売買を検証している。"
        "実口座・実発注はない。",
        "※このコーナーの損益ステータスはまだ取得できていない。資金・損益を聞かれたら、"
        "今は数字を確認できないと答え、推測で金額を作らない。PAPER取引(仮想資金)であることは必ず伝える。",
        "ソ連ゲームのスコア・建国・戦略改善の話ではない。",
    ))


def _direction(value: Decimal) -> str:
    """Direction of the value as displayed: sub-yen moves are still 0円."""
    rounded = _rounded_yen(value)
    if rounded is None or rounded == 0:
        return "変わっていない"
    return "増えている" if rounded > 0 else "減っている"


@dataclass(frozen=True)
class _Facts:
    as_of: float | None
    age: float | None
    stale: bool
    capital: Decimal | None
    equity: Decimal | None
    cumulative: Decimal | None
    today: Decimal | None
    unrealized: Decimal | None
    realized_total: Decimal | None
    inconsistent: bool
    positions: int | None
    markets: int | None
    deployed: Decimal | None
    worker: str


def _facts(status: Mapping[str, object], moment: float) -> _Facts:
    perf = _performance(status)
    cumulative = _decimal(perf.get("cumulative_pnl_jpy"))
    capital = _decimal(status.get("capital_reference"))
    equity = _decimal(perf.get("equity_jpy"))
    inconsistent = perf.get("complete") is True and cumulative is None
    if (not inconsistent and capital is not None and equity is not None
            and cumulative is not None and abs((equity - capital) - cumulative) > 1):
        # equity must be capital + cumulative; a mismatch is corrupted data.
        inconsistent = True
    today = _decimal(perf.get("today_realized_pnl_jpy"))
    unrealized = _decimal(perf.get("unrealized_pnl_jpy"))
    realized_total = _decimal(perf.get("realized_total_jpy"))
    if inconsistent:
        # A complete valuation must carry a cumulative value, and equity must
        # equal capital + cumulative.  Treat the whole performance block as
        # unusable instead of mixing partial fields.
        cumulative = today = unrealized = realized_total = None
    as_of = _timestamp(perf.get("as_of")) or _timestamp(status.get("snapshot_generated_at"))
    age = None if as_of is None else max(0.0, moment - as_of)
    return _Facts(
        as_of=as_of,
        age=age,
        stale=age is not None and age > STALE_AFTER_S,
        capital=capital,
        equity=equity,
        cumulative=cumulative,
        today=today,
        unrealized=unrealized,
        realized_total=realized_total,
        inconsistent=inconsistent,
        positions=_position_count(status),
        markets=_market_count(status),
        deployed=_decimal(status.get("deployed_reference")),
        worker=_worker_label(status),
    )


def _game_state(f: _Facts) -> str:
    """Measured PAPER corner facts plus the rule for money questions."""
    lines = [
        "現在のメイン画面: PAPER暗号資産コーナー(paper-view)。"
        "実際のお金は使わないPAPER取引で、仮想資金による暗号資産の模擬売買を検証している。"
        "実口座・実発注はない。",
    ]
    if f.as_of is None:
        lines.append("画面の成績(最終更新時刻は不明):")
    else:
        lines.append(f"画面の成績(最終更新 {_jst_text(f.as_of)}、{_age_text(f.age)}):")
    if f.stale:
        lines.append(
            "※最終更新が古い。いまの損益とは断定せず、参考値として扱い、その旨を答える。"
        )

    if f.cumulative is not None:
        capital_text = _yen(f.capital, signed=False) if f.capital is not None else "不明"
        equity_text = _yen(f.equity, signed=False) if f.equity is not None else "不明"
        lines.append(
            f"- 資金 {capital_text} → 評価込み資産 {equity_text}。"
            f"累積損益 {_yen(f.cumulative)}で、元手より{_direction(f.cumulative)}。"
        )
    else:
        capital_text = _yen(f.capital, signed=False) if f.capital is not None else "不明"
        reason = "取得できていない" if f.inconsistent else "評価待ち(保有の価格評価が揃っていない)"
        lines.append(f"- 資金 {capital_text}。累積損益は{reason}。")

    parts: list[str] = []
    if f.today is not None:
        parts.append(f"本日損益(確定分) {_yen(f.today)}")
    if f.unrealized is not None:
        parts.append(f"含み損益 {_yen(f.unrealized)}")
    if f.realized_total is not None:
        parts.append(f"確定損益の通算 {_yen(f.realized_total)}")
    if parts:
        lines.append("- " + " / ".join(parts))
    if f.cumulative is None and not f.inconsistent:
        lines.append(
            "- 保有の価格評価がすべて揃っていないため、累積損益と含み損益は評価待ち。"
            "本日の確定分だけが確定値。"
        )

    facts: list[str] = []
    if f.positions is not None:
        facts.append(f"保有 {f.positions}銘柄")
    if f.markets is not None:
        facts.append(f"監視市場 {f.markets}")
    if f.deployed is not None:
        facts.append(f"投入 {_yen(f.deployed, signed=False)}")
    facts.append(f"状態 {f.worker}")
    lines.append("- " + " / ".join(facts))

    lines.append(
        "返答ルール: 「お金増えた？」「儲かった？」など資金・損益を聞かれたら、最初にPAPER取引(仮想資金)で"
        "あることを伝え、そのうえでコーナー内の累積損益の数字と増減を必ず答える。本日損益と含み損益も分かる範囲で"
        "添える。累積損益がプラスならコーナー内では増えている、マイナスなら減っていると言う。"
        "実際のお金は増えないという説明だけで終わらせない。数字が無い・評価待ち・更新が古い場合は、"
        "断定せずにその旨を言い、金額を推測で作らない。実取引の利益のように話さず、"
        "ソ連ゲームのスコア・建国・戦略改善の話とは混ぜない。"
    )
    return "\n".join(lines)[:FIELD_LIMIT]


def _ui_memo(f: _Facts) -> str:
    """The screen memo: every response template includes this slot.

    The chitchat template has no game-state slot, so a short reaction-style
    money question (e.g. ``お金増えた`` without a question mark) would otherwise
    lose the corner's own P/L.  Keep a compact measured line here as well.
    """
    text = UI_MEMO
    if f.cumulative is not None:
        text += (
            f"\nコーナー内の累積損益は{_yen(f.cumulative)}で元手より{_direction(f.cumulative)}"
            "（PAPER取引・仮想資金。実際のお金ではない）。資金や損益を聞かれたらこの数字で答える。"
        )
        if f.stale:
            text += "更新が古いので参考値として扱う。"
    else:
        reason = "取得できていない" if f.inconsistent else "評価待ち"
        text += (
            f"\n累積損益はいまのところ{reason}。資金や損益を聞かれたら、数字が確認できないと答える。"
            "PAPER取引(仮想資金)であることは伝える。"
        )
    return text[:FIELD_LIMIT]


def build_game_state(state_dir: Path | None, *, now: float | None = None) -> str:
    """Measured PAPER corner facts plus the rule for money questions."""
    status = _read_status(state_dir)
    if status is None:
        return _unavailable()
    moment = time.time() if now is None else float(now)
    return _game_state(_facts(status, moment))


def build(state_dir: Path | None, *, now: float | None = None) -> dict[str, object]:
    """The comment profile for ``paper-view`` (persona replacement is added by
    :func:`docich.comment.game_profile.build`)."""
    moment = time.time() if now is None else float(now)
    status = _read_status(state_dir)
    if status is None:
        game_state, ui_memo = _unavailable(), UI_MEMO
    else:
        facts = _facts(status, moment)
        game_state, ui_memo = _game_state(facts), _ui_memo(facts)
    return {
        "schema": 1,
        "game": PAPER_VIEW,
        "game_state": game_state,
        "ui_memo": ui_memo,
        "game_rules": GAME_RULES_EN,
        "game_rules_ja": GAME_RULES_JA,
        "ops_lines": list(OPS_LINES),
    }


__all__ = [
    "BLURB",
    "GAME_RULES_EN",
    "GAME_RULES_JA",
    "OPS_LINES",
    "PAPER_VIEW",
    "STALE_AFTER_S",
    "UI_MEMO",
    "build",
    "build_game_state",
]
