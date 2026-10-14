#!/usr/bin/env python3
"""Classify English comments and build ordered bilingual speech metadata.

The live comment path gets language decisions from the classifier and merges
ordinary Japanese paragraphs with separately translated English paragraphs.
The older marker parser remains only as a compatibility reader for existing
metadata and generated responses; it is not used to infer language in the
live generation path.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ENGLISH_MARKER = "===ENGLISH==="
JAPANESE_MARKER = "===JAPANESE==="
END_MARKER = "===END_BILINGUAL==="
MARKERS = {ENGLISH_MARKER, JAPANESE_MARKER, END_MARKER}

LATIN_RE = re.compile(r"[A-Za-z]")
JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
LEADING_TAG_RE = re.compile(r"^(?:\[[^\]]+\]\s*)+")
SHORT_ENGLISH_MESSAGES = {
    # A single natural-language greeting can still be useful, but Twitch
    # emotes and short reaction tokens must not switch an entire batch into
    # bilingual mode.
    "hello",
    "hi",
    "thanks",
    "thank",
    "sorry",
    "welcome",
}

COMMON_ENGLISH_WORDS = {
    "a",
    "about",
    "again",
    "agree",
    "all",
    "amazing",
    "and",
    "absolutely",
    "are",
    "awesome",
    "can",
    "come",
    "congrats",
    "congratulations",
    "cool",
    "did",
    "do",
    "does",
    "for",
    "from",
    "game",
    "going",
    "good",
    "great",
    "have",
    "hello",
    "help",
    "how",
    "i",
    "interesting",
    "is",
    "it",
    "just",
    "like",
    "love",
    "me",
    "more",
    "my",
    "nice",
    "of",
    "on",
    "play",
    "played",
    "please",
    "really",
    "say",
    "see",
    "so",
    "stream",
    "that",
    "the",
    "this",
    "to",
    "today",
    "victory",
    "want",
    "what",
    "when",
    "where",
    "why",
    "with",
    "watching",
    "well",
    "awaits",
    "you",
    "your",
}


class BilingualFormatError(ValueError):
    """Raised when a required bilingual response is malformed."""


def extract_comment_body(line: str) -> str:
    """Remove queue metadata and the viewer name before language detection."""
    cleaned = line.strip()
    if cleaned.startswith("id=") and "\t" in cleaned:
        cleaned = cleaned.split("\t", 1)[1]
    cleaned = LEADING_TAG_RE.sub("", cleaned)
    if ": " in cleaned:
        cleaned = cleaned.split(": ", 1)[1]
    return URL_RE.sub("", cleaned).strip()


def looks_like_english(text: str) -> bool:
    """Identify natural English without treating Twitch emotes as text.

    Latin characters alone are not evidence of English: usernames, game
    identifiers, emotes, and repeated bot/test strings are common in Twitch
    chat.  Requiring a small amount of word-level evidence keeps the detector
    local and cheap while avoiding the previous ``latin_count >= 3`` trap.
    """
    cleaned = URL_RE.sub("", text)
    japanese_count = len(JAPANESE_RE.findall(cleaned))
    cyrillic_count = len(CYRILLIC_RE.findall(cleaned))
    if japanese_count or cyrillic_count:
        return False

    tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", cleaned.lower())
    if not tokens:
        return False

    # Repeated tokens are overwhelmingly emotes, chants, or bot/test noise
    # rather than a sentence.  This specifically rejects strings such as
    # ``dociaiDoci dociaiDoci dociaiDoci`` and ``LUL LUL LUL``.
    unique_tokens = set(tokens)
    if len(tokens) >= 2 and len(unique_tokens) == 1:
        return False
    if len(tokens) >= 3 and len(unique_tokens) / len(tokens) < 0.67:
        return False

    if len(tokens) == 1:
        return tokens[0] in SHORT_ENGLISH_MESSAGES

    # At least one common English word is required.  Unknown repeated ASCII
    # identifiers should remain ordinary chat instead of forcing bilingual
    # output for the whole batch.
    return any(token in COMMON_ENGLISH_WORDS for token in tokens)


def looks_like_english_output(text: str) -> bool:
    """Detect an English model-output paragraph without Twitch-noise rules."""
    cleaned = URL_RE.sub("", text)
    if CYRILLIC_RE.search(cleaned):
        return False
    tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", cleaned)
    # Model output is not untrusted chat input: a natural phrase can contain
    # uncommon words ("comrade", "victory", etc.), so script-level evidence is
    # sufficient here.  The Twitch detector above remains deliberately stricter.
    latin_count = len(LATIN_RE.findall(cleaned))
    japanese_count = len(JAPANESE_RE.findall(cleaned))
    if japanese_count and (latin_count < 8 or latin_count < japanese_count * 2):
        # A Japanese sentence with an English proper name is still Japanese;
        # a mostly-English sentence may retain a viewer name in Japanese.
        return False
    return bool(tokens) and latin_count >= 2


def batch_has_english(lines: list[str]) -> bool:
    return any(looks_like_english(extract_comment_body(line)) for line in lines)


def count_english_comments(lines: list[str]) -> int:
    return sum(looks_like_english(extract_comment_body(line)) for line in lines)


def _normalized_block(lines: list[str], label: str) -> str:
    text = "\n".join(lines).strip()
    if not text:
        raise BilingualFormatError(f"{label} block is empty")
    return re.sub(r"\n{3,}", "\n\n", text)


def _validate_english_block(text: str) -> None:
    if len(LATIN_RE.findall(text)) < 3:
        raise BilingualFormatError("English block does not contain English text")
    if JAPANESE_RE.search(text):
        raise BilingualFormatError("English block contains Japanese text")
    for line in text.splitlines():
        if re.match(
            r"^\s*(?:target\s+\d+\b|viewer\s+comment\s*:|"
            r"japanese\s+reply\s+to\s+translate\s*:)",
            line,
            flags=re.IGNORECASE,
        ):
            raise BilingualFormatError("English block contains a translation label")


def _validate_japanese_block(text: str) -> None:
    if not JAPANESE_RE.search(text):
        raise BilingualFormatError("Japanese block does not contain Japanese text")


def _strip_exact_duplicate_english_before_markers(text: str) -> str:
    """Remove only an exact English-block duplicate immediately before its marker.

    MiniMax can occasionally emit the reply once and then repeat the same reply
    inside the required marker block. Arbitrary unmarked English must still fail.
    """
    search_from = 0
    while True:
        english_marker_index = text.find(ENGLISH_MARKER, search_from)
        if english_marker_index < 0:
            return text
        english_start = english_marker_index + len(ENGLISH_MARKER)
        japanese_marker_index = text.find(JAPANESE_MARKER, english_start)
        if japanese_marker_index < 0:
            return text

        english_text = text[english_start:japanese_marker_index].strip()
        before = text[:english_marker_index].rstrip()
        if english_text and before.endswith(english_text):
            duplicate_start = len(before) - len(english_text)
            if duplicate_start == 0 or before[duplicate_start - 1] == "\n":
                kept = before[:duplicate_start].rstrip()
                separator = "\n\n" if kept else ""
                text = kept + separator + text[english_marker_index:]
                search_from = len(kept) + len(separator) + len(ENGLISH_MARKER)
                continue

        search_from = japanese_marker_index + len(JAPANESE_MARKER)


def parse_bilingual_response(
    text: str, expected_pairs: int | None = None
) -> dict[str, object]:
    """Parse marker pairs while preserving plain Japanese replies around them."""
    text = _strip_exact_duplicate_english_before_markers(text)
    state = "outside"
    buffer: list[str] = []
    english_text = ""
    segments: list[dict[str, str]] = []
    pair_count = 0

    def flush_plain() -> None:
        nonlocal buffer
        plain = "\n".join(buffer).strip()
        buffer = []
        if not plain:
            return
        if any(
            looks_like_english(line.strip())
            for line in plain.splitlines()
            if line.strip()
        ):
            raise BilingualFormatError("English reply exists outside marker block")
        segments.append({"language": "ja", "role": "reply", "text": plain})

    for raw_line in text.splitlines():
        marker = raw_line.strip()
        if marker not in MARKERS:
            buffer.append(raw_line.rstrip())
            continue

        if marker == ENGLISH_MARKER:
            if state != "outside":
                raise BilingualFormatError("unexpected English marker")
            flush_plain()
            state = "english"
            continue

        if marker == JAPANESE_MARKER:
            if state != "english":
                raise BilingualFormatError("unexpected Japanese marker")
            english_text = _normalized_block(buffer, "English")
            _validate_english_block(english_text)
            buffer = []
            state = "japanese"
            continue

        if state != "japanese":
            raise BilingualFormatError("unexpected bilingual end marker")
        japanese_text = _normalized_block(buffer, "Japanese")
        _validate_japanese_block(japanese_text)
        buffer = []
        segments.append(
            {"language": "en", "role": "reply", "text": english_text}
        )
        segments.append(
            {"language": "ja", "role": "translation", "text": japanese_text}
        )
        pair_count += 1
        state = "outside"

    if state != "outside":
        raise BilingualFormatError("bilingual marker block is incomplete")
    flush_plain()
    if pair_count == 0:
        raise BilingualFormatError("no bilingual marker pair found")
    if expected_pairs is not None and pair_count != expected_pairs:
        raise BilingualFormatError(
            f"expected {expected_pairs} bilingual pair(s), found {pair_count}"
        )

    display_parts: list[str] = []
    for segment in segments:
        segment_text = segment["text"]
        if segment["role"] == "translation":
            display_parts.append(f"日本語訳：\n{segment_text}")
        else:
            display_parts.append(segment_text)

    return {
        "bilingual": True,
        "english_reply_count": pair_count,
        "speech_segments": segments,
        "display_text": "\n\n".join(display_parts).strip(),
    }


def _plain_response_blocks(text: str) -> list[str]:
    """Normalize a marker-free model response into ordered speech blocks."""
    blocks: list[str] = []
    current: list[str] = []

    def line_language(line: str) -> str | None:
        if looks_like_english_output(line):
            return "en"
        if JAPANESE_RE.search(line) or CYRILLIC_RE.search(line):
            return "ja"
        return None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        if line.startswith("```") or line == "^D":
            continue
        if line in MARKERS:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        # Models sometimes add a human-readable label even when told not to.
        # Remove only a leading label; never alter the body of a reply.
        line = re.sub(
            r"^(?:english(?:\s+reply)?|japanese(?:\s+translation)?|"
            r"英語(?:返答)?|日本語(?:訳)?)\s*:\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )
        if line:
            # ``_clean_comment_talk`` intentionally removes blank lines before
            # this helper runs.  Preserve a language transition as a boundary
            # so English -> Japanese pairs still work in the real shell path.
            if current:
                current_language = line_language(current[0])
                next_language = line_language(line)
                if current_language and next_language and current_language != next_language:
                    blocks.append("\n".join(current).strip())
                    current = []
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _plain_block_language(block: str) -> str:
    """Return a conservative language hint for one response block."""
    if looks_like_english_output(block):
        return "en"
    return "ja"


def _plain_response_result(
    text: str, expected_pairs: int | None = None
) -> dict[str, object]:
    """Parse natural paragraphs without requiring control markers.

    The parser is intentionally fail-open.  If an English paragraph cannot be
    paired with a following Japanese paragraph, the whole response remains a
    normal reply instead of blocking the comment queue.
    """
    blocks = _plain_response_blocks(text)
    if not blocks:
        raise BilingualFormatError("response is empty")

    languages = [_plain_block_language(block) for block in blocks]
    if "en" not in languages:
        return {
            "bilingual": False,
            "english_reply_count": 0,
            "speech_segments": [],
            "display_text": "\n\n".join(blocks),
        }

    segments: list[dict[str, str]] = []
    display_parts: list[str] = []
    pair_count = 0
    index = 0
    while index < len(blocks):
        block = blocks[index]
        language = languages[index]
        if language != "en":
            segments.append({"language": "ja", "role": "reply", "text": block})
            display_parts.append(block)
            index += 1
            continue

        # A bilingual pair is represented by two adjacent natural paragraphs:
        # English reply, then Japanese translation.  No marker is required.
        if index + 1 >= len(blocks) or languages[index + 1] != "ja" or not JAPANESE_RE.search(blocks[index + 1]):
            return {
                "bilingual": False,
                "english_reply_count": 0,
                "speech_segments": [],
                "display_text": "\n\n".join(blocks),
            }
        english = block
        japanese = blocks[index + 1]
        segments.append({"language": "en", "role": "reply", "text": english})
        segments.append({"language": "ja", "role": "translation", "text": japanese})
        display_parts.extend([english, f"日本語訳：\n{japanese}"])
        pair_count += 1
        index += 2

    # A count mismatch means we cannot safely tell a translation from the next
    # Japanese reply in a mixed batch.  Degrade the whole batch to ordinary
    # playback rather than speaking the wrong text in English, but do not fail
    # the generation or hold later comments in the queue.
    if expected_pairs is not None and expected_pairs > 0 and pair_count != expected_pairs:
        return {
            "bilingual": False,
            "english_reply_count": 0,
            "speech_segments": [],
            "display_text": "\n\n".join(blocks),
        }
    return {
        "bilingual": pair_count > 0,
        "english_reply_count": pair_count,
        "speech_segments": segments if pair_count > 0 else [],
        "display_text": "\n\n".join(display_parts or blocks).strip(),
    }


def parse_response(text: str, expected_pairs: int | None = None) -> dict[str, object]:
    """Parse the live response contract with marker compatibility and fail-open."""
    if any(line.strip() in MARKERS for line in text.splitlines()):
        try:
            return parse_bilingual_response(text, expected_pairs=expected_pairs)
        except BilingualFormatError:
            # A malformed legacy response should degrade to normal Japanese
            # playback, never trigger the global comment backoff.
            pass
    return _plain_response_result(text, expected_pairs=expected_pairs)


def split_plain_paragraphs(text: str) -> list[str]:
    """Return natural-text paragraphs without guessing their language.

    The live response path uses the classifier's row order as the contract.
    Paragraph boundaries are therefore preserved exactly here instead of
    being reconstructed from English/Japanese script transitions.  This is
    deliberately a small, language-agnostic helper: it removes formatting
    noise, but never merges adjacent paragraphs just because they happen to
    use the same script.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for raw_line in text.replace("\r\n", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append("\n".join(current).strip())
                current = []
            continue
        if line.startswith("```") or line == "^D" or line in MARKERS:
            continue
        line = re.sub(
            r"^(?:english(?:\s+reply)?|japanese(?:\s+translation)?|"
            r"英語(?:返答)?|日本語(?:訳)?)\s*:\s*",
            "",
            line,
            flags=re.IGNORECASE,
        ).strip()
        if line:
            current.append(line)
    if current:
        paragraphs.append("\n".join(current).strip())
    return [paragraph for paragraph in paragraphs if paragraph]


def build_ordered_speech_segments(
    classification: list[dict[str, object]],
    japanese_text: str,
    translation_text: str,
) -> dict[str, object]:
    """Merge ordinary Japanese replies and selected English translations.

    ``classification`` is the source of truth for which rows are English.
    The Japanese model response and the translator both return ordinary
    paragraphs; the number and order of those paragraphs are checked against
    the classifier rows before any bilingual metadata is emitted.  A mismatch
    raises ``BilingualFormatError`` so callers can keep the Japanese-only
    reply without stopping the whole comment queue.
    """
    if not isinstance(classification, list) or not classification:
        raise BilingualFormatError("classification is empty")
    rows = list(classification)
    indices: list[int] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("is_english"), bool):
            raise BilingualFormatError("classification row is invalid")
        try:
            indices.append(int(row.get("index")))
        except (TypeError, ValueError):
            raise BilingualFormatError("classification index is invalid")
    if indices != list(range(1, len(rows) + 1)):
        raise BilingualFormatError("classification indices are not contiguous and ordered")

    japanese_paragraphs = split_plain_paragraphs(japanese_text)
    if len(japanese_paragraphs) != len(rows):
        raise BilingualFormatError(
            f"expected {len(rows)} Japanese paragraph(s), found {len(japanese_paragraphs)}"
        )
    for paragraph in japanese_paragraphs:
        if not JAPANESE_RE.search(paragraph):
            raise BilingualFormatError("Japanese response paragraph has no Japanese text")

    english_rows = [row for row in rows if bool(row.get("is_english"))]
    if not english_rows:
        return {
            "bilingual": False,
            "english_reply_count": 0,
            "speech_segments": [],
            "display_text": "\n\n".join(japanese_paragraphs),
        }

    translations = split_plain_paragraphs(translation_text)
    if len(translations) != len(english_rows):
        raise BilingualFormatError(
            f"expected {len(english_rows)} English translation(s), found {len(translations)}"
        )
    for translation in translations:
        _validate_english_block(translation)

    segments: list[dict[str, str]] = []
    display_parts: list[str] = []
    translation_index = 0
    for row, japanese in zip(rows, japanese_paragraphs):
        if bool(row.get("is_english")):
            english = translations[translation_index]
            translation_index += 1
            segments.append({"language": "en", "role": "translation", "text": english})
            segments.append(
                {"language": "ja", "role": "reply", "text": japanese}
            )
            display_parts.extend([english, f"日本語訳：\n{japanese}"])
        else:
            segments.append({"language": "ja", "role": "reply", "text": japanese})
            display_parts.append(japanese)

    return {
        "bilingual": True,
        "english_reply_count": len(english_rows),
        "speech_segments": segments,
        "display_text": "\n\n".join(display_parts).strip(),
    }


