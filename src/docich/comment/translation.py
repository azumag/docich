"""Comment translation stage (C-S4) for the native pipeline (#829 PR-3e).

The legacy bilingual module is now ported unchanged to ``docich.comment.bilingual``
for drift tracking. This stage remains injectable: production callers may
use functions from ``bilingual`` to build bilingual speech metadata, while a
callable text translator covers simpler reply-only translation. The default is
identity, so a game-context-free fixture can still complete without a second
language model call.

Failures are never silent: a translator that raises, returns a non-string, or
returns an empty/whitespace-only value is recorded with a ``[COMMENT_TRANSLATION]``
marker (timestamp + failure kind) on stderr and via the module logger, matching
the legacy comment path's diagnostic marker. The Japanese reply is still
adopted, so translation remains a best-effort side channel.

Scope note: this module is the native comment base. The production comment path
currently runs through legacy soviet_now (chat_worker.sh dociai -> broadcast/
comment.sh); wiring the same marker into that path is tracked as a separate
follow-up (soviet_now PR + submodule bump). Until then, this native stage is
the reference implementation and test surface for the marker contract.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Callable, Optional

Translator = Callable[[str], str]

logger = logging.getLogger(__name__)


def _comment_translation_timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _log_comment_translation_failure(kind: str, detail: str = "", *, log=None) -> None:
    """Record one translation failure (never raises)."""
    # ``detail`` may include provider error text returned by the translator.
    # It is diagnostic only and must not be treated as trusted content.
    message = f"[COMMENT_TRANSLATION] {_comment_translation_timestamp()} failed: {kind}"
    if detail:
        message = f"{message}: {detail}"
    chosen_log = log if log is not None else logger
    try:
        chosen_log.warning(message)
    except Exception:
        pass
    try:
        print(message, file=sys.stderr)
    except Exception:
        pass


def identity_translator(text: str) -> str:
    return text


def translate_reply(
    text: str,
    *,
    translator: Translator | None = None,
    logger: Optional[object] = None,
    log: Optional[object] = None,
    on_failure: Callable[[str, str], None] | None = None,
    **_ignored: object,
) -> str:
    if not text:
        return ""
    chosen = translator or identity_translator
    chosen_logger = logger if logger is not None else (log if log is not None else None)
    try:
        result = chosen(text)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        _log_comment_translation_failure("exception", detail, log=chosen_logger)
        if on_failure is not None:
            try:
                on_failure("exception", detail)
            except Exception:
                pass
        return text
    if not isinstance(result, str):
        detail = type(result).__name__
        _log_comment_translation_failure("invalid_type", detail, log=chosen_logger)
        if on_failure is not None:
            try:
                on_failure("invalid_type", detail)
            except Exception:
                pass
        return text
    if not result.strip():
        _log_comment_translation_failure("empty_result", "", log=chosen_logger)
        if on_failure is not None:
            try:
                on_failure("empty_result", "")
            except Exception:
                pass
        return text
    return result
