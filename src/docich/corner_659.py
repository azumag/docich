"""チャート解説コーナー (issue #659).

チャートデータから配信向け解説コンテンツを決定論的に生成する。

生成物:
  - 音声解説原稿 : 音声合成キューへ渡す日本語ナレーション
  - 注文チャート実況 : 約定/注文イベントを時系列に追った実況テキスト
  - 約定時判断シグナル可視化 : 各約定時点の買・売・保有判断と可視化用マーク
  - アニメーション解説 : フレームレート制御付きの解説フレーム列

チャート取得は ``ChartFetcher`` に閉じ込め、本モジュールはネットワークに
接続しない。既定の ``FixedChartFetcher`` は固定フィクスチャを返すので
外部APIに依存せずユニットテスト可能。外部API版の ``ApiChartFetcher`` は
レート制限 (HTTP 429) に指数バックオフ＋キャッシュフォールバックで
対応する（リスク欄の「外部 API 依存」に対処）。

アニメーションは ``AnimationPlayer`` がフレームレートを厳密に制御し
（``frame rate 制御必須`` のリスクに対処）、画面同期負荷を抑える。
クロックとスリープは注入可能で、テストでは実時間を待たない。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol, runtime_checkable

from .game_switch import atomic_write_json


SCHEMA_VERSION = 1
DEFAULT_SYMBOL = "USD/JPY"
# 生成物の上限。無制限な列挙を避け、配信枠に乗る量に抑える。
MAX_CANDLES = 400
MAX_ORDERS = 150
MAX_EXECUTIONS = 150
MAX_COMMENTARY_LINES = 80
MAX_AUDIO_LINES = 40
MAX_AUDIO_LINE_CHARS = 60
# アニメーションのフレームレート (fps)。1..60 の範囲で制御する。
DEFAULT_FPS = 15
MIN_FPS = 1
MAX_FPS = 60
MAX_ANIMATION_FRAMES = 300
# 同方向のシグナルを連発しない間隔 (秒)。
SIGNAL_COOLDOWN_SEC = 3.0
# 移動平均乖離で「トレンドあり」とみなす価格変動率。
PRICE_CHANGE_THRESHOLD = 0.002
SHORT_MA_WINDOW = 5
LONG_MA_WINDOW = 20
# 外部API版のキャッシュ有効期限 (秒) と指数バックオフ。
CACHE_TTL_SEC = 300.0
BACKOFF_BASE_SEC = 0.5
BACKOFF_MAX_SEC = 20.0
MAX_FETCH_RETRIES = 5
# 状態ファイル。
STATE_FILENAME = "chart_corner_659.json"

class ChartError(Exception):
    """チャート解説コーナーの回復不能でない失敗。"""


class ChartRateLimitError(ChartError):
    """外部APIがレート制限 (429 等) を返した。"""


class ChartFetchError(ChartError):
    """チャートデータ取得に失敗した。"""


class SignalSide(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class SignalStrength(Enum):
    WEAK = "weak"
    NORMAL = "normal"
    STRONG = "strong"


class EventType(Enum):
    ORDER_PLACED = "order_placed"
    EXECUTION = "execution"
    SIGNAL = "signal"


@dataclass(frozen=True)
class ChartCandle:
    """1本の足。timestamp はエポック秒。"""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Order:
    order_id: str
    timestamp: float
    side: str  # "buy" | "sell"
    price: float
    size: float
    status: str  # "placed" | "filled" | "cancelled"


@dataclass(frozen=True)
class Execution:
    execution_id: str
    order_id: str
    timestamp: float
    price: float
    size: float
    side: str  # "buy" | "sell"


@dataclass(frozen=True)
class ChartSnapshot:
    """1回の取得で得られるチャートの全状態。不変。"""
    symbol: str
    fetched_at: float
    candles: tuple[ChartCandle, ...]
    orders: tuple[Order, ...]
    executions: tuple[Execution, ...]
    source: str  # "fixture" | "api"
    note: str = ""


@dataclass(frozen=True)
class JudgmentSignal:
    """約定時点の判断シグナル。可視化用マークを含む。"""
    timestamp: float
    price: float
    side: SignalSide
    strength: SignalStrength
    reason: str
    marker: str  # "買"/"売"/"保有"
    execution_id: str | None = None


@dataclass(frozen=True)
class CommentaryItem:
    timestamp: float
    event_type: EventType
    text: str  # 日本語の実況テキスト (1行、境界サイズ)
    order_id: str | None = None
    execution_id: str | None = None


@dataclass(frozen=True)
class AnimationFrame:
    frame_index: int
    # 再生予定時刻 (モノトニック秒)。player がこの刻みで sleep する。
    scheduled_at: float
    symbol: str
    price: float
    signal: JudgmentSignal | None
    commentary: str | None
    candles_count: int


@dataclass(frozen=True)
class AudioScript:
    title: str
    lines: tuple[str, ...]  # 日本語ナレーション、1行 MAX_AUDIO_LINE_CHARS 以内
    estimated_seconds: float


@dataclass(frozen=True)
class CornerContent:
    """corner_659 の生成物全体。状態ファイルにもそのまま保存する。"""
    symbol: str
    generated_at: float
    audio: AudioScript
    commentary: tuple[CommentaryItem, ...]
    signals: tuple[JudgmentSignal, ...]
    animation_frames: tuple[AnimationFrame, ...]


# ---------------------------------------------------------------------------
# チャート取得インタフェース
# ---------------------------------------------------------------------------


@runtime_checkable
class ChartFetcher(Protocol):
    """チャートデータ取得器。実装はネットワーク依存を持ってもよい。"""

    def fetch(self, symbol: str) -> ChartSnapshot:
        """``symbol`` のチャートスナップショットを返す。失敗は ChartError 群。"""


class FixedChartFetcher:
    """固定フィクスチャを返す取得器。テスト・オフライン動作の既定。

    ``side_effect`` に例外を入れると取得失敗を模擬し、
    ``rate_limit`` に True を入れると 429 を模擬する。
    """

    def __init__(
        self,
        snapshot: ChartSnapshot,
        *,
        side_effect: Exception | None = None,
        rate_limit: bool = False,
    ) -> None:
        self._snapshot = snapshot
        self._side_effect = side_effect
        self._rate_limit = rate_limit
        self.calls: list[str] = []

    def fetch(self, symbol: str) -> ChartSnapshot:
        self.calls.append(symbol)
        if self._side_effect is not None:
            raise self._side_effect
        if self._rate_limit:
            raise ChartRateLimitError("synthetic 429 too many requests")
        return self._snapshot


class _CacheEntry:
    __slots__ = ("snapshot", "fetched_at")

    def __init__(self, snapshot: ChartSnapshot, fetched_at: float) -> None:
        self.snapshot = snapshot
        self.fetched_at = fetched_at

    def is_stale(self, now: float, ttl: float) -> bool:
        return now - self.fetched_at > ttl

class ApiChartFetcher:
    """外部チャートAPI版取得器。

    429 レート制限に指数バックオフでリトライし、繰り返し失敗したら
    キャッシュ内の最新スナップショットをフォールバック返却する。
    標準ライブラリのみを使用し、外部パッケージに依存しない。
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: float = 10.0,
        cache_ttl_sec: float = CACHE_TTL_SEC,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._cache_ttl_sec = cache_ttl_sec
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[str, _CacheEntry] = {}
        self._last_status: dict[str, int | None] = {}

    def fetch(self, symbol: str) -> ChartSnapshot:
        now = self._clock()
        cached = self._cache.get(symbol)

        attempt = 0
        while True:
            attempt += 1
            try:
                snapshot = self._fetch_once(symbol)
                self._cache[symbol] = _CacheEntry(snapshot, self._clock())
                self._last_status[symbol] = 200
                return snapshot
            except ChartRateLimitError:
                self._last_status[symbol] = 429
                if attempt >= MAX_FETCH_RETRIES:
                    if cached is not None and not cached.is_stale(now, self._cache_ttl_sec):
                        return cached.snapshot
                    raise ChartFetchError(
                        f"rate limit persists for {symbol} after {attempt} attempts"
                    )
                wait = min(BACKOFF_BASE_SEC * (2 ** (attempt - 1)), BACKOFF_MAX_SEC)
                self._sleep(wait)
            except (ChartFetchError, ChartError):
                raise
            except Exception as exc:  # ネットワーク・パースエラーを一本化
                raise ChartFetchError(f"unexpected fetch failure for {symbol}: {exc}") from exc

    def _fetch_once(self, symbol: str) -> ChartSnapshot:
        url = f"{self._base_url}/chart?symbol={symbol}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_sec) as response:
                body = response.read().decode("utf-8")
                status = response.status or 200
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ChartRateLimitError(f"HTTP 429 for {symbol}") from exc
            raise ChartFetchError(f"HTTP {exc.code} for {symbol}") from exc
        except urllib.error.URLError as exc:
            raise ChartFetchError(f"URL error for {symbol}: {exc}") from exc

        try:
            data = json.loads(body)
        except ValueError as exc:
            raise ChartFetchError(f"invalid JSON for {symbol}") from exc
        return self._parse_payload(symbol, data, status)

    def _parse_payload(self, symbol: str, data: Mapping[str, object], status: int) -> ChartSnapshot:
        if not isinstance(data, dict):
            raise ChartFetchError(f"invalid chart payload for {symbol}")
        candles_raw = data.get("candles", [])
        orders_raw = data.get("orders", [])
        executions_raw = data.get("executions", [])
        if not isinstance(candles_raw, list) or not isinstance(orders_raw, list) \
                or not isinstance(executions_raw, list):
            raise ChartFetchError(f"invalid chart payload shape for {symbol}")

        candles = tuple(self._candle(item) for item in candles_raw[:MAX_CANDLES])
        orders = tuple(self._order(item) for item in orders_raw[:MAX_ORDERS])
        executions = tuple(self._execution(item) for item in executions_raw[:MAX_EXECUTIONS])
        fetched_at = float(data.get("fetched_at", time.time()))
        return ChartSnapshot(
            symbol=str(data.get("symbol", symbol)),
            fetched_at=fetched_at,
            candles=candles,
            orders=orders,
            executions=executions,
            source="api",
            note=f"fetched from external API (HTTP {status})",
        )

    @staticmethod
    def _candle(item: Mapping[str, object]) -> ChartCandle:
        return ChartCandle(
            timestamp=_finite_float(item, "timestamp"),
            open=_finite_float(item, "open"),
            high=_finite_float(item, "high"),
            low=_finite_float(item, "low"),
            close=_finite_float(item, "close"),
            volume=_finite_float(item, "volume", default=0.0),
        )

    @staticmethod
    def _order(item: Mapping[str, object]) -> Order:
        return Order(
            order_id=_required_str(item, "order_id"),
            timestamp=_finite_float(item, "timestamp"),
            side=_required_str(item, "side"),
            price=_finite_float(item, "price"),
            size=_finite_float(item, "size"),
            status=_required_str(item, "status"),
        )

    @staticmethod
    def _execution(item: Mapping[str, object]) -> Execution:
        return Execution(
            execution_id=_required_str(item, "execution_id"),
            order_id=_required_str(item, "order_id"),
            timestamp=_finite_float(item, "timestamp"),
            price=_finite_float(item, "price"),
            size=_finite_float(item, "size"),
            side=_required_str(item, "side"),
        )


