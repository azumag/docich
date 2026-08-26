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
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

try:  # POSIX only; the endpoint state file is shared by concurrent synth processes
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


class SpeechError(RuntimeError):
    """User-facing VOICEVOX synthesis error."""


DEFAULT_LOCAL_URL = "http://127.0.0.1:50021"
DEFAULT_SPEAKER = 3  # ずんだもん ノーマル
DEFAULT_MAX_CHARS = 200
DEFAULT_TIMEOUT = 30
DEFAULT_HEALTH_TIMEOUT = 0.7
DEFAULT_NY_PAUSE_LEN = 0.20
# Endpoint chain (VOICEVOX_URLS) + persisted multiplicative backoff.
DEFAULT_PROBE_TIMEOUT = 2.5          # GET /version before committing a synth to an endpoint
DEFAULT_BACKOFF_BASE_SEC = 30.0      # 1st failure -> 30s, then x mult per consecutive failure
DEFAULT_BACKOFF_MULT = 2.0
DEFAULT_BACKOFF_MAX_SEC = 900.0      # cap: retry at least every 15 min
STATE_FILE_NAME = "voicevox_endpoints.json"
CHAIN_LOG_NAME = "voicevox_chain.log"
CHAIN_LOG_MAX_BYTES = 512 * 1024
MAX_STATE_EVENTS = 60


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
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT
    backoff_base_sec: float = DEFAULT_BACKOFF_BASE_SEC
    backoff_mult: float = DEFAULT_BACKOFF_MULT
    backoff_max_sec: float = DEFAULT_BACKOFF_MAX_SEC
    # Persisted per-endpoint failure/backoff state shared by every synth process
    # (None = in-memory only, e.g. unit tests).
    state_file: Path | None = None

    @classmethod
    def from_env(
        cls,
        *,
        repo_root: Path | None = None,
        env: "os._Environ[str] | dict[str, str] | None" = None,
        soren_root: Path | None = None,
    ) -> "SpeechConfig":
        env = os.environ if env is None else env

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

        # VOICEVOX_URLS="http://a:50021,http://b:50021,http://127.0.0.1:50021" is the
        # explicit ordered chain (first = highest priority, last = final fallback).
        # Without it, the legacy PRIMARY/REMOTE/URL/FALLBACK/LOCAL keys are folded
        # into the same ordered list.
        urls: list[str] = parse_url_chain(env.get("VOICEVOX_URLS", ""))
        if not urls:
            primary = env.get("VOICEVOX_URL_PRIMARY", "") or env.get("VOICEVOX_URL_REMOTE", "")
            fallback = env.get("VOICEVOX_URL_FALLBACK", "") or DEFAULT_LOCAL_URL
            direct = env.get("VOICEVOX_URL", "") or (primary or DEFAULT_LOCAL_URL)
            local = env.get("VOICEVOX_URL_LOCAL", "") or DEFAULT_LOCAL_URL
            for candidate in (primary, direct, fallback, local):
                candidate = candidate.strip().rstrip("/")
                if candidate and candidate not in urls:
                    urls.append(candidate)
        if not urls:
            urls = [DEFAULT_LOCAL_URL]

        state_file: Path | None = None
        if env.get("VOICEVOX_STATE_FILE"):
            state_file = Path(env["VOICEVOX_STATE_FILE"]).expanduser()
        else:
            state_dir = default_state_dir(env=env, soren_root=soren_root, repo_root=repo_root)
            if state_dir is not None:
                state_file = state_dir / STATE_FILE_NAME

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
            probe_timeout=max(0.2, _float("VOICEVOX_PROBE_TIMEOUT", DEFAULT_PROBE_TIMEOUT)),
            backoff_base_sec=max(1.0, _float("VOICEVOX_BACKOFF_BASE_SEC", DEFAULT_BACKOFF_BASE_SEC)),
            backoff_mult=max(1.0, _float("VOICEVOX_BACKOFF_MULT", DEFAULT_BACKOFF_MULT)),
            backoff_max_sec=max(1.0, _float("VOICEVOX_BACKOFF_MAX_SEC", DEFAULT_BACKOFF_MAX_SEC)),
            state_file=state_file,
        )


def parse_url_chain(raw: str) -> list[str]:
    """Split a comma/space separated URL list, dropping blanks and duplicates."""

    urls: list[str] = []
    for token in re.split(r"[,\s]+", raw or ""):
        token = token.strip().rstrip("/")
        if token and token not in urls:
            urls.append(token)
    return urls


