#!/usr/bin/env python3
"""Apply the reviewed Soren NEWS spam-dispatch fix to the exact live preimage."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from pathlib import Path

TARGET = Path("/home/ubuntu/soren/broadcast/radio_news.sh")
OLD_SHA256 = "929b9550d6c22270b86f984f86e54c85d6be7d64547fd71b304335e48682f986"
NEW_SHA256 = "54c76ecce50aae4a1c372ba69dbfe3e5b936b78d3a749fbf7aaa2a49608391b8"

OLD_FRAGMENT = '# --- AIスパム判定 (opencode glm) ---\n_news_ai_spam_check() {\n\tlocal title="$1" block="$2"\n\tlocal spam_timeout="${NEWS_SPAM_CHECK_TIMEOUT_SEC:-20}"\n\t# タイトル+本文冒頭をAIに判定させる\n\tlocal body_excerpt\n\tbody_excerpt=$(printf \'%s\' "$block" | head -n 5 | tail -n +2 | head -c 300)\n\n\tlocal verdict rc prompt_text\n\tprompt_text="以下の記事がニュースとして紹介する価値があるか判定してください。\n宣伝、広告、アフィリエイト、プロモーションコード紹介、商品レビュー偽装、SEOスパム、企業PR記事であれば SPAM と答えてください。\n正当な報道・ニュース・時事であれば NEWS と答えてください。\nSPAM か NEWS の1単語だけ答えてください。\n\nタイトル: ${title}\n本文冒頭: ${body_excerpt}"\n\tlocal queue_token=""\n\tif [ "${AI_GENERATION_QUEUE_ENABLED:-1}" = "1" ]; then\n\t\t_ai_generation_queue_enter "NEWS:spam_check"\n\t\tqueue_token="$AI_GENERATION_QUEUE_LAST_TOKEN"\n\tfi\n\tverdict=$(\n\t\tANTHROPIC_AUTH_TOKEN="ollama" \\\n\t\tANTHROPIC_BASE_URL="${OLLAMA_BASE_URL:-http://192.168.11.13:11434}" \\\n\t\tANTHROPIC_API_KEY="" \\\n\t\ttimeout "${spam_timeout}s" claude -p "$prompt_text" --model=qwen3.5:9b 2>/dev/null \\\n\t\t\t| tr -d \'[:space:]\'\n\t)\n\trc=$?\n\t[ -n "$queue_token" ] && _ai_generation_queue_leave "$queue_token" "NEWS:spam_check"\n\n\tif [ "$verdict" = "SPAM" ]; then\n\t\tlog "[NEWS:SPAM] AI判定: SPAM → ${title}"\n\t\treturn 0  # spam detected\n\tfi\n\tif [ "$rc" -eq 124 ]; then\n\t\tlog "[NEWS:SPAM] AI判定タイムアウト(${spam_timeout}s) → PASS: ${title}"\n\t\treturn 1\n\tfi\n\tif [ "$rc" -ne 0 ]; then\n\t\tlog "[NEWS:SPAM] AI判定失敗 rc=${rc} verdict=${verdict:-EMPTY} → PASS: ${title}"\n\t\treturn 1\n\tfi\n\tlog "[NEWS:SPAM] AI判定: ${verdict:-UNKNOWN}(PASS) → ${title}"\n\treturn 1  # not spam\n}\n\n'
NEW_FRAGMENT = '# --- AIスパム判定 ---\n_news_spam_verdict_valid() {\n\tlocal verdict="${1:-}"\n\tverdict=$(printf \'%s\' "$verdict" | tr -d \'[:space:]\' | tr \'[:lower:]\' \'[:upper:]\')\n\t[ "$verdict" = "SPAM" ] || [ "$verdict" = "NEWS" ]\n}\n\n_news_ai_spam_check() {\n\tlocal title="$1" block="$2"\n\tlocal spam_timeout="${NEWS_SPAM_CHECK_TIMEOUT_SEC:-20}"\n\tlocal primary_agent="${NEWS_SPAM_CHECK_PRIMARY_AGENT:-local}"\n\tlocal fallback_agent="${NEWS_SPAM_CHECK_FALLBACK_AGENT:-opencode:muse-spark-1.3-contributor-free}"\n\tlocal body_excerpt\n\tbody_excerpt=$(printf \'%s\' "$block" | head -n 5 | tail -n +2 | head -c 300)\n\n\tlocal verdict rc prompt_text prompt_file\n\tprompt_text="以下の記事がニュースとして紹介する価値があるか判定してください。\n宣伝、広告、アフィリエイト、プロモーションコード紹介、商品レビュー偽装、SEOスパム、企業PR記事であれば SPAM と答えてください。\n正当な報道・ニュース・時事であれば NEWS と答えてください。\nSPAM か NEWS の1単語だけ答えてください。\n\nタイトル: ${title}\n本文冒頭: ${body_excerpt}"\n\tprompt_file=$(mktemp /tmp/news_spam_prompt_XXXXXXXX)\n\tprintf \'%s\\n\' "$prompt_text" >"$prompt_file"\n\tverdict=$(ai_generate \\\n\t\t"NEWS:spam_check" "$prompt_file" \\\n\t\t"$primary_agent" "$fallback_agent" \\\n\t\t"$spam_timeout" _news_spam_verdict_valid)\n\trc=$?\n\trm -f "$prompt_file"\n\tverdict=$(printf \'%s\' "$verdict" | tr -d \'[:space:]\' | tr \'[:lower:]\' \'[:upper:]\')\n\n\tif [ "$rc" -eq 0 ] && [ "$verdict" = "SPAM" ]; then\n\t\tlog "[NEWS:SPAM] AI判定: SPAM → ${title}"\n\t\treturn 0\n\tfi\n\tif [ "$rc" -ne 0 ]; then\n\t\tlog "[NEWS:SPAM] AI判定失敗 rc=${rc} verdict=${verdict:-EMPTY} → PASS: ${title}"\n\t\treturn 1\n\tfi\n\tlog "[NEWS:SPAM] AI判定: ${verdict:-UNKNOWN}(PASS) → ${title}"\n\treturn 1\n}\n\n'


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(text: str) -> str:
    if text.count(OLD_FRAGMENT) != 1:
        raise ValueError("reviewed hotfix preimage fragment mismatch")
    return text.replace(OLD_FRAGMENT, NEW_FRAGMENT, 1)


def apply(path: Path = TARGET) -> str:
    raw = path.read_bytes()
    current = sha256(raw)
    if current == NEW_SHA256:
        return "already_applied"
    if current != OLD_SHA256:
        raise ValueError(f"refusing unreviewed live drift: sha256={current}")
    updated = transform(raw.decode("utf-8")).encode("utf-8")
    if sha256(updated) != NEW_SHA256:
        raise ValueError("hotfix output hash does not match reviewed soviet_now source")
    st = path.stat()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.hotfix-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(updated)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, stat.S_IMODE(st.st_mode))
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    if sha256(path.read_bytes()) != NEW_SHA256:
        raise RuntimeError("post-replace verification failed")
    return "applied"


def main() -> int:
    if len(sys.argv) != 1:
        print("no arguments accepted", file=sys.stderr)
        return 2
    try:
        result = apply()
    except Exception as exc:
        print(f"hotfix refused: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