def load_speech_segments(metadata_path: Path) -> list[dict[str, str]]:
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BilingualFormatError(f"cannot read speech metadata: {exc}") from exc

    raw_segments = payload.get("speech_segments")
    if not payload.get("bilingual") or not isinstance(raw_segments, list):
        raise BilingualFormatError("metadata is not bilingual")

    segments: list[dict[str, str]] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise BilingualFormatError("speech segment is not an object")
        language = str(raw.get("language") or "")
        role = str(raw.get("role") or "reply")
        text = str(raw.get("text") or "").strip()
        if language not in {"en", "ja"} or not text:
            raise BilingualFormatError("speech segment has invalid language or text")
        segments.append({"language": language, "role": role, "text": text})
    if not segments or not any(segment["language"] == "en" for segment in segments):
        raise BilingualFormatError("metadata has no English speech segment")
    for index, segment in enumerate(segments):
        if segment["language"] == "en":
            if segment["role"] not in {"reply", "translation"}:
                raise BilingualFormatError("English segment has an invalid role")
            if index + 1 >= len(segments):
                raise BilingualFormatError("English segment has no Japanese translation")
            following = segments[index + 1]
            if following["language"] != "ja" or following["role"] not in {"reply", "translation"}:
                raise BilingualFormatError("English segment is not followed by Japanese")
        elif segment["role"] not in {"reply", "translation"}:
            raise BilingualFormatError("Japanese segment has an invalid role")
    return segments


