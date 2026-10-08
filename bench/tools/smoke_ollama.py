#!/usr/bin/env python3
"""Ollama 起動確認 + VRAM 実測スモークテスト (#1263).

RTX 3060 実機の Ollama (HTTP API) に対し、選定モデルごとに **1 プロンプト**を
流して「起動して応答を返すか」を確認し、同時に実測 VRAM を取る。本番の精度
比較は `bench/jev_bench.py`（PR #1933, OpenAI 互換 `/v1/chat/completions`）
が担う。ここはモデルを入れ替えても同じ手順で回せる起動確認用の薄い層。

計測するもの:

- TTFT (ms): 最初の非空 content チャンクまで
- 総生成時間 (ms) / eval tokens-per-sec
- 実測 VRAM (`/api/ps` の `size_vram`) と重み込みロードサイズ (`size`)
- 出力テキスト（JSON 分類契約を満たしたかどうかの目視/機械確認用）

プロンプトは全モデル共通。モデル差は chat template のみ（Ollama 側が適用）。

使い方::

    python3 bench/tools/smoke_ollama.py \\
        --base-url http://desktop-9j2it17:11434 \\
        --model qwen2.5:7b-instruct-q4_K_M --num-ctx 4096 --num-ctx 16384 \\
        --out bench/results/2026-10-09_smoke

    # モデルを省略すると /api/tags の全モデルが対象。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request

# 分類ラベル。正本は src/docich/comment_classifier/jev.py の CRITERIA で、
# 本番ベンチはそちらを import する（ここは起動確認用に固定列挙）。
LABELS = (
    "card_gacha", "raid", "subscription", "stream_goal", "bits",
    "sing_request", "game_question", "game_status", "general_question",
    "strategy_advice", "comment_advice", "stream_bug_report", "chitchat", "other",
)

# 全モデル共通の固定プロンプト（本番 Jev の判定対象はコメント本文のみ）。
SYSTEM_PROMPT = (
    "あなたは Twitch 配信のコメントを分類する分類器です。"
    "コメントの意図を次のラベルから1つだけ選び、必ず JSON のみを返してください。\n"
    '{"choice": "<label>", "confidence": <0-1>}\n'
    "ラベル: " + ", ".join(LABELS) + "\n"
    "判断できない場合は other を返してください。"
)
USER_PROMPT = "つぎカードつかって、5れんしょう狙おう"

# Qwen3 系は thinking を切らないと JSON 契約に届かない（num_predict を思考で
# 使い切る）。モデル固有の chat template / 生成モード差なので、ここだけ
# Ollama の `think: false` を渡す。本文の指示・生成パラメータは全モデル共通。
NO_THINK = {"qwen3"}


def http_json(url, payload=None, timeout=600):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def stream_chat(base_url, model, num_ctx, max_tokens=128, no_think=False):
    """1 プロンプトをストリーミングで流し、TTFT と生成量を実測する。"""
    system = SYSTEM_PROMPT
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": USER_PROMPT},
        ],
        "stream": True,
        "options": {
            "temperature": 0.0,
            "top_p": 1.0,
            "num_predict": max_tokens,
            "seed": 42,
            "num_ctx": num_ctx,
        },
    }
    if no_think:
        payload["think"] = False
    req = urllib.request.Request(
        base_url + "/api/chat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    ttft = None
    text_parts = []
    final = {}
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            raw = raw.strip()
            if not raw:
                continue
            ev = json.loads(raw.decode())
            if ev.get("error"):
                raise RuntimeError(ev["error"])
            piece = (ev.get("message") or {}).get("content") or ""
            if piece and ttft is None:
                ttft = (time.time() - t0) * 1000.0
            if piece:
                text_parts.append(piece)
            if ev.get("done"):
                final = ev
    total_ms = (time.time() - t0) * 1000.0
    eval_count = final.get("eval_count") or 0
    eval_ns = final.get("eval_duration") or 0
    return {
        "ttft_ms": round(ttft, 1) if ttft is not None else None,
        "total_ms": round(total_ms, 1),
        "prompt_eval_count": final.get("prompt_eval_count"),
        "eval_count": eval_count,
        "eval_tok_per_s": round(eval_count / (eval_ns / 1e9), 1) if eval_ns else None,
        "load_ms": round((final.get("load_duration") or 0) / 1e6, 1),
        "response": "".join(text_parts).strip(),
    }


def loaded(base_url, model):
    """ロード中のモデルの VRAM 実測値 (/api/ps)。"""
    try:
        ps = http_json(base_url + "/api/ps", timeout=30)
    except Exception:  # noqa: BLE001
        return None
    for m in ps.get("models", []):
        if m.get("name") == model or m.get("model") == model:
            return {"size": m.get("size"), "size_vram": m.get("size_vram")}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://desktop-9j2it17:11434")
    ap.add_argument("--model", action="append", default=None,
                    help="対象モデル（複数指定可）。省略時は /api/tags の全モデル。")
    ap.add_argument("--num-ctx", action="append", type=int, default=None,
                    help="コンテキスト長（複数指定可）。既定 4096。")
    ap.add_argument("--out", default=None, help="結果 JSON の出力先ディレクトリ")
    ap.add_argument("--prompt", default=None, help="USER_PROMPT を差し替える")
    ap.add_argument("--warmup", type=int, default=1,
                    help="計測前に捨てる呼び出し回数（既定1、初回ロード分を除外）")
    args = ap.parse_args()

    global USER_PROMPT  # noqa: PLW0603
    if args.prompt:
        USER_PROMPT = args.prompt

    base = args.base_url.rstrip("/")
    version = http_json(base + "/api/version", timeout=30)
    tags = http_json(base + "/api/tags", timeout=30)
    disk = {m["name"]: m.get("size") for m in tags.get("models", [])}
    models = args.model or list(disk)
    ctxs = args.num_ctx or [4096]

    results = []
    for model in models:
        family = model.split("/")[-1].split(":")[0]
        for ctx in ctxs:
            row = {"model": model, "num_ctx": ctx,
                   "disk_bytes": disk.get(model),
                   "ollama_version": version.get("version"),
                   "prompt": USER_PROMPT}
            try:
                for _ in range(max(0, args.warmup)):
                    stream_chat(base, model, ctx, no_think=family in NO_THINK)
                row.update(stream_chat(base, model, ctx,
                                       no_think=family in NO_THINK))
                row["status"] = "ok"
            except Exception as exc:  # noqa: BLE001
                row["status"] = "error"
                row["error"] = "%s: %s" % (type(exc).__name__, exc)
            ps = loaded(base, model)
            if ps:
                row["loaded_size"] = ps["size"]
                row["size_vram"] = ps["size_vram"]
                if ps["size"] and ps["size_vram"] is not None:
                    row["vram_offload_pct"] = round(
                        100.0 * ps["size_vram"] / ps["size"], 1)
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            # 次のコンテキスト長のロードがモデルを置き換えないよう明示的に解放
            try:
                http_json(base + "/api/generate",
                          {"model": model, "keep_alive": 0}, timeout=30)
            except Exception:  # noqa: BLE001
                pass

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        path = os.path.join(args.out, "smoke_%s.json" % stamp)
        with open(path, "w") as fh:
            json.dump({"base_url": base, "version": version, "results": results},
                      fh, ensure_ascii=False, indent=2)
        print("wrote " + path)


if __name__ == "__main__":
    main()
