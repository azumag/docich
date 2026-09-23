"""docich-owned viewer memory (#829 PR-3c-2): the comment.sh wrappers around the
helper (argv / defaults / output and sidecar handling), pinned by
tests/fixtures/comment_viewer_memory_golden.json, and the helper itself
byte-identical to soviet_now's copy until cutover."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.comment import state, viewer_memory  # noqa: E402

GOLDEN = json.loads((ROOT / "tests/fixtures/comment_viewer_memory_golden.json").read_text(encoding="utf-8"))
SOVIET_NOW = ROOT / "games/soviet_now"


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[f"{i}-{c['op']}" for i, c in enumerate(GOLDEN["cases"])])
def test_wrapper_matches_the_legacy(case, tmp_path, monkeypatch):
    calls = []

    def fake_main(argv):
        calls.append(list(argv))
        if case["stub_out"] is not None:
            print(case["stub_out"])
        raise SystemExit(case["stub_rc"])

    monkeypatch.setattr(viewer_memory, "main", fake_main)
    sidecar = tmp_path / GOLDEN["sidecar"]
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    if case["with_sidecar"]:
        sidecar.write_text("{}", encoding="utf-8")
    st = state.CommentState(tmp_path, env=dict(case["env"]))
    if case["op"] == "context":
        stdout, rc = st.viewer_memory_context("tmp/b.txt", "twitch", "soren91"), 0
    elif case["op"] == "stage":
        stdout, rc = "", 0 if st.stage_viewer_memory(GOLDEN["target"], "tmp/b.txt", "tmp/r.txt",
                                                     "youtube", "main", "h1") else 1
    else:
        stdout, rc = "", 0 if st.commit_viewer_memory(GOLDEN["target"]) else 1
    assert calls == case["calls"]
    assert (stdout, rc) == (case["stdout"], case["rc"])
    assert sidecar.exists() == case["sidecar_after"]


def test_helper_runs_from_the_soren_root(tmp_path):
    """Relative paths in the argv resolve against the Soren root, as in the legacy cwd."""
    st = state.CommentState(tmp_path, env={})
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp/b.txt").write_text("alice: こんにちは\n", encoding="utf-8")
    assert st.viewer_memory_context("tmp/b.txt", "twitch", "main").endswith("\n")


def test_helper_is_byte_identical_to_the_legacy_until_cutover():
    legacy = SOVIET_NOW / "lib/comment_viewer_memory.py"
    if not legacy.is_file():
        pytest.skip("games/soviet_now not checked out")
    assert (ROOT / "src/docich/comment/viewer_memory.py").read_bytes() == legacy.read_bytes()


def test_golden_regenerates_identically_on_this_linux_host():
    if not (SOVIET_NOW / "eloop_lib.sh").is_file() or sys.platform != "linux":
        pytest.skip("needs the soviet_now checkout on Linux (GNU tools, like production)")
    out = Path(subprocess.check_output(["mktemp"], text=True).strip())
    subprocess.run([sys.executable, str(ROOT / "scripts/golden/comment_viewer_memory_golden.py"),
                    "--soviet-now", str(SOVIET_NOW), "--out", str(out)], check=True, capture_output=True)
    fresh = json.loads(out.read_text(encoding="utf-8"))
    fresh["provenance"].pop("soviet_now_commit")
    pinned = json.loads(json.dumps(GOLDEN))
    pinned["provenance"].pop("soviet_now_commit")
    assert fresh == pinned