def command_detect(path: Path) -> int:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        print(f"comment_bilingual: {exc}", file=sys.stderr)
        return 2
    count = count_english_comments(lines)
    print(count)
    return 0 if count else 1


def command_parse(metadata_path: Path, expected_pairs: int | None) -> int:
    try:
        parsed = parse_bilingual_response(sys.stdin.read(), expected_pairs=expected_pairs)
        metadata_path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, BilingualFormatError) as exc:
        print(f"comment_bilingual: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(str(parsed["display_text"]))
    return 0


def command_parse_response(metadata_path: Path, expected_pairs: int | None) -> int:
    try:
        parsed = parse_response(sys.stdin.read(), expected_pairs=expected_pairs)
        metadata_path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, BilingualFormatError) as exc:
        print(f"comment_bilingual: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(str(parsed["display_text"]))
    return 0


def command_build_segments(
    classification_path: Path,
    japanese_path: Path,
    translation_path: Path,
    metadata_path: Path,
) -> int:
    try:
        classification = json.loads(classification_path.read_text(encoding="utf-8"))
        japanese_text = japanese_path.read_text(encoding="utf-8")
        translation_text = translation_path.read_text(encoding="utf-8")
        if not isinstance(classification, list):
            raise BilingualFormatError("classification is not an array")
        parsed = build_ordered_speech_segments(
            classification, japanese_text, translation_text
        )
        if not parsed.get("bilingual"):
            raise BilingualFormatError("no English translation was produced")
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError, BilingualFormatError) as exc:
        print(f"comment_bilingual: {exc}", file=sys.stderr)
        return 1
    return 0


