"""Reference-run helper for free-text AI generation (Issue #198, Stage 3).

Thin wrapper over ``docich.ai_generate``'s ``ai_generate_list`` dispatch. It
exists so the PAPER corner narration and the end-of-corner strategy
improvement can request plain text through one audited code path.

A real invocation only happens when ``DOCICH_ALLOW_REAL_AI=1``; otherwise a
typed error is raised so callers can fall back deterministically. Output is
returned to the caller and never logged here.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..config import GlobalConfig


class AiTextError(RuntimeError):
    """User-facing failure when requesting generated text."""


def _safe_detail(value: BaseException | str) -> str:
    return str(value).replace("\n", " ")[:240]


def extract_json_object(text: str) -> dict | None:
    """Best-effort extraction of one JSON object from model output.

    Tolerates prose around the object and ```json fences, and scans the first
    balanced ``{...}`` that parses. Returns ``None`` when no object is found.
    """
    raw = str(text or "")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    candidate = fenced.group(1) if fenced else raw
    start = candidate.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(candidate)):
            char = candidate[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(candidate[start:index + 1])
                    except ValueError:
                        data = None
                    if isinstance(data, dict):
                        return data
                    break
        start = candidate.find("{", start + 1)
    return None


def generate_text(
    g: GlobalConfig,
    *,
    label: str,
    agents: str,
    prompt_text: str,
    timeout: int = 600,
) -> str:
    """Run one AI generation and return its stdout.

    Mirrors ``corner_improve._default_llm``: real execution is gated on
    ``DOCICH_ALLOW_REAL_AI=1`` and every failure (non-zero rc, empty output,
    transport error) raises ``AiTextError``. The prompt is written to a private
    temp file; only ``label``/``agents`` are forwarded to the dispatch layer.
    """
    if os.environ.get("DOCICH_ALLOW_REAL_AI") != "1":
        raise AiTextError("AI生成の実実行には DOCICH_ALLOW_REAL_AI=1 が必要です")
    if type(timeout) is not int or timeout < 1:
        raise AiTextError("timeout は1以上である必要があります")

    import tempfile

    from ..ai_generate import build_ai_invocation
    from ..procs import run

    with tempfile.TemporaryDirectory(prefix="docich-ai-text-") as tmp:
        prompt_file = Path(tmp) / "prompt.txt"
        prompt_file.write_text(prompt_text, encoding="utf-8")
        try:
            inv = build_ai_invocation(
                g,
                game_name="sorengame",
                label=label,
                agents=agents,
                prompt_file=prompt_file,
                timeout=timeout,
            )
        except Exception as exc:
            raise AiTextError(f"AI呼び出しの準備に失敗しました: {_safe_detail(exc)}") from exc
        try:
            completed = run(
                inv.argv, cwd=str(inv.cwd), env_extra=inv.env,
                timeout=float(timeout + 60), capture=True,
            )
        except Exception as exc:
            raise AiTextError(f"AI呼び出しに失敗しました: {_safe_detail(exc)}") from exc
    if completed.returncode != 0:
        raise AiTextError(f"AI生成が失敗しました (rc={completed.returncode})")
    output = (completed.stdout or "").strip()
    if not output:
        raise AiTextError("AI生成の出力が空でした")
    return output
