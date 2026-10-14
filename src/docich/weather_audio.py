"""Pure contract for the weather-to-shared-audio queue adapter.

This module validates request and receipt values only. It does not publish to
the Soren comment queue, create a local spool, or start a player. A validated
``played`` receipt shape does not prove that a real player completed playback;
only the pinned shared consumer can provide that evidence.
"""
from __future__ import annotations

from datetime import date, datetime, time as daytime, timedelta
import hashlib
import json
import math
from typing import Mapping, Protocol

from .game_switch import validate_request_id
from .naming import NameValidationError, runtime_id_generation, validate_runtime_id
from .weather import (
    ATTRIBUTION, CACHE_TTL, CITIES, ISSUE_TTL, JST, SOURCE, TERMS, WeatherError,
    stamp, text as weather_text,
)

WEATHER_AUDIO_SOURCE = "weather_corner"
WEATHER_VIEW_NAME = "weather-view"
REQUEST_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
MAX_ITEM_TEXT_CHARS = 1000  # existing shared audio text contract limit
MAX_ITEM_INDEX = 12  # weather.narration currently emits 13 literal lines
RUNTIME_IDENTITY_KEYS = ("game", "runtime_id", "generation", "lease_id")
RUNTIME_FENCE_KEYS = frozenset((*RUNTIME_IDENTITY_KEYS, "expires_at"))
REQUEST_KEYS = frozenset({
    "schema_version", "source", "execution_id", "item_index", "item_key",
    "text", "runtime_fence", "forecast",
})
FORECAST_KEYS = frozenset({"source_url", "date", "issued_at", "report_digest"})
PROJECT_VIEW_KEYS = frozenset({
    "schema_version", "date", "generated_at", "expires_at", "server_now",
    "attribution", "source_url", "terms_url", "cities",
})
PROJECT_CITY_KEYS = frozenset({
    "city", "area_code", "station", "office", "date", "issued_at", "weather",
    "high_c", "low_c", "pops", "source_url",
})
RECEIPT_KEYS = frozenset({
    "schema_version", "source", "item_key", "request_digest", "status", "runtime_fence",
    "forecast", "recorded_at", "reason",
})
RECEIPT_STATUSES = frozenset({"queued", "played", "rejected", "interrupted"})
_REJECTION_REASONS = frozenset({
    "expired", "runtime_mismatch", "queue_rejected", "player_rejected",
})
_INTERRUPTION_REASONS = frozenset({
    "runtime_fence_lost", "worker_interrupted", "playback_interrupted",
})
RECEIPT_REASONS = _REJECTION_REASONS | _INTERRUPTION_REASONS


class WeatherAudioError(ValueError):
    """A weather audio request or consumer receipt violates the fixed contract."""


class SharedWeatherAudioPort(Protocol):
    """One-item operations supported by the existing shared consumer."""

    def enqueue_weather_audio(self, request: Mapping[str, object]) -> Mapping[str, object]:
        """Return a validated per-item receipt without content-based dedup."""

    def get_weather_audio_receipt(self, item_key: str) -> Mapping[str, object] | None:
        """Return that item's queued/terminal receipt, if it exists."""

    def interrupt_weather_audio(self, item_key: str) -> Mapping[str, object] | None:
        """Interrupt one item and return only after the consumer confirms quiescence."""


def item_idempotency_key(execution_id: str, item_index: int) -> str:
    """Stable per narration item; deliberately independent of spoken text."""
    if not isinstance(execution_id, str):
        raise WeatherAudioError("weather audio execution identity is invalid")
    try:
        execution_id = validate_request_id(execution_id)
    except NameValidationError as exc:
        raise WeatherAudioError("weather audio execution identity is invalid") from exc
    if type(item_index) is not int or not 0 <= item_index <= MAX_ITEM_INDEX:
        raise WeatherAudioError("weather audio item index is invalid")
    return f"{WEATHER_AUDIO_SOURCE}:{execution_id}:{item_index:02d}"