def _finite_float(item: Mapping[str, object], key: str, *, default: float = 0.0) -> float:
    raw = item.get(key, default)
    if isinstance(raw, bool) or raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ChartFetchError(f"invalid numeric field {key}: {raw!r}") from exc
    if not math.isfinite(value):
        return default
    return value


def _required_str(item: Mapping[str, object], key: str) -> str:
    raw = item.get(key)
    if not isinstance(raw, str) or not raw:
        raise ChartFetchError(f"invalid string field {key}: {raw!r}")
    return raw

# ---------------------------------------------------------------------------
# チャート分析（シグナル判定）
# ---------------------------------------------------------------------------


def _closes(candles: Iterable[ChartCandle]) -> list[float]:
    return [c.close for c in candles]


def _moving_average(values: list[float], window: int) -> list[float | None]:
    """単純移動平均。window 分未満は None。"""
    result: list[float | None] = []
    if window < 1:
        return result
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= window:
            total -= values[i - window]
        if i >= window - 1:
            result.append(total / window)
        else:
            result.append(None)
    return result


def _recent_candle_at_or_before(
    candles: tuple[ChartCandle, ...], timestamp: float
) -> ChartCandle | None:
    chosen: ChartCandle | None = None
    for candle in candles:
        if candle.timestamp <= timestamp:
            chosen = candle
        else:
            break
    return chosen


