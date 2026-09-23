#!/usr/bin/env python3
"""Regenerate tests/fixtures/comment_prompt_golden.json (#829 PR-3a).

Executes the *legacy* soviet_now comment prompt code (functions as loaded by
the production ``eloop_lib.sh`` -- so ``comment_runtime_policy.sh``'s wrappers
apply -- the inline python blocks of
``generate_comment_response`` extracted verbatim, and ``envsubst`` on the
real templates) for fixed synthetic inputs, and records its stdout. The
docich native prompt layer (``docich.comment.prompt``) must reproduce these
bytes. Needs bash, python3 and GNU ``envsubst``; no network, no secrets.

    python3 scripts/golden/comment_prompt_golden.py --soviet-now games/soviet_now
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
from pathlib import Path
import subprocess
import tempfile

OUT = Path(__file__).resolve().parents[2] / "tests/fixtures/comment_prompt_golden.json"

CATEGORIES = ["card_gacha", "raid", "subscription", "stream_goal", "bits", "sing_request",
              "game_question", "game_status", "general_question", "strategy_advice",
              "comment_advice", "stream_bug_report", "chitchat", "other"]

# Every name either template allowlist can substitute (category + mixed).
CONTEXT_NAMES = [
    "_comment_persona", "current_time", "time_period", "twitch_comments_for_prompt",
    "comment_batch_context", "strategy_advice_candidates", "comment_advice_candidates",
    "codex_advice_candidates", "comment_advice_context", "previous_comments_context",
    "recent_spoken_comment_context", "viewer_memory_context", "comment_followup_hints",
    "past_topics", "celebration_history_context", "comment_thumbnail_ocr_context",
    "PAST_RADIO_TOPICS", "RUSSIA_CREATION_HISTORY_FILE", "SOVIET_CREATION_HISTORY_FILE",
    "ROLLING_SCORES_FILE", "game_state_context", "comment_ops_context", "_comment_ui_memo",
    "_comment_channel_intro", "_comment_length_policy", "sing_reference", "_prediction_cycle_games",
]

COMMENTS_BLOCK = ("viewer_a: 音声が聞こえません\n"
                  "Wizebot: bob が【SR】赤い星 を獲得しました（112 種中 112 種所持）\n"
                  "carol: $current_time の ${time_period} って何？ $$ 100%\n"
                  "dave: hello how are you")
# Full text is kept for two representative prompts (readable diffs); the
# rest are pinned by sha256 + length, which is just as byte-exact.
FULL_TEXT_CASES = {("main", "card_gacha"), ("soren91", "mixed")}
CLASSIFICATIONS = "[1] viewer_a: 音声が聞こえません -> stream_bug_report -> japanese/other"


def context(mode, sn):
    """Synthetic but realistic values; values carry $ forms to pin 'no recursion'."""
    ctx = {name: f"<<{name}>> 行2 $current_time ${{time_period}} ${{UNLISTED}} $$" for name in CONTEXT_NAMES}
    for name, stem in (("_comment_persona", "comment_persona"), ("_comment_ui_memo", "comment_ui_memo"),
                       ("_comment_channel_intro", "comment_channel_intro")):
        ctx[name] = (sn / "prompts" / f"{stem}_{mode}.md").read_text(encoding="utf-8").rstrip("\n")
    ctx.update(current_time="21:07", time_period="夜", _prediction_cycle_games="12",
               twitch_comments_for_prompt=COMMENTS_BLOCK)
    ctx["_comment_length_policy"] = soren91_policies(sn)[0] if mode == "soren91" else ""
    return ctx


def bash(script, sn, env=None, stdin=None, args=()):
    base = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "HOME": os.environ.get("HOME", "/tmp"),
            "ELOOP_LIB_DIR": str(sn)}
    # Load exactly like production (eloop_lib.sh), so later layers such as
    # comment_runtime_policy.sh wrap the comment.sh definitions. The checkout
    # has no .env; nothing is started.
    # core/config.sh assigns some of the same names unconditionally (e.g.
    # GACHA_COMPLETED_USERS_FILE), so the injected values are re-exported
    # after loading; otherwise the legacy would write into the checkout.
    reassert = "".join(f"export {name}={shlex.quote(value)}; " for name, value in (env or {}).items())
    result = subprocess.run(["bash", "-c", 'source ./eloop_lib.sh >/dev/null 2>&1; log(){ :; }; ' + reassert + script,
                             "golden", *args],
                            cwd=sn, env={**base, **(env or {})}, input=stdin, capture_output=True,
                            text=True, timeout=60)
    if result.returncode:
        raise SystemExit(f"legacy call failed ({script[:60]}): {result.stderr[-400:]}")
    return result.stdout


def inline_python(sn, start_marker, end_marker):
    """The verbatim python source of an inline block in generate_comment_response."""
    text = (sn / "broadcast/comment.sh").read_text(encoding="utf-8")
    start = text.index(start_marker) + len(start_marker)
    return text[start:text.index(end_marker, start)]


def soren91_policies(sn):
    """The mode's verbatim $'...' length-policy assignments, evaluated by bash."""
    policy_src = inline_python(sn, '\t\tif [ "$_comment_mode_generated" = "soren91" ]; then\n\t\t\t_comment_length_policy=',
                               "\n\t\tfi\n")
    return json.loads(subprocess.check_output(
        ["bash", "-c", "_comment_length_policy=" + policy_src +
         '\npython3 -c \'import json,sys;print(json.dumps(sys.argv[1:]))\' "$_comment_length_policy" "$_comment_retry_length_policy"'],
        text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--soviet-now", type=Path, required=True)
    checkout = parser.parse_args().soviet_now.resolve()
    commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    # Run on a disposable export of that commit: loading eloop_lib.sh creates
    # tmp/ dirs and the gacha note appends to its list file.
    with tempfile.TemporaryDirectory(prefix="legacy-soviet-now-") as export:
        archive = subprocess.run(["git", "-C", str(checkout), "archive", commit], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", export], input=archive, check=True)
        generate(Path(export), commit)


def generate(sn, commit):

    sanitize_inputs = [
        "", "   \n\n", "普通の行\n  前後空白  \n\n次の行",
        "Error: something broke\n良い行\n✗ read failed foo\n→ Read file.txt\n残す",
        "tool_call を実行\nRate limit exceeded 429\n申し訳ありません、具体的なタスク指示が見当たりません\nOK行",
        "WebFetch でインターネットにアクセスできません\n検索は不要です\nwarning: x",
        "行末空白あり   \n\t タブ行\t",
    ]
    sanitize = [{"input": text, "output": bash('_sanitize_comment_prompt_context', sn, stdin=text)}
                for text in sanitize_inputs]

    dominant_src = inline_python(sn, 'dominant_category=$(python3 -c "\n', '" <<<"$classification_json")')
    formatted_src = inline_python(
        sn, 'formatted_classifications=$(python3 - "$classification_json" "$comment_prompt_batch_file" <<\'PY\' 2>/dev/null\n',
        "\nPY\n")
    def row(i, cat, english=False, user="u", comment="c"):
        return {"index": i, "user": user, "comment": comment, "category": cat, "is_english": english}
    classification_cases = [
        [row(1, "chitchat")],
        [row(1, "raid"), row(2, "raid"), row(3, "raid"), row(4, "raid"), row(5, "raid"), row(6, "chitchat")],
        [row(1, "raid"), row(2, "raid"), row(3, "raid"), row(4, "chitchat")],
        [row(1, "chitchat"), row(2, "game_question")],
        [{"index": 1}, {"index": 2, "category": "sing_request"}],
        [row(1, "card_gacha", True), row(2, "chitchat", True), row(3, "other")],
        [],
    ]
    dominant = []
    for rows in classification_cases:
        result = subprocess.run(["python3", "-c", dominant_src], input=json.dumps(rows), capture_output=True, text=True)
        dominant.append({"classification": rows, "rc": result.returncode, "output": result.stdout})
    english = [{"classification": rows,
                "output": bash('_comment_classification_english_count "$1"', sn, args=(json.dumps(rows),))}
               for rows in classification_cases]

    batch_lines = ["viewer_a: 音声が聞こえません", "no colon line", "carol: a: b: c", "", "  ", "dave: hi"]
    formatted_cases = [
        [row(1, "stream_bug_report"), row(2, "chitchat", True), row(3, "other"), row(4, "chitchat"), row(9, "x")],
        [{"index": "2", "category": "sing_request", "is_english": "true"}, {"index": "x"}, {"category": "raid"}],
    ]
    formatted = []
    with tempfile.TemporaryDirectory() as tmp:
        batch = Path(tmp) / "batch.txt"
        batch.write_text("\n".join(batch_lines) + "\n", encoding="utf-8")
        for rows in formatted_cases:
            result = subprocess.run(["python3", "-", json.dumps(rows), str(batch)], input=formatted_src,
                                    capture_output=True, text=True)
            formatted.append({"classification": rows, "batch_lines": batch_lines, "output": result.stdout})

        gacha_cases = [
            {"block": COMMENTS_BLOCK, "known": ""},
            {"block": COMMENTS_BLOCK, "known": "Bob\n"},
            {"block": "alice: @Zed が 10 種中 10 種所持\nx: Yu が 5種中 3種所持\n[sys]: w が 2 種中 2 種所持\nZED: zed が 3種中3種所持", "known": "someone\n"},
            {"block": "", "known": "a\n"},
        ]
        gacha = []
        for case in gacha_cases:
            listfile = Path(tmp) / "gacha.txt"
            listfile.write_text(case["known"], encoding="utf-8")
            note = bash('_build_gacha_completion_note "$1"; printf %s "$GACHA_COMPLETION_NOTE"', sn,
                        env={"GACHA_COMPLETED_USERS_FILE": str(listfile)}, args=(case["block"],))
            gacha.append({**case, "note": note, "known_after": listfile.read_text(encoding="utf-8")})

        prompts = []
        for mode in ("main", "soren91"):
            ctx = context(mode, sn)
            for category in CATEGORIES + ["mixed"]:
                listfile = Path(tmp) / "gacha_prompt.txt"
                listfile.write_text("", encoding="utf-8")
                out = Path(tmp) / "prompt.txt"
                if category == "mixed":
                    allow = inline_python(sn, "envsubst '${_comment_persona}", "'")
                    allow = "${_comment_persona}" + allow
                    assert allow.split() == ["${" + n + "}" for n in CONTEXT_NAMES], allow
                    script = f'envsubst \'{allow}\' <prompts/comment_template.md >"$1"; _append_comment_reply_contract "$1"'
                    bash(script, sn, env={**ctx}, args=(str(out),))
                else:
                    script = ('_build_category_prompt "$1" "$2" "$3" "$4"; _append_comment_reply_contract "$4"')
                    bash(script, sn, env={**ctx, "GACHA_COMPLETED_USERS_FILE": str(listfile)},
                         args=(category, COMMENTS_BLOCK, CLASSIFICATIONS, str(out)))
                rendered = out.read_bytes()
                case = {"mode": mode, "category": category,
                        "sha256": hashlib.sha256(rendered).hexdigest(), "bytes": len(rendered)}
                if (mode, category) in FULL_TEXT_CASES:
                    case["prompt"] = rendered.decode("utf-8")
                prompts.append(case)

    time_period_src = inline_python(sn, "\tlocal current_time current_hour time_period\n", "\n\tlocal comment_parent_pid")
    periods = {}
    for hour in range(24):
        periods[f"{hour:02d}"] = subprocess.check_output(
            ["bash", "-c", time_period_src.replace("$(date '+%H:%M')", "00:00").replace("$(date '+%H')", f"{hour:02d}")
             .replace("\tlocal current_time current_hour time_period\n", "") + '\nprintf %s "$time_period"'],
            text=True)

    # Verbatim heredocs / policy strings / default resolution of generate_comment_response.
    text = (sn / "broadcast/comment.sh").read_text(encoding="utf-8")
    contract = inline_python(sn, "<<'COMMENTREPLYCONTRACT'\n", "COMMENTREPLYCONTRACT\n")
    policy_text = (sn / "broadcast/comment_runtime_policy.sh").read_text(encoding="utf-8")
    start = policy_text.index("<<'COMMENTRUNTIMEPOLICY'\n") + len("<<'COMMENTRUNTIMEPOLICY'\n")
    policy_contract = policy_text[start:policy_text.index("COMMENTRUNTIMEPOLICY\n", start)]
    retry_addendum = inline_python(sn, "<<'RETRYCOMMENT'\n", "RETRYCOMMENT\n")
    policies = soren91_policies(sn)
    defaults_src = inline_python(sn, "\t\t# Pre-resolve defaults for envsubst\n", "\n\n\t\t# Export all template variables")
    default_names = ["comment_batch_context", "strategy_advice_candidates", "comment_advice_candidates",
                     "codex_advice_candidates", "comment_advice_context", "previous_comments_context",
                     "recent_spoken_comment_context", "viewer_memory_context", "comment_followup_hints",
                     "celebration_history_context", "comment_thumbnail_ocr_context", "game_state_context",
                     "comment_ops_context"]
    defaults = []
    for case in ({}, {name: f"値 {name}\nError: drop me\n残す" for name in default_names}):
        dump = ('python3 -c \'import json,os,sys;print(json.dumps({n: os.environ.get(n) for n in sys.argv[1:]}))\' '
                + " ".join(default_names))
        script = defaults_src + "\nexport " + " ".join(default_names) + "\n" + dump
        out = bash(script, sn, env={k: v for k, v in case.items()})
        defaults.append({"input": case, "output": json.loads(out)})

    golden = {
        "provenance": {
            "source": "azumag/soviet_now broadcast/comment.sh prompt stage (see scripts/golden/comment_prompt_golden.py)",
            "soviet_now_commit": commit,
            "generated_by": "executing the legacy shell functions / verbatim inline python / envsubst; expected = stdout",
            "purpose": "docich.comment.prompt must reproduce these bytes (#829 PR-3a)",
        },
        "comments_block": COMMENTS_BLOCK, "classifications": CLASSIFICATIONS,
        "contexts": {mode: context(mode, sn) for mode in ("main", "soren91")},
        "sanitize": sanitize, "dominant_category": dominant, "english_count": english,
        "formatted_classifications": formatted, "gacha_completion_note": gacha,
        "time_period": periods, "prompts": prompts,
        "reply_contract_heredoc": contract, "runtime_policy_contract_heredoc": policy_contract, "retry_addendum_heredoc": retry_addendum,
        "soren91_length_policy": policies[0], "soren91_retry_length_policy": policies[1],
        "prompt_defaults": defaults,
    }
    OUT.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(prompts)} prompts) from soviet_now {commit[:8]}")


if __name__ == "__main__":
    main()
