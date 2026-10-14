"""Native comment orchestration (#829 PR-3e) smoke tests."""

from __future__ import annotations

import pathlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.comment import intake, pipeline  # noqa: E402
from docich.comment.contracts import FileDeliverySink  # noqa: E402


def test_offline_fixture_completes(tmp_path):
    intake_obj = intake.comments_from_text("alice: hello\nbob: world\n")
    sink = FileDeliverySink(root=tmp_path / "out")
    result = pipeline.run_pipeline(intake_obj, delivery_sink=sink)
    assert result.ok
    assert result.reply_text
    assert len(result.delivery_files) == 3
    for path in result.delivery_files:
        assert pathlib.Path(path).is_file()
    assert "alice: hello" in result.prompt


def test_comments_file_roundtrip(tmp_path):
    src = tmp_path / "batch.txt"
    src.write_text("alice: a\nbob: b\n", encoding="utf-8")
    obj = intake.comments_from_file(src)
    assert [m.author for m in obj.comments] == ["alice", "bob"]
    dry = pipeline.comments_from_text("x: y")
    assert dry.comments[0].text == "y"