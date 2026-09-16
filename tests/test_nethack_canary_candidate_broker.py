from __future__ import annotations

from docich.nethack_canary_candidate_broker import dispatch_public_request
from docich.nethack_strategist import StrategistDispatchResult
from docich.nethack_strategy import StrategicProposal


class FakeStrategist:
    def __init__(self):
        self.requests = []

    def dispatch(self, request):
        self.requests.append(request)
        return StrategistDispatchResult(
            status="proposed",
            proposal=StrategicProposal(
                schema_version=1,
                kind="hold",
                rationale="arena blind",
            ),
        )


def public_request():
    return {
        "schema_version": 1,
        "intent": "survival_emergency",
        "reason": "visible HP is critical",
        "observation": {
            "message": "danger",
            "prompt": "none",
            "player": [2, 1],
            "vitals": {"hp": 2, "hp_max": 10, "hp_ratio": 0.2, "dungeon_level": 3, "turn": 12},
            "conditions": [],
            "local_map": ["..@.."],
        },
        "inventory": [],
        "constraints": ["public only"],
    }


def test_valid_public_request_is_the_only_candidate_input():
    strategist = FakeStrategist()
    result = dispatch_public_request(strategist, public_request())
    assert result["status"] == "proposed"
    assert result["proposal"]["kind"] == "hold"
    assert len(strategist.requests) == 1
    assert strategist.requests[0].observation["vitals"]["hp"] == 2


def test_hidden_field_is_rejected_before_candidate_dispatch():
    strategist = FakeStrategist()
    raw = public_request()
    raw["observation"]["hidden_map"] = ["secret"]
    result = dispatch_public_request(strategist, raw)
    assert result["status"] == "error"
    assert strategist.requests == []
    assert "unknown fields" in result["error"]


def test_top_level_unknown_field_is_rejected_before_candidate_dispatch():
    strategist = FakeStrategist()
    raw = public_request()
    raw["arena_path"] = "/canary/episode"
    result = dispatch_public_request(strategist, raw)
    assert result["status"] == "error"
    assert strategist.requests == []


def test_broker_response_never_contains_gameplay_action_list():
    strategist = FakeStrategist()
    result = dispatch_public_request(strategist, public_request())
    assert set(result) == {"schema_version", "status", "proposal", "error"}
    assert "actions" not in result
