#!/usr/bin/env python3
"""Build the ``bench/jev_eval_v1`` public eval suite for the Jev LLM benchmark.

Input is a raw pair file produced by :mod:`extract_live_logs` (one JSON object
per line with ``batch_line``/``comment``/``category``) plus the curated and
synthetic coverage cases embedded below. The output is a suite directory in the
same ``docich.eval.*`` contract shape as ``evals/comment/v1`` so the existing
offline runner / graders / reports can read it unchanged.

Two guarantees are enforced here, not assumed:

* every emitted case passes ``docich.eval.contracts.validate_case`` and
  ``assert_public_safe`` (so no secret / private path / private IP can be
  committed), and
* the batch line is projected exactly like production: the model never sees a
  username, only the comment body (``comment_classifier.heuristic.split_line``),
  and URLs / @handles / secrets are replaced with markers.

Usage::

    python3 bench/tools/build_jev_suite.py \\
        --input /tmp/jev_live_pairs.jsonl \\
        --out bench/jev_eval_v1

The raw pair file is *not* committed: it is production-derived and stays
outside Git (comment-eval.md section 2). Only this projected, reviewed suite is.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from docich.comment_classifier import heuristic, jev  # noqa: E402
from docich.eval import contracts, corpus  # noqa: E402

CATEGORIES = tuple(jev.CRITERIA)

# Salt for the irreversible viewer token. Not a secret: it only stops the same
# handle in two suites from being trivially cross-linked; it is documented.
BENCH_SALT = "docich-bench-jev-live-v1"
RUBRIC_VERSION = "jev-category-v1"
MAX_COMMENT_CHARS = contracts.MAX_COMMENT_CHARS

NOTIFICATION_CATEGORIES = (
    "card_gacha", "raid", "subscription", "stream_goal", "bits")

# Per-label cap for stratified sampling so the live window's card_gacha /
# chitchat volume cannot drown the rare intent labels.
CAPS = {
    "card_gacha": 12, "raid": 4, "subscription": 2, "stream_goal": 2,
    "bits": 2, "sing_request": 4, "game_question": 6, "game_status": 15,
    "general_question": 8, "strategy_advice": 8, "comment_advice": 6,
    "stream_bug_report": 12, "chitchat": 25, "other": 20,
}

INTENT_FAMILY = {
    "card_gacha": "notification", "raid": "notification",
    "subscription": "notification", "stream_goal": "notification",
    "bits": "notification", "sing_request": "advice",
    "game_question": "game", "game_status": "game",
    "strategy_advice": "game", "general_question": "question",
    "comment_advice": "advice", "stream_bug_report": "stream_ops",
    "chitchat": "chitchat", "other": "other",
}

# Hand-authored cases carried over from the #829 PR-0 evaluation fixture
# (soviet_now branch codex/issue-829-pr0b-advice). Kept as ``curated`` origin so
# the live-log rows and the hand-written rows stay distinguishable.
CURATED = [
    ("BGM聞こえない？", "stream_bug_report", ["audio"]),
    ("画面が固まってる", "stream_bug_report", ["screen", "frozen"]),
    ("コメントへの返答が出てない", "stream_bug_report", ["reply"]),
    ("右に置いた方がよくない？", "strategy_advice", ["deictic", "screen"]),
    ("nextを見て置くべきでは？", "strategy_advice", ["deictic"]),
    ("ゲームのルールを教えて", "game_question", ["rules"]),
    ("スコアが伸びてきたね", "game_status", ["score"]),
    ("きらきら星を歌って", "sing_request", ["song"]),
    ("この歌、誰の曲？", "general_question", ["song", "song-not-request"]),
    ("返答をもう少し短くして", "comment_advice", ["length"]),
    ("Amazing stream!", "chitchat", ["english", "greeting"]),
    ("LUL LUL", "chitchat", ["emote"]),
    ("なるほど", "chitchat", ["short"]),
    ("ソ連はいつ崩壊したの？", "general_question", ["history"]),
    ("それおかしくない？", "other", ["ambiguous"]),
    ("さっきのやつ", "other", ["ambiguous", "deictic"]),
]

# Authored coverage for labels the sampled live window did not contain
# (subscription / stream_goal / bits) plus hard negatives where the surface
# keyword appears but the intent is a notification, not a discussion of one.
# Every row is tagged ``synthetic`` so a reader never mistakes it for a log row.
SYNTHETIC = [
    ("viewer_x が サブスクリプションしました (Tier 1)", "subscription", ["synthetic", "notification"]),
    ("viewer_y が ギフトサブスクを 5 個贈りました", "subscription", ["synthetic", "notification"]),
    ("配信ゴール達成: 新規フォロワー 100 人", "stream_goal", ["synthetic", "notification"]),
    ("ストリーム目標を達成しました!視聴者数 100 人", "stream_goal", ["synthetic", "notification"]),
    ("viewer_z が 100 bits をチアしました", "bits", ["synthetic", "notification"]),
    ("viewer_w cheered 500 bits", "bits", ["synthetic", "notification"]),
    ("サブスクの話だけど、解約するか迷ってる", "chitchat", ["synthetic", "notification-hard-negative"]),
    ("レイドありがとうございました、また来ます", "chitchat", ["synthetic", "notification-hard-negative"]),
    ("歌ってくれる?", "sing_request", ["synthetic", "song"]),
    ("このゲームのルールどうなってるの?", "game_question", ["synthetic", "rules"]),
    ("why did the piece disappear?", "game_question", ["synthetic", "english"]),
    ("うわ、また負けた", "game_status", ["synthetic", "short"]),
    ("つぎはこっちをまもって", "strategy_advice", ["synthetic", "typo", "deictic"]),
    ("同志おつかれさまです", "chitchat", ["synthetic", "greeting"]),
    ("", None, []),  # placeholder removed below
]
SYNTHETIC = [row for row in SYNTHETIC if row[1]]


def _load_pairs(paths):
    rows = []
    for path in paths:
        with Path(path).open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _live_text(row):
    """The text the classifier model would see for one live log row."""
    batch_line = str(row.get("batch_line") or row.get("comment") or "")
    comment = str(row.get("comment") or "")
    # Card / multi-gacha notifications are matched on the whole raw line in the
    # heuristic, so keep the whole line rather than the (often mangled) split
    # body. Everything else is projected to the comment body only.
    if heuristic.CARD_ACQUIRED_RE.search(batch_line) or heuristic.CARD_MULTI_RE.search(batch_line):
        return redact_handles(batch_line)
    return redact_handles(comment or batch_line)


# ``sanitize_text`` only understands ASCII ``@[A-Za-z0-9_]`` handles, so a
# Japanese display name (``@もやしちゃん``) would survive it untouched. Redact
# every @-token here, ASCII or not, before the projection runs.
_HANDLE_RE = re.compile(r"@[^\s@、。!?！？]{1,32}")


def redact_handles(text):
    return _HANDLE_RE.sub("[user]", text)


def _group_for(source):
    digest = hashlib.sha256(str(source or "live").encode("utf-8")).hexdigest()[:10]
    return "jev-live-" + digest


def build_cases(pairs):
    """Return the ordered list of projected cases for the suite."""
    selected = collections.defaultdict(list)
    seen = set()
    for row in pairs:
        category = str(row.get("category") or "")
        if category not in CATEGORIES:
            continue
        text = corpus.sanitize_text(_live_text(row), salt=BENCH_SALT)
        if not text:
            continue
        if len(text) > MAX_COMMENT_CHARS:
            continue  # outside the eval contract; drop rather than truncate
        key = (text, category)
        if key in seen:
            continue
        seen.add(key)
        selected[category].append({
            "text": text,
            "category": category,
            "origin": "live-log",
            "tags": ["live-log"]
                    + (["notification"] if category in NOTIFICATION_CATEGORIES else [])
                    + ([] if str(row.get("user") or "").strip() else ["system-announcement"]),
            "group": _group_for(row.get("source")),
        })

    # Deterministic, even spread per label: order by a stable hash of the body
    # then take up to the label cap.
    for category, items in selected.items():
        items.sort(key=lambda item: hashlib.sha256(item["text"].encode("utf-8")).hexdigest())
        selected[category] = items[:CAPS.get(category, 10)]

    ordered = []
    for category in CATEGORIES:
        ordered.extend(selected.get(category, []))
    for text, category, tags in CURATED:
        ordered.append({"text": text, "category": category, "origin": "curated-829",
                        "tags": ["curated"] + list(tags), "group": "jev-curated"})
    for index, (text, category, tags) in enumerate(SYNTHETIC, 1):
        ordered.append({"text": text, "category": category, "origin": "synthetic-coverage",
                        "tags": list(tags), "group": f"jev-synthetic-{index:02d}"})
    return ordered


def to_case(index, row):
    return {
        "schema": contracts.CASE_SCHEMA,
        "case_id": f"jev-{index:04d}",
        "group_id": row["group"],
        "input": {
            "comment": row["text"],
            "active_game_hint": None,
            "host_mode": "main",
            "image_attached": False,
        },
        "expected": {
            "category": row["category"],
            "intent_family": INTENT_FAMILY[row["category"]],
        },
        "tags": sorted({"origin:" + row["origin"]} | set(row["tags"])),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True,
                        help="raw pair JSONL from extract_live_logs (repeatable)")
    parser.add_argument("--out", required=True, help="suite output directory")
    args = parser.parse_args(argv)

    rows = build_cases(_load_pairs(args.input))
    cases = []
    critical = []
    counts = collections.Counter()
    origins = collections.Counter()
    for index, row in enumerate(rows, 1):
        case = contracts.validate_case(to_case(index, row))
        contracts.assert_public_safe(case)
        counts[case["expected"]["category"]] += 1
        origins[row["origin"]] += 1
        (critical if "boundary" in row["tags"] or "ambiguous" in row["tags"] else cases).append(case)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    corpus.write_jsonl(out / "public_cases.jsonl", cases)
    corpus.write_jsonl(out / "critical_cases.jsonl", critical)
    manifest = {
        "schema": contracts.SUITE_SCHEMA,
        "suite": "jev-live-v1",
        "seed": 33,
        "ratios": dict(contracts.DEFAULT_RATIOS),
        "rubric_version": RUBRIC_VERSION,
        "grader_versions": {"deterministic": "deterministic-v1", "classifier": "classifier-v1"},
        "public_cases": "public_cases.jsonl",
        "critical_cases": "critical_cases.jsonl",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    (out / "rubric.json").write_text(json.dumps({
        "version": RUBRIC_VERSION,
        "description": "Jev comment-category rubric (reused from docich.comment_classifier.jev.CRITERIA).",
        "labels": {name: text for name, text in jev.CRITERIA.items()},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"suite={out} cases={len(cases)} critical={len(critical)} total={len(cases) + len(critical)}")
    print("origin:", dict(origins))
    for label in jev.CRITERIA:
        print(f"  {counts.get(label, 0):3d}  {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
