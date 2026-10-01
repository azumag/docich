"""Offline schema checks for the future weather/shared-audio boundary.

The queue and player below are test doubles. These tests do not import the
Soren shell library, create the shared comment queue, or claim real playback.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import sys
from pathlib import Path
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.weather_audio import (
    RECEIPT_REASONS,
    RECEIPT_STATUSES,
    WEATHER_AUDIO_SOURCE,
    WeatherAudioError,
    build_weather_audio_receipt,
    build_weather_audio_request,
    item_idempotency_key,
    request_fingerprint,
    runtime_identity_matches,
    weather_report_digest,
    validate_weather_audio_receipt,
    validate_weather_audio_request,
)
from docich.weather import ATTRIBUTION, CITIES, SOURCE, TERMS


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc).timestamp()
EXECUTION_ID = "12345678-1234-4234-8234-123456789abc"
IDENTITY = {
    "game": "weather-view",
    "runtime_id": "g7-a1b2c3d4",
    "generation": 7,
    "lease_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
}


def _view(*, target_date="2026-10-02", issued_at="2026-10-01T20:00:00+09:00",
          expires_at=NOW + 600, changed_weather_office=None):
    rows = []
    for city in CITIES:
        weather = "曇り" if city.office == changed_weather_office else "晴れ"
        rows.append({
            "city": city.name,
            "area_code": city.area,
            "station": city.station,
            "office": city.office,
            "date": target_date,
            "issued_at": issued_at,
            "weather": weather,
            "high_c": 18,
            "low_c": 10,
            "pops": [
                {"start": start, "end": start + 6, "percent": 0}
                for start in (0, 6, 12, 18)
            ],
            "source_url": f"{SOURCE}#area_type=offices&area_code={city.office}",
        })
    return {
        "schema_version": 1,
        "date": target_date,
        "generated_at": NOW,
        "expires_at": expires_at,
        "server_now": NOW,
        "attribution": ATTRIBUTION,
        "source_url": SOURCE,
        "terms_url": TERMS,
        "cities": rows,
    }


def _request(*, index=1, text="札幌。晴れ。最高気温は18度。", **changes):
    args = {
        "execution_id": EXECUTION_ID,
        "item_index": index,
        "text": text,
        "runtime_identity": IDENTITY,
        "forecast_view": _view(),
        "now": NOW,
    }
    args.update(changes)
    return build_weather_audio_request(**args)


class DummyQueue:
    """In-memory stand-in for the shared consumer contract; never production."""

    def __init__(self):
        self.requests = {}
        self.receipts = {}

    def enqueue(self, request, *, now):
        request = validate_weather_audio_request(request, now=now)
        key = request["item_key"]
        previous = self.requests.get(key)
        if previous is not None and request_fingerprint(previous) != request_fingerprint(request):
            raise WeatherAudioError("idempotency key conflicts with an existing item")
        if previous is None:
            self.requests[key] = deepcopy(request)
            self.receipts[key] = build_weather_audio_receipt(
                request, status="queued", recorded_at=now
            )
        return deepcopy(self.receipts[key])

    def finish(self, request, *, status, recorded_at, reason=None):
        receipt = build_weather_audio_receipt(
            request, status=status, recorded_at=recorded_at, reason=reason
        )
        self.receipts[request["item_key"]] = deepcopy(receipt)
        return validate_weather_audio_receipt(request, receipt)


class DummyPlayer:
    def __init__(self, outcome="played", *, after_start=None):
        self.outcome = outcome
        self.after_start = after_start
        self.calls = []

    def play(self, text):
        self.calls.append(text)
        if self.after_start is not None:
            self.after_start()
        return self.outcome


def _consume_with_dummy(queue, key, *, now, active_identity, player):
    """Model only the future consumer's start/finish receipt contract."""
    request = queue.requests[key]
    fence = request["runtime_fence"]
    if now >= fence["expires_at"]:
        return queue.finish(request, status="rejected", recorded_at=now, reason="expired")
    if not runtime_identity_matches(active_identity, fence):
        return queue.finish(request, status="rejected", recorded_at=now,
                            reason="runtime_mismatch")
    outcome = player.play(request["text"])
    if outcome == "rejected":
        return queue.finish(request, status="rejected", recorded_at=now,
                            reason="player_rejected")
    if outcome == "interrupted" or not runtime_identity_matches(active_identity, fence):
        return queue.finish(request, status="interrupted", recorded_at=now,
                            reason="playback_interrupted")
    return queue.finish(request, status="played", recorded_at=now)


