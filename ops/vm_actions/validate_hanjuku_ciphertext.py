#!/usr/bin/env python3
"""Public transport check: bounded DER AuthEnvelopedData, no decryption/printing."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hanjuku_evidence as evidence

# RFC 5083 id-ct-authEnvelopedData (1.2.840.113549.1.9.16.1.23).
AUTH_ENVELOPED_DATA = bytes.fromhex("060b2a864886f70d0109100117")
MAX_CIPHERTEXT = evidence.MAX_TOTAL + 1024 * 1024


def validate(raw):
    if not isinstance(raw, bytes) or not 16 < len(raw) <= MAX_CIPHERTEXT or raw[0] != 0x30:
        raise evidence.EvidenceError("invalid_ciphertext")
    first = raw[1]
    if first < 128:
        length, offset = first, 2
    else:
        count = first & 127
        if not 1 <= count <= 4 or len(raw) < 2 + count or raw[2] == 0:
            raise evidence.EvidenceError("invalid_ciphertext")
        length, offset = int.from_bytes(raw[2:2 + count], "big"), 2 + count
        if length < 128:
            raise evidence.EvidenceError("invalid_ciphertext")
    if length != len(raw) - offset or raw[offset:offset + len(AUTH_ENVELOPED_DATA)] != AUTH_ENVELOPED_DATA:
        raise evidence.EvidenceError("invalid_ciphertext")
    return raw


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 1:
            raise evidence.EvidenceError("invalid_arguments")
        path = Path(argv[0])
        with evidence._directory(path.parent) as parent:
            validate(evidence._read(parent, path.name, MAX_CIPHERTEXT))
    except Exception:
        print("Hanjuku ciphertext validation rejected", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
