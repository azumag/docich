#!/usr/bin/env python3
"""Shared quality checks for text that will be spoken on stream.

This module intentionally validates text instead of rewriting it. Punctuation is
part of the model's prose, so silently inserting commas after generation can
change nuance and produce unnatural pauses. Callers should reject a bad
candidate and let their normal generation fallback/retry path handle it.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable

JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
# A comment must actually *end* with sentence punctuation.  Accept common
# closing quotes/brackets after that punctuation, but do not let punctuation in
# an earlier sentence make an unpunctuated final sentence pass validation.
TERMINAL_PUNCTUATION_RE = re.compile(r"[。！？!?][」』】）》）)\]\"'”’]*\s*\Z")
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?])")
COMMA_RE = re.compile(r"[、，,]")
WHITESPACE_RE = re.compile(r"\s+")

# The comment prompt asks for a reading pause at least every 15-30 characters.
# Keep the validator at the permissive edge of that contract: a Japanese run
# longer than 30 visible characters must contain a comma/pause boundary.
COMMENT_MAX_UNPUNCTUATED_RUN = 30


def _compact(value: str) -> str:
    return WHITESPACE_RE.sub("", value)


def _has_long_unpunctuated_japanese_run(
    text: str, *, max_run: int = COMMENT_MAX_UNPUNCTUATED_RUN
) -> bool:
    for sentence in SENTENCE_BOUNDARY_RE.split(text):
        if not JAPANESE_RE.search(sentence):
            continue
        body = sentence.rstrip("。！？!? \t\r\n")
        for run in COMMA_RE.split(body):
            compact = _compact(run)
            if JAPANESE_RE.search(compact) and len(compact) > max_run:
                return True
    return False


def comment_reply_issues(text: str) -> tuple[str, ...]:
    """Return stable issue codes for a finished Japanese comment reply."""
    issues: list[str] = []
    compact = _compact(text)
    if len(compact) < 3:
        issues.append("too_short")
    if not JAPANESE_RE.search(text):
        issues.append("missing_japanese")
    if not TERMINAL_PUNCTUATION_RE.search(text):
        issues.append("missing_terminal_punctuation")
    if _has_long_unpunctuated_japanese_run(text):
        issues.append("long_japanese_run_without_comma")
    return tuple(issues)


PROFILE_VALIDATORS: dict[str, Callable[[str], tuple[str, ...]]] = {
    "comment": comment_reply_issues,
}


def validate(text: str, *, profile: str) -> tuple[str, ...]:
    try:
        validator = PROFILE_VALIDATORS[profile]
    except KeyError as exc:  # pragma: no cover - argparse normally prevents this
        raise ValueError(f"unknown profile: {profile}") from exc
    return validator(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docich speech-quality",
        description="読み上げテキストの品質を検証する。stdin を読み、合格なら 0、不合格なら 1。",
    )
    parser.add_argument("--profile", choices=sorted(PROFILE_VALIDATORS), default="comment")
    parser.add_argument(
        "--explain",
        action="store_true",
        help="不合格理由の安定 issue code を stderr へ出力する",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    issues = validate(sys.stdin.read(), profile=args.profile)
    if not issues:
        return 0
    if args.explain:
        for issue in issues:
            print(issue, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
