"""入力タイムライン (左右ジェットの切替時刻列) の記録・再生形式。

タイムラインは「試行開始 (最初の入力) からの経過 ms と、その時点以降の左右の状態」の列。
物理が決定的 (フレームレート非依存) なら開ループ再生で同じ結果になるはずなので、
人間のクリア操作や制御器の成功操作を保存しておけば配信向けに確実な再現ができる。
決定性は未検証 (実機で replay を複数回回して確認する)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Timeline:
    level: int | None = None
    events: list[tuple[int, bool, bool]] = field(default_factory=list)  # (t_ms, left, right)
    meta: dict = field(default_factory=dict)

    def add(self, t_ms: int, left: bool, right: bool) -> None:
        if self.events and (self.events[-1][1], self.events[-1][2]) == (left, right):
            return
        if not self.events and not (left or right):
            return  # 最初の入力までの待ちは記録しない
        if self.events and t_ms < self.events[-1][0]:
            raise ValueError("timeline must be monotonic")
        self.events.append((int(t_ms), bool(left), bool(right)))

    def state_at(self, t_ms: float) -> tuple[bool, bool]:
        state = (False, False)
        for t, left, right in self.events:
            if t > t_ms:
                break
            state = (left, right)
        return state

    @property
    def duration_ms(self) -> int:
        return self.events[-1][0] if self.events else 0

    def to_json(self) -> dict:
        return {"level": self.level, "meta": self.meta, "events": [[t, int(l_), int(r)] for t, l_, r in self.events]}

    @classmethod
    def from_json(cls, data: dict) -> "Timeline":
        return cls(data.get("level"), [(int(t), bool(l_), bool(r)) for t, l_, r in data.get("events", [])], data.get("meta", {}))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), ensure_ascii=False) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Timeline":
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))
