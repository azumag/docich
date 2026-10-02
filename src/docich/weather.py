"""Relay JMA's published forecasts; never generate or interpolate a forecast.

The JSON resources are used by the JMA website, NOT a supported public API.
Only the fixed JMA host/office allowlist is contacted. No provider fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as daytime, timedelta
import json
import math
from pathlib import Path
import re
import tempfile
import os
import time
import urllib.request
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
SOURCE = "https://www.jma.go.jp/bosai/forecast/"
TERMS = "https://www.jma.go.jp/jma/kishou/info/coment.html"
ATTRIBUTION = "出典：気象庁ホームページ／気象庁の発表をもとにdocichが編集"
CACHE_TTL = 15 * 60
ISSUE_TTL = 18 * 3600
MAX_BYTES = 256 * 1024
MAX_BUNDLE_BYTES = 4 * 1024 * 1024


class WeatherError(ValueError):
    """A forecast cannot be safely presented. Messages are fixed reason codes."""


@dataclass(frozen=True)
class City:
    name: str
    office: str
    area: str
    station: str


CITIES = (
    City("札幌", "016000", "016010", "札幌"),
    City("仙台", "040000", "040010", "仙台"),
    City("東京", "130000", "130010", "東京"),
    City("新潟", "150000", "150010", "新潟"),
    City("名古屋", "230000", "230010", "名古屋"),
    City("大阪", "270000", "270000", "大阪"),
    City("広島", "340000", "340010", "広島"),
    City("高松", "370000", "370000", "高松"),
    City("福岡", "400000", "400010", "福岡"),
    City("鹿児島", "460100", "460010", "鹿児島"),
    City("那覇", "471000", "471010", "那覇"),
)
OFFICES = frozenset(city.office for city in CITIES)


def epoch(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise WeatherError("invalid-clock")
    return float(value)


def stamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise WeatherError("invalid-timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise WeatherError("invalid-timestamp") from exc
    if result.tzinfo is None:
        raise WeatherError("missing-timezone")
    return result.astimezone(JST)


def text(value: object) -> str:
    # No markup/control characters can pass into TTS or the presentation.
    if (not isinstance(value, str) or not value.strip() or len(value) > 240
            or any(ord(c) < 32 or c in "<>" for c in value)):
        raise WeatherError("invalid-forecast-text")
    return " ".join(value.split())


def number(value: object, low: int, high: int) -> int | None:
    if value == "":
        return None
    if not isinstance(value, str) or re.fullmatch(r"-?[0-9]{1,3}", value) is None:
        raise WeatherError("invalid-forecast-number")
    result = int(value)
    if not low <= result <= high:
        raise WeatherError("forecast-number-out-of-range")
    return result


def decode(raw: bytes, *, limit: int = MAX_BYTES) -> object:
    if len(raw) > limit:
        raise WeatherError("response-too-large")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise WeatherError("duplicate-json-key")
            result[key] = value
        return result

    def nonfinite(_):
        raise WeatherError("nonfinite-json")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise WeatherError("invalid-json") from exc


def write_json(path: Path, value: object) -> None:
    """Atomic local artifact/cache write; never writes to a stream queue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".weather-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def url_for(office: str) -> str:
    if office not in OFFICES:
        raise WeatherError("unknown-office")
    return f"https://www.jma.go.jp/bosai/forecast/data/forecast/{office}.json"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise WeatherError("unexpected-redirect")


def download(office: str, timeout: float) -> object:
    url = url_for(office)
    request = urllib.request.Request(url, headers={
        "User-Agent": "docich-weather/1.0 (JMA forecast relay)",
        "Accept": "application/json", "Accept-Encoding": "identity",
    })
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            if response.status != 200 or response.geturl() != url:
                raise WeatherError("unexpected-response")
            return decode(response.read(MAX_BYTES + 1))
    except WeatherError:
        raise
    except (OSError, ValueError) as exc:
        raise WeatherError("fetch-failed") from exc


