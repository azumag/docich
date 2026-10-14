"""Comment translation stage (C-S4) for the native pipeline (#829 PR-3e).

The legacy bilingual module is now ported unchanged to ``docich.comment.bilingual``
for drift tracking. This stage remains injectable: production callers may
use functions from ``bilingual`` to build bilingual speech metadata, while a
callable text translator covers simpler reply-only translation. The default is
identity, so a game-context-free fixture can still complete without a second
language model call.
"""

from __future__ import annotations

from typing import Callable


Translator = Callable[[str], str]


def identity_translator(text: str) -> str:
    return text


def translate_reply(text: str, *, translator: Translator | None = None) -> str:
    if not text:
        return ""
    chosen = translator or identity_translator
    try:
        result = chosen(text)
    except Exception:
        return text
    if not isinstance(result, str) or not result.strip():
        return text
    return result