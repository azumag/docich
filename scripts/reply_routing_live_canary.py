"""Owner canary for reply-evidence-v1. Synthetic inputs only; no production state."""
from __future__ import annotations

import json
import os
import time

from docich.reply_routing import decide


CASES = (
    ("simple_comment", "こんにちは、今日も見ています。", "api_only"),
    ("gacha_reaction", "SSR出た！", "api_only"),
    ("public_current_fact", "OpenAIの最新モデルは何ですか？", "web"),
    ("project_game_logic", "このゲームのガチャ抽選ロジックはどのソースコードで決まっていますか？", "code"),
)


def main() -> int:
    env = dict(os.environ)
    if not env.get("TYPESAFE_API_KEY"):
        print(json.dumps({"status": "missing_key"}, sort_keys=True))
        return 2
    env["DOCICH_ALLOW_REAL_AI"] = "1"
    env["DOCICH_JEV_ROUTE"] = "direct"
    rows = []
    ok = True
    for name, text, expected in CASES:
        started = time.monotonic()
        decision = decide([{"role": "user", "text": text}], env=env)
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        matched = decision.status == "jev" and decision.scope == expected
        ok = ok and matched
        rows.append({
            "case": name,
            "expected": expected,
            "scope": decision.scope,
            "status": decision.status,
            "confidence": decision.confidence,
            "latency_ms": latency_ms,
            "matched": matched,
        })
    print(json.dumps({"status": "ok" if ok else "mismatch", "rubric": "reply-evidence-v1", "results": rows},
                     ensure_ascii=False, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