def _signal_at(
    candles: tuple[ChartCandle, ...],
    execution: Execution,
    prev_price: float | None,
    cooldown_until: float,
    last_side: SignalSide | None = None,
) -> tuple[JudgmentSignal | None, float]:
    """1約定ごとの判断シグナルを作成する。

    短期/長期移動平均の並びと約定価格の乖離で買・売・保有を判定し、
    ``SIGNAL_COOLDOWN_SEC`` 以内の同方向シグナルは抑制する。
    """
    candle = _recent_candle_at_or_before(candles, execution.timestamp)
    if candle is None:
        return None, cooldown_until
    closes = _closes([c for c in candles if c.timestamp <= execution.timestamp])
    short_ma_list = _moving_average(closes, SHORT_MA_WINDOW)
    long_ma_list = _moving_average(closes, LONG_MA_WINDOW)
    short_ma = short_ma_list[-1] if short_ma_list else None
    long_ma = long_ma_list[-1] if long_ma_list else None

    price = execution.price
    side = SignalSide.HOLD
    strength = SignalStrength.WEAK
    reason_parts: list[str] = []

    if short_ma is not None and long_ma is not None:
        if price > short_ma and short_ma > long_ma:
            side = SignalSide.BUY
            reason_parts.append("短期移動平均が長期を上回り買い優勢")
        elif price < short_ma and short_ma < long_ma:
            side = SignalSide.SELL
            reason_parts.append("短期移動平均が長期を下回り売り優勢")
    if prev_price is not None and prev_price > 0:
        change = (price - prev_price) / prev_price
        if abs(change) >= PRICE_CHANGE_THRESHOLD:
            strength = SignalStrength.STRONG
            reason_parts.append(
                f"約定価格が直前比 {change * 100:.1f}% の変動"
            )
        elif side == SignalSide.HOLD:
            reason_parts.append("方向感は乏しく保有判断")
    if not reason_parts:
        reason_parts.append("約定を確認し現状維持")
    reason = "、".join(reason_parts)

    marker = {
        SignalSide.BUY: "買",
        SignalSide.SELL: "売",
        SignalSide.HOLD: "保有",
    }[side]

    # クールダウン中の同方向シグナルは保有に落とす（連発抑制）。
    if execution.timestamp < cooldown_until and side == last_side and side != SignalSide.HOLD:
        side = SignalSide.HOLD
        strength = SignalStrength.WEAK
        reason = "クールダウン中のため保有に留保"
        marker = "保有"

    new_cooldown = cooldown_until
    if side != SignalSide.HOLD:
        new_cooldown = execution.timestamp + SIGNAL_COOLDOWN_SEC

    signal = JudgmentSignal(
        timestamp=execution.timestamp,
        price=price,
        side=side,
        strength=strength,
        reason=reason,
        marker=marker,
        execution_id=execution.execution_id,
    )
    return signal, new_cooldown


