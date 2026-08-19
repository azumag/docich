"""Canonical VOICEVOX synthesis engine (C4, docs/common_parts_tts_c4.md).

This is the docich-side promotion of ``games/soviet_now/voicevox_tts.sh``.
It is stdlib-only, never plays audio, and mirrors the shell contract:
URL failover, chunk splitting, word replacement, audio_query (pitch/tempo/
intonation), synthesis, and WAV concatenation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import wave


class SpeechError(RuntimeError):
    """User-facing VOICEVOX synthesis error."""


DEFAULT_LOCAL_URL = "http://127.0.0.1:50021"
DEFAULT_SPEAKER = 3  # ずんだもん ノーマル
DEFAULT_MAX_CHARS = 200
DEFAULT_TIMEOUT = 30
DEFAULT_HEALTH_TIMEOUT = 0.7
DEFAULT_NY_PAUSE_LEN = 0.20


@dataclass(frozen=True)
class SpeechConfig:
    urls: tuple[str, ...]
    speaker: int = DEFAULT_SPEAKER
    max_chars: int = DEFAULT_MAX_CHARS
    timeout: float = DEFAULT_TIMEOUT
    health_timeout: float = DEFAULT_HEALTH_TIMEOUT
    pitch: float = 0.0
    tempo: float = 1.0
    intonation: float = 1.0
    ny_pause_fix: bool = True
    ny_pause_len: float = DEFAULT_NY_PAUSE_LEN
    word_replace_file: Path | None = None

    @classmethod
    def from_env(cls, *, repo_root: Path | None = None) -> "SpeechConfig":
        env = os.environ

        def _float(name: str, default: float) -> float:
            raw = env.get(name, "")
            try:
                return float(raw) if raw else default
            except ValueError:
                return default

        def _int(name: str, default: int) -> int:
            raw = env.get(name, "")
            try:
                return int(raw) if raw else default
            except ValueError:
                return default

        primary = env.get("VOICEVOX_URL_PRIMARY", "") or env.get("VOICEVOX_URL_REMOTE", "")
        fallback = env.get("VOICEVOX_URL_FALLBACK", "") or DEFAULT_LOCAL_URL
        direct = env.get("VOICEVOX_URL", "") or (primary or DEFAULT_LOCAL_URL)
        local = env.get("VOICEVOX_URL_LOCAL", "") or DEFAULT_LOCAL_URL
        urls: list[str] = []
        for candidate in (primary, direct, fallback, local):
            if candidate and candidate not in urls:
                urls.append(candidate)
        if not urls:
            urls = [DEFAULT_LOCAL_URL]

        replace_file = None
        if repo_root is not None:
            candidate = repo_root / "config" / "voicevox_word_replace.txt"
            if candidate.is_file():
                replace_file = candidate
        elif env.get("VOICEVOX_WORD_REPLACE_FILE"):
            replace_file = Path(env["VOICEVOX_WORD_REPLACE_FILE"])

        return cls(
            urls=tuple(urls),
            speaker=_int("VOICEVOX_SPEAKER", DEFAULT_SPEAKER),
            max_chars=_int("VOICEVOX_MAX_CHARS", DEFAULT_MAX_CHARS),
            timeout=_float("VOICEVOX_TIMEOUT", DEFAULT_TIMEOUT),
            health_timeout=_float("VOICEVOX_HEALTH_TIMEOUT", DEFAULT_HEALTH_TIMEOUT),
            pitch=_float("VOICEVOX_PITCH", 0.0),
            tempo=_float("VOICEVOX_TEMPO", 1.0),
            intonation=_float("VOICEVOX_INTONATION", 1.0),
            ny_pause_fix=env.get("VOICEVOX_NY_PAUSE_FIX", "1") != "0",
            ny_pause_len=_float("VOICEVOX_NY_PAUSE_LEN", DEFAULT_NY_PAUSE_LEN),
            word_replace_file=replace_file,
        )


def _http_get_bytes(url: str, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise SpeechError(f"VOICEVOX 接続失敗: {url}: {exc}") from exc


def _http_post_bytes(url: str, data: str | bytes, timeout: float) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise SpeechError(f"VOICEVOX HTTP {exc.code}: {url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SpeechError(f"VOICEVOX 接続失敗: {url}: {exc}") from exc


def _candidate_urls(config: SpeechConfig) -> list[str]:
    active = os.environ.get("VOICEVOX_ACTIVE_URL", "")
    out: list[str] = []
    for candidate in (active, *config.urls):
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def check_server(config: SpeechConfig) -> str:
    """Return the first reachable VOICEVOX URL (GET /speakers health probe)."""

    for url in config.urls:
        try:
            _http_get_bytes(f"{url}/speakers", config.health_timeout)
            return url
        except SpeechError:
            continue
    raise SpeechError("VOICEVOX engine is not running at any configured URL")


def list_speakers(config: SpeechConfig, active_url: str) -> list[dict]:
    raw = _http_get_bytes(f"{active_url}/speakers", config.timeout)
    try:
        speakers = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SpeechError(f"speakers 応答を解析できません: {exc}") from exc
    if not isinstance(speakers, list):
        raise SpeechError("speakers 応答が配列ではありません")
    return speakers


def load_word_replace(path: Path | None) -> list[tuple[str, str]]:
    """Read TAB-separated word replacement entries (行頭 # はコメント)."""

    if path is None:
        return []
    pairs: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in stripped:
            from_word, to_word = stripped.split("\t", 1)
            if from_word and from_word.startswith("#") is False:
                pairs.append((from_word, to_word))
    return pairs


def sanitize_text(text: str, replacements: list[tuple[str, str]]) -> str:
    """Remove crash-prone chars and apply the replacement dictionary."""

    text = text.replace("#", "").replace("＃", "")
    for from_word, to_word in replacements:
        text = text.replace(from_word, to_word)
    return text


def split_chunks(text: str, max_chars: int) -> list[str]:
    """Split on newline / 。 / 、 like voicevox_tts.sh."""

    chunks: list[str] = []
    for line in text.split("\n"):
        for sentence in line.split("。"):
            sentence = sentence.strip()
            if not sentence:
                continue
            sentence += "。"
            if chunks and len(chunks[-1]) + len(sentence) <= max_chars:
                chunks[-1] += sentence
            elif len(sentence) > max_chars:
                buf = ""
                for part in sentence.split("、"):
                    candidate = buf + ("、" if buf else "") + part
                    if len(candidate) > max_chars and buf:
                        chunks.append(buf)
                        buf = part
                    else:
                        buf = candidate
                if buf:
                    chunks.append(buf)
            else:
                chunks.append(sentence)
    return chunks


def _pause_mora(length: float) -> dict:
    return {
        "text": "、",
        "consonant": None,
        "consonant_length": None,
        "vowel": "pau",
        "vowel_length": length,
        "pitch": 0.0,
    }


def _is_i_ny(prev: dict, cur: dict) -> bool:
    return cur.get("consonant") == "ny" and str(prev.get("vowel") or "").lower() == "i"


def apply_ny_pause_fix(query: dict, pause_len: float) -> dict:
    """Insert a pau mora before i母音直後の「ニュ」 (voicevox_tts.sh の NY_PAUSE_FIX)."""

    if not (0.0 < pause_len <= 1.0):
        raise SpeechError(f"VOICEVOX_NY_PAUSE_LEN は 0〜1 の範囲です: {pause_len}")
    out: list[dict] = []
    for phrase in query.get("accent_phrases", []):
        moras = phrase["moras"]
        idx = next(
            (i for i in range(1, len(moras)) if _is_i_ny(moras[i - 1], moras[i])),
            None,
        )
        if idx is None:
            out.append(phrase)
            continue
        out.append(
            {
                "moras": moras[:idx],
                "accent": min(phrase["accent"], idx),
                "pause_mora": _pause_mora(pause_len),
                "is_interrogative": False,
            }
        )
        remainder = {
            "moras": moras[idx:],
            "accent": max(1, min(phrase["accent"] - idx, len(moras) - idx)),
            "pause_mora": phrase.get("pause_mora"),
            "is_interrogative": phrase.get("is_interrogative", False),
        }
        out.append(remainder)
    for i in range(len(out) - 1):
        cur, nxt = out[i], out[i + 1]
        if (
            cur.get("pause_mora") is None
            and cur["moras"]
            and nxt["moras"]
            and _is_i_ny(cur["moras"][-1], nxt["moras"][0])
        ):
            cur["pause_mora"] = _pause_mora(pause_len)
    query["accent_phrases"] = out
    return query


def _synthesize_one_at_url(
    url: str,
    text: str,
    output: Path,
    config: SpeechConfig,
) -> None:
    params = urllib.parse.urlencode({"text": text, "speaker": config.speaker})
    query_raw = _http_post_bytes(
        f"{url}/audio_query?{params}", "", config.timeout
    )
    try:
        query = json.loads(query_raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SpeechError("audio_query 応答を解析できません") from exc
    if "detail" in query:
        raise SpeechError("audio_query failed")

    if config.pitch:
        query["pitchScale"] = query.get("pitchScale", 0.0) + config.pitch
    if config.tempo:
        query["speedScale"] = config.tempo
    if config.intonation:
        query["intonationScale"] = config.intonation
    if config.ny_pause_fix:
        try:
            apply_ny_pause_fix(query, config.ny_pause_len)
        except SpeechError:
            pass  # WARN相当: 元の query を使う

    wav = _http_post_bytes(
        f"{url}/synthesis?speaker={config.speaker}",
        json.dumps(query, ensure_ascii=False),
        config.timeout,
    )
    if not wav:
        raise SpeechError("synthesis returned empty response")
    output.write_bytes(wav)


def _synthesize_one(text: str, output: Path, config: SpeechConfig) -> None:
    for url in _candidate_urls(config):
        try:
            _synthesize_one_at_url(url, text, output, config)
            return
        except SpeechError:
            output.unlink(missing_ok=True)
            continue
    raise SpeechError(f"VOICEVOX synthesis failed at all URLs: {config.urls}")


def concat_wavs(output: Path, files: list[Path]) -> None:
    if not files:
        raise SpeechError("結合する WAV がありません")
    with wave.open(str(output), "wb") as out:
        params_set = False
        for file in files:
            with wave.open(str(file), "rb") as inp:
                if not params_set:
                    out.setparams(inp.getparams())
                    params_set = True
                out.writeframes(inp.readframes(inp.getnframes()))


def synthesize(text: str, output: Path, config: SpeechConfig) -> None:
    """Synthesize ``text`` to ``output`` (WAV). Never plays audio."""

    if not text:
        raise SpeechError("テキストが空です")
    check_server(config)
    cleaned = sanitize_text(text, load_word_replace(config.word_replace_file))
    chunks = split_chunks(cleaned, config.max_chars)
    if len(chunks) <= 1:
        _synthesize_one(cleaned, output, config)
        return
    chunk_files: list[Path] = []
    try:
        for i, chunk in enumerate(chunks):
            chunk_wav = output.parent / f".voicevox_chunk_{os.getpid()}_{i}.wav"
            _synthesize_one(chunk, chunk_wav, config)
            chunk_files.append(chunk_wav)
        concat_wavs(output, chunk_files)
    finally:
        for file in chunk_files:
            file.unlink(missing_ok=True)


def synth_from_file(
    text_file: Path,
    output: Path,
    config: SpeechConfig,
) -> None:
    text = text_file.read_text(encoding="utf-8")
    synthesize(text, output, config)


def cli_synth(args) -> int:
    from .cli import _load_global

    g = _load_global(args)
    config = SpeechConfig.from_env(repo_root=g.repo_root)
    if args.dry_run:
        print(
            "docich: voicevox dry-run: "
            f"urls={list(config.urls)} speaker={config.speaker} "
            f"output={args.output} text={args.file}"
        )
        return 0
    try:
        synth_from_file(Path(args.file), Path(args.output), config)
    except (SpeechError, OSError, ValueError) as exc:
        raise SpeechError(str(exc)) from exc
    print(f"docich: voicevox 合成完了: {args.output}")
    return 0


def cli_speakers(args) -> int:
    from .cli import _load_global

    g = _load_global(args)
    config = SpeechConfig.from_env(repo_root=g.repo_root)
    try:
        active = check_server(config)
        speakers = list_speakers(config, active)
    except SpeechError as exc:
        raise SpeechError(str(exc)) from exc
    for speaker in speakers:
        print(speaker.get("name", ""))
        for style in speaker.get("styles", []):
            print(f"  [{style.get('id')}] {style.get('name')}")
    return 0


def configure_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "voicevox", help="docich 正典の VOICEVOX 合成エンジン (C4)"
    )
    p_sub = p.add_subparsers(dest="voicevox_command", required=True)

    p_synth = p_sub.add_parser("synth", help="テキストを WAV へ合成する (再生しない)")
    p_synth.add_argument("-f", "--file", required=True, metavar="PATH", help="テキストファイル")
    p_synth.add_argument("-o", "--output", required=True, metavar="WAV", help="出力 WAV")
    p_synth.add_argument(
        "--speaker", type=int, default=None, metavar="N",
        help="話者 ID (既定: VOICEVOX_SPEAKER / 3)",
    )
    p_synth.add_argument("--dry-run", action="store_true", help="合成せず設定を表示する")
    p_synth.set_defaults(func=cli_synth)

    p_speakers = p_sub.add_parser("speakers", help="話者一覧を表示する")
    p_speakers.set_defaults(func=cli_speakers)


def run_args(args: argparse.Namespace) -> int:
    if getattr(args, "voicevox_command", None):
        return args.func(args)
    raise SpeechError("voicevox サブコマンドが必要です (synth / speakers)")
