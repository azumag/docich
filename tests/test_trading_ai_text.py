"""Regression tests for model-output JSON extraction (9/17 AI script outage)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.trading.ai_text import extract_json_object  # noqa: E402


def test_pretty_printed_json_with_raw_newlines_parses():
    raw = '{\n  "corner": "あいう\nえお",\n  "news": "x"\n}'
    data = extract_json_object(raw)
    assert data is not None
    assert "あいう" in data["corner"] and "えお" in data["corner"]
    assert data["news"] == "x"


def test_prose_and_fences_around_json_parse():
    raw = 'はい、台本です。\n```json\n{"corner": "a",\n"news": "b"}\n```\n以上です。'
    data = extract_json_object(raw)
    assert data == {"corner": "a", "news": "b"}


def test_compact_json_still_parses():
    assert extract_json_object('{"corner": "a"}') == {"corner": "a"}


def test_no_json_returns_none():
    assert extract_json_object("ただの文章です。") is None


def test_truncated_json_returns_none():
    assert extract_json_object('{"corner": "a", "news":') is None


def test_non_dict_json_returns_none():
    assert extract_json_object("[1, 2, 3]") is None