def compute_signals(
    snapshot: ChartSnapshot,
) -> tuple[JudgmentSignal, ...]:
    """全約定に対する判断シグナルを時系列に返す。"""
    candles = tuple(sorted(snapshot.candles, key=lambda c: c.timestamp))
    executions = tuple(sorted(snapshot.executions, key=lambda e: e.timestamp))
    signals: list[JudgmentSignal] = []
    cooldown_until = 0.0
    prev_price: float | None = None
    last_side: SignalSide | None = None
    for execution in executions:
        signal, cooldown_until = _signal_at(candles, execution, prev_price, cooldown_until, last_side)
        if signal is not None:
            signals.append(signal)
            if signal.side != SignalSide.HOLD:
                last_side = signal.side
        prev_price = execution.price
    return tuple(signals[:MAX_EXECUTIONS])

# ---------------------------------------------------------------------------
# コンテンツ生成（受入条件：音声解説・実況・シグナル可視化）
# ---------------------------------------------------------------------------


def _clean_text(text: str, limit: int) -> str:
    text = str(text or "").replace("\n", " ").strip()
    return text[:limit]


def _format_price(price: float) -> str:
    return f"{price:,.2f}"


def _format_time(timestamp: float) -> str:
    try:
        import datetime as dt

        return dt.datetime.fromtimestamp(float(timestamp)).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "--:--:--"


