"""Comment reply output guard and candidate validation, owned by docich (#829 PR-3b).

Native port of the legacy chain as production loads it (``eloop_lib.sh``,
``DOCICH_BIN`` set on the VM):

- ``comment.sh``: ``_comment_strip_worknote_head``, ``_comment_strip_reasoning_tags``,
  ``_comment_guard_model_text``, ``_comment_strip_nonjapanese_head``,
  ``_comment_guard_japanese_text``, ``_comment_is_valid_generation_candidate``
- ``core/helpers.sh``: ``_contains_provider_error_text``
- ``radio_engine.sh``: ``_clean_comment_talk``, ``_sanitize_onair_text``
  (-> ``docich.onair_text``), the base ``_is_valid_comment_talk``
- ``comment_quality.sh``: the ``docich speech-quality --profile comment`` layer
- ``comment_runtime_policy.sh``: the plain-honorific-address rejection

``docich ai-guard`` / ``speech-quality`` are called in-process
(``model_output_guard`` / ``spoken_text_quality``). Pinned by
``tests/fixtures/comment_guard_golden.json`` (generated on Linux).

Shell details kept on purpose: inline pythons that read stdin see
universal-newline text; ``$(...)`` drops trailing newlines; ``grep`` matches
line by line.
"""
from __future__ import annotations

import os
import re

from docich import model_output_guard, spoken_text_quality
from docich.onair_text import sanitize_onair_text


