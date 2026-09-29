#!/usr/bin/env python3
"""Recipient-only Hanjuku recognition evaluation; never sends game input.

Read a decrypted #1302 archive, recheck its integrity, and compare the current
pure screen parser with human labels. No extraction, model call or VM access.
Archive integrity is NOT authentication of the GitHub run that supplied it.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 1
MAX_LABELS = 1024 * 1024
MAX_SAMPLES = 240
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
FIELDS = frozenset({"kind", "selected", "hand", "cursor", "marker", "menu_cursor",
                    "enemy", "enemy_hp", "ally", "ally_hp"})
PARSER_FILES = (
    "src/docich/hanjuku_bot.py", "src/docich/hanjuku_screen.py",
    "src/docich/hanjuku_font.py", "src/docich/hanjuku_glyphs.py",
    "src/docich/hanjuku_pixels.py",
)
COUNTS = ("labeled", "expected_present", "correct_present", "correct_absent",
          "wrong_value", "false_present", "abstained")


class EvaluationError(ValueError):
    """Fixed error codes only; no archive contents in error messages."""


def _backend():
    # The trusted checkout supplies code. Nothing inside the archive is imported.
    parent = str(Path(__file__).resolve().parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    import hanjuku_evidence
    from receive_hanjuku_evidence import verify_archive
    return hanjuku_evidence, verify_archive


def _verified(raw: bytes, runtime_id: str) -> dict:
    evidence, verify = _backend()
    manifest = verify(raw)
    if (not isinstance(runtime_id, str) or not evidence.RUN_ID.fullmatch(runtime_id)
            or manifest["identity"]["runtime_id"] != runtime_id):
        raise EvaluationError("unexpected_runtime")
    return manifest


def _source_hashes() -> dict:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in PARSER_FILES}


def _source(raw: bytes, manifest: dict) -> dict:
    return {
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "runtime_id": manifest["identity"]["runtime_id"],
        "history_complete": False,
        "github_provenance": "must_be_verified_separately",
    }


def prepare(raw: bytes, runtime_id: str, split: str) -> dict:
    """Create empty human-label slots, never labels from the current parser.

    Identical RGB images in different ring slots occur only once in the template.
    An empty expected object is unlabelled, not a successful recognition.
    """
    if type(split) is not str or split not in {"calibration", "holdout"}:
        raise EvaluationError("invalid_split")
    manifest = _verified(raw, runtime_id)
    samples, seen = [], set()
    for name, meta in sorted(manifest["files"].items()):
        if not name.endswith(".png") or meta["rgb_sha256"] in seen:
            continue
        seen.add(meta["rgb_sha256"])
        samples.append({"snapshot": name, "file_sha256": meta["export_file_sha256"],
                        "rgb_sha256": meta["rgb_sha256"], "expected": {}})
    return {"schema": SCHEMA, "runtime_id": runtime_id,
            "archive_sha256": hashlib.sha256(raw).hexdigest(),
            "split": split, "samples": samples}


def _valid_expected(key: str, value) -> bool:
    if value is None:
        return key != "kind"  # An unclassifiable screen is not ground truth.
    if key in {"kind", "selected", "enemy", "ally"}:
        return (isinstance(value, str) and 0 < len(value) <= 128
                and "\ufffd" not in value and not any(ord(ch) < 32 for ch in value)
                and not (key == "kind" and value == "unknown"))
    if key in {"enemy_hp", "ally_hp"}:
        return type(value) is int and 0 <= value <= 999
    if key == "menu_cursor":
        return type(value) is int and 0 <= value < 224
    if type(value) is not list or any(type(v) is not int for v in value):
        return False
    if key == "hand":
        return (len(value) == 4 and 0 <= value[0] <= value[2] < 256
                and 0 <= value[1] <= value[3] < 224)
    return (len(value) == 2 and -16 <= value[0] < 256 and -16 <= value[1] < 224)


def validate_labels(labels: dict, raw: bytes, manifest: dict) -> list[dict]:
    if (type(labels) is not dict or set(labels) != {
            "schema", "runtime_id", "archive_sha256", "split", "samples"}
            or type(labels["schema"]) is not int or labels["schema"] != SCHEMA
            or labels["runtime_id"] != manifest["identity"]["runtime_id"]
            or labels["archive_sha256"] != hashlib.sha256(raw).hexdigest()
            or type(labels["split"]) is not str
            or labels["split"] not in {"calibration", "holdout"}
            or type(labels["samples"]) is not list
            or len(labels["samples"]) > MAX_SAMPLES):
        raise EvaluationError("invalid_labels")
    seen_names, seen_rgb = set(), set()
    for sample in labels["samples"]:
        if type(sample) is not dict or set(sample) != {
                "snapshot", "file_sha256", "rgb_sha256", "expected"}:
            raise EvaluationError("invalid_sample")
        name = sample["snapshot"]
        if not isinstance(name, str) or not name.endswith(".png"):
            raise EvaluationError("invalid_snapshot")
        meta = manifest["files"].get(name)
        if (not isinstance(meta, dict)
                or any(not isinstance(sample[k], str) or not DIGEST.fullmatch(sample[k])
                       for k in ("file_sha256", "rgb_sha256"))
                or sample["file_sha256"] != meta.get("export_file_sha256")
                or sample["rgb_sha256"] != meta.get("rgb_sha256")):
            raise EvaluationError("snapshot_hash_mismatch")
        if name in seen_names or sample["rgb_sha256"] in seen_rgb:
            raise EvaluationError("duplicate_sample")
        seen_names.add(name)
        seen_rgb.add(sample["rgb_sha256"])
        expected = sample["expected"]
        if (type(expected) is not dict or not set(expected) <= FIELDS
                or any(not _valid_expected(k, v) for k, v in expected.items())):
            raise EvaluationError("invalid_expected")
    return sorted(labels["samples"], key=lambda sample: sample["snapshot"])


def _frame(raw: bytes):
    """Decode the export's verified, fixed 256x224/filter-0 format in memory."""
    evidence, _ = _backend()
    evidence._png(raw)  # Strict format, CRC, inflation and ancillary-chunk checks.
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    from docich.hanjuku_pixels import Frame
    size = struct.unpack(">I", raw[33:37])[0]
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(raw[41:41 + size], 224 * 769 + 1)
    if len(pixels) != 224 * 769 or not decoder.eof or decoder.unused_data:
        raise EvaluationError("invalid_frame")
    rgb = b"".join(pixels[y * 769 + 1:(y + 1) * 769] for y in range(224))
    return Frame(256, 224, rgb)


