#!/usr/bin/env python3
"""Plan and control native Twitch closed captions for docich speech.

The module deliberately keeps model output and FFmpeg control traffic behind
strict schemas.  Only a JSON array of translations is accepted from the model;
reasoning, tool traces, markdown fences, and any other surrounding text make
the caption plan fail closed while audio continues normally.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import textwrap
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Mapping, Sequence


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 4096
MAX_TRANSLATION_RESPONSE_BYTES = 65536
PROTOCOL_PAGE_SLOTS = 32
DEFAULT_SOCKET_PATH = str(
    Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.geteuid()}")
    / "docich"
    / "ffmpeg-cc.sock"
)
DEFAULT_TRANSLATION_URL = "http://127.0.0.1:4100/v1/chat/completions"
DEFAULT_TRANSLATION_MODEL = "minimax-m3"
EXECUTION_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class CaptionError(RuntimeError):
    """Base class for expected, fail-open caption errors."""


class CaptionPlanError(CaptionError):
    """The translation or normalized caption plan was invalid."""


class CaptionProtocolError(CaptionError):
    """The FFmpeg caption socket rejected or malformed a request."""


@dataclass(frozen=True)
class CaptionPage:
    index: int
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True)
class BilingualSpeechChunk:
    index: int
    jaText: str
    enText: str
    pages: tuple[CaptionPage, ...]


_PUNCTUATION_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
)


def validate_execution_id(value: str) -> str:
    if not EXECUTION_ID_RE.fullmatch(value):
        raise CaptionPlanError("executionId contains unsupported characters")
    return value


def validate_page(value: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value < PROTOCOL_PAGE_SLOTS
    ):
        raise CaptionProtocolError("caption page must be between 0 and 31")
    return value


def sequence_to_protocol_page(sequence: int) -> int:
    """Map an unbounded speech sequence onto reusable FFmpeg page slots."""
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise CaptionProtocolError("caption sequence must be a non-negative integer")
    return sequence % PROTOCOL_PAGE_SLOTS


def normalize_caption_text(value: str) -> str:
    """Normalize model output to the conservative CEA-608 ASCII subset."""
    if not isinstance(value, str):
        raise CaptionPlanError("caption translation must be a string")
    value = value.translate(_PUNCTUATION_MAP)
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = "".join(ch if 0x20 <= ord(ch) <= 0x7E else " " for ch in value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        raise CaptionPlanError("caption translation became empty after normalization")
    return value


def wrap_caption_pages(
    value: str,
    *,
    max_columns: int = 32,
    max_lines: int = 2,
    max_pages: int = 1,
) -> tuple[CaptionPage, ...]:
    if not 1 <= max_columns <= 32:
        raise CaptionPlanError("maxColumns must be between 1 and 32")
    if not 1 <= max_lines <= 2:
        raise CaptionPlanError("maxLines must be between 1 and 2")
    if not 1 <= max_pages <= 32:
        raise CaptionPlanError("maxPages must be between 1 and 32")

    normalized = normalize_caption_text(value)
    wrapper = textwrap.TextWrapper(
        width=max_columns,
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=True,
        drop_whitespace=True,
    )
    lines = wrapper.wrap(normalized)
    if not lines:
        raise CaptionPlanError("caption has no displayable lines")
    page_count = (len(lines) + max_lines - 1) // max_lines
    if page_count > max_pages:
        raise CaptionPlanError(
            f"caption requires {page_count} pages; maximum is {max_pages}"
        )
    return tuple(
        CaptionPage(index=index, lines=tuple(lines[offset : offset + max_lines]))
        for index, offset in enumerate(range(0, len(lines), max_lines))
    )


def _translation_prompt(chunks: Sequence[str], max_chars: int) -> str:
    # Batch radio requests can contain many proper nouns. A 32-character target
    # leaves enough margin for occasional model overshoot while preserving the
    # hard two-line CEA-608 limit of 64 characters.
    target_chars = min(max_chars, 32)
    numbered = "\n".join(
        f"{index}: {json.dumps(chunk, ensure_ascii=False)}"
        for index, chunk in enumerate(chunks)
    )
    return f"""You are a real-time Japanese-to-English broadcast caption compressor.