def _stdin(text: str) -> str:
    """What a legacy inline python sees via ``sys.stdin.read()`` (text mode)."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _subst(text: str) -> str:
    """``$(...)``: trailing newlines are removed."""
    return text.rstrip("\n")


def _grep(pattern: str, text: str, flags=re.IGNORECASE) -> bool:
    """``printf '%s' text | grep -E[i]q pattern``: any line matches."""
    compiled = re.compile(pattern, flags)
    return any(compiled.search(line) for line in text.split("\n"))


# ------------------------------------------------------------------ strippers

_SEPARATOR_LINE_RE = re.compile(r"^[ \t]*[-‐-―]{3,}[ \t]*$")
_VIEWER_ADDRESS_RE = re.compile(r"さん[、,：:]")
_WORK_NOTE_RE = re.compile(
    r"(?:以下|下記)[、,]?\s*\d*\s*(?:件|通り)?\s*(?:の)?\s*(?:コメント返し|返信|返答|回答)"
    r"|(?:コメント返し|返信|返答|生成|出力)(?:自体)?は?\s*(?:完了|できました|終わりました)"
    r"|サンドボックス|ネットワーク制約|実行できません|実行できない"
    r"|(?:スクリプト|ツール|コマンド|環境|インジケーター)[^\n]{0,24}"
    r"(?:失敗|エラー|制約|できません|できない|見当たりません)"
    r"|(?:^|\n)\s*(?:I need to|We need to|Let's|Let me|I will|I'll|Analyzing|First,)\b"
    r"|WebFetch|WebSearch|exec_command|sandbox",
    re.IGNORECASE,
)


def strip_worknote_head(text: str) -> str:
    text = _stdin(text)
    if not text.strip():
        return text
    lines = text.splitlines()
    seps = [i for i, line in enumerate(lines) if _SEPARATOR_LINE_RE.match(line)]
    result = lines
    if seps:
        first = seps[0]
        head = "\n".join(lines[:first]).strip()
        body = "\n".join(lines[first + 1:]).strip()
        if (body and head and len(head) <= 600 and not _VIEWER_ADDRESS_RE.search(head)
                and _WORK_NOTE_RE.search(head)):
            result = lines[first + 1:]
    out = "\n".join(line for line in result if not _SEPARATOR_LINE_RE.match(line)).strip()
    return out if out else text


_TAGS = r"analysis|thinking|think"
_BLOCK_RE = re.compile(rf"<(?P<tag>{_TAGS})\b[^>]*>.*?</(?P=tag)\s*>", re.IGNORECASE | re.DOTALL)
_CLOSE_HEAD_RE = re.compile(rf"\A.*</(?:{_TAGS})\s*>", re.IGNORECASE | re.DOTALL)
_OPEN_TAIL_RE = re.compile(rf"<(?:{_TAGS})\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)


def strip_reasoning_tags(text: str) -> str:
    text = _stdin(text)
    if not text.strip():
        return text
    value, previous = text, None
    while previous != value:
        previous = value
        value = _BLOCK_RE.sub("", value)
    value = _OPEN_TAIL_RE.sub("", _CLOSE_HEAD_RE.sub("", value)).strip()
    return value if value else text


_JP_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿、。]")
_MARKER_RE = re.compile(r"===[A-Z_]+===")


def strip_nonjapanese_head(text: str, env=None) -> str:
    """Drop leading paragraphs that are almost not Japanese (English work notes)."""
    text = _stdin(text)
    if not text.strip():
        return text
    env = os.environ if env is None else env
    try:
        threshold = float(env.get("COMMENT_MIN_JP_RATIO", "0.5"))
    except Exception:
        threshold = 0.5

    def jp_ratio(para):
        body = re.sub(r"\s+", "", para)
        return len(_JP_RE.findall(body)) / len(body) if body else 1.0

    paragraphs = re.split(r"\n[ \t]*\n", text)
    start = 0
    for para in paragraphs:
        if not para.strip():
            start += 1
            continue
        if _MARKER_RE.search(para) or jp_ratio(para) >= threshold:
            break
        start += 1
    if start == 0 or start >= len(paragraphs):
        return text
    rest = "\n\n".join(paragraphs[start:]).strip()
    return rest if rest and _JP_RE.search(rest) else text


def guard_model_text(raw: str) -> str:
    """ai-guard, then reasoning tags, then the work-note head. "" = unsalvageable."""
    if not raw:
        return ""
    return strip_worknote_head(strip_reasoning_tags(model_output_guard.extract_final_text(_stdin(raw))))


def guard_japanese_text(raw: str, env=None) -> str:
    """Japanese-reply guard (never used for the English translation)."""
    guarded = _subst(guard_model_text(raw))
    return strip_nonjapanese_head(guarded, env) if guarded else ""


# ------------------------------------------------------------------ cleaners

def clean_comment_talk(text: str, preserve_paragraphs: bool = False) -> str:
    lines = _stdin(text + "\n").splitlines()
    clean: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            if preserve_paragraphs and clean and clean[-1] != "":
                clean.append("")
            continue
        if re.fullmatch(r'(assistant|analysis|final|tool_call|tool_result)', line, re.I):
            continue
        if re.fullmatch(r'(zai|glmflash|sonnet|claude|opencode)', line, re.I):
            continue
        if re.match(r'(agent|model|provider)\s*[:=]', line, re.I):
            continue
        if re.match(r'^[✗✕×].*\b(read|glob|grep|ls|edit|write|multiedit)\b.*\bfailed\b', line, re.I):
            continue
        if re.match(r'^[✱→►▸]\s*(read|glob|grep|ls|edit|write|multiedit)\b', line, re.I):
            continue
        if re.match(r'^(read|glob|grep|ls|edit|write|multiedit)\b', line, re.I):
            continue
        if re.match(r'^(error|warning)\s*:', line, re.I):
            continue
        if re.search(r'file not found:|no such file or directory|permission denied|invalid arguments|'
                     r'could not find oldstring|no changes to apply', line, re.I):
            continue
        if line.startswith('```') or line == '^D':
            continue
        clean.append(raw.rstrip())
    if preserve_paragraphs:
        while clean and clean[-1] == "":
            clean.pop()
    while clean:
        head = clean[0].strip()
        if re.match(r'^同志[^。]{0,140}という(コメント|ご質問|ご報告|ご挨拶|ご相談|ご指摘|話)ですね。?$', head):
            clean = clean[1:]
            continue
        if re.match(r'^(返信対象コメント|コメント前後文脈|直前コメント履歴|最近自分が実際に読み上げたコメント返し|'
                    r'前回のトーク内容|現在のゲーム状態メモ|配信UI説明メモ|ルール|再生成指示)', head):
            clean = clean[1:]
            continue
        if re.match(r'^(以下、|まず、?コメント|コメントを読み上げ)', head):
            clean = clean[1:]
            continue
        break
    if preserve_paragraphs:
        text = "\n".join(clean).strip()
    else:
        text = "\n".join(line for line in clean if line.strip()).strip()
    return re.sub(r'\n{3,}', '\n\n', text)


# ------------------------------------------------------------------ predicates

_PROVIDER_ERROR_RE = (
    r'invalid bearer token|authentication_error|failed to authenticat(e|ed)|api error[: ]|bad request|'
    r'request_id|invalid error token|invalid token|not logged in|please run /login|'
    r'unexpected error, check log file|failed to run the query|pragma wal_checkpoint|insufficient balance|'
    r'no resource package|rate limit exceeded|freeusagelimiterror|degraded function cannot be invoked|'
    r'function id .*degraded|providermodelnotfounderror|model not found|no such model|modelid|providerid|'
    r'agent ["\s]*[^"\s]+["\s]* not found|free tier users do not have access to this model|'
    r'potentially unsafe or sensitive content|avoid using prompts that may generate sensitive content|'
    r'unsafe or sensitive content in input or generation|content policy|safety policy|'
    r'(^|[^\w]|_)error:\s*gone|status["\s]*:\s*410|reached its end of life|is no longer available|'
    r'unknownerror|unexpected server error'
)


def contains_provider_error_text(text: str) -> bool:
    return _grep(_PROVIDER_ERROR_RE, text)


_JAPANESE_RE = re.compile(r"[぀-ヿ㐀-鿿]")


def _base_is_valid_comment_talk(talk: str) -> bool:
    """radio_engine.sh ``_is_valid_comment_talk`` (the base captured by later layers)."""
    compact = re.sub(r"[ \t\n\r\f\v]", "", talk)  # tr -d '[:space:]' (byte-wise, ASCII)
    if len(compact) < 3:
        return False
    if not _JAPANESE_RE.search(_stdin(talk)):
        return False
    if not _grep(r'[。！？]', talk, 0):
        return False
    if _grep(r'tool_call|tool_result|assistant_response|^analysis$|^final$|^assistant$|'
             r'^provider\s*[:=]|^model\s*[:=]|^agent\s*[:=]', talk):
        return False
    if contains_provider_error_text(talk) or _grep(
            r'unexpected token|syntaxerror|referenceerror|typeerror|could not find oldstring|'
            r'no changes to apply|rejected permission', talk):
        return False
    if _grep(r'(^|\s)(read failed|edit failed|write failed|file not found:|no such file or directory|'
             r'permission denied|invalid arguments)', talk):
        return False
    if _grep(r'(^|\s)(read|glob|grep|ls|edit|write|multiedit)\s+["./]', talk):
        return False
    if _grep(r'^\s*[✗✕×✱→►▸]', talk):
        return False
    if _grep(r'(WebFetch|WebSearch)|(^|\s)[✗✕×]\s*(webfetch|websearch)\s+failed\b', talk):
        return False
    if _grep(r'I can use the .* tool|WebFetch tool|Before I can proceed|grant permission|'
             r'Would you like me to proceed', talk):
        return False
    if _grep(r'具体的な(質問|指示|情報)を|何について知りたい|どのようなご用件|遠慮なくお話し|今日のテーマは何|'
             r'具体的に何について|準備はできています|お話を聞く準備', talk, 0):
        return False
    return True


def has_plain_honorific_address(text: str) -> bool:
    """comment_runtime_policy.sh: a paragraph opening with ○○さん/様/くん/ちゃん."""
    text = _stdin(text)
    allowed = ("同志", "みなさん", "皆さん")
    for para in re.split(r"\n\s*\n+", text):
        head = para.lstrip()
        if not head or head.startswith(allowed):
            continue
        if re.match(r"^@?[^\s、。！？!?：:,]{1,48}(?:さん|様|くん|ちゃん)[、,：:]", head):
            return True
    return False


def is_valid_comment_talk(talk: str) -> bool:
    """Effective validator: base -> docich speech-quality (comment) -> honorific policy."""
    if not _base_is_valid_comment_talk(talk):
        return False
    if spoken_text_quality.validate(_stdin(talk), profile="comment"):
        return False
    return not has_plain_honorific_address(talk)


def is_valid_generation_candidate(raw: str, env=None) -> bool:
    """The ai_generate_list validator: guard, clean, on-air sanitize, then validate."""
    if not raw or contains_provider_error_text(raw):
        return False
    guarded = guard_japanese_text(raw, env)
    if not guarded:
        return False
    cleaned = _subst(clean_comment_talk(guarded, True))
    cleaned = _subst(sanitize_onair_text(_stdin(cleaned)))
    return bool(cleaned) and is_valid_comment_talk(cleaned)