def default_state_dir(
    *,
    env: "os._Environ[str] | dict[str, str] | None" = None,
    soren_root: Path | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    """Where the shared endpoint state lives: ``<soren>/tmp/state``.

    Resolution order mirrors how the shell side finds soren: explicit
    soren_root (webui), ELOOP_LIB_DIR (workers), a cwd that looks like a soren
    checkout (``docich voicevox synth`` is run from there by voicevox_tts.sh),
    then the docich repo root. None means "no persistence".
    """

    env = os.environ if env is None else env
    if soren_root is not None:
        return Path(soren_root) / "tmp" / "state"
    if env.get("ELOOP_LIB_DIR"):
        return Path(env["ELOOP_LIB_DIR"]) / "tmp" / "state"
    cwd = Path.cwd()
    if (cwd / "eloop_lib.sh").is_file() or (cwd / "say_enqueue.sh").is_file():
        return cwd / "tmp" / "state"
    if repo_root is not None:
        return Path(repo_root) / "tmp" / "state"
    return None


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


# ---------------------------------------------------------------------------
# Endpoint chain state (shared JSON, flock) and multiplicative backoff
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.time()


def _empty_state() -> dict:
    return {"endpoints": {}, "events": [], "active_url": "", "updated_at": 0}


def load_endpoint_state(path: Path | None) -> dict:
    if path is None:
        return _empty_state()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    if not isinstance(data.get("endpoints"), dict):
        data["endpoints"] = {}
    if not isinstance(data.get("events"), list):
        data["events"] = []
    data.setdefault("active_url", "")
    return data


def _update_endpoint_state(path: Path | None, mutate) -> dict | None:
    """Read-modify-write ``path`` under an exclusive lock; returns the new state."""

    if path is None:
        return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with open(lock_path, "a+", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = load_endpoint_state(path)
            mutate(data)
            data["updated_at"] = _now()
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)
    return data


def _push_event(data: dict, event: dict) -> None:
    events = data.setdefault("events", [])
    events.append(event)
    if len(events) > MAX_STATE_EVENTS:
        del events[: len(events) - MAX_STATE_EVENTS]


def backoff_delay(failures: int, config: SpeechConfig) -> float:
    """Delay before retrying an endpoint after ``failures`` consecutive failures."""

    if failures <= 0:
        return 0.0
    delay = config.backoff_base_sec * (config.backoff_mult ** (failures - 1))
    return float(min(config.backoff_max_sec, delay))


def record_failure(config: SpeechConfig, url: str, error: object) -> float:
    """Mark ``url`` failed; returns the new backoff delay in seconds."""

    now = _now()
    result = {"delay": 0.0, "failures": 1}

    def mutate(data: dict) -> None:
        ep = data["endpoints"].setdefault(url, {})
        ep["failures"] = int(ep.get("failures") or 0) + 1
        result["failures"] = ep["failures"]
        ep["fail_count"] = int(ep.get("fail_count") or 0) + 1
        ep["last_error"] = str(error)[:200]
        ep["last_error_at"] = now
        delay = backoff_delay(ep["failures"], config)
        ep["backoff_sec"] = delay
        ep["next_retry_at"] = now + delay
        result["delay"] = delay
        _push_event(data, {"t": now, "url": url, "event": "fail", "error": ep["last_error"],
                           "failures": ep["failures"], "backoff_sec": delay})

    # Compute the delay even without persistence so callers can log it.
    updated = _update_endpoint_state(config.state_file, mutate)
    if updated is None:
        result["delay"] = backoff_delay(1, config)
    _chain_log(config, f"FAIL {url} failures={result['failures']} backoff={result['delay']:.0f}s error={str(error)[:160]}")
    return result["delay"]


def record_success(config: SpeechConfig, url: str, elapsed_ms: float) -> None:
    now = _now()

    def mutate(data: dict) -> None:
        ep = data["endpoints"].setdefault(url, {})
        had_failures = int(ep.get("failures") or 0)
        previous = data.get("active_url") or ""
        ep["failures"] = 0
        ep.pop("next_retry_at", None)
        ep.pop("backoff_sec", None)
        ep["ok_count"] = int(ep.get("ok_count") or 0) + 1
        ep["last_ok_at"] = now
        ep["last_ms"] = round(elapsed_ms)
        prev_avg = ep.get("avg_ms")
        ep["avg_ms"] = round(elapsed_ms if not prev_avg else prev_avg * 0.8 + elapsed_ms * 0.2)
        data["active_url"] = url
        data["last_ok_at"] = now
        if had_failures:
            _push_event(data, {"t": now, "url": url, "event": "recover", "ms": round(elapsed_ms)})
        elif previous and previous != url:
            _push_event(data, {"t": now, "url": url, "event": "switch", "from": previous, "ms": round(elapsed_ms)})

    _update_endpoint_state(config.state_file, mutate)


def set_endpoint_disabled(config: SpeechConfig, url: str, disabled: bool) -> dict | None:
    now = _now()

    def mutate(data: dict) -> None:
        ep = data["endpoints"].setdefault(url, {})
        ep["disabled"] = bool(disabled)
        ep["disabled_at"] = now if disabled else None
        _push_event(data, {"t": now, "url": url, "event": "disable" if disabled else "enable"})

    _chain_log(config, f"{'DISABLE' if disabled else 'ENABLE'} {url}")
    return _update_endpoint_state(config.state_file, mutate)


def reset_endpoint(config: SpeechConfig, url: str | None = None) -> dict | None:
    """Clear failure/backoff counters (all endpoints when ``url`` is None)."""

    now = _now()

    def mutate(data: dict) -> None:
        targets = [url] if url else list(data["endpoints"].keys())
        for target in targets:
            ep = data["endpoints"].get(target)
            if not ep:
                continue
            ep["failures"] = 0
            ep.pop("next_retry_at", None)
            ep.pop("backoff_sec", None)
        _push_event(data, {"t": now, "url": url or "*", "event": "reset"})

    _chain_log(config, f"RESET {url or '*'}")
    return _update_endpoint_state(config.state_file, mutate)


def _chain_log(config: SpeechConfig, message: str) -> None:
    """Append one line to ``<state dir>/voicevox_chain.log`` (size capped)."""

    if config.state_file is None:
        return
    path = Path(config.state_file).with_name(CHAIN_LOG_NAME)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
        if path.stat().st_size > CHAIN_LOG_MAX_BYTES:
            data = path.read_bytes()[-CHAIN_LOG_MAX_BYTES // 2 :]
            path.write_bytes(data[data.find(b"\n") + 1 :])
    except OSError:
        pass


def plan_endpoints(config: SpeechConfig, state: dict | None = None, now: float | None = None) -> list[dict]:
    """Ordered attempt plan for one synthesis.

    Enabled endpoints whose backoff has expired come first (chain order), then
    enabled endpoints still in backoff (chain order) so a request is never
    refused while any endpoint might work; disabled endpoints are used only
    when nothing else is configured.
    """

    state = load_endpoint_state(config.state_file) if state is None else state
    now = _now() if now is None else now
    ready: list[dict] = []
    waiting: list[dict] = []
    disabled: list[dict] = []
    for item in classify_endpoints(config, state, now):
        {"ready": ready, "backoff": waiting, "disabled": disabled}[item["status"]].append(item)
    plan = ready + waiting
    return plan if plan else disabled


def classify_endpoints(config: SpeechConfig, state: dict, now: float) -> list[dict]:
    """Every configured endpoint in chain order with status ready/backoff/disabled."""

    items: list[dict] = []
    for position, url in enumerate(_candidate_urls(config), start=1):
        ep = state.get("endpoints", {}).get(url, {}) or {}
        item = {"url": url, "position": position, "failures": int(ep.get("failures") or 0), "retry_in_sec": 0.0}
        if ep.get("disabled"):
            item["status"] = "disabled"
        else:
            retry_at = float(ep.get("next_retry_at") or 0)
            if retry_at > now:
                item["status"] = "backoff"
                item["retry_in_sec"] = round(retry_at - now, 1)
            else:
                item["status"] = "ready"
        items.append(item)
    return items


def probe_endpoint(url: str, config: SpeechConfig) -> float:
    """GET /version; returns latency in ms or raises SpeechError."""

    t0 = time.monotonic()
    raw = _http_get_bytes(f"{url}/version", config.probe_timeout)
    if raw is None:
        raise SpeechError(f"VOICEVOX 応答なし: {url}")
    return (time.monotonic() - t0) * 1000.0


def endpoint_report(config: SpeechConfig, *, probe: bool = False, now: float | None = None) -> dict:
    """Status of the whole chain for the CLI / webui (optionally live-probing)."""

    now = _now() if now is None else now
    state = load_endpoint_state(config.state_file)
    rows: list[dict] = []
    for item in classify_endpoints(config, state, now):
        url = item["url"]
        ep = state.get("endpoints", {}).get(url, {}) or {}
        row = {
            "url": url,
            "position": item["position"],
            "status": item["status"],
            "enabled": not ep.get("disabled"),
            "failures": int(ep.get("failures") or 0),
            "retry_in_sec": item.get("retry_in_sec", 0.0),
            "next_retry_at": ep.get("next_retry_at"),
            "backoff_sec": ep.get("backoff_sec"),
            "ok_count": int(ep.get("ok_count") or 0),
            "fail_count": int(ep.get("fail_count") or 0),
            "last_ok_at": ep.get("last_ok_at"),
            "last_ms": ep.get("last_ms"),
            "avg_ms": ep.get("avg_ms"),
            "last_error": ep.get("last_error") or "",
            "last_error_at": ep.get("last_error_at"),
            "active": (state.get("active_url") or "") == url,
        }
        if probe:
            try:
                ms = probe_endpoint(url, config)
                row["probe"] = {"ok": True, "ms": round(ms)}
                if row["failures"]:
                    reset_endpoint(config, url)
            except SpeechError as exc:
                row["probe"] = {"ok": False, "error": str(exc)[:200]}
                record_failure(config, url, exc)
        rows.append(row)
    if probe:
        # re-read so the rows reflect what the probe just recorded
        fresh = endpoint_report(config, probe=False, now=_now())
        by_url = {r["url"]: r for r in fresh["endpoints"]}
        for row in rows:
            merged = by_url.get(row["url"], {})
            for key in ("status", "failures", "retry_in_sec", "next_retry_at", "backoff_sec", "ok_count", "fail_count", "last_error", "last_error_at"):
                if key in merged:
                    row[key] = merged[key]
    return {
        "endpoints": rows,
        "active_url": state.get("active_url") or "",
        "last_ok_at": state.get("last_ok_at"),
        "updated_at": state.get("updated_at"),
        "events": list(state.get("events", []))[-MAX_STATE_EVENTS:],
        "state_file": str(config.state_file) if config.state_file else "",
        "backoff": {
            "base_sec": config.backoff_base_sec,
            "mult": config.backoff_mult,
            "max_sec": config.backoff_max_sec,
            "probe_timeout": config.probe_timeout,
        },
        "now": now,
    }


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


def _synthesize_chunks_at_url(url: str, chunks: list[str], output: Path, config: SpeechConfig) -> None:
    """Synthesize every chunk at ONE endpoint and write ``output``.

    Raises SpeechError on any failure (partial files are removed) so the
    caller can fall through to the next endpoint of the chain.
    """

    if len(chunks) == 1:
        try:
            _synthesize_one_at_url(url, chunks[0], output, config)
        except SpeechError:
            output.unlink(missing_ok=True)
            raise
        return
    chunk_files: list[Path] = []
    try:
        for i, chunk in enumerate(chunks):
            chunk_wav = output.parent / f".voicevox_chunk_{os.getpid()}_{i}.wav"
            _synthesize_one_at_url(url, chunk, chunk_wav, config)
            chunk_files.append(chunk_wav)
        concat_wavs(output, chunk_files)
    except SpeechError:
        output.unlink(missing_ok=True)
        raise
    finally:
        for file in chunk_files:
            file.unlink(missing_ok=True)


def synthesize_chunks(chunks: list[str], output: Path, config: SpeechConfig) -> str:
    """Try the endpoint chain in backoff-aware order; returns the URL that served."""

    if not chunks:
        raise SpeechError("テキストが空です")
    errors: list[str] = []
    plan = plan_endpoints(config)
    for item in plan:
        url = item["url"]
        t0 = time.monotonic()
        try:
            probe_endpoint(url, config)
            _synthesize_chunks_at_url(url, chunks, output, config)
        except SpeechError as exc:
            errors.append(f"{url}: {exc}")
            record_failure(config, url, exc)
            continue
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        record_success(config, url, elapsed_ms)
        if item["position"] != 1 or item["status"] != "ready":
            _chain_log(config, f"OK {url} pos={item['position']} was={item['status']} ms={elapsed_ms:.0f}")
        return url
    raise SpeechError("VOICEVOX synthesis failed at all endpoints: " + "; ".join(errors))


def _synthesize_one(text: str, output: Path, config: SpeechConfig) -> None:
    synthesize_chunks([text], output, config)


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
    cleaned = sanitize_text(text, load_word_replace(config.word_replace_file))
    chunks = split_chunks(cleaned, config.max_chars)
    if not chunks:
        chunks = [cleaned]
    synthesize_chunks(chunks, output, config)


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
        plan = [f"{i['url']}({i['status']})" for i in plan_endpoints(config)]
        print(
            "docich: voicevox dry-run: "
            f"urls={list(config.urls)} plan={plan} speaker={config.speaker} "
            f"state_file={config.state_file} output={args.output} text={args.file}"
        )
        return 0
    try:
        synth_from_file(Path(args.file), Path(args.output), config)
    except (SpeechError, OSError, ValueError) as exc:
        raise SpeechError(str(exc)) from exc
    print(f"docich: voicevox 合成完了: {args.output}")
    return 0


def _fmt_age(now: float, stamp) -> str:
    try:
        value = float(stamp or 0)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    delta = max(0, int(now - value))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    return f"{delta // 3600}h{(delta % 3600) // 60:02d}m ago"


def format_endpoint_report(report: dict) -> str:
    now = float(report.get("now") or _now())
    lines = [
        "VOICEVOX endpoint chain (priority order; backoff "
        f"{report['backoff']['base_sec']:.0f}s x{report['backoff']['mult']:g} max {report['backoff']['max_sec']:.0f}s)"
    ]
    for row in report["endpoints"]:
        status = row["status"]
        if status == "backoff":
            status = f"backoff({row.get('retry_in_sec', 0):.0f}s left, {row['failures']} fails)"
        probe = row.get("probe")
        probe_text = ""
        if probe is not None:
            probe_text = f" probe={'ok %dms' % probe['ms'] if probe.get('ok') else 'NG ' + str(probe.get('error', ''))[:60]}"
        active = " *active*" if row.get("active") else ""
        lines.append(
            f"  {row['position']}. {row['url']:<36} {status:<28} ok={row['ok_count']} fail={row['fail_count']}"
            f" avg={row.get('avg_ms') or '-'}ms last_ok={_fmt_age(now, row.get('last_ok_at'))}{probe_text}{active}"
        )
        if row.get("last_error"):
            lines.append(f"       last_error({_fmt_age(now, row.get('last_error_at'))}): {row['last_error'][:100]}")
    if report.get("state_file"):
        lines.append(f"  state: {report['state_file']}")
    return "\n".join(lines)


def cli_endpoints(args) -> int:
    from .cli import _load_global

    g = _load_global(args)
    config = SpeechConfig.from_env(repo_root=g.repo_root)
    target = getattr(args, "url", None)
    if getattr(args, "reset", False):
        reset_endpoint(config, target)
    if getattr(args, "disable", False):
        if not target:
            raise SpeechError("--disable には --url が必要です")
        set_endpoint_disabled(config, target, True)
    if getattr(args, "enable", False):
        if not target:
            raise SpeechError("--enable には --url が必要です")
        set_endpoint_disabled(config, target, False)
    report = endpoint_report(config, probe=bool(getattr(args, "probe", False)))
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(format_endpoint_report(report))
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

    p_ep = p_sub.add_parser(
        "endpoints",
        help="合成エンドポイント連鎖 (VOICEVOX_URLS) の状態表示・疎通確認・backoff 操作",
    )
    p_ep.add_argument("--probe", action="store_true", help="各エンドポイントへ GET /version で疎通確認し結果を記録する")
    p_ep.add_argument("--json", action="store_true", help="JSON で出力する")
    p_ep.add_argument("--url", metavar="URL", default=None, help="操作対象 (--reset/--disable/--enable)")
    p_ep.add_argument("--reset", action="store_true", help="失敗回数と backoff を消す (--url 省略時は全部)")
    p_ep.add_argument("--disable", action="store_true", help="--url を連鎖から外す (状態ファイルに記録)")
    p_ep.add_argument("--enable", action="store_true", help="--url を連鎖に戻す")
    p_ep.set_defaults(func=cli_endpoints)


def run_args(args: argparse.Namespace) -> int:
    if getattr(args, "voicevox_command", None):
        return args.func(args)
    raise SpeechError("voicevox サブコマンドが必要です (synth / speakers / endpoints)")
