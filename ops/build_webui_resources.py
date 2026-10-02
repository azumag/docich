#!/usr/bin/env python3
"""Publish the manifest after editing reviewed WebUI HTML/default resources."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.webui_resources_loader import ResourceLoader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    resources = ROOT / "src/docich/webui_resources"
    manifest = {"schema": 1, "files": {
        name: hashlib.sha256((resources / name).read_bytes()).hexdigest()
        for name in ("index.html", "defaults.json")
    }}
    content = json.dumps(manifest, indent=2) + "\n"
    target = resources / "manifest.json"
    if args.check:
        if target.read_text() != content:
            parser.error("resource manifest is stale")
    else:
        # webui startup validates this manifest against the backend's allowlist.
        # Restore the previous manifest if the edited resources are invalid.
        previous = target.read_bytes()
        try:
            target.write_text(content)
            from docich import webui
            ResourceLoader(resources, webui.WEBUI_ALLOWLIST, webui._validate_value)
        except Exception:
            target.write_bytes(previous)
            raise
    print("webui resource manifest: ok")


if __name__ == "__main__":
    main()