No analysis is needed.
Translate every numbered Japanese speech chunk into concise, natural English.
TARGET {target_chars} CHARACTERS: every English string should be at most {target_chars} ASCII characters total, including spaces and punctuation.
The absolute transport limit is {max_chars} characters. Preserve the core meaning only; omit secondary detail and use short words.
Example: 通常読み上げの字幕が音声と同じタイミングで表示されることを確認しています。 -> Checking captions stay synced.
Do not add explanations, notes, markdown, speaker labels, or facts.
Return exactly one JSON object in this schema and nothing else:
{{"translations":["translation 0","translation 1"]}}
Return translations in input order. If you cannot finish every item, return
the available prefix; the caller will caption only the entries it received.

INPUT:
{numbered}
"""


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CaptionPlanError(f"translation content repeats key: {key}")
        result[key] = value
    return result


class TranslationRuntimeClient:
    """Small OpenAI-compatible translation client for the local LiteLLM runtime."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_TRANSLATION_URL,
        models: Sequence[str] = (DEFAULT_TRANSLATION_MODEL,),
        timeout: float = 30.0,
        attempts_per_model: int = 3,
        opener: Callable[..., object] = urllib.request.urlopen,
    ) -> None:
        try:
            parsed_endpoint = urllib.parse.urlsplit(endpoint)
            endpoint_port = parsed_endpoint.port
        except ValueError as exc:
            raise CaptionPlanError("translation endpoint is invalid") from exc
        if (
            parsed_endpoint.scheme != "http"
            or parsed_endpoint.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed_endpoint.username is not None
            or parsed_endpoint.password is not None
            or endpoint_port is None
            or not parsed_endpoint.path
            or parsed_endpoint.query
            or parsed_endpoint.fragment
        ):
            raise CaptionPlanError("translation endpoint must be loopback HTTP")
        clean_models = tuple(model.strip() for model in models if model.strip())
        if not clean_models:
            raise CaptionPlanError("at least one translation model is required")
        if not 0.1 <= timeout <= 120:
            raise CaptionPlanError("translation timeout must be between 0.1 and 120 seconds")
        if not isinstance(attempts_per_model, int) or isinstance(
            attempts_per_model, bool
        ):
            raise CaptionPlanError("translation attempts must be an integer")
        if not 1 <= attempts_per_model <= 5:
            raise CaptionPlanError("translation attempts must be between 1 and 5")
        self.endpoint = endpoint
        self.models = clean_models
        self.timeout = timeout
        self.attempts_per_model = attempts_per_model
        self._opener = opener

    def translate(self, chunks: Sequence[str], *, max_chars: int = 64) -> list[str]:
        if not chunks:
            raise CaptionPlanError("no speech chunks were provided")
        # Do not impose a fixed number of speech chunks. Each chunk is sent
        # through the same one-page CEA-608 operation, so long speech can keep
        # advancing after the provider returns a complete translation array.
        chunks = list(chunks)
        if any(not isinstance(chunk, str) or not chunk.strip() for chunk in chunks):
            raise CaptionPlanError("speech chunks must be non-empty strings")
        if any(len(chunk) > 1000 for chunk in chunks):
            raise CaptionPlanError("speech chunk exceeds 1000 characters")
        if not 1 <= max_chars <= 64:
            raise CaptionPlanError("translation max_chars must be between 1 and 64")
        prompt = _translation_prompt(chunks, max_chars)
        errors: list[str] = []
        for model in self.models:
            for attempt in range(1, self.attempts_per_model + 1):
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": max(256, len(chunks) * 96),
                    # Ask compatible runtimes for JSON mode. Provider support
                    # varies, so the strict parser below remains the trust boundary.
                    "response_format": {"type": "json_object"},
                }
                # MiniMax M3 can spend the whole completion budget in hidden
                # reasoning for this simple formatting task.  The production
                # LiteLLM route exposes the OpenAI-compatible control only when
                # it is explicitly allowlisted per request.
                if model.rsplit("/", 1)[-1].lower() == "minimax-m3":
                    payload["reasoning_effort"] = "none"
                    payload["allowed_openai_params"] = ["reasoning_effort"]
                request = urllib.request.Request(
                    self.endpoint,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    method="POST",
                )
                try:
                    with self._opener(request, timeout=self.timeout) as response:
                        raw_response = response.read(MAX_TRANSLATION_RESPONSE_BYTES + 1)
                    if len(raw_response) > MAX_TRANSLATION_RESPONSE_BYTES:
                        raise CaptionPlanError("translation response exceeds 64KiB")
                    result = json.loads(raw_response.decode("utf-8"))
                    content = result["choices"][0]["message"]["content"]
                    # Strict parsing is intentional: surrounding thinking/tool output
                    # must never become a spoken or displayed caption. A fresh request
                    # may recover, but malformed content is never extracted or reused.
                    if not isinstance(content, str):
                        raise CaptionPlanError("translation content must be a JSON string")
                    parsed = json.loads(
                        content.strip(), object_pairs_hook=_strict_json_object
                    )
                    if not isinstance(parsed, dict) or set(parsed) != {"translations"}:
                        raise CaptionPlanError(
                            "translation content must match the exact schema"
                        )
                    translations = parsed["translations"]
                    if not isinstance(translations, list):
                        raise CaptionPlanError("translations must be a JSON array")
                    if len(translations) > len(chunks):
                        translations = translations[: len(chunks)]
                    if not translations:
                        raise CaptionPlanError("translation array is empty")
                    if not all(isinstance(item, str) for item in translations):
                        raise CaptionPlanError("translation array contains a non-string value")
                    normalized = [normalize_caption_text(item) for item in translations]
                    if any(len(item) > max_chars for item in normalized):
                        raise CaptionPlanError(
                            f"translation exceeds {max_chars} ASCII characters"
                        )
                    return list(translations)
                except (OSError, urllib.error.URLError) as exc:
                    # Transport failures can consume the full request timeout. Do not
                    # multiply that delay by the malformed-output retry count; move to
                    # the next configured model so the audio path remains fail-open.
                    errors.append(f"{model} attempt {attempt}: {exc}")
                    break
                except (
                    CaptionPlanError,
                    KeyError,
                    IndexError,
                    TypeError,
                    ValueError,
                ) as exc:
                    errors.append(f"{model} attempt {attempt}: {exc}")
        raise CaptionPlanError("all translation models failed: " + "; ".join(errors))