def build_audio_script(
    snapshot: ChartSnapshot, signals: Iterable[JudgmentSignal]
) -> AudioScript:
    """チャート概況とシグナル概要の音声解説原稿を生成する。"""
    candles = tuple(snapshot.candles)
    closes = _closes(candles)
    latest = max(candles, key=lambda c: c.timestamp).close if candles else 0.0
    high = max((c.high for c in candles), default=0.0)
    low = min((c.low for c in candles), default=0.0)
    volume = sum(c.volume for c in candles)
    orders_count = len(snapshot.orders)
    executions_count = len(snapshot.executions)
    signals = tuple(signals)
    buys = sum(1 for s in signals if s.side == SignalSide.BUY)
    sells = sum(1 for s in signals if s.side == SignalSide.SELL)
    holds = sum(1 for s in signals if s.side == SignalSide.HOLD)

    lines: list[str] = []
    lines.append(f"{snapshot.symbol} チャート解説です。")
    if closes:
        lines.append(
            f"現在値 {_format_price(latest)}、高値 {_format_price(high)}、"
            f"安値 {_format_price(low)}、出来高 {volume:,.0f} です。"
        )
    lines.append(
        f"注文 {orders_count} 件、約定 {executions_count} 件、シグナルは"
        f"買い {buys} 件、売り {sells} 件、保有 {holds} 件です。"
    )
    if signals:
        last = signals[-1]
        lines.append(
            f"最新シグナルは {last.marker}（{last.reason}）。"
        )
    lines.append("以上、チャート解説を終わります。")

    audio_lines = tuple(_clean_text(line, MAX_AUDIO_LINE_CHARS) for line in lines)
    estimated = max(1.0, len(audio_lines) * 2.5)
    return AudioScript(
        title=f"{snapshot.symbol} チャート解説",
        lines=audio_lines[:MAX_AUDIO_LINES],
        estimated_seconds=estimated,
    )


def build_live_commentary(snapshot: ChartSnapshot) -> tuple[CommentaryItem, ...]:
    """注文・約定イベントを時系列に追った実況テキストを生成する。"""
    events: list[CommentaryItem] = []
    for order in sorted(snapshot.orders, key=lambda o: o.timestamp):
        side_word = "買い" if order.side == "buy" else "売り"
        text = (
            f"{side_word}注文を {_format_price(order.price)} で {order.size:.2f} 単位、"
            f"{order.status} です。"
        )
        events.append(
            CommentaryItem(
                timestamp=order.timestamp,
                event_type=EventType.ORDER_PLACED,
                text=_clean_text(text, MAX_AUDIO_LINE_CHARS),
                order_id=order.order_id,
            )
        )
    for execution in sorted(snapshot.executions, key=lambda e: e.timestamp):
        side_word = "買い" if execution.side == "buy" else "売り"
        text = (
            f"{side_word}約定を {_format_price(execution.price)} で "
            f"{execution.size:.2f} 単位約定しました。"
        )
        events.append(
            CommentaryItem(
                timestamp=execution.timestamp,
                event_type=EventType.EXECUTION,
                text=_clean_text(text, MAX_AUDIO_LINE_CHARS),
                order_id=execution.order_id,
                execution_id=execution.execution_id,
            )
        )
    events.sort(key=lambda item: item.timestamp)
    return tuple(events[:MAX_COMMENTARY_LINES])


def build_animation_frames(
    snapshot: ChartSnapshot,
    signals: Iterable[JudgmentSignal],
    *,
    fps: float = DEFAULT_FPS,
    clock: Callable[[], float] = time.time,
) -> tuple[AnimationFrame, ...]:
    """アニメーション解説用フレーム列を生成する。

    約定・シグナルごとに1フレームを割り当て、既定 ``fps`` の間隔で
    再生予定時刻を設定する。``fps`` は [1, 60] にクランプし、
    フレーム数は ``MAX_ANIMATION_FRAMES`` で上限を設ける（画面同期負荷対策）。
    """
    frames_per_second = max(MIN_FPS, min(MAX_FPS, float(fps or DEFAULT_FPS)))
    if frames_per_second <= 0:
        frames_per_second = DEFAULT_FPS
    interval = 1.0 / frames_per_second

    signals = tuple(signals)
    executions = tuple(snapshot.executions)
    events = sorted(
        list(signals) + list(executions),
        key=lambda item: item.timestamp,
    )
    if not events and snapshot.candles:
        # 約定がない場合は最新足を1フレームだけ出す。
        events = [max(snapshot.candles, key=lambda c: c.timestamp)]  # type: ignore[list-item]

    frames: list[AnimationFrame] = []
    base = clock()
    for index, event in enumerate(events[:MAX_ANIMATION_FRAMES]):
        if isinstance(event, Execution):
            signal = next(
                (s for s in signals if s.execution_id == event.execution_id), None
            )
            price = event.price
            commentary = (
                f"{_format_time(event.timestamp)} 約定 {event.size:.2f}@"
                f"{_format_price(event.price)}"
            )
        elif isinstance(event, JudgmentSignal):
            signal = event
            price = event.price
            commentary = f"{_format_time(event.timestamp)} シグナル {event.marker}"
        else:
            # 最後の足だけを代表フレームとして使用。
            candle = event
            signal = None
            price = candle.close
            commentary = f"{_format_time(candle.timestamp)} 足更新 {_format_price(candle.close)}"
        frames.append(
            AnimationFrame(
                frame_index=index,
                scheduled_at=base + index * interval,
                symbol=snapshot.symbol,
                price=price,
                signal=signal,
                commentary=_clean_text(commentary, MAX_AUDIO_LINE_CHARS),
                candles_count=len(snapshot.candles),
            )
        )
    return tuple(frames)