def test_weather_request_has_dedicated_source_and_exact_expiring_runtime_fence():
    item = _request()
    assert WEATHER_AUDIO_SOURCE == "weather_corner"
    assert item["source"] == "weather_corner"
    assert item["runtime_fence"] == {
        **IDENTITY,
        "expires_at": NOW + 600,
    }
    assert item["forecast"] == {
        "source_url": "https://www.jma.go.jp/bosai/forecast/",
        "date": "2026-10-02",
        "issued_at": "2026-10-01T20:00:00+09:00",
        "report_digest": weather_report_digest(_view(), now=NOW),
    }


def test_item_key_is_stable_per_execution_and_ordinal_not_text():
    first = _request(index=1, text="同じ本文")
    retry = _request(index=1, text="同じ本文")
    edited = _request(index=1, text="違う本文")
    second = _request(index=2, text="同じ本文")
    assert first["item_key"] == retry["item_key"] == edited["item_key"]
    assert first["item_key"] != second["item_key"]
    assert request_fingerprint(first) != request_fingerprint(edited)
    assert item_idempotency_key(EXECUTION_ID, 1) == first["item_key"]


@pytest.mark.parametrize("change", [
    {"text": "更新された本文"},
    {"forecast_view": _view(target_date="2026-10-01")},
    {"forecast_view": _view(issued_at="2026-10-01T20:01:00+09:00")},
    {"forecast_view": _view(changed_weather_office="016000")},
    {"runtime_identity": {**IDENTITY, "lease_id": "bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee"}},
])
def test_success_receipt_cannot_be_reused_for_changed_payload_or_runtime(change):
    original = _request(index=4)
    changed = _request(index=4, **change)
    receipt = build_weather_audio_receipt(
        original, status="played", recorded_at=NOW + 1
    )

    assert changed["item_key"] == original["item_key"]
    assert request_fingerprint(changed) != request_fingerprint(original)
    with pytest.raises(WeatherAudioError):
        validate_weather_audio_receipt(changed, receipt)


@pytest.mark.parametrize("change", [
    {"runtime_identity": {**IDENTITY, "game": "hanjuku-hero"}},
    {"runtime_identity": {**IDENTITY, "generation": True}},
    {"runtime_identity": {**IDENTITY, "runtime_id": "g8-a1b2c3d4"}},
    {"runtime_identity": {**IDENTITY, "lease_id": "not-a-lease"}},
    {"forecast_view": _view(expires_at=NOW)},
    {"forecast_view": _view(expires_at=NOW - 1)},
    {"forecast_view": _view(expires_at=NOW + 901)},
    {"forecast_view": _view(expires_at=True)},
    {"forecast_view": _view(expires_at=float("nan"))},
    {"forecast_view": _view(expires_at=float("inf"))},
    {"forecast_view": _view(target_date="2026-02-30")},
    {"forecast_view": _view(target_date="2026-10-03")},
    {"forecast_view": _view(issued_at="2026-10-01T20:00:00")},
    {"forecast_view": _view(issued_at="2026-10-01T21:00:01+09:00")},
    {"forecast_view": _view(issued_at="2026-09-30T02:00:00+09:00")},
    {"text": "\u0000unsafe"},
    {"text": "x" * 1001},
])
def test_request_rejects_unfenced_or_stale_weather_items(change):
    with pytest.raises(WeatherAudioError):
        _request(**change)


def test_request_text_limit_matches_the_shared_audio_boundary_without_truncation():
    item = _request(text="x" * 1000)
    assert len(item["text"]) == 1000


def test_request_validation_fixes_source_and_rejects_hanjuku_contract_shape():
    item = _request()
    altered = deepcopy(item)
    altered["source"] = "hanjuku_commentary"
    with pytest.raises(WeatherAudioError, match="source"):
        validate_weather_audio_request(altered, now=NOW)

    altered = deepcopy(item)
    altered["runtime_fence"].pop("lease_id")
    with pytest.raises(WeatherAudioError, match="fence"):
        validate_weather_audio_request(altered, now=NOW)

    altered = deepcopy(item)
    altered["schema_version"] = True
    with pytest.raises(WeatherAudioError, match="schema"):
        validate_weather_audio_request(altered, now=NOW)


