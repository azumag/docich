from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "reply_radio_cloudflare_rollout.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "reply-radio-cloudflare-rollout.yml"
EPOCH = ROOT / "ops" / "vm_actions" / "reply_radio_cloudflare_rollout_epoch"


def test_rollout_shell_is_valid_and_one_time():
    run = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert EPOCH.read_text(encoding="utf-8") == "2026-10-24-v1\n"
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "paths:\n      - 'ops/vm_actions/reply_radio_cloudflare_rollout_epoch'" in workflow
    assert "github.actor_id == 9018513" in workflow
    assert "github.triggering_actor == 'azumag'" in workflow
    assert "github.ref_protected == true" in workflow
    assert 'data.get("status") == "configured"' in workflow
    assert 'data.get("sha") == sys.argv[2]' in workflow
    assert 'exec docich production $SHA' in workflow


def test_real_canaries_precede_mutation_and_roll_back_on_failure():
    text = SCRIPT.read_text(encoding="utf-8")
    comment = text.index("from docich.comment_classifier import web_batch")
    radio = text.index("python3 -P -m docich.radio.consumer")
    backup = text.index("backup_file=\"$(mktemp")
    patch = text.index("Atomically replace only this rollout's marked block")
    assert comment < radio < backup < patch
    assert "cp -p -- \"$backup_file\" \"$env_file\"" in text
    assert "rollback_needed=1" in text
    assert "restart_radio_worker.sh" in text
    assert "restart_chat_kick_workers.sh" in text
    assert "DOCICH_ALLOW_REAL_AI=1" not in _persistent_block(text)
    assert "DOCICH_JEV_ROUTE=" not in _persistent_block(text)


def test_rollout_scope_is_narrow_and_fail_closed():
    text = SCRIPT.read_text(encoding="utf-8")
    block = _persistent_block(text)
    assert "DOCICH_REPLY_WEB_DIRECT_BATCH_ENABLED=1" in block
    assert "DOCICH_REPLY_WEB_SEARCH_BACKEND=cloudflare" in block
    assert "DOCICH_RADIO_NATIVE_CONSUMER_ENABLED=1" in block
    assert "DOCICH_RADIO_NATIVE_CORNERS=ai_knowledge" in block
    assert "DOCICH_RADIO_NATIVE_AGENTS=cloudflare-api:cf/qwen/qwen3-30b-a3b-fp8" in block
    assert "finance" not in block
    assert "RADIO_GENERATION_ENABLED" not in block
    assert "opencode" not in text.lower()
    assert "COMMENT_CLASSIFIER_BACKEND=jev" in text
    assert "DOCICH_REPLY_ROUTING_ENABLED=1" in text
    assert "verified_web_rollout_enabled" in text


def _persistent_block(text: str) -> str:
    start = text.index('block = "\\n".join([')
    end = text.index("]) + \"\\n\"", start)
    return text[start:end]
