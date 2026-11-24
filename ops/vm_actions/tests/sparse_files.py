"""Huge telemetry files without huge disk writes.

The diagnostics collector reads at most the last ``MAX_JSONL_SCAN_BYTES``
(8 MiB) of a JSONL file: ``collect_diagnostics._iter_jsonl`` seeks to the tail
and drops the first partial line.  The "huge input stays bounded" tests used to
write every line of a 60000-line (~300 MB) or 30000-line (~90 MB) file on each
run, which was ~90% of this suite's disk writes and wore the developer Mac's SSD
every time an agent ran the suite.

``write_sparse_tail_jsonl`` keeps the same apparent file size but writes real
lines only for twice the scan window at the end of the file.  Everything before
that is a sparse hole: it is never written to disk, reads back as NUL bytes, and
the tail reader never reaches it.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

_REGISTRY = Path(__file__).resolve().parents[1] / "runtime_registry.py"


def jsonl_scan_window_bytes() -> int:
    """The collector's JSONL tail window (``runtime_registry.MAX_JSONL_SCAN_BYTES``)."""
    spec = importlib.util.spec_from_file_location("runtime_registry_for_sparse_files", str(_REGISTRY))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.MAX_JSONL_SCAN_BYTES)


def write_sparse_tail_jsonl(path, line: str, total_lines: int) -> int:
    """Create ``path`` with the apparent size of ``total_lines`` copies of ``line``.

    ``line`` must end with a newline.  Real copies cover twice the collector's
    scan window at the end of the file; the rest is a sparse hole.  Returns the
    apparent size in bytes.
    """
    if not line.endswith("\n"):
        raise ValueError("line must end with a newline")
    data = line.encode("utf-8")
    real_lines = min(total_lines, 2 * jsonl_scan_window_bytes() // len(data) + 1)
    total = len(data) * total_lines
    with open(path, "wb") as handle:
        handle.truncate(total - len(data) * real_lines)
        handle.seek(0, os.SEEK_END)
        handle.write(data * real_lines)
    return total
