from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.corner_script import _chart_text, build_next_prompt, build_prompt  # noqa: E402


_TIMEFRAMES = (
    ("1d", "日足"),
    ("1h", "1時間足"),
    ("15m", "15分足"),
    ("5m", "5分足（公開OHLC）"),
    ("1m", "1分足"),
)


def _view(timeframe: str, label: str) -> dict[str, object]:
    return {
        "timeframe": timeframe,
        "label": label,
        "bars": 24,
        "trend": "横ばい",
        "range_change_pct": 0.2,
        "momentum_pct": 0.1,
        "bb_upper": "110",
        "bb_lower": "90",
        "bb_position": 0.5,
        "bb_phrase": "バンド中央付近",
        "high": "111",
        "low": "89",
        "last_close": "100",
        "stale": False,
    }


def test_fallback_narrates_all_five_public_timeframes_in_order() -> None:
    facts = {"timeframes": [_view(timeframe, label) for timeframe, label in _TIMEFRAMES]}

    text = _chart_text(facts)

    assert "5つの時間足" in text
    positions = [text.index(label) for _, label in _TIMEFRAMES]
    assert positions == sorted(positions)
    assert all(label in text for _, label in _TIMEFRAMES)


def test_fallback_names_all_five_timeframes_when_no_public_candles_exist() -> None:
    text = _chart_text({"timeframes": []})

    assert all(label.replace("（公開OHLC）", "") in text for _, label in _TIMEFRAMES)
    assert "取得が間に合わず" in text


def test_full_and_sequential_chart_prompts_cover_all_five_timeframes() -> None:
    facts = {"timeframes": []}
    prompts = (
        build_prompt(facts),
        build_next_prompt(facts, covered=[], target_key="chart"),
    )

    for prompt in prompts:
        assert "日足・1時間足・15分足・5分足・1分足" in prompt
        assert "facts.timeframes に含まれる全ての時間足" in prompt
        assert "4つの時間足" not in prompt
        assert "戦略用5分足（保存済み終値系列）" in prompt
