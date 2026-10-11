"""Shared Meriken voice selection for PAPER narration and Soren91 notices."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

from .config import GlobalConfig, load_game


MERIKEN_SPEAKER_DEFAULT = "14"  # config/games/soren91.toml
_SPEAKER_ENV = "SOREN91_VOICEVOX_SPEAKER"


def _speaker_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    raw = str(value).strip()
    # This setting feeds VOICEVOX's numeric style ID, not a voice name.
    return raw if re.fullmatch(r"[0-9]{1,64}", raw) else ""


def resolve_meriken_speaker(g: GlobalConfig, soren_root: Path) -> str:
    """Process env -> runtime .env -> Soren91 game setting -> public default.

    Missing or invalid overrides fall through without logging their values.
    Both callers use the shared audio runtime root, never the game identity.
    """
    speaker = _speaker_id(os.environ.get(_SPEAKER_ENV))
    if speaker:
        return speaker
    try:
        from .webui import _read_dotenv_dict

        speaker = _speaker_id(_read_dotenv_dict(soren_root).get(_SPEAKER_ENV))
        if speaker:
            return speaker
    except Exception:
        pass
    try:
        raw = load_game(g, "soren91").raw.get("soren91")
        speaker = _speaker_id(raw.get("voicevox_speaker")) if isinstance(raw, Mapping) else ""
        if speaker:
            return speaker
    except Exception:
        pass
    return MERIKEN_SPEAKER_DEFAULT
