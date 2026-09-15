"""Read-only market data adapters. No order URL/method is exposed here."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

from .core import JST, Quote, decimal, fx_week_open, stamp


class FeedUnavailable(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an API token to a redirect target.
        raise FeedUnavailable("market-data redirect refused")


def _read(url: str, headers=None) -> bytes:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=5) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise FeedUnavailable("market-data response exceeds limit")
            return raw
    except Exception as exc:
        # Do not include account IDs, request URLs, response bodies or tokens.
        raise FeedUnavailable(f"market data unavailable ({type(exc).__name__})") from None


def _iso(value: str) -> float:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone missing")
    return stamp(parsed.timestamp())


def jpx_open(calendar_path: Path, now: float) -> bool:
    """Explicit exchange sessions, including holidays. Missing coverage fails closed."""
    try:
        raw = json.loads(calendar_path.read_text())
        day = dt.datetime.fromtimestamp(now, JST)
        date = day.date().isoformat()
        if not raw["valid_from"] <= date <= raw["valid_through"]:
            return False
        if date not in raw["sessions"] or day.weekday() >= 5:
            return False
        minute = day.hour * 60 + day.minute
        return 540 <= minute < 690 or 750 <= minute < 930
    except (OSError, ValueError, KeyError, TypeError):
        return False


def read_quotes(config: dict, market: str, root: Path, now: float) -> list[Quote]:
    symbols = config.get("symbols", [])
    if not isinstance(symbols, list) or not symbols or len(symbols) > 20:
        raise FeedUnavailable("configure 1..20 symbols")
    if any(not isinstance(s, str) or not re.fullmatch(r"[A-Z0-9_]{3,20}", s) for s in symbols):
        raise FeedUnavailable("invalid configured symbol")
    source = config.get("feed", "file")
    if source == "file":
        path = root / config.get("quote_file", f"market-{market}-quotes.json")
        if path.stat().st_size > 1024 * 1024:
            raise FeedUnavailable("quote input exceeds limit")
        raw = json.loads(path.read_text())
        if raw.get("market") != market or raw.get("realtime") is not True:
            raise FeedUnavailable("wrong market or delayed/synthetic input")
        quotes = [Quote(**q) for q in raw["quotes"] if q.get("symbol") in symbols]
    elif source == "oanda" and market == "fx":
        if any(not re.fullmatch(r"[A-Z]{3}_JPY", s) for s in symbols):
            raise FeedUnavailable("non-JPY crosses need a conversion adapter")
        account = os.environ.get("DOCICH_OANDA_ACCOUNT_ID", "")
        token = os.environ.get("DOCICH_OANDA_TOKEN", "")
        if not re.fullmatch(r"[0-9-]{5,40}", account) or not token:
            raise FeedUnavailable("OANDA pricing credentials not configured")
        host = "https://api-fxpractice.oanda.com"  # practice-only, prices only
        url = f"{host}/v3/accounts/{account}/pricing?" + urllib.parse.urlencode({"instruments": ",".join(symbols)})
        payload = json.loads(_read(url, {"Authorization": f"Bearer {token}"}))
        quotes = []
        for row in payload["prices"]:
            if row["instrument"] not in symbols or not row.get("bids") or not row.get("asks"):
                continue
            bid, ask = row["bids"][0], row["asks"][0]
            quotes.append(Quote(row["instrument"], _iso(row["time"]), str(bid["price"]), str(ask["price"]),
                                str(bid["liquidity"]), str(ask["liquidity"]),
                                row.get("tradeable") is True and row.get("status", "tradeable") == "tradeable",
                                "oanda-practice-pricing"))
    elif source == "kabu" and market == "stocks":
        # Run this on the authorised Windows host, or through an explicitly
        # configured HTTPS proxy. Authentication/token acquisition is external.
        base = os.environ.get("DOCICH_KABU_DATA_URL", "http://127.0.0.1:18080/kabusapi").rstrip("/")
        parsed = urllib.parse.urlsplit(base)
        if (parsed.username or parsed.password or parsed.query or parsed.fragment
                or not (parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")))):
            raise FeedUnavailable("kabu requires loopback or HTTPS")
        token = os.environ.get("DOCICH_KABU_TOKEN", "")
        if not token:
            raise FeedUnavailable("kabu data token not configured")
        quotes = []
        for symbol in symbols:
            if not re.fullmatch(r"[0-9A-Z]{4}", symbol):
                raise FeedUnavailable("invalid TSE symbol")
            row = json.loads(_read(f"{base}/board/{symbol}@1", {"X-API-KEY": token}))
            # kabu's BidPrice is SELL-side and AskPrice is BUY-side, opposite
            # conventional FX bid/ask naming. Use the documented meanings.
            ts = min(_iso(row["BidTime"]), _iso(row["AskTime"]), _iso(row["CurrentPriceTime"]))
            quotes.append(Quote(symbol, ts, str(row["AskPrice"]), str(row["BidPrice"]),
                                str(row["AskQty"]), str(row["BidQty"]),
                                row.get("AskSign") == "0101" and row.get("BidSign") == "0101", "kabu-board"))
    else:
        raise FeedUnavailable("unsupported market/feed")
    if market == "stocks" and not jpx_open(root / config.get("calendar_file", "jpx-calendar.json"), now):
        return []
    if market == "fx" and not fx_week_open(now):
        return []
    return quotes


def read_news(urls: list[str], now: float) -> list[dict]:
    """Bounded RSS headlines, with publication/observation time and provenance.

    Headlines are untrusted data, not instructions; no fetched scripts or
    article bodies are executed. A failed feed is reported, not invented.
    """
    result = []
    for url in urls[:5]:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.username or parts.password:
            continue
        try:
            root = ET.fromstring(_read(url))
            for item in root.findall(".//item")[:30]:
                title, link, date = (item.findtext(k, "") for k in ("title", "link", "pubDate"))
                published = parsedate_to_datetime(date)
                if published.tzinfo is None:
                    continue
                ts = published.timestamp()
                if not 0 <= now - ts <= 72 * 3600 or not title.strip():
                    continue
                result.append({"title": title.strip()[:240], "url": link[:1000],
                               "published_at": ts, "observed_at": now, "source": parts.hostname})
        except (FeedUnavailable, ET.ParseError, ValueError, TypeError, OverflowError):
            continue
    return result[:40]
