#!/usr/bin/env python3
"""Regenerate tests/fixtures/comment_guard_golden.json (#829 PR-3b).

Executes the legacy comment output guard / validation functions exactly as
production loads them (``eloop_lib.sh``, with ``DOCICH_BIN`` pointing at this
docich checkout, as on the VM) for a fixed synthetic corpus of model outputs,
and records stdout / exit status. ``docich.comment.guard`` must reproduce it.

Run it on Linux (GNU grep, like production), e.g. in ``ubuntu:24.04``:

    git -C games/soviet_now archive <sha> | tar -x -C /tmp/sn
    docker run --rm -v "$PWD":/w -v /tmp/sn:/sn:ro -w /w ubuntu:24.04 bash -c \\
      'apt-get update -qq && apt-get install -y -qq python3 gettext-base >/dev/null &&
       python3 scripts/golden/comment_guard_golden.py --soviet-now /sn --commit <sha>'
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comment_prompt_golden import bash  # noqa: E402  (production-style legacy loader)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests/fixtures/comment_guard_golden.json"

JA = "同志アリョーシャ、ご質問ありがとうございます。音声は今のところ、正常に流れているはずです。"
# A plain reply that ai-guard's WORK_NOTE_RE ("確認.{0,16}します") rejects whole
# in production; pinned as-is (the port must not silently "fix" it).
JA_WORKNOTE_FALSE_POSITIVE = "同志アリョーシャ、ご質問ありがとうございます。音声は私の側でも確認しますね。"
JA2 = "同志ボブ、レアカードの獲得、おめでとうございます。赤い星は、とても良い引きです。"
CORPUS = [
    "", "   ", JA, JA_WORKNOTE_FALSE_POSITIVE, JA + "\n\n" + JA2, JA + "\n\n\n\n" + JA2,
    "<think>I should answer in Japanese.</think>" + JA,
    "<thinking>\nplan\n</thinking>\n" + JA + "\n<analysis>x</analysis>",
    "reasoning leaked here</think>" + JA,
    JA + "<think>unfinished reasoning",
    "<think>only reasoning</think>",
    "以下、3件のコメント返しです。\n---\n" + JA,
    "コメント返しは完了しました。\n-----\n" + JA,
    "同志Aさん、以下の返信です\n---\n" + JA,
    JA + "\n---\n" + JA2,
    "The work indicator script is failing due to sandbox network restrictions.\n\nLet me craft a reply.\n\n" + JA,
    "Let me think.\n\n" + JA + "\n\n===SING===\n{\"notes\": []}",
    "English only paragraph.\n\nAnother English paragraph.",
    "Invalid bearer token", "API Error: 500 upstream", JA + "\nrate limit exceeded",
    JA + "\nError: File not found: /tmp/x",
    "✗ read failed foo.txt\n" + JA, "→ Read file.txt\n" + JA,
    "assistant\n" + JA, "model: deepseek\n" + JA, "```\n" + JA + "\n```",
    "同志Aさん、という質問ですね。\n" + JA,
    "同志ボブという話ですね。\n" + JA,
    "返信対象コメント: xxx\n" + JA,
    "まずコメントを読み上げます\n" + JA,
    JA + "\nhttps://example.com/a\n" + JA2,
    JA + " 詳しくは https://example.com を。",
    "我们今天讨论这个问题非常重要的内容。\n" + JA,
    JA + "我们今天讨论这个问题非常重要。",
    "WebFetch https://x\n" + JA,
    JA + "#タグ ＃全角",
    "誰も聞いていないけど、" + JA,
    "ボブさん、" + JA2,
    "みなさん、" + JA2,
    "同志ボブ、それは良い質問です",
    "はい",
    "これは句読点のない非常に長い日本語の文章でありまして途中に読点が全く無いまま最後まで続いてしまいます。",
    "OK!", "tool_call: search", "具体的な質問を教えてください。",
    JA + "\n\n" + "I can use the WebFetch tool to check.",
    "  " + JA + "   \n\n  \n" + JA2 + "  ",
    JA + "\r\n\r\n" + JA2 + "\r",
]
TEXT_FUNCTIONS = ["_comment_strip_worknote_head", "_comment_strip_reasoning_tags",
                  "_comment_strip_nonjapanese_head", "_sanitize_onair_text"]


def run(sn, commit, out_path, docich_root):
    env = {"DOCICH_BIN": str(docich_root / "bin/docich")}
    cases = []
    for text in CORPUS:
        case = {"input": text}
        for fn in TEXT_FUNCTIONS:
            case[fn] = bash(fn, sn, env=env, stdin=text)
        case["_comment_guard_model_text"] = bash('_comment_guard_model_text "$1"', sn, env=env, args=(text,))
        case["_comment_guard_japanese_text"] = bash('_comment_guard_japanese_text "$1"', sn, env=env, args=(text,))
        for preserve in ("0", "1"):
            case[f"_clean_comment_talk_{preserve}"] = bash('_clean_comment_talk "$1" "$2"', sn, env=env,
                                                          args=(text, preserve))
        for fn in ("_contains_provider_error_text", "_is_valid_comment_talk",
                   "_comment_is_valid_generation_candidate"):
            case[fn] = bash(f'if {fn} "$1"; then printf 0; else printf 1; fi', sn, env=env, args=(text,))
        cases.append(case)
    golden = {
        "provenance": {
            "source": "azumag/soviet_now comment output guard/validation as loaded by eloop_lib.sh "
                      "(see scripts/golden/comment_guard_golden.py)",
            "soviet_now_commit": commit,
            "docich_bin": "this checkout's bin/docich (DOCICH_BIN, as on the VM)",
            "generated_by": "executing the legacy functions on Linux; text = stdout, predicates = 0/1 exit",
            "purpose": "docich.comment.guard must reproduce these (#829 PR-3b)",
        },
        "cases": cases,
    }
    out_path.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({len(cases)} cases) from soviet_now {commit[:8]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--soviet-now", type=Path, required=True)
    parser.add_argument("--commit", help="for a plain `git archive` export")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    checkout = args.soviet_now.resolve()
    with tempfile.TemporaryDirectory(prefix="legacy-soviet-now-") as export:
        if args.commit:
            commit = args.commit
            shutil.copytree(checkout, export, dirs_exist_ok=True)
        else:
            commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
            archive = subprocess.run(["git", "-C", str(checkout), "archive", commit],
                                     check=True, capture_output=True).stdout
            subprocess.run(["tar", "-x", "-C", export], input=archive, check=True)
        run(Path(export), commit, args.out, ROOT)


if __name__ == "__main__":
    main()
