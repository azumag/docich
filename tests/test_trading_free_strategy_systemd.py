from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "scripts" / "systemd" / "docich-free-strategy-worker.service"
ENV_EXAMPLE = ROOT / "scripts" / "systemd" / "free-strategy-worker.env.example"
DOC = ROOT / "docs" / "operations" / "free-strategy-paper-worker.md"


def test_unit_requires_explicit_local_environment_and_enable_flag():
    text = UNIT.read_text(encoding="utf-8")
    assert "EnvironmentFile=/home/ubuntu/.config/docich/free-strategy-worker.env" in text
    assert "EnvironmentFile=-" not in text
    assert "User=ubuntu" in text
    assert "SupplementaryGroups=docker" in text
    assert "free-strategy-worker" in text
    assert "--enabled" in text
    assert "--image ${DOCICH_FREE_STRATEGY_IMAGE}" in text
    assert "--trading-dir ${DOCICH_FREE_STRATEGY_TRADING_DIR}" in text
    assert "--interval ${DOCICH_FREE_STRATEGY_INTERVAL}" in text
    assert "NoNewPrivileges=yes" in text
    assert "CPUQuota=100%" in text
    assert "MemoryMax=1G" in text
    assert "TasksMax=128" in text
    assert "WantedBy=multi-user.target" in text


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
    assert "sudo systemctl enable --now docich-free-strategy-worker.service" in text
    assert "実注文" in text
    assert "別設計・別レビュー・別承認" in text


DSH_UNIT = ROOT / "scripts" / "systemd" / "docich-free-strategy-dsh.service"
DSH_TIMER = ROOT / "scripts" / "systemd" / "docich-free-strategy-dsh.timer"
DSH_ENV_EXAMPLE = ROOT / "scripts" / "systemd" / "free-strategy-dsh.env.example"


def _execstart(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("ExecStart="))


def test_dsh_unit_reuses_improve_command_and_is_disabled_by_default():
    text = DSH_UNIT.read_text(encoding="utf-8")
    improve = (ROOT / "scripts" / "systemd" / "docich-free-strategy-improve.service").read_text(encoding="utf-8")
    assert _execstart(text) == _execstart(improve)
    assert "Environment=DOCICH_FREE_STRATEGY_GENERATOR=dsh" in text
    assert "EnvironmentFile=/home/ubuntu/.config/docich/free-strategy-worker.env" in text
    assert "EnvironmentFile=/home/ubuntu/.config/docich/free-strategy-dsh.env" in text
    assert "ConditionPathExists=/home/ubuntu/.config/docich/free-strategy-dsh.env" in text
    assert "TimeoutStartSec=1500" in text and "TimeoutStartSec=600" not in text
    assert "初期は無効。有効化はオーナーが明示的に行う（systemctl enable と DOCICH_FREE_STRATEGY_DSH_ENABLED=1）" in text
    assert "docich-free-strategy-improve.timer の代わりに" in text
    assert "両方を同時に動かさない" in text
    assert "DSH 側で同じプロンプトファイルを定期実行" in text
    assert "DSH 側オートメーション設定は本タスクでは未検証" in text


def test_dsh_timer_replaces_the_improve_timer_and_keeps_the_daily_slot():
    text = DSH_TIMER.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 06:40:00 Asia/Tokyo" in text
    assert "Persistent=true" in text
    assert "AccuracySec=1min" in text
    assert "Unit=docich-free-strategy-dsh.service" in text
    assert "WantedBy=timers.target" in text
    assert "docich-free-strategy-improve.timer の代わりに" in text
    assert "両方を同時に動かさない" in text


def test_dsh_env_example_is_off_and_has_no_secret_value():
    text = DSH_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "DOCICH_FREE_STRATEGY_DSH_ENABLED=0" in text
    assert "# OPENCODE_API_KEY=<" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert "KEY=" not in stripped and "TOKEN=" not in stripped and "SECRET=" not in stripped


def test_no_deploy_script_installs_or_enables_the_dsh_units():
    allowed = {DSH_UNIT.resolve(), DSH_TIMER.resolve(), DSH_ENV_EXAMPLE.resolve()}
    for base in ("ops", "scripts", "bin"):
        for path in (ROOT / base).rglob("*"):
            if not path.is_file() or path.resolve() in allowed:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            assert "docich-free-strategy-dsh" not in text, path


def test_operations_doc_documents_dsh_and_the_context_limit_instead_of_a_symbol_cap():
    text = DOC.read_text(encoding="utf-8")
    assert "DSH 側で同じプロンプトファイルを定期実行" in text
    assert "DSH 側オートメーション設定は本タスクでは未検証" in text
    assert "固定の銘柄数上限はない" in text
    assert "context_input_limit" in text
    assert "prompts/generate_strategy.md" in text
