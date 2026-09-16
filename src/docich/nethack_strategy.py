"""Strategic decision schema for the NetHack layered policy (P3c).

P3c prepares compact, public-information requests for a future LLM strategist
but does not invoke a model and does not translate a proposal into a keypress.
That separation keeps an untrusted/unfinished strategic response outside the
gameplay action path until a later reviewed executor exists.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from .nethack_inventory import VisibleInventoryItem, inventory_public_summary
from .nethack_observation import NethackObservation
from .nethack_policy import PolicyDecision


SCHEMA_VERSION = 1
ProposalKind = Literal[
    "hold",
    "inspect",
    "move_to_stairs",
    "ascend",
    "descend",
    "consume",
    "equip",
    "use",
    "answer_prompt",
]
_ALLOWED_KINDS = frozenset(
    {
        "hold",
        "inspect",
        "move_to_stairs",
        "ascend",
        "descend",
        "consume",
        "equip",
        "use",
        "answer_prompt",
    }
)


@dataclass(frozen=True)
class StrategicRequest:
    schema_version: int
    intent: str
    reason: str
    observation: dict[str, object]
    inventory: list[dict[str, object]]
    constraints: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "intent": self.intent,
            "reason": self.reason,
            "observation": self.observation,
            "inventory": self.inventory,
            "constraints": list(self.constraints),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class StrategicProposal:
    schema_version: int
    kind: ProposalKind
    rationale: str
    inventory_letter: str | None = None
    prompt_answer: str | None = None
    narration: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "rationale": self.rationale,
            "inventory_letter": self.inventory_letter,
            "prompt_answer": self.prompt_answer,
            "narration": self.narration,
        }


_BASE_CONSTRAINTS = (
    "Use only information present in this request; do not assume hidden NetHack state.",
    "Do not reinterpret an unidentified item as its hidden true identity.",
    "A proposal is advisory only and will not be executed directly.",
    "Prefer survival over progress when the visible state is ambiguous.",
)


def build_strategic_request(
    obs: NethackObservation,
    decision: PolicyDecision,
    inventory: tuple[VisibleInventoryItem, ...] = (),
) -> StrategicRequest:
    """Build a small public-only strategist request from normalized state."""
    return StrategicRequest(
        schema_version=SCHEMA_VERSION,
        intent=decision.intent,
        reason=decision.reason,
        observation=obs.public_summary(),
        inventory=inventory_public_summary(inventory),
        constraints=_BASE_CONSTRAINTS,
    )


def parse_strategic_proposal(data: str | bytes | dict[str, object]) -> StrategicProposal:
    """Validate a future strategist response without producing game actions."""
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode("utf-8")
    if isinstance(data, str):
        try:
            raw = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"strategic proposal is not valid JSON: {exc}") from exc
    else:
        raw = data
    if not isinstance(raw, dict):
        raise ValueError("strategic proposal must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported strategic proposal schema_version")

    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in _ALLOWED_KINDS:
        raise ValueError(f"invalid strategic proposal kind: {kind!r}")
    rationale = raw.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("strategic proposal rationale is required")

    inventory_letter = raw.get("inventory_letter")
    if inventory_letter is not None and (
        not isinstance(inventory_letter, str)
        or len(inventory_letter) != 1
        or not inventory_letter.isascii()
        or not inventory_letter.isalnum()
    ):
        raise ValueError("inventory_letter must be one ASCII alphanumeric character or null")

    prompt_answer = raw.get("prompt_answer")
    if prompt_answer is not None and (
        not isinstance(prompt_answer, str) or len(prompt_answer) > 32
    ):
        raise ValueError("prompt_answer must be a short string or null")

    narration = raw.get("narration", "")
    if not isinstance(narration, str) or len(narration) > 240:
        raise ValueError("narration must be a string of at most 240 characters")

    if kind in {"consume", "equip", "use"} and inventory_letter is None:
        raise ValueError(f"{kind} proposal requires inventory_letter")
    if kind == "answer_prompt" and prompt_answer is None:
        raise ValueError("answer_prompt proposal requires prompt_answer")
    if kind not in {"consume", "equip", "use"} and inventory_letter is not None:
        raise ValueError(f"{kind} proposal cannot carry inventory_letter")
    if kind != "answer_prompt" and prompt_answer is not None:
        raise ValueError(f"{kind} proposal cannot carry prompt_answer")

    return StrategicProposal(
        schema_version=SCHEMA_VERSION,
        kind=kind,  # type: ignore[arg-type]
        rationale=rationale.strip(),
        inventory_letter=inventory_letter,
        prompt_answer=prompt_answer,
        narration=narration.strip(),
    )


def should_narrate(decision: PolicyDecision, *, previous_intent: str | None = None) -> bool:
    """Keep speech sparse: only risky/meaningful state changes are narrated."""
    if decision.requires_llm:
        return True
    if decision.intent in {
        "survival_emergency",
        "status_emergency",
        "food_emergency",
        "prompt_decision",
        "assess_contact",
        "exploration_blocked",
    }:
        return True
    return previous_intent is not None and previous_intent != decision.intent