def build_caption_plan(
    chunks: Sequence[str],
    translations: Sequence[str],
    *,
    execution_id: str,
    max_columns: int = 32,
    max_lines: int = 2,
    max_pages: int = 1,
) -> dict[str, object]:
    validate_execution_id(execution_id)
    chunks = list(chunks)
    translations = list(translations)
    if not chunks or not translations:
        raise CaptionPlanError("speech chunks and translations must be non-empty")
    # Translation providers occasionally return a partial or overlong array.
    # Preserve the aligned prefix instead of discarding the whole caption plan:
    # missing tail chunks continue as audio-only, and extra model entries are
    # ignored because there is no corresponding speech chunk.
    aligned_count = min(len(chunks), len(translations))
    chunks = chunks[:aligned_count]
    translations = translations[:aligned_count]

    planned: list[BilingualSpeechChunk] = []
    for index, (japanese, english) in enumerate(zip(chunks, translations, strict=True)):
        japanese = japanese.strip()
        if not japanese:
            raise CaptionPlanError(f"Japanese chunk {index} is empty")
        normalized = normalize_caption_text(english)
        pages = wrap_caption_pages(
            normalized,
            max_columns=max_columns,
            max_lines=max_lines,
            max_pages=max_pages,
        )
        planned.append(
            BilingualSpeechChunk(
                index=index,
                jaText=japanese,
                enText=normalized,
                pages=pages,
            )
        )

    return {
        "v": PROTOCOL_VERSION,
        "executionId": execution_id,
        "language": "en",
        "maxColumns": max_columns,
        "maxLines": max_lines,
        "chunks": [asdict(chunk) for chunk in planned],
    }


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load_plan(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CaptionPlanError(f"could not read caption plan: {path}") from exc
    if not isinstance(payload, dict):
        raise CaptionPlanError("caption plan must be a JSON object")
    expected_keys = {
        "v",
        "executionId",
        "language",
        "maxColumns",
        "maxLines",
        "chunks",
    }
    if set(payload) != expected_keys:
        raise CaptionPlanError("caption plan must match the exact schema")
    if payload.get("v") != PROTOCOL_VERSION:
        raise CaptionPlanError("caption plan protocol version is unsupported")
    validate_execution_id(str(payload.get("executionId", "")))
    if payload.get("language") != "en":
        raise CaptionPlanError("caption plan language must be en")
    max_columns = payload.get("maxColumns")
    max_lines = payload.get("maxLines")
    if (
        not isinstance(max_columns, int)
        or isinstance(max_columns, bool)
        or not 1 <= max_columns <= 32
    ):
        raise CaptionPlanError("caption plan maxColumns must be between 1 and 32")
    if (
        not isinstance(max_lines, int)
        or isinstance(max_lines, bool)
        or not 1 <= max_lines <= 2
    ):
        raise CaptionPlanError("caption plan maxLines must be between 1 and 2")
    chunks = payload.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise CaptionPlanError("caption plan must contain at least one chunk")
    for chunk_index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict) or set(chunk) != {
            "index",
            "jaText",
            "enText",
            "pages",
        }:
            raise CaptionPlanError(f"caption chunk {chunk_index} is malformed")
        if chunk.get("index") != chunk_index:
            raise CaptionPlanError(f"caption chunk {chunk_index} index is malformed")
        japanese = chunk.get("jaText")
        english = chunk.get("enText")
        if not isinstance(japanese, str) or not japanese.strip():
            raise CaptionPlanError(f"caption chunk {chunk_index} Japanese text is empty")
        if not isinstance(english, str) or normalize_caption_text(english) != english:
            raise CaptionPlanError(f"caption chunk {chunk_index} English text is not normalized")
        pages = chunk.get("pages")
        if not isinstance(pages, list) or not 1 <= len(pages) <= 32:
            raise CaptionPlanError(f"caption chunk {chunk_index} pages are malformed")
        flattened: list[str] = []
        for page_index, page in enumerate(pages):
            if not isinstance(page, dict) or set(page) != {"index", "lines"}:
                raise CaptionPlanError(
                    f"caption chunk {chunk_index} page {page_index} is malformed"
                )
            if page.get("index") != page_index:
                raise CaptionPlanError(
                    f"caption chunk {chunk_index} page {page_index} index is malformed"
                )
            lines = page.get("lines")
            if not isinstance(lines, list) or not 1 <= len(lines) <= max_lines:
                raise CaptionPlanError(
                    f"caption chunk {chunk_index} page {page_index} lines are malformed"
                )
            for line in lines:
                if (
                    not isinstance(line, str)
                    or normalize_caption_text(line) != line
                    or len(line) > max_columns
                ):
                    raise CaptionPlanError(
                        f"caption chunk {chunk_index} page {page_index} exceeds its bounds"
                    )
                flattened.append(line)
        if " ".join(flattened) != english:
            raise CaptionPlanError(
                f"caption chunk {chunk_index} pages do not match English text"
            )
    return payload


