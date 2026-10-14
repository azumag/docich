"""Offline 同志カード ranking derivation, validation and injection contracts.

No Twitch, no network: ``http_get`` is injected and every failure asserts the
fixed non-sensitive message (no endpoint, no query, no streamer id, no body).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.doushi_ranking import (  # noqa: E402
    ALLOWED_PERIODS,
    DEFAULT_LIMIT,
    DEFAULT_PERIOD,
    DoushiRankingError,
    MAX_LIMIT,
    build_request,
    derive_endpoint,
    fetch_ranking,
    validate_payload,
)
from doushi_payload_fixtures import make_payload  # noqa: E402

STREAMER = "11111111-2222-4b33-8444-555555555555"


def test_derive_endpoint_keeps_origin_and_drops_the_overlay_query():
    endpoint = derive_endpoint(
        f"https://twica.example/overlay/{STREAMER}?token=SECRET-QUERY#frag"
    )
    assert endpoint == f"https://twica.example/api/overlay/{STREAMER}/card-ranking"
    assert "SECRET" not in endpoint and "token" not in endpoint


def test_derive_endpoint_accepts_loopback_http_and_rejects_everything_else():
    for host in ("127.0.0.1", "[::1]", "localhost"):
        endpoint = derive_endpoint(f"http://{host}:8080/overlay/{STREAMER}")
        assert endpoint.startswith(f"http://{host}")

    # A trailing slash is the only tolerated extra segment.
    assert derive_endpoint(f"https://twica.example/overlay/{STREAMER}/") == (
        f"https://twica.example/api/overlay/{STREAMER}/card-ranking"
    )

    for bad in (
        "",
        "/overlay/" + STREAMER,
        "ftp://twica.example/overlay/" + STREAMER,
        "http://twica.example/overlay/" + STREAMER,
        "http://::1/overlay/" + STREAMER,
        "https://user@twica.example/overlay/" + STREAMER,
        f"https://twica.example/overlay/{STREAMER.upper()}",
        f"https://twica.example/overlay/{STREAMER}/extra",
        "https://twica.example/other/" + STREAMER,
    ):
        with pytest.raises(DoushiRankingError):
            derive_endpoint(bad)


@pytest.mark.parametrize("bad", [None, 12, ["not", "a", "url"]])
def test_derive_endpoint_rejects_non_string_config(bad):
    with pytest.raises(DoushiRankingError):
        derive_endpoint(bad)


@pytest.mark.parametrize("period", ["hour", "ALL", "", None, 7])
def test_build_request_rejects_unknown_periods(period):
    with pytest.raises(DoushiRankingError):
        build_request("https://x/api/overlay/" + STREAMER + "/card-ranking", period=period)


@pytest.mark.parametrize("limit", [0, -1, MAX_LIMIT + 1, "10", True, 1.5])
def test_build_request_bounds_the_limit(limit):
    with pytest.raises(DoushiRankingError):
        build_request("https://x/api/overlay/" + STREAMER + "/card-ranking", limit=limit)


def test_build_request_is_the_only_place_query_parameters_appear():
    url = build_request("https://x/api/overlay/" + STREAMER + "/card-ranking")
    assert url == (
        f"https://x/api/overlay/{STREAMER}/card-ranking"
        f"?period={DEFAULT_PERIOD}&limit={DEFAULT_LIMIT}"
    )
    assert set(ALLOWED_PERIODS) == {"all", "7d", "30d"}


def test_validate_payload_accepts_the_published_shape_and_strips_extra_keys():
    payload = make_payload()
    payload["by_count"][0]["secret_viewer_note"] = "SECRET-BODY"

    validated = validate_payload(payload, period=DEFAULT_PERIOD, limit=MAX_LIMIT)

    assert "secret_viewer_note" not in validated["by_count"][0]
    assert set(validated) == {
        "generated_at", "period", "metric_labels", "note", "card_type_total",
        "by_count", "by_points", "by_unique", "popular_cards",
    }
    assert validated["by_count"][0]["username"] == "alice0"
    assert validated["card_type_total"] == 128


def test_validate_payload_rejects_a_stale_or_naive_generated_at():
    stale = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    for value in (stale.isoformat().replace("+00:00", "Z"), "2026-01-01T00:00:00", None, 5):
        with pytest.raises(DoushiRankingError):
            validate_payload(
                make_payload(generated_at=value) if value is not None
                else {**make_payload(), "generated_at": None},
                period=DEFAULT_PERIOD, limit=MAX_LIMIT)


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(period="7d"),
    lambda p: p.pop("note"),
    lambda p: p.update(metric_labels={}),
    lambda p: p.update(card_type_total=True),
    lambda p: p.update(by_count="not-a-list"),
    lambda p: p["by_count"].append("not-a-row"),
    lambda p: p["by_count"].__setitem__(0, {"rank": 2, "username": "x", "redemption_count": 1}),
    lambda p: p["by_count"].__setitem__(0, {"rank": 1, "username": "", "redemption_count": 1}),
    lambda p: p["popular_cards"][0].update(rank=9),
    lambda p: p["by_unique"][0].pop("total_cards"),
])
def test_validate_payload_fail_closed_on_schema_damage(mutate):
    payload = make_payload()
    mutate(payload)
    with pytest.raises(DoushiRankingError):
        validate_payload(payload, period=DEFAULT_PERIOD, limit=MAX_LIMIT)


def test_validate_payload_never_trims_a_longer_board_than_the_contract():
    payload = make_payload(limit=1)
    payload["by_count"].append(
        {"rank": 2, "username": "bob", "redemption_count": 1}
    )
    with pytest.raises(DoushiRankingError):
        validate_payload(payload, period=DEFAULT_PERIOD, limit=1)


def test_validate_payload_bounds_display_names_without_failing():
    payload = make_payload()
    payload["by_count"][0]["username"] = "  " + "\u202e" + "x" * 200
    payload["popular_cards"][0]["card_name"] = " " + "カード" * 60 + " "

    validated = validate_payload(payload, period=DEFAULT_PERIOD, limit=MAX_LIMIT)

    assert len(validated["by_count"][0]["username"]) <= 40
    assert "\u202e" not in validated["by_count"][0]["username"]
    assert len(validated["popular_cards"][0]["card_name"]) <= 60
    assert validated["popular_cards"][0]["card_name"].startswith("カード")


def test_fetch_ranking_uses_only_the_injected_http_get_and_returns_validated_rows():
    seen = []
    payload = make_payload()

    def http_get(url, timeout):
        seen.append((url, timeout))
        return json.dumps(payload).encode("utf-8")

    validated = fetch_ranking(
        f"https://x/api/overlay/{STREAMER}/card-ranking",
        http_get=http_get, timeout=3.0,
    )

    assert seen == [(f"https://x/api/overlay/{STREAMER}/card-ranking?period=all&limit=10", 3.0)]
    assert validated["by_count"][0]["redemption_count"] == 50
    assert [row["rank"] for row in validated["popular_cards"]] == [1, 2, 3]


@pytest.mark.parametrize("http_get", [None, "nope", 7])
def test_fetch_ranking_requires_a_callable_injection(http_get):
    with pytest.raises(DoushiRankingError):
        fetch_ranking(f"https://x/api/overlay/{STREAMER}/card-ranking", http_get=http_get)


def test_fetch_ranking_reports_every_failure_with_one_fixed_message():
    endpoint = f"https://x/api/overlay/{STREAMER}/card-ranking"

    def boom(_url, _timeout):
        raise OSError("SECRET-CONNECTION-DETAIL")

    with pytest.raises(DoushiRankingError) as raised:
        fetch_ranking(endpoint, http_get=boom)
    message = str(raised.value)
    assert message == "doushi ranking unavailable (fetch)"
    for secret in ("SECRET-CONNECTION-DETAIL", STREAMER, "period=", "https://"):
        assert secret not in message
    assert not raised.value.__cause__

    for body in (b"{broken", b"\xff\xfe", b"[]", b"null"):
        with pytest.raises(DoushiRankingError):
            fetch_ranking(endpoint, http_get=lambda _u, _t, _b=body: _b)

    with pytest.raises(DoushiRankingError):
        fetch_ranking(endpoint, http_get=lambda _u, _t: "not-bytes")


def test_fetch_ranking_rejects_an_out_of_band_timeout():
    with pytest.raises(DoushiRankingError):
        fetch_ranking(
            f"https://x/api/overlay/{STREAMER}/card-ranking",
            http_get=lambda _u, _t: b"{}",
            timeout=0,
        )