def _now(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise WeatherAudioError("weather audio clock is invalid")
    return float(value)


def _expiry(value: object, *, now: float | None) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise WeatherAudioError("weather audio expiry is invalid")
    expiry = float(value)
    if now is not None and not now < expiry <= now + CACHE_TTL:
        raise WeatherAudioError("weather audio item is expired or exceeds the forecast TTL")
    return expiry


def _runtime_identity(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(RUNTIME_IDENTITY_KEYS):
        raise WeatherAudioError("weather audio runtime identity is invalid")
    game = value.get("game")
    runtime_id = value.get("runtime_id")
    generation = value.get("generation")
    lease_id = value.get("lease_id")
    if game != WEATHER_VIEW_NAME:
        raise WeatherAudioError("weather audio source requires weather-view")
    if type(generation) is not int or generation < 1:
        raise WeatherAudioError("weather audio runtime generation is invalid")
    try:
        validate_runtime_id(runtime_id)
        if runtime_id_generation(runtime_id) != generation:
            raise WeatherAudioError("weather audio runtime generation does not match runtime_id")
        normalized_lease = validate_request_id(lease_id)
    except (NameValidationError, TypeError) as exc:
        raise WeatherAudioError("weather audio runtime identity is invalid") from exc
    if normalized_lease != lease_id:
        raise WeatherAudioError("weather audio lease identity is not canonical")
    return {
        "game": WEATHER_VIEW_NAME,
        "runtime_id": runtime_id,
        "generation": generation,
        "lease_id": lease_id,
    }


def runtime_identity_matches(current: object, expected_fence: object) -> bool:
    """Compare the complete runtime tuple; malformed observations fail closed."""
    if not isinstance(expected_fence, Mapping) or set(expected_fence) != RUNTIME_FENCE_KEYS:
        return False
    try:
        current_identity = _runtime_identity(current)
        expected_identity = _runtime_identity({key: expected_fence[key] for key in RUNTIME_IDENTITY_KEYS})
    except (WeatherAudioError, KeyError):
        return False
    return current_identity == expected_identity


def _runtime_fence(value: object, *, now: float | None) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != RUNTIME_FENCE_KEYS:
        raise WeatherAudioError("weather audio runtime fence is invalid")
    identity = _runtime_identity({key: value[key] for key in RUNTIME_IDENTITY_KEYS})
    expiry = _expiry(value.get("expires_at"), now=now)
    return {**identity, "expires_at": expiry}


def _forecast(value: object, *, expiry: float, now: float | None) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != FORECAST_KEYS:
        raise WeatherAudioError("weather audio forecast metadata is invalid")
    if value.get("source_url") != SOURCE:
        raise WeatherAudioError("weather audio source URL is invalid")
    report_digest = value.get("report_digest")
    if (not isinstance(report_digest, str)
            or len(report_digest) != 64
            or any(char not in "0123456789abcdef" for char in report_digest)):
        raise WeatherAudioError("weather audio report identity is invalid")
    target_date = value.get("date")
    if not isinstance(target_date, str):
        raise WeatherAudioError("weather audio forecast date is invalid")
    try:
        parsed_date = date.fromisoformat(target_date)
    except ValueError as exc:
        raise WeatherAudioError("weather audio forecast date is invalid") from exc
    if parsed_date.isoformat() != target_date:
        raise WeatherAudioError("weather audio forecast date is not canonical")
    issued_at = value.get("issued_at")
    try:
        issued = stamp(issued_at)
        issued_epoch = issued.timestamp()
    except (WeatherError, TypeError, OverflowError, OSError, ValueError) as exc:
        raise WeatherAudioError("weather audio issue time is invalid") from exc
    if issued.isoformat() != issued_at:
        raise WeatherAudioError("weather audio issue time is not canonical")
    if issued_epoch >= expiry or expiry > issued_epoch + ISSUE_TTL:
        raise WeatherAudioError("weather audio forecast issue time is outside the valid window")
    if now is not None:
        try:
            local_today = datetime.fromtimestamp(now, JST).date()
        except (OverflowError, OSError, ValueError) as exc:
            raise WeatherAudioError("weather audio clock is outside the valid window") from exc
        if (issued_epoch > now
                or now - issued_epoch > ISSUE_TTL
                or parsed_date not in (local_today, local_today + timedelta(days=1))
                or not issued.date() <= parsed_date <= issued.date() + timedelta(days=2)):
            raise WeatherAudioError("weather audio forecast issue or target is outside the valid window")
    return {
        "source_url": SOURCE,
        "date": target_date,
        "issued_at": issued_at,
        "report_digest": report_digest,
    }


def _validated_report_view(value: object, *, now: float) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Validate the projected 11-city JMA result before assigning it an identity."""
    if not isinstance(value, Mapping) or set(value) != PROJECT_VIEW_KEYS:
        raise WeatherAudioError("weather audio report view fields are invalid")
    if (type(value.get("schema_version")) is not int or value.get("schema_version") != 1
            or value.get("source_url") != SOURCE or value.get("terms_url") != TERMS
            or value.get("attribution") != ATTRIBUTION):
        raise WeatherAudioError("weather audio report view identity is invalid")
    target_text = value.get("date")
    if not isinstance(target_text, str):
        raise WeatherAudioError("weather audio report date is invalid")
    try:
        target = date.fromisoformat(target_text)
    except ValueError as exc:
        raise WeatherAudioError("weather audio report date is invalid") from exc
    if target.isoformat() != target_text:
        raise WeatherAudioError("weather audio report date is not canonical")
    today = datetime.fromtimestamp(now, JST).date()
    if target not in (today, today + timedelta(days=1)):
        raise WeatherAudioError("weather audio report date is outside the valid window")

    generated = _now(value.get("generated_at"))
    server_now = _now(value.get("server_now"))
    expiry = _expiry(value.get("expires_at"), now=now)
    local_end = datetime.combine(target + timedelta(days=1), daytime(), JST).timestamp()
    if (generated > server_now or server_now > now
            or now - generated >= CACHE_TTL
            or expiry > min(generated + CACHE_TTL, local_end)):
        raise WeatherAudioError("weather audio report view is stale or inconsistent")

    rows = value.get("cities")
    if not isinstance(rows, list) or len(rows) != len(CITIES):
        raise WeatherAudioError("weather audio report must contain all 11 cities")
    normalized: list[dict[str, object]] = []
    for row, city in zip(rows, CITIES):
        if not isinstance(row, Mapping) or set(row) != PROJECT_CITY_KEYS:
            raise WeatherAudioError("weather audio report city fields are invalid")
        source_url = f"{SOURCE}#area_type=offices&area_code={city.office}"
        if (row.get("city") != city.name or row.get("office") != city.office
                or row.get("area_code") != city.area or row.get("station") != city.station
                or row.get("date") != target_text or row.get("source_url") != source_url):
            raise WeatherAudioError("weather audio report city identity is invalid")
        try:
            issued = stamp(row.get("issued_at"))
            issued_epoch = issued.timestamp()
            clean_weather = weather_text(row.get("weather"))
        except (WeatherError, TypeError, OverflowError, OSError, ValueError) as exc:
            raise WeatherAudioError("weather audio report city data is invalid") from exc
        if (issued.isoformat() != row.get("issued_at")
                or issued_epoch > now or now - issued_epoch > ISSUE_TTL
                or not issued.date() <= target <= issued.date() + timedelta(days=2)
                or expiry > issued_epoch + ISSUE_TTL
                or clean_weather != row.get("weather")):
            raise WeatherAudioError("weather audio report city issue is stale or inconsistent")

        high, low = row.get("high_c"), row.get("low_c")
        for temperature in (high, low):
            if temperature is not None and (
                type(temperature) is not int or not -60 <= temperature <= 60
            ):
                raise WeatherAudioError("weather audio report temperature is invalid")
        if high is not None and low is not None and low > high:
            raise WeatherAudioError("weather audio report temperature range is invalid")

        pops = row.get("pops")
        if not isinstance(pops, list) or len(pops) != 4:
            raise WeatherAudioError("weather audio report precipitation periods are invalid")
        clean_pops: list[dict[str, object]] = []
        for pop, start in zip(pops, (0, 6, 12, 18)):
            if (not isinstance(pop, Mapping) or set(pop) != {"start", "end", "percent"}
                    or type(pop.get("start")) is not int or pop.get("start") != start
                    or type(pop.get("end")) is not int or pop.get("end") != start + 6):
                raise WeatherAudioError("weather audio report precipitation periods are invalid")
            percent = pop.get("percent")
            if percent is not None and (type(percent) is not int or not 0 <= percent <= 100):
                raise WeatherAudioError("weather audio report precipitation is invalid")
            clean_pops.append({"start": start, "end": start + 6, "percent": percent})

        normalized.append({
            "city": city.name, "office": city.office, "area_code": city.area,
            "station": city.station, "date": target_text, "issued_at": issued.isoformat(),
            "weather": clean_weather, "high_c": high, "low_c": low,
            "pops": clean_pops, "source_url": source_url,
        })
    return {"date": target_text, "expires_at": expiry}, normalized


def weather_report_digest(view: Mapping[str, object], *, now: int | float) -> str:
    """Fingerprint every official field in an already projected 11-city report."""
    clock = _now(now)
    header, cities = _validated_report_view(view, now=clock)
    return _report_digest(header, cities)


def _report_digest(header: Mapping[str, object], cities: list[dict[str, object]]) -> str:
    payload = {"source_url": SOURCE, "date": header["date"], "cities": cities}
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WeatherAudioError("weather audio report cannot be fingerprinted") from exc
    return hashlib.sha256(raw).hexdigest()


def _text(value: object) -> str:
    if (not isinstance(value, str) or len(value) > MAX_ITEM_TEXT_CHARS
            or any(ord(char) < 32 for char in value) or "<" in value or ">" in value):
        raise WeatherAudioError("weather audio text is invalid")
    normalized = " ".join(value.split())
    if not normalized:
        raise WeatherAudioError("weather audio text is invalid")
    return normalized


def _validate_request(request: object, *, now: float | None) -> dict[str, object]:
    if not isinstance(request, Mapping) or set(request) != REQUEST_KEYS:
        raise WeatherAudioError("weather audio request fields are invalid")
    if (type(request.get("schema_version")) is not int
            or request.get("schema_version") != REQUEST_SCHEMA_VERSION):
        raise WeatherAudioError("weather audio request schema is unsupported")
    if request.get("source") != WEATHER_AUDIO_SOURCE:
        raise WeatherAudioError("weather audio source is invalid")
    try:
        execution_id = validate_request_id(request.get("execution_id"))
    except NameValidationError as exc:
        raise WeatherAudioError("weather audio execution identity is invalid") from exc
    item_index = request.get("item_index")
    key = item_idempotency_key(execution_id, item_index)
    if request.get("item_key") != key:
        raise WeatherAudioError("weather audio item key does not match its execution item")
    text = _text(request.get("text"))
    fence = _runtime_fence(request.get("runtime_fence"), now=now)
    expiry = fence["expires_at"]
    forecast = _forecast(request.get("forecast"), expiry=expiry, now=now)
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "source": WEATHER_AUDIO_SOURCE,
        "execution_id": execution_id,
        "item_index": item_index,
        "item_key": key,
        "text": text,
        "runtime_fence": fence,
        "forecast": forecast,
    }


def build_weather_audio_request(
    *, execution_id: str, item_index: int, text: str,
    runtime_identity: Mapping[str, object], forecast_view: Mapping[str, object],
    now: int | float,
) -> dict[str, object]:
    """Build one queue value from literal narration and a validated JMA projection."""
    clock = _now(now)
    identity = _runtime_identity(runtime_identity)
    index_key = item_idempotency_key(execution_id, item_index)
    header, cities = _validated_report_view(forecast_view, now=clock)
    digest = _report_digest(header, cities)
    # City items align with weather.narration order; the intro/closing use the
    # latest issue in the same validated national report.
    if 1 <= item_index <= len(CITIES):
        issued_at = cities[item_index - 1]["issued_at"]
    else:
        issued_at = max(cities, key=lambda city: stamp(city["issued_at"]).timestamp())["issued_at"]
    request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "source": WEATHER_AUDIO_SOURCE,
        "execution_id": execution_id,
        "item_index": item_index,
        "item_key": index_key,
        "text": text,
        "runtime_fence": {**identity, "expires_at": header["expires_at"]},
        "forecast": {
            "source_url": SOURCE,
            "date": header["date"],
            "issued_at": issued_at,
            "report_digest": digest,
        },
    }
    return _validate_request(request, now=clock)


def validate_weather_audio_request(
    request: object, *, now: int | float,
) -> dict[str, object]:
    """Validate an enqueue/replay request against its freshness window."""
    return _validate_request(request, now=_now(now))


def request_fingerprint(request: Mapping[str, object]) -> str:
    """Hash the canonical whole request for conflict checks, never as the item key."""
    try:
        item = _validate_request(request, now=None)
        raw = json.dumps(item, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WeatherAudioError("weather audio request cannot be fingerprinted") from exc
    return hashlib.sha256(raw).hexdigest()


def build_weather_audio_receipt(
    request: Mapping[str, object], *, status: str,
    recorded_at: int | float, reason: str | None = None,
) -> dict[str, object]:
    """Build a contract receipt fixture; this does not prove actual playback."""
    item = _validate_request(request, now=None)
    if not isinstance(status, str) or status not in RECEIPT_STATUSES:
        raise WeatherAudioError("weather audio receipt status is invalid")
    if type(recorded_at) not in (int, float) or not math.isfinite(recorded_at) or recorded_at < 0:
        raise WeatherAudioError("weather audio receipt time is invalid")
    expiry = item["runtime_fence"]["expires_at"]
    issued_at = stamp(item["forecast"]["issued_at"]).timestamp()
    if recorded_at < issued_at:
        raise WeatherAudioError("weather audio receipt predates its forecast issue")
    if status == "queued":
        if reason is not None or recorded_at >= expiry:
            raise WeatherAudioError("weather audio queued receipt is inconsistent")
    elif status == "played":
        if reason is not None:
            raise WeatherAudioError("weather audio played receipt has a failure reason")
    elif status == "rejected":
        if reason not in _REJECTION_REASONS:
            raise WeatherAudioError("weather audio rejection reason is invalid")
        if reason == "expired" and recorded_at < expiry:
            raise WeatherAudioError("weather audio expiry rejection predates expiry")
    elif status == "interrupted":
        if reason not in _INTERRUPTION_REASONS:
            raise WeatherAudioError("weather audio interruption reason is invalid")
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "source": WEATHER_AUDIO_SOURCE,
        "item_key": item["item_key"],
        "request_digest": request_fingerprint(item),
        "status": status,
        "runtime_fence": item["runtime_fence"],
        "forecast": item["forecast"],
        "recorded_at": float(recorded_at),
        "reason": reason,
    }


def validate_weather_audio_receipt(
    request: Mapping[str, object], receipt: object,
) -> dict[str, object]:
    """Validate one queue/consumer receipt against the exact requested item."""
    item = _validate_request(request, now=None)
    if not isinstance(receipt, Mapping) or set(receipt) != RECEIPT_KEYS:
        raise WeatherAudioError("weather audio receipt fields are invalid")
    if (type(receipt.get("schema_version")) is not int
            or receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION):
        raise WeatherAudioError("weather audio receipt schema is unsupported")
    receipt_fence = _runtime_fence(receipt.get("runtime_fence"), now=None)
    if (receipt.get("source") != WEATHER_AUDIO_SOURCE
            or receipt.get("item_key") != item["item_key"]
            or receipt.get("request_digest") != request_fingerprint(item)
            or receipt_fence != item["runtime_fence"]
            or receipt.get("forecast") != item["forecast"]):
        raise WeatherAudioError("weather audio receipt does not match its item")
    status = receipt.get("status")
    recorded_at = receipt.get("recorded_at")
    reason = receipt.get("reason")
    return build_weather_audio_receipt(
        item, status=status, recorded_at=recorded_at, reason=reason
    )
