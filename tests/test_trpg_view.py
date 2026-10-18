"""Offline view tests for the TRPG/murder-mystery corner (#684, PR-3)."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich import trpg_view


def test_payload_roundtrip(tmp_path):
    path = trpg_view.payload_path(tmp_path)
    assert trpg_view.read_payload(path) is None
    payload = {
        "schema": "trpg-view",
        "version": 1,
        "phase": "SCENE",
        "scene_text": "test",
        "choices": ["a", "b"],
        "votes": {},
    }
    trpg_view.write_payload(path, payload)
    assert trpg_view.read_payload(path) == payload


def test_view_name():
    assert trpg_view.VIEW_NAME == "trpg-view"


def test_handler_instantiates(tmp_path):
    path = trpg_view.payload_path(tmp_path)
    h = trpg_view.handler_for(path)
    assert h is not None