class CaptionSocketClient:
    def __init__(self, socket_path: str, *, timeout: float = 3.0) -> None:
        path = Path(socket_path)
        if not path.is_absolute() or len(os.fsencode(path)) >= 104:
            raise CaptionProtocolError("caption socket path must be an absolute Unix path under 104 bytes")
        if not 0.05 <= timeout <= 60:
            raise CaptionProtocolError("caption socket timeout must be between 0.05 and 60 seconds")
        self.socket_path = str(path)
        self.timeout = timeout

    def _request(self, payload: Mapping[str, object], expected_event: str) -> dict[str, object]:
        wire = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
        if len(wire) > MAX_MESSAGE_BYTES:
            raise CaptionProtocolError("caption request exceeds 4KiB")
        buffer = bytearray()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout)
            try:
                connection.connect(self.socket_path)
                connection.sendall(wire)
                while len(buffer) <= MAX_MESSAGE_BYTES:
                    block = connection.recv(1024)
                    if not block:
                        break
                    buffer.extend(block)
                    while b"\n" in buffer:
                        raw, _, rest = buffer.partition(b"\n")
                        buffer = bytearray(rest)
                        response = json.loads(raw.decode("utf-8"))
                        if not isinstance(response, dict):
                            raise ValueError("caption response must be a JSON object")
                        if response.get("v") != PROTOCOL_VERSION:
                            raise ValueError("caption response protocol version is unsupported")
                        if response.get("event") == "error":
                            raise CaptionProtocolError(
                                f"{response.get('code', 'ERROR')}: {response.get('message', '')}"
                            )
                        if response.get("event") == expected_event:
                            return response
            except (OSError, ValueError, UnicodeError) as exc:
                raise CaptionProtocolError(f"caption socket request failed: {exc}") from exc
        raise CaptionProtocolError(f"caption socket closed before {expected_event} acknowledgement")

    def prepare(self, execution_id: str, page: int, text: str) -> dict[str, object]:
        validate_execution_id(execution_id)
        validate_page(page)
        lines = text.splitlines()
        if not 1 <= len(lines) <= 2:
            raise CaptionProtocolError("caption text must contain one or two lines")
        normalized_lines = [normalize_caption_text(line) for line in lines]
        if any(len(line) > 32 for line in normalized_lines):
            raise CaptionProtocolError("caption line exceeds 32 ASCII characters")
        normalized = "\n".join(normalized_lines)
        encoded = base64.b64encode(normalized.encode("ascii")).decode("ascii")
        return self._request(
            {
                "v": PROTOCOL_VERSION,
                "op": "prepare",
                "executionId": execution_id,
                "page": page,
                "textBase64": encoded,
            },
            "prepared",
        )

    def commit(self, execution_id: str, page: int) -> dict[str, object]:
        validate_execution_id(execution_id)
        validate_page(page)
        return self._request(
            {"v": PROTOCOL_VERSION, "op": "commit", "executionId": execution_id, "page": page},
            "committed",
        )

    def clear(self, execution_id: str) -> dict[str, object]:
        validate_execution_id(execution_id)
        return self._request(
            {"v": PROTOCOL_VERSION, "op": "clear", "executionId": execution_id},
            "cleared",
        )

    def reset(self) -> dict[str, object]:
        return self._request({"v": PROTOCOL_VERSION, "op": "reset"}, "reset")