def observe(raw: bytes) -> dict:
    """Use the same phase classification and parser as the bot, but NOT decide()."""
    frame = _frame(raw)
    from docich.hanjuku_bot import classify
    from docich.hanjuku_screen import parse
    screen = parse(frame, phase=classify(frame))
    battle = screen.battle
    return {
        "kind": None if screen.kind == "unknown" else screen.kind,
        "selected": screen.selected,
        "hand": list(screen.hand) if screen.hand is not None else None,
        "cursor": list(screen.cursor) if screen.cursor is not None else None,
        "marker": list(screen.marker) if screen.marker is not None else None,
        "menu_cursor": screen.menu_cursor,
        "enemy": battle.enemy if battle else None,
        "enemy_hp": battle.enemy_hp if battle else None,
        "ally": battle.ally if battle else None,
        "ally_hp": battle.ally_hp if battle else None,
    }


def outcome(expected, actual) -> str:
    if expected is None:
        return "correct_absent" if actual is None else "false_present"
    if actual is None:
        return "abstained"
    return "correct_present" if type(actual) is type(expected) and actual == expected else "wrong_value"


def metrics(counts: dict) -> dict:
    counts = dict(counts)
    wrong = counts["wrong_value"] + counts["false_present"]
    correct = counts["correct_present"] + counts["correct_absent"]
    decided = wrong + correct
    present = counts["expected_present"]
    labeled = counts["labeled"]
    counts.update(
        correct=correct, wrong=wrong, decided=decided,
        accuracy_when_decided=correct / decided if decided else None,
        decision_coverage=decided / labeled if labeled else None,
        present_coverage=(present - counts["abstained"]) / present if present else None,
        wrong_rate=wrong / labeled if labeled else None,
    )
    return counts


def evaluate(raw: bytes, runtime_id: str, labels: dict) -> dict:
    manifest = _verified(raw, runtime_id)
    samples = validate_labels(labels, raw, manifest)  # Validate ALL labels before parsing.
    source_hashes = _source_hashes()
    counters = {field: dict.fromkeys(COUNTS, 0) for field in sorted(FIELDS)}
    results = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for sample in samples:
            if not sample["expected"]:
                continue
            actual = observe(archive.read(sample["snapshot"]))
            if not isinstance(actual, dict) or not FIELDS <= actual.keys():
                raise EvaluationError("invalid_observation")
            fields = {}
            for field, expected in sorted(sample["expected"].items()):
                value = actual[field]
                category = outcome(expected, value)
                counts = counters[field]
                counts["labeled"] += 1
                counts["expected_present"] += expected is not None
                counts[category] += 1
                fields[field] = {"expected": expected, "actual": value, "outcome": category}
            results.append({"snapshot": sample["snapshot"], "rgb_sha256": sample["rgb_sha256"],
                            "fields": fields})
    if _source_hashes() != source_hashes:
        raise EvaluationError("parser_source_changed")
    total = {name: sum(c[name] for c in counters.values()) for name in COUNTS}
    return {"schema": SCHEMA, "source": _source(raw, manifest), "split": labels["split"],
            "labels_sha256": hashlib.sha256(json.dumps(labels, sort_keys=True,
                ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
            "parser_source_sha256": source_hashes,
            "saved_frames": sum(name.endswith(".png") for name in manifest["files"]),
            "selected_samples": len(samples), "labeled_samples": len(results),
            "unlabeled_samples": len(samples) - len(results),
            "status": "evaluated" if results else "no_ground_truth",
            "aggregate": metrics(total),
            "by_field": {field: metrics(counts) for field, counts in counters.items()},
            "samples": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--runtime-id", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", choices=("calibration", "holdout"))
    mode.add_argument("--labels", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        evidence, _ = _backend()
        with evidence._directory(args.archive.parent) as parent:
            raw = evidence._read(parent, args.archive.name, evidence.MAX_TOTAL)
        if args.prepare:
            result = prepare(raw, args.runtime_id, args.prepare)
        else:
            with evidence._directory(args.labels.parent) as parent:
                label_raw = evidence._read(parent, args.labels.name, MAX_LABELS)
            labels = evidence._json(label_raw)
            result = evaluate(raw, args.runtime_id, labels)
        evidence.write_private(args.output, evidence._dump(result))
    except (OSError, ValueError, TypeError, KeyError, RecursionError, ImportError,
            RuntimeError, AttributeError, IndexError, zlib.error, zipfile.BadZipFile):
        # Never expose an exception containing labels, paths or archive contents.
        print("vision evaluation rejected: invalid_input_or_environment", file=sys.stderr)
        return 1
    print("private vision evaluation output ready")
    return 2 if result.get("status") == "no_ground_truth" else 0


if __name__ == "__main__":
    raise SystemExit(main())