def test_dummy_shared_queue_deduplicates_by_item_key_not_body_and_rejects_conflict():
    queue = DummyQueue()
    same_text_a = _request(index=1, text="同じ本文")
    same_text_b = _request(index=2, text="同じ本文")
    queued_a = queue.enqueue(same_text_a, now=NOW)
    queued_a_retry = queue.enqueue(same_text_a, now=NOW)
    queued_b = queue.enqueue(same_text_b, now=NOW)

    assert queued_a["status"] == queued_a_retry["status"] == "queued"
    assert queued_a["item_key"] == queued_a_retry["item_key"]
    assert queued_a["item_key"] != queued_b["item_key"]
    assert len(queue.requests) == 2

    conflict = _request(index=1, text="本文が変わった")
    with pytest.raises(WeatherAudioError, match="conflicts"):
        queue.enqueue(conflict, now=NOW)


@pytest.mark.parametrize("status,reason", [
    ("played", None),
    ("rejected", "expired"),
    ("interrupted", "playback_interrupted"),
])
def test_receipt_validator_accepts_only_item_bound_terminal_outcomes(status, reason):
    item = _request()
    receipt = build_weather_audio_receipt(
        item, status=status,
        recorded_at=(NOW + 601 if reason == "expired" else NOW + 1),
        reason=reason,
    )
    assert validate_weather_audio_receipt(item, receipt) == receipt
    assert status in RECEIPT_STATUSES
    if reason is not None:
        assert reason in RECEIPT_REASONS


@pytest.mark.parametrize("mutation", [
    {"source": "hanjuku_commentary"},
    {"item_key": "weather_corner:other:1"},
    {"request_digest": "0" * 64},
    {"status": "success"},
    {"runtime_fence": {**IDENTITY, "expires_at": NOW + 601}},
    {"reason": "raw-provider-error"},
])
def test_receipt_rejects_wrong_source_key_status_fence_or_reason(mutation):
    item = _request()
    receipt = build_weather_audio_receipt(
        item, status="played", recorded_at=NOW + 1
    )
    receipt.update(mutation)
    with pytest.raises(WeatherAudioError):
        validate_weather_audio_receipt(item, receipt)


def test_dummy_consumer_records_played_rejected_and_interrupted_without_live_audio():
    queue = DummyQueue()
    active = dict(IDENTITY)
    items = [_request(index=i) for i in range(3)]
    for item in items:
        queue.enqueue(item, now=NOW)

    player = DummyPlayer()
    played = _consume_with_dummy(
        queue, items[0]["item_key"], now=NOW + 1,
        active_identity=active, player=player,
    )
    assert played["status"] == "played"
    assert player.calls == [items[0]["text"]]

    expired_player = DummyPlayer()
    rejected = _consume_with_dummy(
        queue, items[1]["item_key"], now=NOW + 601,
        active_identity=active, player=expired_player,
    )
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "expired"
    assert expired_player.calls == []

    interrupted_player = DummyPlayer(after_start=lambda: active.update(runtime_id="g8-a1b2c3d4"))
    interrupted = _consume_with_dummy(
        queue, items[2]["item_key"], now=NOW + 1,
        active_identity=active, player=interrupted_player,
    )
    assert interrupted["status"] == "interrupted"
    assert interrupted["reason"] == "playback_interrupted"
    assert interrupted_player.calls == [items[2]["text"]]


def test_legacy_content_dedup_result_is_not_a_weather_audio_receipt():
    item = _request()
    with pytest.raises(WeatherAudioError):
        validate_weather_audio_receipt(
            item, {"ok": True, "dedup": True, "filename": None}
        )


@pytest.mark.parametrize("now", [True, float("nan"), float("inf")])
def test_request_clock_rejects_bool_and_nonfinite_values(now):
    with pytest.raises(WeatherAudioError):
        validate_weather_audio_request(_request(), now=now)


def test_receipt_schema_does_not_treat_bool_as_integer_version_or_time():
    item = _request()
    receipt = build_weather_audio_receipt(
        item, status="played", recorded_at=NOW + 1
    )
    bad_version = deepcopy(receipt)
    bad_version["schema_version"] = True
    with pytest.raises(WeatherAudioError):
        validate_weather_audio_receipt(item, bad_version)

    with pytest.raises(WeatherAudioError):
        build_weather_audio_receipt(item, status="played", recorded_at=True)
