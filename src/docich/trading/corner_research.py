"""Bounded public research inputs for the PAPER crypto corner.

This module is deliberately read-only with respect to exchanges: it searches
public Google News RSS feeds and Wikipedia, selects one actually-held asset,
and persists a small allowlisted research record for narration and the later
strategy-improvement pass. No exchange credentials or order endpoints exist
here.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import html
import json
import os
from pathlib import Path
import random
import re
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, Mapping

RESEARCH_FILENAME = "paper_corner_research.json"
HISTORY_FILENAME = "paper_corner_research_history.json"
USER_AGENT = "docich-paper-corner-research/1.0"
MAX_NEWS_ITEMS = 6
MAX_ASSET_NEWS = 3
MAX_SUMMARY_CHARS = 600
MAX_BACKGROUND_CHARS = 1800
MAX_HISTORY_NEWS = 120
MAX_HISTORY_ASSETS = 12

ASSET_ANGLES = (
    ("origin_history", "誕生の経緯や歴史的なエピソード"),
    ("technology", "技術的な仕組みや設計上の特徴"),
    ("tokenomics", "供給量・発行・手数料などトークノミクス"),
    ("culture_community", "コミュニティ文化やネット上での意外な立ち位置"),
    ("real_world_use", "実利用・採用事例や何に使われているか"),
    ("controversy_risk", "過去の論争・失敗・リスクから学べる点"),
    ("market_structure", "取引・流動性・市場構造上の特徴"),
)

ASSET_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "XRP": "XRP", "BCH": "Bitcoin Cash",
    "LTC": "Litecoin", "MONA": "Monacoin", "XLM": "Stellar", "QTUM": "Qtum",
    "BAT": "Basic Attention Token", "LINK": "Chainlink", "ADA": "Cardano",
    "DOT": "Polkadot", "DOGE": "Dogecoin", "AVAX": "Avalanche", "MATIC": "Polygon",
    "POL": "Polygon", "ASTR": "Astar Network", "SOL": "Solana", "ARB": "Arbitrum",
    "OP": "Optimism", "SHIB": "Shiba Inu", "DAI": "Dai", "MKR": "Maker",
    "TRX": "TRON", "ATOM": "Cosmos", "NEAR": "NEAR Protocol", "APT": "Aptos",
    "SUI": "Sui", "TON": "Toncoin", "PEPE": "Pepe", "RNDR": "Render",
    "RENDER": "Render", "FIL": "Filecoin", "OAS": "Oasys", "AXS": "Axie Infinity",
    "SAND": "The Sandbox", "MANA": "Decentraland", "CHZ": "Chiliz", "APE": "ApeCoin",
    "GRT": "The Graph", "FLR": "Flare", "GALA": "Gala", "IMX": "Immutable",
}


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        tmp.unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _clean_text(value: object, limit: int) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _title_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower().strip()
    text = re.sub(r"[\s\u3000]+", "", text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] not in ("P", "S"))
    return text[:240]


def _jst_date(now: float) -> str:
    tz = dt.timezone(dt.timedelta(hours=9))
    return dt.datetime.fromtimestamp(float(now), tz=tz).date().isoformat()


def _http_get(url: str, timeout: float = 6.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def _rss_url(query: str, *, lang: str, gl: str, ceid: str) -> str:
    params = urllib.parse.urlencode({"q": query, "hl": lang, "gl": gl, "ceid": ceid})
    return f"https://news.google.com/rss/search?{params}"


def _parse_pubdate(value: str) -> float | None:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_rss(raw: str) -> list[dict[str, object]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    rows: list[dict[str, object]] = []
    for item in root.findall("./channel/item"):
        title = _clean_text(item.findtext("title", default=""), 300)
        if not title:
            continue
        rows.append({
            "title": title,
            "url": _clean_text(item.findtext("link", default=""), 700),
            "source": _clean_text(item.findtext("source", default=""), 120),
            "published_at": _parse_pubdate(item.findtext("pubDate", default="")),
            "summary": _clean_text(item.findtext("description", default=""), MAX_SUMMARY_CHARS),
        })
    return rows


def _merge_news(feeds: list[list[dict[str, object]]], seen: set[str], limit: int) -> list[dict[str, object]]:
    unique: list[dict[str, object]] = []
    fallback: list[dict[str, object]] = []
    current: set[str] = set()
    for rows in feeds:
        for row in rows:
            key = _title_key(row.get("title"))
            if not key or key in current:
                continue
            current.add(key)
            clean = dict(row)
            clean["key"] = key
            fallback.append(clean)
            if key not in seen:
                unique.append(clean)
    selected = unique[:limit]
    if len(selected) < limit:
        selected_keys = {str(item.get("key")) for item in selected}
        selected.extend(item for item in fallback if str(item.get("key")) not in selected_keys)
    return selected[:limit]


def _fetch_crypto_news(fetcher: Callable[[str], str], seen: set[str]) -> list[dict[str, object]]:
    queries = (
        _rss_url("暗号資産 OR 仮想通貨 OR ビットコイン OR イーサリアム when:2d", lang="ja", gl="JP", ceid="JP:ja"),
        _rss_url("cryptocurrency OR bitcoin OR ethereum when:2d", lang="en-US", gl="US", ceid="US:en"),
    )
    feeds: list[list[dict[str, object]]] = []
    for url in queries:
        try:
            feeds.append(_parse_rss(fetcher(url)))
        except Exception:
            continue
    return _merge_news(feeds, seen, MAX_NEWS_ITEMS)


def _base_code(symbol: str) -> str:
    text = str(symbol or "").upper().strip()
    for sep in ("/", "_", "-"):
        if sep in text:
            return text.split(sep, 1)[0]
    return text


def _asset_name(symbol: str) -> str:
    code = _base_code(symbol)
    return ASSET_NAMES.get(code, code)


def _held_symbols(trading_dir: Path) -> list[str]:
    status = _read_json(trading_dir / "status.json")
    raw = status.get("open_positions")
    if not isinstance(raw, Mapping):
        return []
    held: list[str] = []
    for symbol, amount in raw.items():
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if value.is_finite() and value > 0:
            held.append(str(symbol))
    return sorted(set(held))


def _choose(pool: list[str], chooser=None) -> str:
    if not pool:
        return ""
    if chooser is not None:
        return str(chooser(list(pool)))
    return random.SystemRandom().choice(pool)


def _select_asset(held: list[str], history: Mapping[str, object], chooser=None) -> tuple[str, str, str]:
    if not held:
        return "", "", ""
    recent = history.get("asset_symbols") if isinstance(history.get("asset_symbols"), list) else []
    recent_window = {str(item) for item in recent[-min(4, max(1, len(held))):]}
    candidates = [symbol for symbol in held if symbol not in recent_window] or held
    symbol = _choose(candidates, chooser)

    topic_history = history.get("asset_topics") if isinstance(history.get("asset_topics"), Mapping) else {}
    used = topic_history.get(symbol) if isinstance(topic_history.get(symbol), list) else []
    unused = [key for key, _label in ASSET_ANGLES if key not in {str(x) for x in used}]
    angle_key = _choose(unused or [key for key, _label in ASSET_ANGLES], chooser)
    angle_label = dict(ASSET_ANGLES).get(angle_key, dict(ASSET_ANGLES)[ASSET_ANGLES[0][0]])
    return symbol, angle_key, angle_label


def _wikipedia_background(name: str, fetcher: Callable[[str], str]) -> str:
    if not name:
        return ""
    params = urllib.parse.urlencode({
        "action": "query", "generator": "search", "gsrsearch": f"{name} 暗号資産",
        "gsrlimit": 1, "prop": "extracts", "exintro": 1, "explaintext": 1,
        "format": "json", "formatversion": 2,
    })
    try:
        raw = fetcher(f"https://ja.wikipedia.org/w/api.php?{params}")
        data = json.loads(raw)
        pages = data.get("query", {}).get("pages", []) if isinstance(data, dict) else []
        if isinstance(pages, list) and pages and isinstance(pages[0], Mapping):
            return _clean_text(pages[0].get("extract"), MAX_BACKGROUND_CHARS)
    except Exception:
        pass
    return ""


def _asset_news(name: str, fetcher: Callable[[str], str], seen: set[str]) -> list[dict[str, object]]:
    if not name:
        return []
    url = _rss_url(f'"{name}" 暗号資産 OR cryptocurrency when:30d', lang="ja", gl="JP", ceid="JP:ja")
    try:
        return _merge_news([_parse_rss(fetcher(url))], seen, MAX_ASSET_NEWS)
    except Exception:
        return []


def _history(trading_dir: Path) -> dict:
    data = _read_json(trading_dir / HISTORY_FILENAME)
    news_keys = data.get("news_keys") if isinstance(data.get("news_keys"), list) else []
    asset_symbols = data.get("asset_symbols") if isinstance(data.get("asset_symbols"), list) else []
    asset_topics = data.get("asset_topics") if isinstance(data.get("asset_topics"), Mapping) else {}
    return {
        "schema_version": 1,
        "news_keys": [str(x) for x in news_keys[-MAX_HISTORY_NEWS:]],
        "asset_symbols": [str(x) for x in asset_symbols[-MAX_HISTORY_ASSETS:]],
        "asset_topics": {
            str(key): [str(x) for x in value[-len(ASSET_ANGLES):]]
            for key, value in asset_topics.items() if isinstance(value, list)
        },
    }


def load_research_result(trading_dir) -> dict:
    """Return the bounded persisted research record, or {} when absent/corrupt."""
    data = _read_json(Path(trading_dir) / RESEARCH_FILENAME)
    if data.get("schema_version") != 1 or data.get("status") not in {"prepared", "finalized"}:
        return {}
    return data


def prepare_research_context(
    trading_dir,
    *,
    now: float | None = None,
    fetcher: Callable[[str], str] | None = None,
    chooser=None,
) -> dict:
    """Search public crypto news and select one held asset with deduped angle."""
    target = Path(trading_dir)
    moment = time.time() if now is None else float(now)
    date = _jst_date(moment)
    existing = load_research_result(target)
    if existing.get("date") == date:
        return existing

    fetch = fetcher or _http_get
    history = _history(target)
    seen = set(history["news_keys"])
    held = _held_symbols(target)
    symbol, angle, angle_label = _select_asset(held, history, chooser)
    name = _asset_name(symbol) if symbol else ""

    news_items = _fetch_crypto_news(fetch, seen)
    background = _wikipedia_background(name, fetch) if symbol else ""
    related = _asset_news(name, fetch, seen) if symbol else []
    payload = {
        "schema_version": 1,
        "status": "prepared",
        "date": date,
        "generated_at": moment,
        "news_items": news_items,
        "news_analysis": "",
        "asset": {
            "symbol": symbol,
            "name": name,
            "angle": angle,
            "angle_label": angle_label,
            "background": background,
            "news_items": related,
        } if symbol else {},
        "asset_spotlight": "",
        "improvement_hints": [],
    }
    _atomic_json(target / RESEARCH_FILENAME, payload)
    return payload


def _sanitize_hints(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    allowed_kind = {"parameter", "feature", "risk", "data"}
    allowed_confidence = {"low", "medium", "high"}
    for item in raw[:4]:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "feature").lower()
        confidence = str(item.get("confidence") or "medium").lower()
        title = _clean_text(item.get("title") or item.get("idea"), 140)
        rationale = _clean_text(item.get("rationale") or item.get("why"), 500)
        evidence = _clean_text(item.get("evidence"), 400)
        if not title:
            continue
        result.append({
            "kind": kind if kind in allowed_kind else "feature",
            "title": title,
            "rationale": rationale,
            "evidence": evidence,
            "confidence": confidence if confidence in allowed_confidence else "medium",
        })
    return result


def finalize_research_result(
    trading_dir,
    context: Mapping[str, object],
    model_data: Mapping[str, object],
    *,
    now: float | None = None,
) -> dict:
    """Persist AI analysis/hints and advance bounded dedupe history."""
    target = Path(trading_dir)
    moment = time.time() if now is None else float(now)
    payload = dict(context)
    payload.update({
        "schema_version": 1,
        "status": "finalized",
        "updated_at": moment,
        "news_analysis": _clean_text(model_data.get("news_analysis"), 1200),
        "asset_spotlight": _clean_text(model_data.get("asset_spotlight"), 1200),
        "improvement_hints": _sanitize_hints(model_data.get("improvement_hints")),
    })
    _atomic_json(target / RESEARCH_FILENAME, payload)

    history = _history(target)
    for item in payload.get("news_items", []):
        if isinstance(item, Mapping):
            key = _title_key(item.get("title"))
            if key:
                history["news_keys"].append(key)
    history["news_keys"] = history["news_keys"][-MAX_HISTORY_NEWS:]

    asset = payload.get("asset") if isinstance(payload.get("asset"), Mapping) else {}
    symbol = str(asset.get("symbol") or "")
    angle = str(asset.get("angle") or "")
    if symbol:
        history["asset_symbols"].append(symbol)
        history["asset_symbols"] = history["asset_symbols"][-MAX_HISTORY_ASSETS:]
        topics = history["asset_topics"].setdefault(symbol, [])
        if angle:
            topics.append(angle)
            history["asset_topics"][symbol] = topics[-len(ASSET_ANGLES):]
    _atomic_json(target / HISTORY_FILENAME, history)
    return payload