# ---------------------------------------------------------------------------
# アニメーション再生（フレームレート制御）
# ---------------------------------------------------------------------------


class AnimationPlayer:
    """フレームレートを制御してアニメーションフレームを再生する。

    ``clock``/``sleep`` は注入可能。テストでは実時間を待たず、仮想クロック
    で再生スケジュールを検証する。1フレームごとのコールバック
    ``on_frame(frame)`` は呼び出し側で描画／音声出力を行う。
    """

    def __init__(
        self,
        *,
        fps: float = DEFAULT_FPS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.fps = max(MIN_FPS, min(MAX_FPS, float(fps or DEFAULT_FPS)))
        self._clock = clock
        self._sleep = sleep
        self.played: list[AnimationFrame] = []

    def play(
        self,
        frames: Iterable[AnimationFrame],
        on_frame: Callable[[AnimationFrame], None],
        *,
        deadline: float | None = None,
    ) -> int:
        """フレームを順に再生し、再生したフレーム数を返す。

        先頭の ``scheduled_at`` を再生開始時刻に対応させ、相対間隔と fps を守り、
        指定の ``deadline`` を超えたら打ち切る（負荷・同期抑制）。
        """
        count = 0
        start = self._clock()
        first_scheduled = None
        last_drawn = None
        for frame in frames:
            now = self._clock()
            if first_scheduled is None:
                first_scheduled = frame.scheduled_at
            target = start + (frame.scheduled_at - first_scheduled)
            if last_drawn is not None:
                target = max(target, last_drawn + 1.0 / self.fps)
            if deadline is not None and max(now, target) >= deadline:
                break
            wait = target - now
            if wait > 0:
                self._sleep(wait)
            last_drawn = self._clock()
            if deadline is not None and last_drawn >= deadline:
                break
            on_frame(frame)
            self.played.append(frame)
            count += 1
        return count


# ---------------------------------------------------------------------------
# コーナーマネージャ（生成物の永続化までを一括処理）
# ---------------------------------------------------------------------------


class ChartCornerManager:
    """チャート解説コーナーの1実行を管理する。

    ``g`` は ``state_dir`` 属性を持つオブジェクト（docich のグローバル設定
    またはテスト用スタブ）を想定する。GameSwitch とのスロット連携は
    行わず、生成コンテンツを状態ファイルに保存するだけに留める。
    """

    def __init__(
        self,
        g,
        *,
        fetcher: ChartFetcher | None = None,
        symbol: str = DEFAULT_SYMBOL,
        fps: float = DEFAULT_FPS,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.g = g
        self._fetcher = fetcher or FixedChartFetcher(chart_fixture(symbol=symbol))
        self._symbol = symbol
        self._fps = fps
        self._clock = clock
        self._sleep = sleep

    def _state_path(self) -> Path:
        state_dir = getattr(self.g, "state_dir", None)
        if state_dir is None:
            state_dir = Path.cwd()
        return Path(state_dir) / STATE_FILENAME

    def run(self, request_id: str) -> CornerContent:
        """1回の解説実行を行い、生成物を状態ファイルに保存する。"""
        snapshot = self._fetcher.fetch(self._symbol)
        signals = compute_signals(snapshot)
        audio = build_audio_script(snapshot, signals)
        commentary = build_live_commentary(snapshot)
        frames = build_animation_frames(snapshot, signals, fps=self._fps, clock=self._clock)
        content = CornerContent(
            symbol=snapshot.symbol,
            generated_at=self._clock(),
            audio=audio,
            commentary=commentary,
            signals=signals,
            animation_frames=frames,
        )
        self._save(content, request_id)
        return content

    def _save(self, content: CornerContent, request_id: str) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id,
            "symbol": content.symbol,
            "generated_at": content.generated_at,
            "audio": {
                "title": content.audio.title,
                "lines": list(content.audio.lines),
                "estimated_seconds": content.audio.estimated_seconds,
            },
            "commentary": [
                {
                    "timestamp": item.timestamp,
                    "event_type": item.event_type.value,
                    "text": item.text,
                    "order_id": item.order_id,
                    "execution_id": item.execution_id,
                }
                for item in content.commentary
            ],
            "signals": [
                {
                    "timestamp": s.timestamp,
                    "price": s.price,
                    "side": s.side.value,
                    "strength": s.strength.value,
                    "reason": s.reason,
                    "marker": s.marker,
                    "execution_id": s.execution_id,
                }
                for s in content.signals
            ],
            "animation_frames": [
                {
                    "frame_index": f.frame_index,
                    "scheduled_at": f.scheduled_at,
                    "symbol": f.symbol,
                    "price": f.price,
                    "commentary": f.commentary,
                    "candles_count": f.candles_count,
                    "signal": (
                        {
                            "timestamp": f.signal.timestamp,
                            "price": f.signal.price,
                            "side": f.signal.side.value,
                            "marker": f.signal.marker,
                        }
                        if f.signal is not None
                        else None
                    ),
                }
                for f in content.animation_frames
            ],
        }
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload)

