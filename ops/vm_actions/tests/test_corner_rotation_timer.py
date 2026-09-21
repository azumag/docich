from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_rotation_timer_installer_is_fixed_and_does_not_restart_shared_services():
    script = (ROOT / "ops/vm_actions/ensure_corner_rotation_timer.sh").read_text()
    assert 'timer="docich-retro-corner.timer"' in script
    assert "docich-retro-corner.service" in script
    assert "docich-retro-corner.timer" in script
    assert "systemctl --user daemon-reload" in script
    assert 'systemctl --user enable --now "$timer"' in script
    assert 'systemctl --user is-enabled --quiet "$timer"' in script
    assert 'systemctl --user is-active --quiet "$timer"' in script
    assert "docich.service" not in script
    assert "systemctl --user restart" not in script
    assert "systemctl --user stop" not in script
    assert "sudo" not in script
    assert "$1" not in script


def test_vm_deploy_reconciles_the_timer_after_successful_production_deploy():
    workflow = (ROOT / ".github/workflows/vm-operations.yml").read_text()
    marker = "- name: Ensure rolling corner rotation timer"
    assert marker in workflow
    block = workflow.split(marker, 1)[1].split(
        "- name: Restart radio worker after reviewed Soren runtime update", 1
    )[0]
    assert "steps.deploy_initial.outcome == 'success'" in block
    assert "steps.deploy_retry.outcome == 'success'" in block
    assert "control/ops/vm_actions/ensure_corner_rotation_timer.sh" in block
    assert 'exec docich production $SHA' in block
