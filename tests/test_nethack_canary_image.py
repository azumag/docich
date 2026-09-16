from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "containers" / "nethack-canary" / "Dockerfile"
PINNED_PYTHON = "python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea"
SOURCE_SHA = "2959b7886aac76185b90aea0c9f80d14343f604de0ae96b3dd2a760f7ab3bde9"


def test_canary_image_pins_base_and_official_nethack_source_and_playground():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert text.count(f"FROM {PINNED_PYTHON}") == 2
    assert "FROM debian:bookworm-slim" not in text
    assert "nethack-500-src.tgz" in text
    assert SOURCE_SHA in text
    assert "sha256sum -c -" in text
    assert "VAR_PLAYGROUND='\"/canary/episode/playground\"'" in text
    assert "HACKDIR=/opt/nethack/playground" in text
    # NetHack's linux.500 evaluates this as a make conditional, so the setting
    # must be inserted immediately before the conditional, not appended later.
    assert "sed -i '/^ifdef WANT_SYSTEM_LUA/i WANT_SYSTEM_LUA=1'" in text
    assert "make install WANT_SYSTEM_LUA=1" not in text


def test_canary_image_has_runtime_attestation_labels():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert 'org.docich.nethack-canary.abi="1"' in text
    assert 'org.docich.nethack.version="5.0.0"' in text
    assert f'org.docich.nethack.source-sha256="{SOURCE_SHA}"' in text
    assert "VOLUME " not in text


def test_canary_image_disables_debug_explore_and_shell_permissions():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "s/^WIZARDS=.*/WIZARDS=/" in text
    assert "s/^EXPLORERS=.*/EXPLORERS=/" in text
    assert "SHELLERS=" in text
    assert "DUMPLOGFILE=/canary/episode/playground/dumps/" in text


def test_canary_image_entrypoint_is_only_the_worker_module():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["python3", "-m", "docich.nethack_canary_worker"]' in text
    assert "COPY src /opt/docich/src" in text
    assert "COPY brains /opt/docich/brains" in text
