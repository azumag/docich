"""TwiCa 同志カードランキングの取得と fail-closed 検証（docs/features/doushi-card-corner.md §6/PR-3）。

このモジュールはネットワークを持たない。HTTP は呼び出し元から注入された
``http_get`` だけを使い、URL 導出・JSON 検証・並び順の非改変をここで確定させる。

- endpoint は既存 ``SOREN_DIRECT_TWICA_OVERLAY_URL``（``/overlay/{streamerId}``）の
  origin から導出し、URL 本文・クエリ・トークンをログ・argv・state へ残さない。
- 返却 payload は design §6 の allowlist だけ。検証に失敗した場合は前回値を出さず
  ``DoushiRankingError`` で失敗させる（当該コーナーの開始 skip = fail-closed）。
- 並びは TwiCa 正本のまま。docich は再ソートしない・順位を付け直さない。
"""
from __future__ import annotations

import datetime as dt
import json
import unicodedata
from urllib.parse import urlsplit

#: 部門ごとの表示上限（design §4: 上位 10 件）。
DEFAULT_LIMIT = 10
#: 集計期間（design §7-1: 既定は全期間。将来 7d / 30d 窓を追加できる形）。
DEFAULT_PERIOD = "all"
ALLOWED_PERIODS = ("all", "7d", "30d")
MAX_LIMIT = 50

#: ``generated_at`` がこの秒数より古い取得結果は stale として受け付けない。
#: TwiCa 側は no-store で毎回生成するため、到達した値はほぼ現在時刻である。
STALE_SECONDS = 24 * 60 * 60
#: 表示名の上限。TwiCa 側の発行値が壊れていてもレイアウトを壊さない。
MAX_USERNAME_CHARS = 40
MAX_CARD_NAME_CHARS = 60
MAX_NOTE_CHARS = 400

METRIC_KEYS = ("by_count", "by_points", "by_unique", "popular_cards")
METRIC_LABEL_KEYS = ("by_count", "by_points", "by_unique", "popular_cards")

#: design §3 の 4 部門。値は (部門キー, 行に必須な整数フィールド)。
ROW_INTEGER_FIELDS = {
    "by_count": ("rank", "redemption_count"),
    "by_points": ("rank", "total_points", "redemption_count"),
    "by_unique": ("rank", "unique_count", "total_cards"),
    "popular_cards": ("rank", "actual_count", "drawer_count"),
}
ROW_TEXT_FIELDS = {
    "by_count": ("username",),
    "by_points": ("username",),
    "by_unique": ("username",),
    "popular_cards": ("card_name",),
}


class DoushiRankingError(RuntimeError):
    """A fixed, non-sensitive 同志カード ranking fetch/validation failure.

    メッセージは固定文言だけにする。URL・レスポンス本文・視聴者名を運ぶと
    ログ経由の持ち出しになってしまうため、 ``raise ... from exc`` の連鎖にも
    原文を残さない。
    """


def _fail(reason: str) -> "DoushiRankingError":
    # reason は本モジュール内で決めた固定語彙だけを渡す。
    return DoushiRankingError(f"doushi ranking unavailable ({reason})")


def derive_endpoint(overlay_url: str) -> str:
    """``SOREN_DIRECT_TWICA_OVERLAY_URL`` から公開ランキングの endpoint を導出する。

    戻り値は内部でしか使わない URL である。呼び出し元はこれをログ・argv・state に
    書いてはならない（``twica_renderer.validate_url`` と同じ契約）。
    """
    if not isinstance(overlay_url, str) or not overlay_url:
        raise _fail("config-missing")
    try:
        parsed = urlsplit(overlay_url)
        allowed = parsed.scheme == "https" or (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        )
        valid = (
            allowed
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
        )
        _ = parsed.port
    except (ValueError, TypeError):
        raise _fail("config-invalid") from None
    if not valid:
        raise _fail("config-invalid")

    parts = [segment for segment in parsed.path.split("/") if segment]
    if len(parts) != 2 or parts[0] != "overlay":
        raise _fail("config-invalid")
    streamer_id = _validate_streamer_id(parts[1])
    # query は含めない（既存 overlay URL のクエリは秘密情報を含み得るため破棄）。
    return f"{parsed.scheme}://{parsed.netloc}/api/overlay/{streamer_id}/card-ranking"


def _validate_streamer_id(value: str) -> str:
    """TwiCa 側 ``isValidStreamerId`` と同じ UUID 形式だけを通す。"""
    try:
        import uuid

        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise _fail("config-invalid") from None
    if str(parsed) != value:
        raise _fail("config-invalid")
    return value


def build_request(endpoint: str, *, period: str = DEFAULT_PERIOD,
                  limit: int = DEFAULT_LIMIT) -> str:
    if period not in ALLOWED_PERIODS:
        raise _fail("invalid-period")
    if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise _fail("invalid-limit")
    return f"{endpoint}?period={period}&limit={limit}"