def _plan_page(plan: Mapping[str, object], chunk_index: int, page_index: int) -> tuple[str, str]:
    chunks = plan["chunks"]
    if not isinstance(chunks, list) or not 0 <= chunk_index < len(chunks):
        raise CaptionPlanError("caption chunk index is out of range")
    chunk = chunks[chunk_index]
    if not isinstance(chunk, dict):
        raise CaptionPlanError("caption chunk is malformed")
    pages = chunk.get("pages")
    if not isinstance(pages, list) or not 0 <= page_index < len(pages):
        raise CaptionPlanError("caption page index is out of range")
    page = pages[page_index]
    if not isinstance(page, dict) or not isinstance(page.get("lines"), list):
        raise CaptionPlanError("caption page is malformed")
    lines = page["lines"]
    if not lines or len(lines) > 2 or not all(isinstance(line, str) for line in lines):
        raise CaptionPlanError("caption page lines are malformed")
    text = "\n".join(lines)
    return str(plan["executionId"]), text


def _read_chunks(path: Path) -> list[str]:
    try:
        chunks = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as exc:
        raise CaptionPlanError(f"could not read speech chunks: {path}") from exc
    if not chunks:
        raise CaptionPlanError("speech chunk file is empty")
    return chunks


def _read_translation_fixture(path: Path) -> list[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CaptionPlanError(f"could not read translation fixture: {path}") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CaptionPlanError("translation fixture must be a JSON string array")
    return value


def configure_parser(parser: argparse.ArgumentParser) -> None:
    """Add caption planning and FFmpeg IPC commands to ``parser``."""
    subparsers = parser.add_subparsers(dest="caption_action", required=True)

    plan = subparsers.add_parser("plan", help="translate ordered Japanese speech chunks")
    plan.add_argument("--chunks-file", type=Path, required=True)
    plan.add_argument("--execution-id", required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--translations-file", type=Path)
    plan.add_argument("--max-columns", type=int, default=32)
    plan.add_argument("--max-lines", type=int, default=2)
    plan.add_argument("--max-pages", type=int, default=1)

    send = subparsers.add_parser("send", help="send one control operation to FFmpeg")
    send.add_argument("op", choices=("prepare", "commit", "clear", "reset"))
    send.add_argument("--socket")
    send.add_argument("--plan", type=Path)
    send.add_argument("--chunk", type=int, default=0)
    send.add_argument("--page", type=int, default=0)
    send.add_argument(
        "--sequence",
        type=int,
        help="unbounded speech sequence mapped to a reusable protocol page slot",
    )
    send.add_argument("--execution-id")
    send.add_argument("--timeout", type=float, default=3.0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="docich native closed-caption control")
    configure_parser(parser)
    return parser


def run_args(args: argparse.Namespace, *, default_socket: str | None = None) -> int:
    if args.caption_action == "plan":
        chunks = _read_chunks(args.chunks_file)
        if args.translations_file:
            translations = _read_translation_fixture(args.translations_file)
        else:
            endpoint = os.environ.get("DOCICH_CC_TRANSLATION_URL", DEFAULT_TRANSLATION_URL)
            models = tuple(
                part.strip()
                for part in os.environ.get(
                    "DOCICH_CC_TRANSLATION_MODELS", DEFAULT_TRANSLATION_MODEL
                ).split(",")
                if part.strip()
            )
            try:
                timeout = float(os.environ.get("DOCICH_CC_TRANSLATION_TIMEOUT_SEC", "30"))
                attempts = int(os.environ.get("DOCICH_CC_TRANSLATION_ATTEMPTS", "3"))
            except ValueError as exc:
                raise CaptionPlanError(
                    "translation timeout and attempts must be numeric"
                ) from exc
            translations = TranslationRuntimeClient(
                endpoint=endpoint,
                models=models,
                timeout=timeout,
                attempts_per_model=attempts,
            ).translate(chunks, max_chars=args.max_columns * args.max_lines)
        payload = build_caption_plan(
            chunks,
            translations,
            execution_id=args.execution_id,
            max_columns=args.max_columns,
            max_lines=args.max_lines,
            max_pages=args.max_pages,
        )
        _atomic_write_json(args.output, payload)
        return 0

    socket_path = (
        args.socket
        or default_socket
        or os.environ.get("DOCICH_CC_SOCKET")
        or DEFAULT_SOCKET_PATH
    )
    client = CaptionSocketClient(socket_path, timeout=args.timeout)
    protocol_page = (
        args.page
        if args.sequence is None
        else sequence_to_protocol_page(args.sequence)
    )
    if args.op == "reset":
        response = client.reset()
    else:
        plan = load_plan(args.plan) if args.plan else None
        execution_id = args.execution_id or (
            str(plan["executionId"]) if plan is not None else ""
        )
        if args.op == "prepare":
            if plan is None:
                raise CaptionPlanError("prepare requires --plan")
            execution_id, text = _plan_page(plan, args.chunk, args.page)
            response = client.prepare(execution_id, protocol_page, text)
        elif args.op == "commit":
            response = client.commit(execution_id, protocol_page)
        elif args.op == "clear":
            response = client.clear(execution_id)
        else:  # pragma: no cover - argparse enforces this
            raise AssertionError(args.op)
    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    return 0


def main(argv: Sequence[str] | None = None, *, default_socket: str | None = None) -> int:
    return run_args(_build_parser().parse_args(argv), default_socket=default_socket)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CaptionError as exc:
        print(f"docich captions: {exc}", file=os.sys.stderr)
        raise SystemExit(2)