def _series(report: dict, field: str, identity: str, *, by_name=False) -> list[tuple[datetime, object]]:
    """Match exact area/station; never use an arbitrary first area/array index."""
    found = []
    series = report.get("timeSeries")
    if not isinstance(series, list):
        raise WeatherError("missing-time-series")
    for item in series:
        if not isinstance(item, dict) or not isinstance(item.get("areas"), list):
            raise WeatherError("invalid-time-series")
        for area in item["areas"]:
            if not isinstance(area, dict) or not isinstance(area.get("area"), dict):
                raise WeatherError("invalid-area")
            if field not in area or area["area"].get("name" if by_name else "code") != identity:
                continue
            values, times = area[field], item.get("timeDefines")
            if (not isinstance(values, list) or not isinstance(times, list)
                    or len(values) != len(times) or not times):
                raise WeatherError("unaligned-time-series")
            points = [(stamp(t), v) for t, v in zip(times, values)]
            if len({t for t, _ in points}) != len(points):
                raise WeatherError("duplicate-time-axis")
            found.append(points)
    if len(found) != 1:
        raise WeatherError("missing-or-ambiguous-area-series")
    return found[0]


def normalize(payload: object, city: City, target: date, now: float) -> dict:
    now = epoch(now)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise WeatherError("invalid-short-range-report")
    # The short-range product is separate from the weekly report and its issue.
    report = payload[0]
    issued = stamp(report.get("reportDatetime"))
    if not 0 <= now - issued.timestamp() <= ISSUE_TTL:
        raise WeatherError("stale-or-future-issue")
    if not issued.date() <= target <= issued.date() + timedelta(days=2):
        raise WeatherError("target-outside-short-range")
    weather = [(t, value) for t, value in _series(report, "weathers", city.area) if t.date() == target]
    if len(weather) != 1:
        raise WeatherError("missing-or-ambiguous-target-day")
    pops = {}
    for when, value in _series(report, "pops", city.area):
        if when.date() != target:
            continue
        if when.hour not in (0, 6, 12, 18) or when.minute or when.second or when.microsecond:
            raise WeatherError("unexpected-precipitation-period")
        pops[when.hour] = number(value, 0, 100)
    if not pops:
        raise WeatherError("missing-precipitation-periods")
    temperatures = _series(report, "temps", city.station, by_name=True)
    high = low = None
    for when, value in temperatures:
        if when.date() != target:
            continue
        if when.hour not in (0, 9) or when.minute or when.second or when.microsecond:
            raise WeatherError("unexpected-temperature-period")
        parsed = number(value, -60, 60)
        if when.hour == 9:
            high = parsed
        elif target > issued.date():
            # JMA's SAME-DAY 00:00 entry repeats the day's maximum, not minimum.
            # Never invent a daily minimum from it or from a weekly product.
            low = parsed
    if high is not None and low is not None and low > high:
        raise WeatherError("inconsistent-temperature-range")
    return {
        "city": city.name, "area_code": city.area, "station": city.station,
        "office": city.office, "date": target.isoformat(),
        "issued_at": issued.isoformat(), "weather": text(weather[0][1]),
        "high_c": high, "low_c": low,
        "pops": [{"start": h, "end": h + 6, "percent": pops.get(h)} for h in (0, 6, 12, 18)],
        "source_url": f"{SOURCE}#area_type=offices&area_code={city.office}",
    }


