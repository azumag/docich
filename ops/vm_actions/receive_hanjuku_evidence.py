#!/usr/bin/env python3
"""Recipient-side CMS decryption and bounded archive verification; no extraction."""
from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import stat
import sys
import zipfile

# Resolve the sibling by absolute installed path, never the current directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hanjuku_evidence as evidence
import validate_hanjuku_ciphertext as transport


def verify_archive(raw):
    if len(raw) > evidence.MAX_TOTAL:
        raise evidence.EvidenceError("archive_too_large")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (len(names) > len(evidence.FILES) + evidence.MAX_FRAMES + 1
                    or len(names) != len(set(names)) or "manifest.json" not in names):
                raise evidence.EvidenceError("invalid_archive")
            total = 0
            for entry in entries:
                name = entry.filename
                frame = (name.startswith("hanjuku_frames/")
                         and evidence.FRAME_NAME.fullmatch(name.removeprefix("hanjuku_frames/")))
                limit = evidence.MAX_PNG if frame else evidence.MAX_FILE
                if name == "manifest.json":
                    limit = evidence.MAX_TOTAL
                if (name not in (*evidence.FILES, "manifest.json") and not frame
                        or entry.flag_bits & 1 or entry.is_dir() or entry.compress_type != zipfile.ZIP_DEFLATED
                        or stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFREG
                        or entry.file_size > limit):
                    raise evidence.EvidenceError("invalid_archive")
                total += entry.file_size
                if total > evidence.MAX_TOTAL:
                    raise evidence.EvidenceError("archive_too_large")
            manifest = evidence._json(archive.read("manifest.json"))
            if (not isinstance(manifest, dict) or type(manifest.get("schema")) is not int
                    or manifest["schema"] != evidence.SCHEMA or not isinstance(manifest.get("files"), dict)
                    or set(manifest["files"]) != set(names) - {"manifest.json"}):
                raise evidence.EvidenceError("invalid_manifest")
            run = evidence._json(archive.read("hanjuku_run.json"))
            identity = evidence.terminal_identity(run, run.get("runtime_id", ""))
            if identity != manifest.get("identity"):
                raise evidence.EvidenceError("invalid_manifest")
            for name, meta in manifest["files"].items():
                data = archive.read(name)
                if (not isinstance(meta, dict) or len(data) != meta.get("export_bytes")
                        or hashlib.sha256(data).hexdigest() != meta.get("export_file_sha256")):
                    raise evidence.EvidenceError("archive_hash_mismatch")
                if name.endswith(".png") and evidence._png(data) != meta.get("rgb_sha256"):
                    raise evidence.EvidenceError("archive_hash_mismatch")
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, NotImplementedError):
        raise evidence.EvidenceError("invalid_archive") from None
    return manifest


def decrypt(ciphertext, key, certificate, *, runtime_id):
    if len(ciphertext) > evidence.MAX_TOTAL + 1024 * 1024:
        raise evidence.EvidenceError("ciphertext_too_large")
    transport.validate(ciphertext)
    # OpenSSL can emit partial plaintext before an authentication error. The
    # helper captures it in memory and returns nothing unless its exit is zero.
    clear = evidence._openssl(["cms", "-decrypt", "-binary", "-inform", "DER",
                               "-recip", str(certificate), "-inkey", str(key)], ciphertext)
    manifest = verify_archive(clear)
    if manifest["identity"]["runtime_id"] != runtime_id:
        raise evidence.EvidenceError("unexpected_runtime")
    return clear


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--runtime-id", required=True, help="Expected generation requested from the VM")
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--recipient", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        # Recipient-side paths are explicit local operator inputs, never an SSH
        # request. Do not copy a private key into the runtime or repository.
        key_stat = args.key.lstat()
        if (not stat.S_ISREG(key_stat.st_mode) or key_stat.st_nlink != 1
                or key_stat.st_uid != os.getuid()
                or key_stat.st_mode & 0o077):
            raise evidence.EvidenceError("private_key_permissions")
        with evidence._directory(args.input.parent) as parent:
            ciphertext = evidence._read(parent, args.input.name, evidence.MAX_TOTAL + 1024 * 1024)
        clear = decrypt(ciphertext, args.key, args.recipient, runtime_id=args.runtime_id)
        evidence.write_private(args.output, clear)
    except evidence.EvidenceError as exc:
        print(f"evidence receipt rejected: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, RecursionError):
        print("evidence receipt rejected: io_or_format_error", file=sys.stderr)
        return 1
    print("verified private evidence ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
