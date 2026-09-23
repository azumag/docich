"""docich-owned comment state (#829 PR-3c): replays the legacy scenario pinned by
tests/fixtures/comment_state_golden.json (generated on Linux with a fixed clock)
step by step against the same on-disk formats. No network, no secrets."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.comment import state  # noqa: E402

GOLDEN = json.loads((ROOT / "tests/fixtures/comment_state_golden.json").read_text(encoding="utf-8"))
TRACKED = [
    "tmp/.comment_queue/processed_batch_hashes.log",
    "tmp/.comment_queue/inflight_batch.log",
    "tmp/.comment_queue/processed_line_hashes.log",
    "tmp/state/comment_generation_backoff_until",
    "tmp/.comment_queue/spoken_history/.reply_hashes",
]


def _snapshot(root: Path):
    files = {rel: (root / rel).read_text(encoding="utf-8") if (root / rel).is_file() else None for rel in TRACKED}
    spoken = root / "tmp/.comment_queue/spoken_history"
    files["spoken_history_txt"] = sorted(p.read_text(encoding="utf-8") for p in spoken.glob("*.txt")) \
        if spoken.is_dir() else []
    files["spoken_history_modes"] = sorted(p.name.rsplit("_", 1)[-1] for p in spoken.glob("*.txt")) \
        if spoken.is_dir() else []
    history = root / "tmp/history/comment_generation_history.jsonl"
    files["generation_history"] = [
        {k: v for k, v in json.loads(line).items() if k != "generated_at"}
        for line in history.read_text(encoding="utf-8").splitlines()] if history.is_file() else []
    return files


def _rc(ok: bool) -> int:
    return 0 if ok else 1


def _replay(st: state.CommentState, root: Path, op: str, args: list, now: int):
    """(stdout, rc) exactly as the legacy step printed/returned it."""
    if op in ("write", "write_append"):
        path = root / args[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a" if op == "write_append" else "w", encoding="utf-8") as stream:
            stream.write(args[1])
        return "", 0
    if op == "touch":
        (root / args[0]).parent.mkdir(parents=True, exist_ok=True)
        (root / args[0]).touch()
        return "", 0
    if op == "hash":
        return state.hash_text(args[0]) + "\n", 0
    if op == "dedup_key":
        return state.dedup_key(args[0]), 0
    if op == "backoff_remaining":
        return f"{st.failure_backoff_remaining(now)}\n", 0
    if op == "backoff_set":
        st.failure_backoff_set(args[0], now)
        return "", 0
    if op == "backoff_clear":
        st.failure_backoff_clear()
        return "", 0
    if op == "handle_failure":
        st.handle_generation_failure(args[0] == "true", now)
        return "", 0
    if op == "recent_processed":
        return "", _rc(st.is_recent_batch_processed(args[0], now))
    if op == "mark_processed":
        st.mark_batch_processed(args[0], now)
        return "", 0
    if op == "inflight":
        return "", _rc(st.is_batch_inflight(args[0], now))
    if op == "mark_inflight":
        st.mark_batch_inflight(args[0], args[1], now)
        return "", 0
    if op == "clear_inflight":
        st.clear_batch_inflight(args[0])
        return "", 0
    if op == "filter_lines":
        return st.filter_already_processed_lines(args[0], now), 0
    if op == "has_line":
        return "", _rc(st.has_processed_line(args[0], now))
    if op == "record_lines":
        st.record_processed_lines(args[0], now)
        return "", 0
    if op == "format_context":
        return state.format_batch_context(args[0] + "\n"), 0
    if op == "needs_thumbnail":
        return "", _rc(state.needs_thumbnail_context(args[0]))
    if op == "backlog":
        return "{} {}\n".format(*st.backlog_counts()), 0
    if op == "backlog_high":
        return "", _rc(st.is_backlog_high(int(args[0]), args[1]))
    if op == "remember":
        st.remember_reply_text(args[0], args[1], now=now)
        return "", 0
    if op == "store_meta":
        payload = st.store_generation_meta(args[0], mode=args[1], model=args[2], batch_hash=args[3],
                                           attempt=args[4], chars=args[5], primary=args[6],
                                           secondary=args[7], tertiary=args[8])
        payload.pop("generated_at")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True), 0
    raise AssertionError(op)


def test_golden_is_from_a_real_legacy_run():
    assert len(GOLDEN["provenance"]["soviet_now_commit"]) == 40
    assert len(GOLDEN["steps"]) >= 90


def test_scenario_replays_step_by_step(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(__import__("time"), "tzset"):
        __import__("time").tzset()
    st = state.CommentState(tmp_path, env=dict(GOLDEN["effective_env"]))
    for index, step in enumerate(GOLDEN["steps"]):
        stdout, rc = _replay(st, tmp_path, step["op"], step["args"], step["now"])
        where = f"step {index} {step['op']} {step['args']!r:.60}"
        assert (stdout, rc) == (step["stdout"], step["rc"]), where
        assert _snapshot(tmp_path) == step["files"], where


def test_a_live_pid_keeps_the_batch_inflight(tmp_path):
    st = state.CommentState(tmp_path, env={})
    st.mark_batch_inflight("h", os.getpid(), now=100)
    assert st.is_batch_inflight("h", now=150)
    assert st.inflight_file.exists()


def test_sidecar_paths():
    assert state.meta_sidecar_path("q/comment_1.txt") == "q/comment_1.meta.json"
    assert state.meta_sidecar_path("q/comment_1.playing") == "q/comment_1.meta.json"
    assert state.viewer_memory_sidecar_path("q/x") == "q/x.viewer_memory.json"
    assert state.batch_metadata_path("b.txt") == "b.txt.viewer_meta.jsonl"


def test_golden_regenerates_identically_on_this_linux_host():
    """CI (Ubuntu, submodule): regenerate against the checked-out soviet_now and
    require identical steps (provenance commit aside), so a legacy change to
    these helpers fails here until the port follows."""
    sn = ROOT / "games/soviet_now"
    if not (sn / "eloop_lib.sh").is_file() or sys.platform != "linux":
        pytest.skip("needs the soviet_now checkout on Linux (GNU tools, like production)")
    out = Path(subprocess.check_output(["mktemp"], text=True).strip())
    subprocess.run([sys.executable, str(ROOT / "scripts/golden/comment_state_golden.py"),
                    "--soviet-now", str(sn), "--out", str(out)], check=True, capture_output=True)
    fresh = json.loads(out.read_text(encoding="utf-8"))
    fresh["provenance"].pop("soviet_now_commit")
    pinned = json.loads(json.dumps(GOLDEN))
    pinned["provenance"].pop("soviet_now_commit")
    assert fresh == pinned