def build_bundle(cache_dir: Path, *, now=None, day="auto", getter=download, clock=time.time) -> dict:
    """Fetch at most once/office/15min; a failed refresh NEVER replays stale data."""
    started = epoch(clock() if now is None else now)
    local = datetime.fromtimestamp(started, JST)
    if day not in {"auto", "today", "tomorrow"}:
        raise WeatherError("invalid-target-day")
    target = local.date() + timedelta(days=int(day == "tomorrow" or (day == "auto" and local.hour >= 17)))
    records = {}
    deadline = time.monotonic() + 45
    for city in CITIES:
        cached = None
        path = cache_dir / f"{city.office}.json"
        try:
            with path.open("rb") as handle:
                candidate = decode(handle.read(MAX_BYTES + 4097), limit=MAX_BYTES + 4096)
            if (isinstance(candidate, dict) and candidate.get("office") == city.office
                    and candidate.get("schema_version") == 1
                    and 0 <= started - epoch(candidate.get("fetched_at")) < CACHE_TTL):
                # Do not trust cache timestamps alone: validate the actual forecast.
                normalize(candidate["payload"], city, target, started)
                cached = candidate
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        if cached is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WeatherError("fetch-budget-exhausted")
            payload = getter(city.office, min(4.0, remaining))
            normalize(payload, city, target, started)
            cached = {"schema_version": 1, "office": city.office, "fetched_at": started, "payload": payload}
            write_json(path, cached)
        records[city.office] = cached
    bundle = {"schema_version": 1, "generated_at": started, "target_date": target.isoformat(), "records": records}
    # Re-check after network latency: source publication/cache must still be valid.
    project(bundle, now=started if now is not None else clock())
    return bundle


def project(bundle: object, *, now: float) -> dict:
    """Revalidate the raw snapshot every time it is served or spoken."""
    now = epoch(now)
    try:
        if not isinstance(bundle, dict) or bundle.get("schema_version") != 1:
            raise WeatherError("invalid-bundle")
        generated = epoch(bundle["generated_at"])
        if not 0 <= now - generated < CACHE_TTL:
            raise WeatherError("expired-bundle")
        target = date.fromisoformat(bundle["target_date"])
        today = datetime.fromtimestamp(now, JST).date()
        if target not in (today, today + timedelta(days=1)):
            raise WeatherError("expired-target-day")
        records = bundle["records"]
        if not isinstance(records, dict) or set(records) != OFFICES:
            raise WeatherError("incomplete-national-report")
        end = datetime.combine(target + timedelta(days=1), daytime(), JST).timestamp()
        expiry = min(generated + CACHE_TTL, end)
        locations = []
        for city in CITIES:
            record = records[city.office]
            if record.get("office") != city.office or record.get("schema_version") != 1:
                raise WeatherError("cache-identity-mismatch")
            fetched = epoch(record["fetched_at"])
            if not 0 <= now - fetched < CACHE_TTL:
                raise WeatherError("expired-cache")
            item = normalize(record["payload"], city, target, now)
            expiry = min(expiry, fetched + CACHE_TTL, stamp(item["issued_at"]).timestamp() + ISSUE_TTL)
            locations.append(item)
        if now >= expiry:
            raise WeatherError("expired-report")
        return {"schema_version": 1, "date": target.isoformat(), "generated_at": generated,
                "expires_at": expiry, "server_now": now, "attribution": ATTRIBUTION,
                "source_url": SOURCE, "terms_url": TERMS, "cities": locations}
    except WeatherError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise WeatherError("invalid-bundle") from exc


def narration(view: dict) -> list[str]:
    """Literal, date-stamped reading. No model, advice, inferred rain timing or warnings."""
    day = date.fromisoformat(view["date"])
    lines = [f"気象庁発表の、{day.month}月{day.day}日の全国の天気です。代表11地点をお伝えします。"]
    for item in view["cities"]:
        issued = stamp(item["issued_at"])
        line = f'{item["city"]}。{item["weather"]}。'
        if item["high_c"] is not None:
            line += f'最高気温は{item["high_c"]}度。'
        if item["low_c"] is not None:
            line += f'最低気温は{item["low_c"]}度。'
        line += f'気象庁、{issued.day}日{issued.hour}時発表。'
        lines.append(line)
    lines.append("以上、気象庁の予報をもとにdocichが編集してお伝えしました。")
    return lines