def _require_int(row: dict, field: str) -> int:
    value = row.get(field)
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise _fail("schema")
    return value


def _clean_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise _fail("schema")
    # Control and format characters (bidi overrides, zero-width joiners, NUL)
    # come out before the bound: a display name may never repaint the frame.
    text = "".join(
        char for char in value
        if not unicodedata.category(char).startswith("C")
    ).strip()
    if not text:
        raise _fail("schema")
    return text[:limit]


def _require_text(row: dict, field: str, limit: int) -> str:
    return _clean_text(row.get(field), limit)


def _validate_rows(key: str, raw: object, *, limit: int) -> list[dict]:
    if not isinstance(raw, list):
        raise _fail("schema")
    if len(raw) > limit:
        # TwiCa 側は rn <= limit で切る。増えた時点で契約が変わったとみなし、
        # docich で勝手に切り詰めない。
        raise _fail("schema")
    rows: list[dict] = []
    seen_ranks: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise _fail("schema")
        row: dict = {}
        for field in ROW_INTEGER_FIELDS[key]:
            row[field] = _require_int(item, field)
        for field in ROW_TEXT_FIELDS[key]:
            row[field] = _require_text(
                item, field,
                MAX_CARD_NAME_CHARS if field == "card_name" else MAX_USERNAME_CHARS,
            )
        rank = row["rank"]
        # 順位は 1 起点・昇順・重複なし（TwiCa がソート済みである契約）。
        if rank != len(rows) + 1 or rank in seen_ranks:
            raise _fail("order")
        seen_ranks.add(rank)
        rows.append(row)
    return rows


def _validate_generated_at(value: object) -> str:
    if not isinstance(value, str):
        raise _fail("schema")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _fail("schema") from None
    if parsed.tzinfo is None:
        raise _fail("schema")
    # 過去すぎる値は stale として拒否する。未来側はクロック差で本番表示を
    # 落とさないため許容する（API は no-store で毎回生成される）。
    age = dt.datetime.now(dt.timezone.utc) - parsed.astimezone(dt.timezone.utc)
    if age.total_seconds() > STALE_SECONDS:
        raise _fail("stale")
    return value


def validate_payload(payload: object, *, period: str, limit: int) -> dict:
    """design §6 の allowlist に収まるかを検証して、そのまま返す。"""
    if period not in ALLOWED_PERIODS:
        raise _fail("invalid-period")
    if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise _fail("invalid-limit")
    if not isinstance(payload, dict):
        raise _fail("schema")

    if payload.get("period") != period:
        raise _fail("schema")
    generated_at = _validate_generated_at(payload.get("generated_at"))

    labels = payload.get("metric_labels")
    if not isinstance(labels, dict):
        raise _fail("schema")
    metric_labels = {}
    for key in METRIC_LABEL_KEYS:
        metric_labels[key] = _clean_text(labels.get(key), MAX_CARD_NAME_CHARS)

    note = _clean_text(payload.get("note"), MAX_NOTE_CHARS)

    card_type_total = payload.get("card_type_total")
    if type(card_type_total) is not int or isinstance(card_type_total, bool) or card_type_total < 0:
        raise _fail("schema")

    # allowlist のみを再構成して返す。RPC 側で増えた鍵は持ち越さない。
    validated = {
        "generated_at": generated_at,
        "period": period,
        "metric_labels": metric_labels,
        "note": note,
        "card_type_total": card_type_total,
    }
    for key in METRIC_KEYS:
        validated[key] = _validate_rows(key, payload.get(key), limit=limit)
    return validated


def fetch_ranking(endpoint: str, *, period: str = DEFAULT_PERIOD,
                  limit: int = DEFAULT_LIMIT, http_get, timeout: float = 10.0) -> dict:
    """注入された ``http_get(url, timeout) -> bytes`` で取得し、検証済み payload を返す。

    ``http_get`` は ``callable(str, float) -> bytes``。失敗・不正本文はすべて
    ``DoushiRankingError`` に落とす（どの reason かはログに残さず固定文言のみ）。
    """
    if not callable(http_get):
        raise _fail("config-invalid")
    if type(timeout) not in (int, float) or isinstance(timeout, bool) or timeout <= 0:
        raise _fail("config-invalid")
    url = build_request(endpoint, period=period, limit=limit)
    try:
        body = http_get(url, timeout)
    except Exception:
        raise _fail("fetch") from None
    if not isinstance(body, (bytes, bytearray)):
        raise _fail("fetch")
    try:
        payload = json.loads(bytes(body).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _fail("schema") from None
    return validate_payload(payload, period=period, limit=limit)
