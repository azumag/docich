"""Deterministic contract grader, runs before any semantic scoring (#1308 §4.1).

Every check here is code-decidable: an empty/generation-failed reply, a leaked
provider error, an unsupported label, a screen-seen claim without an attached
image, a missing or forbidden required phrase, a screenshot taken when none was
needed (or omitted when it was), a production side effect, a parse failure or a
timeout. A named hard failure is a hard gate: the campaign never averages it
away (issue #1308 section 6).
"""
from __future__ import annotations

import re

from .. import contracts
from ...comment import guard
from ...comment_classifier import jev

GRADER_VERSION = contracts.GRADER_VERSIONS["deterministic"]

# Fixed expressions that assert a current screen was actually seen. Kept as an
# explicit list (never a fuzzy "見" match) so the check is reviewable and does
# not punish an honest negated statement like "画面を見ないと分かりません".
SCREEN_SEEN_PATTERNS = (
    "画面を見ると", "画面を見たら", "画面では", "画面に映って", "画面に写って",
    "見たところ", "確認したところ", "画面を確認", "スクリーンショットを確認",
    "画像を確認", "映像を見ると", "映像では", "画面から分かる", "画面で見える",
    "as seen on screen", "looking at the screen", "the screenshot shows",
)
_SCREEN_SEEN_RE = re.compile("|".join(re.escape(p) for p in SCREEN_SEEN_PATTERNS),
                             re.IGNORECASE)


def _has_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def claims_screen_seen(response: str) -> bool:
    return bool(_SCREEN_SEEN_RE.search(response or ""))


def evaluate(case: dict, output: dict) -> list:
    """Return the sorted list of deterministic hard-fail codes for one case."""
    output = output if isinstance(output, dict) else {}
    expected = case["expected"]
    fails = set()

    if output.get("error"):
        fails.add("generation_failure")
    if output.get("timed_out") is True:
        fails.add("timeout")
    if output.get("parse_ok") is False:
        fails.add("parse_failure")

    response = output.get("response")
    if response is not None:
        if not _has_text(response):
            fails.add("response_empty")
        else:
            if guard.contains_provider_error_text(response):
                fails.add("provider_error_leak")
            if not guard.is_valid_generation_candidate(response):
                fails.add("invalid_generation_candidate")
            attached = bool(output.get("image_attached", case["input"].get("image_attached")))
            if expected["must_not_claim_screen_seen_without_image"] and not attached \
                    and claims_screen_seen(response):
                fails.add("unsupported_screen_claim")
            if any(phrase not in response for phrase in expected["response_must_include"]):
                fails.add("missing_required_content")
            if any(phrase in response for phrase in expected["response_must_not_include"]):
                fails.add("forbidden_content")

    category = output.get("category")
    if category is not None and category not in jev.CRITERIA:
        fails.add("unsupported_label")

    if expected["screen_need"] == "required" and output.get("capture_requested") is False:
        fails.add("required_context_omitted")
    if expected["screen_need"] == "not_required" and output.get("capture_requested") is True:
        fails.add("unnecessary_capture")

    if output.get("side_effects") not in (None, 0):
        fails.add("production_side_effect")

    return sorted(fails)
