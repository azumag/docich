from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "containers" / "nethack-canary" / "Dockerfile"


def test_canary_image_pins_official_nethack_source_and_playground():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "nethack-500-src.tgz" in text
    assert "2959b7886aac76185b90aea0c9f80d14343f604de0ae96b3dd2a760f7ab3bde9" in text
    assert "sha256sum -c -" in text
    assert "VAR_PLAYGROUND='\"/canary/episode/playground\"'" in text
    assert "HACKDIR=/opt/nethack/playground" in text


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
