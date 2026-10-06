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


def build_bundle(cache_dir: Path, *, now=None, day="auto", getter=download, clock=time.time,
                 min_remaining_s=0) -> dict:
    """Fetch a national bundle with enough remaining cache life for its caller."""
    if (type(min_remaining_s) not in (int, float)
            or not math.isfinite(min_remaining_s)
            or not 0 <= min_remaining_s < CACHE_TTL):
        raise WeatherError("invalid-minimum-freshness")
    min_remaining_s = float(min_remaining_s)
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
            fetched_at = epoch(candidate.get("fetched_at")) if isinstance(candidate, dict) else None
            if (isinstance(candidate, dict) and candidate.get("office") == city.office
                    and candidate.get("schema_version") == 1
                    and fetched_at is not None
                    and 0 <= started - fetched_at < CACHE_TTL
                    and fetched_at + CACHE_TTL - started >= min_remaining_s):
                # Do not trust cache timestamps alone: validate the actual forecast.
                # A selected corner may require several minutes of remaining life;
                # near-expiry cache entries are refreshed instead of causing a
                # broadcast that starts successfully and then disappears mid-read.
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
    # Re-check after network latency: source publication/cache must still be valid
    # and must still have the caller's requested runway.
    checked_at = started if now is not None else epoch(clock())
    view = project(bundle, now=checked_at)
    if view["expires_at"] - checked_at < min_remaining_s:
        raise WeatherError("insufficient-forecast-freshness")
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


_RAIN_PERIOD_LABELS = {
    (0, 6): "未明",
    (6, 12): "午前",
    (12, 18): "午後",
    (18, 24): "夜",
    (0, 12): "未明から午前",
    (6, 18): "朝から夕方",
    (12, 24): "午後以降",
    (0, 18): "未明から夕方",
    (6, 24): "朝以降",
    (0, 24): "全時間帯",
}


def _spoken_weather(value: str) -> str:
    """Make JMA's space-delimited weather phrase easier to hear without adding facts."""
    spoken = re.sub(r"[ \u3000]+", " ", value).strip()
    for label in ("夜遅く", "昼過ぎ", "明け方", "未明", "夕方", "昼前", "朝", "夜"):
        spoken = re.sub(rf" {label} から ", f"、{label}からは", spoken)
        spoken = re.sub(rf" {label} まで ", f"、{label}までは", spoken)
        spoken = re.sub(rf" {label} ", f"、{label}は", spoken)
    for connector in ("のち", "時々", "一時", "から", "まで"):
        spoken = spoken.replace(f" {connector} ", connector)
    return spoken.replace(" ", "")


def _rain_period_label(start: int, end: int) -> str:
    return _RAIN_PERIOD_LABELS.get((start, end), f"{start}時から{end}時")


def _rain_period_phrase(start: int, end: int, *, destination=False) -> str:
    label = _rain_period_label(start, end)
    if label == "全時間帯":
        return "全時間帯で"
    if label.endswith("以降"):
        return f"{label}は"
    if "から" in label:
        return f"{label}にかけては" if destination else f"{label}にかけて"
    return f"{label}には" if destination else f"{label}は"


def _rain_summary(pops: list[dict]) -> str:
    """Compress precipitation probabilities into stable trends instead of a four-number list."""
    rows = [
        (period["start"], period["end"], period["percent"])
        for period in pops
        if period["percent"] is not None
    ]
    if not rows:
        return ""

    runs: list[list[int]] = []
    for start, end, percent in rows:
        if runs and runs[-1][1] == start and runs[-1][2] == percent:
            runs[-1][1] = end
        else:
            runs.append([start, end, percent])

    all_periods = [(start, end) for start, end, _ in rows] == [
        (0, 6), (6, 12), (12, 18), (18, 24)
    ]
    if len(runs) == 1:
        start, end, percent = runs[0]
        if all_periods:
            return f"降水確率は全時間帯で{percent}パーセントです。"
        return f"降水確率は、{_rain_period_phrase(start, end)}{percent}パーセントです。"

    values = [run[2] for run in runs]
    if len(runs) == 2:
        first, second = runs
        if second[2] < first[2]:
            return (
                f"降水確率は、{_rain_period_phrase(first[0], first[1])}{first[2]}パーセントですが、"
                f"{_rain_period_phrase(second[0], second[1])}{second[2]}パーセントです。"
            )
        if second[2] > first[2]:
            return (
                f"降水確率は、{_rain_period_phrase(first[0], first[1])}{first[2]}パーセントで、"
                f"{_rain_period_phrase(second[0], second[1], destination=True)}"
                f"{second[2]}パーセントまで上がります。"
            )
        return f"降水確率は確認できる時間帯では{first[2]}パーセントです。"

    increasing = all(a <= b for a, b in zip(values, values[1:]))
    decreasing = all(a >= b for a, b in zip(values, values[1:]))
    first, last = runs[0], runs[-1]
    if increasing and values[0] != values[-1]:
        return (
            f"降水確率は、{_rain_period_phrase(first[0], first[1])}{first[2]}パーセントで、"
            f"{_rain_period_phrase(last[0], last[1], destination=True)}"
            f"{last[2]}パーセントまで上がります。"
        )
    if decreasing and values[0] != values[-1]:
        return (
            f"降水確率は、{_rain_period_phrase(first[0], first[1])}{first[2]}パーセントで、"
            f"{_rain_period_phrase(last[0], last[1], destination=True)}"
            f"{last[2]}パーセントまで下がります。"
        )

    low, high = min(values), max(values)
    return f"降水確率は{low}から{high}パーセントの範囲で変動します。"


def narration(view: dict) -> list[str]:
    """Deterministic spoken forecast summary. No model, advice or invented conditions."""
    day = date.fromisoformat(view["date"])
    lines = [
        f"{day.month}月{day.day}日の全国の天気です。"
        "札幌から那覇まで、代表11地点を順にお伝えします。"
    ]
    for item in view["cities"]:
        line = f'{item["city"]}です。{_spoken_weather(item["weather"])}の予報です。'
        high, low = item["high_c"], item["low_c"]
        if high is not None and low is not None:
            line += f"気温は最高{high}度、最低{low}度です。"
        elif high is not None:
            line += f"最高気温は{high}度です。"
        elif low is not None:
            line += f"最低気温は{low}度です。"
        line += _rain_summary(item["pops"])
        lines.append(line)
    lines.append(
        "以上、全国11地点の天気でした。"
        "気象庁の予報をもとにdocichが編集してお伝えしました。"
    )
    return lines