def command_emit_segments(metadata_path: Path, output_dir: Path) -> int:
    try:
        segments = load_speech_segments(metadata_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        for index, segment in enumerate(segments):
            segment_path = output_dir / f"{index:03d}_{segment['language']}.txt"
            segment_path.write_text(segment["text"] + "\n", encoding="utf-8")
            print(f"{segment['language']}\t{segment_path}")
    except (OSError, BilingualFormatError) as exc:
        print(f"comment_bilingual: {exc}", file=sys.stderr)
        return 1
    return 0


def command_extract_language(metadata_path: Path, language: str) -> int:
    try:
        segments = load_speech_segments(metadata_path)
    except BilingualFormatError as exc:
        print(f"comment_bilingual: {exc}", file=sys.stderr)
        return 1
    selected = [segment["text"] for segment in segments if segment["language"] == language]
    if not selected:
        return 1
    sys.stdout.write("\n\n".join(selected))
    return 0


def command_has_speech(metadata_path: Path) -> int:
    try:
        load_speech_segments(metadata_path)
    except BilingualFormatError:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect = subparsers.add_parser("detect")
    detect.add_argument("path", type=Path)

    parse = subparsers.add_parser("parse")
    parse.add_argument("--metadata", required=True, type=Path)
    parse.add_argument("--expected-pairs", type=int)

    parse_response_parser = subparsers.add_parser("parse-response")
    parse_response_parser.add_argument("--metadata", required=True, type=Path)
    parse_response_parser.add_argument("--expected-pairs", type=int)

    build_segments = subparsers.add_parser("build-segments")
    build_segments.add_argument("--classification", required=True, type=Path)
    build_segments.add_argument("--japanese", required=True, type=Path)
    build_segments.add_argument("--translation", required=True, type=Path)
    build_segments.add_argument("--metadata", required=True, type=Path)

    emit = subparsers.add_parser("emit-segments")
    emit.add_argument("metadata", type=Path)
    emit.add_argument("output_dir", type=Path)

    extract = subparsers.add_parser("extract-language")
    extract.add_argument("metadata", type=Path)
    extract.add_argument("language", choices=("en", "ja"))

    has_speech = subparsers.add_parser("has-speech")
    has_speech.add_argument("metadata", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "detect":
        return command_detect(args.path)
    if args.command == "parse":
        return command_parse(args.metadata, args.expected_pairs)
    if args.command == "parse-response":
        return command_parse_response(args.metadata, args.expected_pairs)
    if args.command == "build-segments":
        return command_build_segments(
            args.classification, args.japanese, args.translation, args.metadata
        )
    if args.command == "emit-segments":
        return command_emit_segments(args.metadata, args.output_dir)
    if args.command == "extract-language":
        return command_extract_language(args.metadata, args.language)
    if args.command == "has-speech":
        return command_has_speech(args.metadata)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
