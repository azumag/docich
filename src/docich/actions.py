"""Action JSON schema and parsing (architecture.md §3.2)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

DEFAULT_HOLD_MS = 100

VALID_PAD_BUTTONS = {
    "a", "b", "x", "y", "l", "r", "start", "select", "up", "down", "left", "right",
}

VALID_TYPES = {"pad", "key", "text", "special", "mouse", "wait"}


class ActionError(Exception):
    """Raised when an action JSON/dict payload is invalid."""


@dataclass
class Action:
    type: str
    buttons: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    text: str = ""
    key: str = ""
    x: int = 0
    y: int = 0
    button: int = 1
    ms: int = 0
    hold_ms: int = DEFAULT_HOLD_MS


def extract_json(text: str) -> str:
    """Strip markdown fences (```json ... ```) around a JSON payload.

    Finds the first '{' or '[' and the last '}' or ']' and returns the slice
    between them (inclusive).
    """
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ActionError("行動 JSON が見つかりません (テキストに { または [ がありません)")
    start = min(starts)

    ends = [i for i in (text.rfind("}"), text.rfind("]")) if i != -1]
    if not ends:
        raise ActionError("行動 JSON の終端が見つかりません ( } または ] がありません)")
    end = max(ends)

    if end < start:
        raise ActionError("行動 JSON の開始・終端が不整合です")
    return text[start : end + 1]


def _require_int(item: dict, key: str, action_type: str) -> int:
    if key not in item:
        raise ActionError(f"{action_type} アクションには {key} が必要です")
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ActionError(f"{action_type} の {key} は整数である必要があります: {value!r}")
    return value


def _parse_one(item) -> Action:
    if not isinstance(item, dict):
        raise ActionError(f"各アクションは object である必要があります: {item!r}")

    action_type = item.get("type")
    if not action_type or not isinstance(action_type, str):
        raise ActionError(f"アクションに type がありません: {item!r}")
    if action_type not in VALID_TYPES:
        raise ActionError(
            f"未知のアクション type です: {action_type!r} (使用可能: {sorted(VALID_TYPES)})"
        )

    if action_type == "pad":
        buttons = item.get("buttons")
        if not isinstance(buttons, list) or not buttons:
            raise ActionError("pad アクションには buttons (空でないリスト) が必要です")
        invalid = [b for b in buttons if b not in VALID_PAD_BUTTONS]
        if invalid:
            raise ActionError(
                f"pad の buttons に不正な値があります: {invalid} "
                f"(使用可能: {sorted(VALID_PAD_BUTTONS)})"
            )
        hold_ms = item.get("hold_ms", DEFAULT_HOLD_MS)
        return Action(type="pad", buttons=list(buttons), hold_ms=hold_ms)

    if action_type == "key":
        keys = item.get("keys")
        if not isinstance(keys, list) or not keys:
            raise ActionError("key アクションには keys (空でないリスト) が必要です")
        hold_ms = item.get("hold_ms", DEFAULT_HOLD_MS)
        return Action(type="key", keys=list(keys), hold_ms=hold_ms)

    if action_type == "text":
        text = item.get("text")
        if not isinstance(text, str) or text == "":
            raise ActionError("text アクションには text (空でない文字列) が必要です")
        return Action(type="text", text=text)

    if action_type == "special":
        key = item.get("key")
        if not isinstance(key, str) or key == "":
            raise ActionError("special アクションには key (空でない文字列) が必要です")
        return Action(type="special", key=key)

    if action_type == "mouse":
        x = _require_int(item, "x", "mouse")
        y = _require_int(item, "y", "mouse")
        button = item.get("button", 1)
        if isinstance(button, bool) or not isinstance(button, int):
            raise ActionError(f"mouse の button は整数である必要があります: {button!r}")
        return Action(type="mouse", x=x, y=y, button=button)

    # wait
    ms = _require_int(item, "ms", "wait")
    return Action(type="wait", ms=ms)


def parse_actions(data) -> list[Action]:
    """Accept a JSON string, dict, list, or {"actions": [...]}."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    if isinstance(data, str):
        raw = extract_json(data)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ActionError(f"行動 JSON の解析に失敗しました: {exc}") from exc

    if isinstance(data, dict) and "actions" in data:
        data = data["actions"]

    if isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = data
    else:
        raise ActionError(f"行動データの形式が不正です (object/array が必要): {type(data).__name__}")

    return [_parse_one(item) for item in items]
