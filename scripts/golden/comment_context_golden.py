#!/usr/bin/env python3
"""Pin PR-3d contexts by executing the legacy soviet_now shell functions.

    python3 scripts/golden/comment_context_golden.py --soviet-now games/soviet_now

The fixture inputs are synthetic and contain no production state or secrets.
On Linux, CI regenerates this file against the checked-out soviet_now gitlink.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests/fixtures/comment_context_golden.json"
FILES = {
    "batch.txt": "alice: なるほど！\nbob: そうなんだ\ncarol: なるほど！\n",
    "history/20260920_120000_main.txt": "迷路の出口について説明しました。\n✗ read failed ignored\n",
    "history/20260921_120000_main.txt": "NetHack の迷路を案内しました。\nerror: do not include\n",
    "history/20260921_120001_soren91.txt": "対戦版は順位で振り返ります。\n",
    "comments/comment_current.txt": "今の返答では迷路の出口を説明しました。\n",
    "comments/comment_current.mode": "main\n",
    "tmp/.say_queue/current_source": "reply|playing|@ROOT@/comments/comment_current.txt\n",
    "advice.txt": "古い助言\n（なし）\n  \n新しい助言\n",
    "advice_comments.txt": (
        "alice: [soren91] 盤面 typeA の配置を改善して\n"
        "bob: コメント返しは短めにして\n"
        "carol: show-status のログを監視して\n"
        "dave: permission denied while reading\n"
        "erin: [main] typeB を右に置いて\n"
    ),
    "radio_topics.txt": (
        "[12:00] Game#4 [strategy]: RAW_TOPIC_PAYLOAD\n"
        "[12:05] Game#5 [news]: 記事本文は保存しない\n"
        "malformed row\n"
        "[12:10] Game#6 [new_corner]: payload omitted\n"
    ),
    "game_state.json": json.dumps({
        "state": "MOVE", "score": 42, "record": 120, "makeSorenCount": 3,
        "pieceCount": 8, "pieces": [{"type": 5}, {"type": "5"}, {"type": 2}, {"type": "x"}],
        "next": {"type": 4}, "nextNext": {"type": 6},
    }, ensure_ascii=False) + "\n",
    "score_history.txt": "game\tscore\n" + "".join(f"{i}\t{i * 10}\n" for i in range(1, 16)),
    "russia.tsv": (
        "2026-09-01T00:00:00Z\t9/1\t2\t200\t80\n"
        "2026-09-10T00:00:00Z\t9/10\t12\t400\t90\n"
    ),
    "soviet.tsv": "2026-09-11T00:00:00Z\t9/11\t13\t500\t100\n",
    "archive.tsv": (
        "2026-08-20T00:00:00Z\t8/20\t1\t100\t40\n"
        "2026-09-10T00:00:00Z\t9/10\t12\t400\t88\n"
    ),
    ".env": (
        "SOREN91_ENABLED=0\nSOREN91_DAILY_ENABLED=1\n"
        "API_KEY=synthetic-secret-marker-never-retain\n"
    ),
    "canonical.json": json.dumps({"active": {"game": "sorengame"}}) + "\n",
    "lifecycle/request.json": json.dumps({"request_id": "fixture-1"}) + "\n",
    "lifecycle/ack.json": json.dumps({"status": "boundary"}) + "\n",
    "game_count.txt": "88\n",
    "prediction.paused": "",
    "work.json": json.dumps({"active": True, "title": "fixture task", "body": "synthetic progress"}) + "\n",
    "improve.json": json.dumps({"status": "running", "phase": "fixture phase"}) + "\n",
    "ab.json": json.dumps({"games_recorded": 3}) + "\n",
    "ops_brief.md": "# heading\n- fixture first\n* fixture second\nnot a secret\n",
}
MTIMES = {
    "history/20260920_120000_main.txt": 1790000000,
    "history/20260921_120000_main.txt": 1790003600,
    "history/20260921_120001_soren91.txt": 1790007200,
    "comments/comment_current.txt": 1790010800,
}


def write_inputs(root: Path) -> None:
    for relative, content in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.replace("@ROOT@", str(root)), encoding="utf-8")
    for relative, timestamp in MTIMES.items():
        os.utime(root / relative, (timestamp, timestamp))


def env_for(root: Path) -> dict[str, str]:
    def p(value: str) -> str:
        return str(root / value)

    return {
        "ELOOP_LIB_DIR": str(root),
        "COMMENT_SPOKEN_HISTORY_DIR": p("history"),
        "COMMENT_SPOKEN_PROMPT_ITEMS": "3",
        "COMMENT_SPOKEN_PROMPT_MAX_CHARS": "500",
        "COMMENT_SPOKEN_ITEM_MAX_CHARS": "100",
        "COMMENT_CELEBRATION_HISTORY_ITEMS": "2",
        "COMMENT_OPS_CONTEXT_MAX_CHARS": "1200",
        "COMMENT_OPS_BRIEF_ITEMS": "2",
        "GAME_STATE": p("game_state.json"),
        "TMP_HISTORY_DIR": "history",
        "RUSSIA_CREATION_HISTORY_FILE": p("russia.tsv"),
        "SOVIET_CREATION_HISTORY_FILE": p("soviet.tsv"),
        "SOVIET_CREATION_ARCHIVE_FILE": p("archive.tsv"),
        "MIN_GAMES_BEFORE_IMPROVE": "12",
        "AB_STATE_FILE": p("ab.json"),
        "PREDICTION_WORKER_PAUSED_FILE": p("prediction.paused"),
        "DOCICH_GAME_SWITCH_CANONICAL_FILE": p("canonical.json"),
        "SOREN_GAME_LIFECYCLE_DIR": p("lifecycle"),
        "SOREN_GAME_STATE_FILE": p("game_state.json"),
        "SOREN_GAME_COUNT_FILE": p("game_count.txt"),
        "SOREN_IMPROVE_PAUSED_FILE": p("improve.paused"),
        "CODEX_WORK_OVERLAY_STATE_FILE": p("work.json"),
        "IMPROVE_STATE_FILE": p("improve.json"),
        "COMMENT_OPS_BRIEF_FILE": p("ops_brief.md"),
        "PAST_RADIO_TOPICS": p("radio_topics.txt"),
        "RADIO_PAST_TOPICS_LIMIT": "3",
    }


def legacy_call(sn: Path, root: Path, command: str, env: dict[str, str], args=(), stdin=None) -> str:
    exports = "".join(f"export {key}={shlex.quote(value)}; " for key, value in env.items())
    prelude = (
        "source ./eloop_lib.sh >/dev/null 2>&1; log(){ :; }; "
        + exports
        + 'cd "$1"; shift; '
    )
    process = subprocess.run(
        ["bash", "-c", prelude + command, "legacy", str(root), *map(str, args)],
        cwd=sn,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
             "LC_ALL": "C.UTF-8", "TZ": "UTC", "HOME": str(root)},
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if process.returncode:
        raise RuntimeError(f"legacy command failed ({command[:70]}): {process.stderr[-500:]}")
    return process.stdout


def generate(sn: Path, commit: str, out_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="comment-context-inputs-") as temp:
        root = Path(temp)
        write_inputs(root)
        env = env_for(root)
        outputs = {
            "batch": legacy_call(sn, root, "_format_comment_batch_context", env,
                                 stdin="alice: one\nbob: two\nalice: three\n"),
            "recent_main": legacy_call(sn, root, 'printf %s "$(_build_recent_spoken_comment_context main)"', env),
            "recent_soren91": legacy_call(sn, root, 'printf %s "$(_build_recent_spoken_comment_context soren91)"', env),
            "followup_main": legacy_call(sn, root,
                                         'printf %s "$(_build_comment_followup_hints "$1" main)"', env,
                                         (root / "batch.txt",)),
            "followup_soren91": legacy_call(sn, root,
                                            'printf %s "$(_build_comment_followup_hints "$1" soren91)"', env,
                                            (root / "batch.txt",)),
            "advice_tail": legacy_call(sn, root,
                                       'printf %s "$(_read_advice_context_tail "$1" 4)"', env,
                                       (root / "advice.txt",)),
            "structured_advice": legacy_call(sn, root,
                                             'printf %s "$(_extract_structured_advice_from_comments "$1" main)"', env,
                                             (root / "advice_comments.txt",)),
            "past_topics": legacy_call(sn, root, 'printf %s "$(_radio_past_topics_block)"', env),
            "game_main": legacy_call(sn, root,
                                     'printf %s "$(_build_comment_game_context "$1")"', env,
                                     (root / "game_state.json",)),
            "ops_main": legacy_call(sn, root, 'printf %s "$(_build_comment_ops_context main)"', env),
            "ops_soren91": legacy_call(sn, root, 'printf %s "$(_build_comment_ops_context soren91)"', env),
            "celebration": legacy_call(sn, root,
                                       'printf %s "$(_build_comment_celebration_history_context)"', env),
        }

        # Pin the lifecycle's stopped branch and its final-game score wording too.
        (root / "canonical.json").write_text("{}\n", encoding="utf-8")
        (root / "lifecycle/ack.json").write_text('{"status":"stopped"}\n', encoding="utf-8")
        (root / "improve.paused").touch()
        outputs["ops_stopped"] = legacy_call(
            sn, root, 'printf %s "$(_build_comment_ops_context main)"', env)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provenance": {
                "generator": "scripts/golden/comment_context_golden.py",
                "soviet_now_commit": commit,
                "platform": platform.platform(),
            },
            "inputs": {"files": FILES, "mtimes": MTIMES},
            "outputs": outputs,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--soviet-now", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    checkout = args.soviet_now.resolve()
    with tempfile.TemporaryDirectory(prefix="legacy-soviet-now-") as temp:
        commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        archive = subprocess.run(["git", "-C", str(checkout), "archive", commit],
                                 check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", temp], input=archive, check=True)
        generate(Path(temp), commit, args.out)


if __name__ == "__main__":
    main()
