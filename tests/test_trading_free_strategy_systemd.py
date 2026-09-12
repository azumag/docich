from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "scripts" / "systemd" / "docich-free-strategy-worker.service"
ENV_EXAMPLE = ROOT / "scripts" / "systemd" / "free-strategy-worker.env.example"
DOC = ROOT / "docs" / "operations" / "free-strategy-paper-worker.md"


def test_unit_requires_explicit_local_environment_and_enable_flag():
    text = UNIT.read_text(encoding="utf-8")
    assert "EnvironmentFile=%h/.config/docich/free-strategy-worker.env" in text
    assert "EnvironmentFile=-" not in text
    assert "free-strategy-worker" in text
    assert "--enabled" in text
    assert "--image ${DOCICH_FREE_STRATEGY_IMAGE}" in text
    assert "--trading-dir ${DOCICH_FREE_STRATEGY_TRADING_DIR}" in text
    assert "--interval ${DOCICH_FREE_STRATEGY_INTERVAL}" in text
    assert "NoNewPrivileges=yes" in text
    assert "WantedBy=default.target" in text


def test_local_env_example_contains_only_paper_runtime_inputs():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "DOCICH_FREE_STRATEGY_TRADING_DIR=" in text
    assert "DOCICH_FREE_STRATEGY_IMAGE=sha256:" in text
    assert "DOCICH_FREE_STRATEGY_INTERVAL=300" in text
    upper = text.upper()
    for forbidden in ("API_KEY=", "TOKEN=", "SECRET=", "PASSWORD=", "PRIVATE_KEY=", "LIVE="):
        assert forbidden not in upper


def test_operations_doc_requires_vm_check_before_enable_and_excludes_live_orders():
    text = DOC.read_text(encoding="utf-8")
    assert "この確認が終わるまではunitをenableしません" in text
    assert "runsc" in text
    assert "sha256:" in text
    assert "systemctl --user enable --now docich-free-strategy-worker.service" in text
    assert "実注文" in text
    assert "別設計・別レビュー・別承認" in text
