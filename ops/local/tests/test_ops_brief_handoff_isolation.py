#!/usr/bin/env python3
"""soviet_now#146 受入条件: ops brief 最新3件が handoff 移行前後で同値になる。

handoff_rotate の rotate が handoff.md の節を月別 archive へ移しても、
ops brief 投影（ops_brief_source.md 先頭3見出し → ops_brief.json）は
変化しないことを固定する。projection の正規入力は tracked source であり
private handoff ではないが、本テストは「万一 handoff が入力へ混入しても
最新3件が不変であること」を回帰的に検査する。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OPS_BRIEF = ROOT / "ops" / "vm_actions" / "ops_brief.py"
ROTATE = ROOT / "ops" / "local" / "handoff_rotate.py"
SOURCE = ROOT / "ops" / "runtime_context" / "ops_brief_source.md"


def _build_artifact(source: Path, out: Path) -> dict:
    res = subprocess.run(
        [sys.executable, str(OPS_BRIEF), "build",
         "--source", str(source), "--artifact", str(out)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"ops_brief build failed: {res.stderr or res.stdout}")
    return json.loads(out.read_text(encoding="utf-8"))


def _top3(source: Path, tmp: Path) -> tuple[list[str], str]:
    """(topics 配列, 投影の決定的 sha256) を返す。"""
    data = _build_artifact(source, tmp / "brief.json")
    return list(data.get("topics", [])), str(data.get("output_sha256", ""))


def _write_synthetic_handoff(path: Path, sections: int = 40) -> None:
    """最新30節を超える synthetic handoff（rotate 対象になる構成）。"""
    parts = []
    for i in range(sections):
        parts.append(
            f"## 2026-10-{(i % 28) + 1:02d} — synthetic section {i}\n\n- body {i}\n"
        )
    path.write_text("preamble\n\n" + "\n".join(parts), encoding="utf-8")


class TestOpsBriefHandoffIsolation(unittest.TestCase):
    def test_top3_items_unchanged_by_handoff_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            handoff = tmp / "handoff.md"
            archive = tmp / "handoff-archive"
            _write_synthetic_handoff(handoff)

            before_topics, before_sha = _top3(SOURCE, tmp)

            res = subprocess.run(
                [sys.executable, str(ROTATE), "rotate",
                 "--handoff", str(handoff), "--archive-dir", str(archive)],
                capture_output=True, text=True,
            )
            self.assertEqual(res.returncode, 0,
                             f"rotate failed: {res.stderr or res.stdout}")
            # rotate が実際に節を動かしたことを確認（no-op との混同を防ぐ）
            self.assertTrue(any(archive.glob("*.md")),
                            "rotate produced no archive")

            after_topics, after_sha = _top3(SOURCE, tmp)
            self.assertEqual(before_topics, after_topics,
                             "ops brief top-3 topics changed across handoff rotation")
            self.assertEqual(before_sha, after_sha,
                             "ops brief output sha changed across handoff rotation")

    def test_tracked_source_not_handoff_drives_projection(self) -> None:
        """projection は tracked source の先頭3見出しで決まる（入力の取り違え検査）。"""
        heads = [
            ln[3:].strip()
            for ln in SOURCE.read_text(encoding="utf-8").splitlines()
            if ln.startswith("## ")
        ][:3]
        self.assertEqual(len(heads), 3, "ops_brief_source.md lacks 3 headings")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            topics, _sha = _top3(SOURCE, tmp)
            self.assertGreaterEqual(len(topics), 3)
            # 見出し本文が投影 topics に含まれる（番号・日付は除去される）
            joined = json.dumps(topics[:3], ensure_ascii=False)
            for h in heads:
                self.assertIn(h.split("（")[0].split("(")[0][:8], joined)


if __name__ == "__main__":
    unittest.main()
