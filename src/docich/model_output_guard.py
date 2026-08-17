#!/usr/bin/env python3
"""Extract only a model's final, speakable text from mixed CLI output.

Agent CLIs can interleave reasoning, web/tool progress, and the final answer on
the same stream.  This parser is intentionally conservative: an unmarked
output containing tool protocol or an obvious work-note lead is discarded so
that callers can use their normal fallback instead of speaking it on air.
"""

from __future__ import annotations

import re
import sys


ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
EXPLICIT_FINAL_RE = re.compile(
    r"<(?:final|assistant_response)(?:\s[^>]*)?>(.*?)"
    r"</(?:final|assistant_response)\s*>",
    re.IGNORECASE | re.DOTALL,
)
UNSAFE_PROTOCOL_RE = re.compile(
    r"</?(?:analysis|think|thinking|tool_call|tool_result|function_calls?|"
    r"invoke|parameter|arg_name|arg_value|search_query|tool)(?:\s|>|$)|"
    r"\]\s*<\]\s*[A-Za-z0-9_.:-]+\s*\[>\[",
    re.IGNORECASE,
)
WORK_NOTE_RE = re.compile(
    r"(?:WebFetch|WebSearch|search_query)|"
    r"(?:材料|情報|出力|内容|候補).{0,16}(?:確認|検討|整理)(?:します|します。|してみます)|"
    r"(?:検索|調査|確認).{0,16}(?:します|してみます|を試します|できない|使えない)|"
    r"自分の知識で|確実性が高いのは|以下のあたり|"
    r"(?:^|\n)\s*(?:I need to|We need to|Let's|I will|I'll|Analyzing)\b",
    re.IGNORECASE,
)
SEPARATOR_RE = re.compile(r"(?m)^\s*---+\s*$")
FINAL_CHANNEL_RE = re.compile(r"(?im)^\s*(?:final|assistant_response)\s*$")


def _trim_cli_noise(value: str) -> str:
    clean: list[str] = []
    for raw in value.replace("\r", "").splitlines():
        line = raw.strip()
        if not line:
            clean.append("")
            continue
        if (
            line == "^D"
            or line.startswith("Script started on ")
            or line.startswith("Script done on ")
            or re.fullmatch(r"/[^ ]*", line)
            or line.startswith("/Users/")
            or line.startswith("⚙")
            or line.startswith(">")
        ):
            continue
        clean.append(raw.rstrip())
    return "\n".join(clean).strip()


def _safe_candidate(value: str) -> str:
    value = _trim_cli_noise(value)
    if not value or UNSAFE_PROTOCOL_RE.search(value):
        return ""
    head_lines = [line for line in value.splitlines() if line.strip()][:6]
    head = "\n".join(head_lines)[:800]
    if WORK_NOTE_RE.search(head):
        return ""
    return value


def extract_final_text(raw: str) -> str:
    text = ANSI_RE.sub("", raw).replace("\x00", "")

    # Fact-check output has its own strict downstream parser.  Preserve the
    # issue section, but discard any reasoning before its required envelope.
    lines = text.replace("\r", "").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "===SAFE_SCRIPT===":
            envelope = "\n".join(lines[index:])
            script = []
            for script_line in lines[index + 1 :]:
                if script_line.strip() in {
                    "===ISSUES===",
                    "===SUMMARY===",
                    "===SELECTED_NEWS===",
                }:
                    break
                script.append(script_line)
            if not _safe_candidate("\n".join(script)):
                return ""
            envelope = _trim_cli_noise(envelope)
            return "" if UNSAFE_PROTOCOL_RE.search(envelope) else envelope

    # Prefer explicit final-channel containers when the backend supplies them.
    explicit = list(EXPLICIT_FINAL_RE.finditer(text))
    if explicit:
        return _safe_candidate(explicit[-1].group(1))
    channel_markers = list(FINAL_CHANNEL_RE.finditer(text))
    if channel_markers:
        return _safe_candidate(text[channel_markers[-1].end() :])

    # Some routed models print untagged work notes, then a Markdown divider,
    # then the requested answer.  Recover only when the prefix is demonstrably
    # a work note; otherwise preserve legitimate dividers in clean prose.
    separators = list(SEPARATOR_RE.finditer(text))
    for separator in reversed(separators):
        prefix = text[: separator.start()]
        candidate = text[separator.end() :]
        if (UNSAFE_PROTOCOL_RE.search(prefix) or WORK_NOTE_RE.search(prefix)) and re.search(
            r"[。！？.!?]", candidate
        ):
            return _safe_candidate(candidate)

    cleaned = _safe_candidate(text)
    return cleaned


def main() -> int:
    result = extract_final_text(sys.stdin.read())
    if result:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
