"""Offline contract tests for the mystery corner's Twitch clients (#684).

Only the strict envelope/validation behaviour is checked here; the HTTP path
uses injected openers, never the network.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import mystery_api
from docich.hanjuku_prediction_api import APIError
from docich.mystery_api import (
    PollHelix,
    configured_poll_client,
    poll_choice_votes,
)


class FakeOpener:
    def __init__(self, body=b'{"data": []}', status=200):
        self.body = body
        self.status = status
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.status != 200:
            import urllib.error

            raise urllib.error.HTTPError(request.full_url, self.status, "err", {}, None)

        import io
        import urllib.response

        return urllib.response.addinfourl(
            io.BytesIO(self.body), headers={}, url=request.full_url, code=self.status)


def _g(tmp_path):
    return SimpleNamespace(state_dir=tmp_path / "run", config_path=tmp_path / "docich.toml")


def test_poll_client_gate_requires_the_channel_opt_in(tmp_path, monkeypatch):
    g = _g(tmp_path)
    for key in ("TWITCH_POLLS_ENABLED", "TWITCH_POLLS_TOKEN", "TWITCH_PREDICTIONS_TOKEN",
                "TWITCH_CLIENT_ID", "TWITCH_BROADCASTER_ID", "EXPLORE_MODE"):
        monkeypatch.delenv(key, raising=False)
    client, reason = configured_poll_client(g)
    assert client is None and reason == "disabled"

    monkeypatch.setenv("TWITCH_POLLS_ENABLED", "1")
    monkeypatch.setenv("EXPLORE_MODE", "1")
    client, reason = configured_poll_client(g)
    assert client is None and reason == "explore"

    monkeypatch.setenv("EXPLORE_MODE", "0")
    client, reason = configured_poll_client(g)
    assert client is None and reason == "unconfigured"

    monkeypatch.setenv("TWITCH_POLLS_TOKEN", "oauth:polltok")
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_BROADCASTER_ID", "1234")
    client, reason = configured_poll_client(g)
    assert reason is None and isinstance(client, PollHelix)
    assert client.broadcaster == "1234"


def test_poll_token_falls_back_to_the_predictions_token(monkeypatch):
    for key in ("TWITCH_POLLS_ENABLED", "TWITCH_POLLS_TOKEN", "TWITCH_PREDICTIONS_TOKEN",
                "TWITCH_CLIENT_ID", "TWITCH_BROADCASTER_ID", "EXPLORE_MODE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TWITCH_POLLS_ENABLED", "1")
    monkeypatch.setenv("TWITCH_PREDICTIONS_TOKEN", "oauth:predtok")
    monkeypatch.setenv("TWITCH_CLIENT_ID", "cid")
    monkeypatch.setenv("TWITCH_BROADCASTER_ID", "1234")
    client, reason = configured_poll_client(None)
    assert reason is None and isinstance(client, PollHelix)


def test_create_rejects_out_of_bound_choices_and_durations():
    client = PollHelix("tok", "cid", "1234", opener=FakeOpener())
    with pytest.raises(APIError):
        client.create("q", ["a"], 120)
    with pytest.raises(APIError):
        client.create("q", ["a"] * 6, 120)
    with pytest.raises(APIError):
        client.create("q", ["a", "b"], 5)
    with pytest.raises(APIError):
        client.create("q", ["a", "b"], 1801)


def test_strict_envelope_rejects_malformed_poll_rows(tmp_path):
    import json

    good = {"id": "p1", "status": "COMPLETED", "title": "q", "choices": [
        {"id": "c1", "title": "a", "votes": 3}, {"id": "c2", "title": "b", "votes": 0}]}
    client = PollHelix("tok", "cid", "1234",
                       opener=FakeOpener(body=f'{{"data": [{json.dumps(good)}]}}'.encode()))
    assert client.list("p1") == [good]

    for body in (
        b'{"data": [{"id": "p1", "status": "WEIRD", "title": "q", "choices": []}]}',
        b'{"data": [{"id": "p1", "status": "ACTIVE", "title": "q", "choices": [1]}]}',
        b'{"data": [{"id": "", "status": "ACTIVE", "title": "q", "choices": [1]}]}',
        b'{"data": "nope"}',
        b'not json',
    ):
        client = PollHelix("tok", "cid", "1234", opener=FakeOpener(body=body))
        with pytest.raises(APIError):
            client.list()

    client = PollHelix("tok", "cid", "1234", opener=FakeOpener(status=429))
    with pytest.raises(APIError) as exc:
        client.list()
    assert exc.value.code == "rate_limited"
    client = PollHelix("tok", "cid", "1234", opener=FakeOpener(status=403))
    with pytest.raises(APIError) as exc:
        client.list()
    assert exc.value.code == "auth"


def test_vote_helpers_are_total_and_deterministic():
    row = {"choices": [{"title": "a", "votes": 0}, {"title": "b", "votes": 5},
                       {"title": "c", "votes": 5}]}
    votes = poll_choice_votes(row)
    assert votes == [0, 5, 5]
    # The winner tie-break itself lives in mystery_participation (trpg core).
    assert poll_choice_votes({"choices": []}) is None
    assert poll_choice_votes({"choices": [{"title": "a"}]}) is None
    assert poll_choice_votes({"choices": [{"title": "a", "votes": -1}]}) is None
    assert poll_choice_votes({"choices": [{"title": "a", "votes": True}]}) is None
