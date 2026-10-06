"""Pure port of the existing RADIO parser's required on-air contract.

Source: soviet_now lib/radio_parser.py at 793990939dbfd262491846be52a804841d7aa56c
(blob f455459926b2d0bb87eb82cd1e7a791907bd5c96). The parsing rules are unchanged;
stdin/file writes are replaced with an immutable in-memory result. This parser
does not verify generated facts or perform speech/queue delivery.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

MAX_RAW_BYTES = 65536


class RadioParseError(ValueError):
    """Fixed, non-content-bearing parser failure."""


@dataclass(frozen=True)
class ParsedScript:
    body: str = field(repr=False)
    summary: str = field(repr=False)
    selected_news: str = field(default="", repr=False)
    body_present: bool = field(default=True, repr=False)


def parse_script(raw: str) -> ParsedScript:
    """Apply the legacy required-marker path without touching a game checkout."""
    if not isinstance(raw, str):
        raise RadioParseError("invalid_output")
    try:
        if not raw or len(raw.encode("utf-8")) > MAX_RAW_BYTES:
            raise RadioParseError("output_limit")
    except UnicodeError:
        raise RadioParseError("invalid_output") from None
    raw = raw.replace("\r", "")

    raw = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw)
    raw = re.sub(
        r"</?(?:arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>",
        "",
        raw,
        flags=re.IGNORECASE,
    )

    lines = [line.strip() for line in raw.splitlines()]
    clean_lines = []
    for line in lines:
        if not line:
            continue
        if line.startswith("```"):
            continue
        if line == "^D":
            continue
        if re.fullmatch(r"/[^ ]*", line):
            continue
        if line.startswith("/Users/"):
            continue
        if re.fullmatch(r"</?[^>]+>", line):
            continue
        clean_lines.append(line)

    def marker_positions(lines, marker):
        return [idx for idx, line in enumerate(lines) if line == marker]

    script_markers = {
        "ON_AIR_SCRIPT_START",
        "===ON_AIR_SCRIPT===",
        "ON_AIR_SCRIPT===",
    }
    script_pos = [idx for idx, line in enumerate(clean_lines) if line in script_markers]
    if not script_pos:
        raise RadioParseError("missing_onair_marker")

    # The last exact script marker wins. Any model reasoning, search narration, or
    # tool chatter emitted before it is outside the on-air boundary by definition.
    script_start = script_pos[-1] + 1
    scoped_lines = clean_lines[script_start:]
    summary_pos = marker_positions(scoped_lines, "===SUMMARY===")
    selected_pos = marker_positions(scoped_lines, "===SELECTED_NEWS===")
    if not summary_pos:
        raise RadioParseError("missing_summary_marker")
    main_lines = scoped_lines[: selected_pos[0]] if selected_pos else scoped_lines

    selected_news = ""
    if selected_pos:
        for line in scoped_lines[selected_pos[0] + 1 :]:
            if not line or line.startswith("==="):
                continue
            selected_news = line
            break
    selected_news = re.sub(r"</?[A-Za-z_][^>]*>", "", selected_news).strip()
    selected_news = re.sub(r"\s+", " ", selected_news)[:240]

    summary = ""
    if summary_pos:
        summary_lines = []
        for line in main_lines[summary_pos[0] + 1 :]:
            if line.startswith("==="):
                break
            if not line:
                continue
            summary_lines.append(line)
            if len(summary_lines) >= 2:
                break
        if summary_lines:
            summary = " / ".join(summary_lines)
    summary = re.sub(r"</?[A-Za-z_][^>]*>", "", summary).strip()
    summary = re.sub(r"\s+", " ", summary)[:220]

    summary_like_header = re.compile(
        r"^(?:要約|まとめ|総括|要点|キーワード|一言要約|今回のまとめ|本日のまとめ)(?:\s*[:：]\s*.*)?$"
    )
    def is_discardable_edge_line(line):
        # The required on-air path discards only labeled metadata.
        return bool(summary_like_header.match(line))

    body_end = summary_pos[0]
    body_lines = [
        line for line in main_lines[:body_end] if line and not line.startswith("===")
    ]
    body_present = bool(body_lines)

    body = "\n".join(body_lines).strip()
    body = re.sub(r"</?[A-Za-z_][^>]*>", "", body).strip()

    if len(body) < 100:
        used_before_summary = False
        if summary_pos and summary_pos[0] < len(main_lines):
            before_summary = [line for line in main_lines[: summary_pos[0]] if not line.startswith("===")]
            if before_summary:
                body = "\n".join(before_summary).strip()
                used_before_summary = True
        if len(body) < 100 and not used_before_summary:
            fallback_lines = [line for line in main_lines if not line.startswith("===")]
            body = "\n".join(fallback_lines).strip()
        body = re.sub(r"</?[A-Za-z_][^>]*>", "", body).strip()

    clean_body_lines = [line.strip() for line in body.splitlines() if line.strip()]
    meta_prefixes = (
        "**注意:",
        "**注意：",
        "*注意:",
        "*注意：",
        "注意:",
        "注意：",
        "承知しました",
        "了解しました",
        "かしこまりました",
        "メッセージの末尾に",
        "プロンプトインジェクション",
        "本来の依頼",
        "ファクトチェック",
        "安全化した",
        "出力します",
        "応答します",
    )
    while clean_body_lines:
        head = clean_body_lines[0]
        if head == "---":
            clean_body_lines = clean_body_lines[1:]
            continue
        if head.startswith(meta_prefixes):
            clean_body_lines = clean_body_lines[1:]
            continue
        if is_discardable_edge_line(head):
            clean_body_lines = clean_body_lines[1:]
            continue
        break
    while clean_body_lines and is_discardable_edge_line(clean_body_lines[-1]):
        clean_body_lines = clean_body_lines[:-1]
    body = "\n".join(clean_body_lines).strip()

    body = re.sub(r"\n{3,}", "\n\n", body)
    if len(body) > 12000:
        body = body[:12000]

    return ParsedScript(body, summary, selected_news, body_present)