# ---------------------------------------------------------------------------
# テスト用固定フィクスチャ
# ---------------------------------------------------------------------------


def chart_fixture(*, symbol: str = DEFAULT_SYMBOL) -> ChartSnapshot:
    """固定のチャートフィクスチャ。テストとデモ用。

    外部APIに依存しない決定論的データ。約定とシグナルの相関が取れる
    よう、価格を段階的に変動させている。
    """
    base = 100.0
    now = 1_700_000_000.0
    candles: list[ChartCandle] = []
    price = base
    for i in range(40):
        step = 0.3 if i < 15 else -0.25
        price = round(price + step, 2)
        open_price = round(price - step, 2)
        high = round(max(open_price, price) + 0.1, 2)
        low = round(min(open_price, price) - 0.1, 2)
        candles.append(
            ChartCandle(
                timestamp=now + i * 60,
                open=open_price,
                high=high,
                low=low,
                close=price,
                volume=100.0 + i * 5,
            )
        )

    orders: list[Order] = []
    executions: list[Execution] = []
    exec_index = 0
    for i in range(0, 40, 5):
        order_price = round(candles[i].close, 2)
        side = "buy" if i % 10 == 0 else "sell"
        orders.append(
            Order(
                order_id=f"ord-{i}",
                timestamp=candles[i].timestamp,
                side=side,
                price=order_price,
                size=1.0,
                status="filled",
            )
        )
        executions.append(
            Execution(
                execution_id=f"exec-{exec_index}",
                order_id=f"ord-{i}",
                timestamp=candles[i].timestamp + 2,
                price=order_price,
                size=1.0,
                side=side,
            )
        )
        exec_index += 1

    return ChartSnapshot(
        symbol=symbol,
        fetched_at=now,
        candles=tuple(candles),
        orders=tuple(orders),
        executions=tuple(executions),
        source="fixture",
        note="fixed fixture for chart_corner_659 unit tests",
    )


__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_SYMBOL",
    "DEFAULT_FPS",
    "STATE_FILENAME",
    "ChartError",
    "ChartRateLimitError",
    "ChartFetchError",
    "SignalSide",
    "SignalStrength",
    "EventType",
    "ChartCandle",
    "Order",
    "Execution",
    "ChartSnapshot",
    "JudgmentSignal",
    "CommentaryItem",
    "AnimationFrame",
    "AudioScript",
    "CornerContent",
    "ChartFetcher",
    "FixedChartFetcher",
    "ApiChartFetcher",
    "compute_signals",
    "build_audio_script",
    "build_live_commentary",
    "build_animation_frames",
    "AnimationPlayer",
    "ChartCornerManager",
    "chart_fixture",
]
