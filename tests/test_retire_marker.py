"""Common retire marker written at confirmed teardown (docich#3122)."""
import json
from pathlib import Path

import pytest

from docich.retire_marker import (
    MARKER_FILE, RetireMarkerError, marker_path, write_retire_marker,
)


@pytest.fixture
def state_dir(tmp_path):
    runtimes = tmp_path / "runtimes"
    runtimes.mkdir()
    return tmp_path


def _gen(state_dir, name="g3-abcdef"):
    directory = state_dir / "runtimes" / name
    directory.mkdir()
    return directory


def test_marker_is_written_atomically_with_canonical_identity(state_dir):
    directory = _gen(state_dir)
    path = write_retire_marker(state_dir, "g3-abcdef")
    assert path == directory / MARKER_FILE
    document = json.loads(path.read_text())
    assert document == {
        "schema": 1, "kind": "runtime_retired",
        "runtime_id": "g3-abcdef", "generation": 3,
    }
    # No temp file may survive the rename.
    assert not list(directory.glob(f".{MARKER_FILE}.*.tmp"))


def test_marker_is_idempotent(state_dir):
    _gen(state_dir)
    first = write_retire_marker(state_dir, "g3-abcdef").read_bytes()
    assert write_retire_marker(state_dir, "g3-abcdef").read_bytes() == first


def test_marker_requires_a_canonical_runtime_id(state_dir):
    _gen(state_dir, "not-a-runtime")
    with pytest.raises(RetireMarkerError):
        write_retire_marker(state_dir, "not-a-runtime")
    assert not (state_dir / "runtimes" / "not-a-runtime" / MARKER_FILE).exists()


def test_marker_refuses_missing_or_symlinked_generation_dir(state_dir, tmp_path):
    with pytest.raises(RetireMarkerError):
        write_retire_marker(state_dir, "g3-abcdef")
    real = _gen(state_dir)
    del real
    link = state_dir / "runtimes" / "g4-bb"
    link.symlink_to(state_dir / "runtimes" / "g3-abcdef")
    with pytest.raises(RetireMarkerError):
        write_retire_marker(state_dir, "g4-bb")


def test_marker_path_stays_inside_runtimes(state_dir):
    _gen(state_dir)
    path = marker_path(state_dir, "g3-abcdef")
    assert path.parent == state_dir / "runtimes" / "g3-abcdef"
    with pytest.raises(Exception):
        marker_path(state_dir, "../escape")
