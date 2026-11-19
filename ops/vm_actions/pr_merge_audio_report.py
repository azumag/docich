#!/usr/bin/env python3
"""Build the bounded sentence the VM audio-worker speaks for a merged PR.

The workflow fetches ``/repos/{repo}/commits/{sha}/pulls`` with the run-scoped
GitHub token and passes the JSON here.  The pull request title is treated as
data: it is reduced to one line, stripped of markdown/URLs/control characters,
bounded to 240 characters, and base64-encoded.  The text itself is never printed;
only the pull request number and its length reach the Actions log.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

MAX_CHARS = 240  # same bound as the owner speak operator and work-indicator audio
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_NUMBER_RE = re.compile(r"[0-9]{1,9}\Z")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TRAILING_REF_RE = re.compile(r"\s*[（(]\s*#\s*[0-9]+\s*[）)]\s*\Z")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_MEANINGFUL_RE = re.compile(r"[0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]")


def normalize_title(raw: str) -> str:
    """Return one speakable line for a pull request title."""
    text = unicodedata.normalize("NFKC", raw)
    text = _COMMENT_RE.sub(" ", text)
    text = _TRAILING_REF_RE.sub("", text)
    text = _IMAGE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("`", "")
    text = re.sub(r"[*~]", "", text)
    text = text.replace("_", " ")
    text = text.replace("#", "").replace("＃", "")
    # The title is quoted with 「」; keep any quotes inside it from nesting.
    text = text.replace("「", "『").replace("」", "』")
    text = _SPACE_RE.sub(" ", text)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = text.strip(" 　-—–:：、,，.|")
    # A title made only of symbols/quotes has nothing useful to read.
    return text if _MEANINGFUL_RE.search(text) else ""


def select_merged_pr(pulls: object, merge_sha: str) -> dict | None:
    """Pick the merged PR for this push, preferring the exact merge commit."""
    if not isinstance(pulls, list):
        raise ValueError("pulls payload must be an array")
    merged: list[dict] = []
    for pr in pulls:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or not (1 <= number <= 999_999_999)
        ):
            continue
        if not isinstance(pr.get("title"), str) or not pr.get("merged_at"):
            continue
        base = pr.get("base")
        if not isinstance(base, dict) or base.get("ref") != "main":
            continue
        merged.append(pr)
    if not merged:
        return None
    exact = [pr for pr in merged if pr.get("merge_commit_sha") == merge_sha]
    pool = exact or merged
    return max(pool, key=lambda pr: str(pr.get("merged_at", "")))


def build_report_text(title: str, number: int, *, max_chars: int = MAX_CHARS) -> str:
    """Return ``mainにプルリクエストN「title」がマージされました。`` within the bound."""
    clean = normalize_title(title).rstrip("。！？!?")
    if not clean:
        return f"mainにプルリクエスト{number}がマージされました。"[:max_chars]
    prefix = f"mainにプルリクエスト{number}「"
    suffix = "」がマージされました。"
    budget = max_chars - len(prefix) - len(suffix)
    if budget < 2:
        return f"mainにプルリクエスト{number}がマージされました。"[:max_chars]
    if len(clean) > budget:
        clean = clean[: budget - 1].rstrip(" 　、,，") + "…"
    return prefix + clean + suffix


def encode_for_vm(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _write_output(path: str, values: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as out:
        for key, value in values.items():
            out.write(f"{key}={value}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pr-merge-audio-report",
        description="Build the audio-worker sentence for one merged pull request.",
    )
    parser.add_argument("pulls", help="JSON from /repos/{repo}/commits/{sha}/pulls")
    parser.add_argument("--merge-sha", required=True)
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT", ""))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not SHA_RE.fullmatch(args.merge_sha):
            raise ValueError("invalid merge sha")
        payload = json.loads(Path(args.pulls).read_text(encoding="utf-8"))
        pr = select_merged_pr(payload, args.merge_sha)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        print("merge report input is invalid", file=sys.stderr)
        return 1
    if pr is None:
        _write_output(args.output, {"found": "false"})
        print(json.dumps({"operation": "pr-merge-audio-report", "found": False}, separators=(",", ":")))
        return 0
    number = int(pr["number"])
    text = build_report_text(pr["title"], number)
    if (
        not text
        or len(text) > MAX_CHARS
        or text != text.strip()
        or "\n" in text
        or "\r" in text
        or any(unicodedata.category(ch)[0] == "C" for ch in text)
    ):
        print("generated report text is invalid", file=sys.stderr)
        return 1
    _write_output(args.output, {"found": "true", "text_b64": encode_for_vm(text)})
    print(
        json.dumps(
            {"operation": "pr-merge-audio-report", "found": True, "pr": number, "chars": len(text)},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
