"""On-air text sanitizer shared by comment replies and radio (#829).

Native port of soviet_now ``broadcast/radio_engine.sh`` ``_sanitize_onair_text``:
drops provider-error / tool-log / URL / Chinese lines, rewrites a few
self-deprecating phrases, removes ``#`` and Chinese sentences. Pinned by
``tests/fixtures/comment_guard_golden.json``.
"""
from __future__ import annotations

import re

_DROP_LINE_PATTERNS = [
    r'^\s*https?://\S*\s*$',
    r'failed to authenticat(?:e|ed)',
    r'api error[: ]',
    r'authentication_error',
    r'invalid bearer token',
    r'request_id',
    r'\binvalid error token\b',
    r'\binvalid token\b',
    r'\bunexpected token\b',
    r'\bsyntaxerror\b',
    r'\breferenceerror\b',
    r'\btypeerror\b',
    r'could not find oldstring',
    r'no changes to apply',
    r'the user rejected permission',
    r'permission to use this specific tool call',
    r'^\s*[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b.*$',
    r'^\s*[✗✕×]\s*(webfetch|websearch)\s+failed\b.*$',
    r'^\s*[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b.*$',
    r'^\s*[✱→►▸]\s*(WebFetch|WebSearch)\b.*$',
    r'^\s*%?\s*(WebFetch|WebSearch)\b.*$',
    r'.*(WebFetch|WebSearch).*$',
    r'^\s*(read|glob|grep|ls|edit|write|multiedit)\b.*$',
    r'^\s*(error|warning)\s*:.*$',
    r'file not found:',
    r'no such file or directory',
    r'permission denied',
    r'invalid arguments',
    r'^\s*\{.*\"type\"\s*:\s*\"error\".*\}\s*$',
    r'現在.*(問題|不具合|障害).*(読み上げ|放送|案内).*(できません|できない)',
    r'現在.*(読み上げ|放送|案内).*(できません|できない)',
    r'検索(が|は)?できません',
    r'調査(が|は)?できません',
    r'情報(が|は)?取得できません',
    r'うまく読み上げできません',
    r'読み上げられません',
    r'^\s*⚙\s*\w',
    r'^\s*\{\s*"query"\s*:',
    r'^\s*\{.*"(notes|lyric|frame_length|f0)\s*".*\}\s*$',
]
_REWRITES = [
    (r'^\s*%?\s*(?:WebFetch|WebSearch)\b\s*', ''),
    (r'[^。\n]*?(?:本文|全文|記事(?:の)?本文)(?:が|を|は)?\s*(?:確認|取得|入手)(?:でき(?:ない|ません|ず|ていな)|不可)[^。\n]*。', ''),
    (r'[（(][^）)]*(?:本文|全文)[^）)]*(?:確認|取得)[^）)]*[）)]', ''),
    (r'誰も(聞いて|見て)い(?:ない|ません)', 'みなさんに届くように'),
    (r'聞き手(?:が|は)?い(?:ない|ません)', '聞き手に届くように'),
    (r'リスナー(?:が|は)?い(?:ない|ません)', 'リスナーに届くように'),
    (r'視聴者(?:が|は)?い(?:ない|ません)', '視聴者に届くように'),
    (r'誰に向けてやってるのか', 'みなさんに向けて'),
    (r'過疎(?:配信|放送)?', 'この配信'),
    (r'無人(?:配信|放送)', '配信'),
    (r'誰もいない', 'みなさんがいる'),
    (r'マージ', '併合'),
    (r'合体', '併合'),
    (r'https?://\S+', ''),
]


def _is_chinese_line(s: str) -> bool:
    """No kana and many CJK ideographs -> Chinese."""
    cjk = len(re.findall(r'[一-鿿]', s))
    kana = len(re.findall(r'[぀-ヿ]', s))
    return (cjk >= 4 and kana == 0) or (cjk >= 8 and kana <= 1)


def _remove_chinese_sentences(t: str) -> str:
    parts = re.split(r'(。)', t)
    result = []
    for i in range(0, len(parts) - 1, 2):
        sent = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ''
        if _is_chinese_line(sent):
            continue
        result.append(sent + sep)
    if len(parts) % 2 == 1 and parts[-1].strip():
        if not _is_chinese_line(parts[-1]):
            result.append(parts[-1])
    return ''.join(result)


def sanitize_onair_text(text: str) -> str:
    filtered = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line:
            low = line.lower()
            if any(re.search(pat, low, flags=re.IGNORECASE) for pat in _DROP_LINE_PATTERNS):
                continue
            if _is_chinese_line(line):
                continue
        filtered.append(raw_line)
    out = "\n".join(filtered)
    for pat, repl in _REWRITES:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    out = re.sub(r'[#＃]', '', out)
    out = _remove_chinese_sentences(out)
    return re.sub(r'\n{3,}', '\n\n', out).strip()
