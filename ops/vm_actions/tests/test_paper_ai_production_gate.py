from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
ENABLE = ROOT / "ops/vm_actions/enable_paper_ai.sh"
DISABLE = ROOT / "ops/vm_actions/disable_paper_ai.sh"
WRAPPER = ROOT / "ops/vm_actions/run_paper_improve_with_ai_env.sh"
CANARY = ROOT / "ops/vm_actions/run_paper_ai_canary.sh"


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "docich"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "T"], check=True)
    (root / "tracked").write_text("ok\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return root, sha


def _receipt(home: Path, sha: str, *, age: int = 0) -> Path:
    directory = home / ".config" / "docich"
    directory.mkdir(parents=True, mode=0o700)
    path = directory / "paper-ai-canary.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "status": "ok",
        "sha": sha,
        "recorded_at": int(time.time()) - age,
        "research_backend": "websearch_verified_body",
        "source_count": 2,
        "asset_background": True,
        "direct_agent": "cloudflare-api:cf/qwen/qwen3-30b-a3b-fp8",
        "output_chars": 42,
        "output_sha256": "a" * 64,
        "publishing": False,
    }), encoding="utf-8")
    path.chmod(0o600)
    return path


def _production_env(soren: Path) -> None:
    soren.mkdir()
    (soren / ".env").write_text(
        "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID=" + "a" * 32 + "\n"
        "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN=SEARCH_TOKEN_123\n"
        "DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID=" + "b" * 32 + "\n"
        "CLOUDFLARE_API_TOKEN=DIRECT_TOKEN_456\n",
        encoding="utf-8",
    )


def _run_fixed(script: Path, *, root: Path, soren: Path | None, home: Path, sha: str):
    text = script.read_text(encoding="utf-8")
    text = text.replace('readonly root="/home/ubuntu/docich"', f'readonly root="{root}"')
    if soren is not None:
        text = text.replace('readonly soren_root="/home/ubuntu/soren"', f'readonly soren_root="{soren}"')
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "EXPECTED_SHA": sha}
    return subprocess.run(
        ["bash", "-c", text], cwd=root, env=env, text=True, capture_output=True,
    )


def test_enable_requires_fresh_exact_sha_receipt_and_writes_only_fixed_capabilities(tmp_path):
    root, sha = _repo(tmp_path)
    home, soren = tmp_path / "home", tmp_path / "soren"
    home.mkdir()
    _production_env(soren)
    _receipt(home, sha)

    run = _run_fixed(ENABLE, root=root, soren=soren, home=home, sha=sha)
    assert run.returncode == 0, run.stderr
    target = home / ".config" / "docich" / "paper-ai.env"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    values = dict(
        line.split("=", 1)
        for line in target.read_text(encoding="utf-8").splitlines()
        if line
    )
    assert values == {
        "DOCICH_PAPER_RESEARCH_BACKEND": "websearch",
        "DOCICH_REPLY_WEB_SEARCH_BACKEND": "cloudflare",
        "DOCICH_REPLY_WEB_SEARCH_ENABLED": "1",
        "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_PROVIDER": "ceramic",
        "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_GATEWAY_ID": "default",
        "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN": "SEARCH_TOKEN_123",
        "DOCICH_PAPER_SCRIPT_DIRECT_ENABLED": "1",
        "DOCICH_PAPER_IMPROVE_DIRECT_ENABLED": "1",
        "DOCICH_CHAT_CLOUDFLARE_ACCOUNT_ID": "b" * 32,
        "CLOUDFLARE_API_TOKEN": "DIRECT_TOKEN_456",
    }
    assert "OPENCODE" not in target.read_text(encoding="utf-8")
    assert "DISCORD" not in target.read_text(encoding="utf-8")


def test_enable_rejects_stale_or_wrong_sha_receipt_without_capability_file(tmp_path):
    for age, wrong in ((86401, False), (0, True)):
        case = tmp_path / f"case-{age}-{wrong}"
        case.mkdir()
        root, sha = _repo(case)
        home, soren = case / "home", case / "soren"
        home.mkdir()
        _production_env(soren)
        _receipt(home, "f" * 40 if wrong else sha, age=age)
        run = _run_fixed(ENABLE, root=root, soren=soren, home=home, sha=sha)
        assert run.returncode != 0
        assert not (home / ".config" / "docich" / "paper-ai.env").exists()


def test_disable_removes_only_regular_capability_file_and_is_idempotent(tmp_path):
    root, sha = _repo(tmp_path)
    home = tmp_path / "home"
    target = home / ".config" / "docich" / "paper-ai.env"
    target.parent.mkdir(parents=True)
    target.write_text("DOCICH_PAPER_SCRIPT_DIRECT_ENABLED=1\n", encoding="utf-8")
    target.chmod(0o600)
    unrelated = target.parent / "keep"
    unrelated.write_text("yes", encoding="utf-8")

    run = _run_fixed(DISABLE, root=root, soren=None, home=home, sha=sha)
    assert run.returncode == 0, run.stderr
    assert not target.exists()
    assert unrelated.read_text(encoding="utf-8") == "yes"
    again = _run_fixed(DISABLE, root=root, soren=None, home=home, sha=sha)
    assert again.returncode == 0


def test_improvement_wrapper_sources_fixed_file_without_putting_secret_in_argv(tmp_path):
    home = tmp_path / "home"
    env_file = home / ".config" / "docich" / "paper-ai.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("CLOUDFLARE_API_TOKEN=WRAPPED_SECRET\n", encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    run = subprocess.run(
        [
            "bash", str(WRAPPER), "--", "python3", "-c",
            "import os;print(os.environ.get('CLOUDFLARE_API_TOKEN',''))",
        ],
        env=env, text=True, capture_output=True,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "WRAPPED_SECRET"
    assert "WRAPPED_SECRET" not in " ".join(run.args)


def test_rotation_and_legacy_units_load_only_optional_paper_ai_capability_file():
    for name in (
        "docich-corner-rotation.service",
        "docich-retro-corner.service",
        "docich-paper-corner.service",
    ):
        text = (ROOT / "scripts" / "systemd" / name).read_text(encoding="utf-8")
        assert "EnvironmentFile=-%h/.config/docich/paper-ai.env" in text
        assert "CLOUDFLARE_API_TOKEN=" not in text


def test_canary_runner_invalidates_old_receipt_and_writes_safe_exact_sha_receipt():
    text = CANARY.read_text(encoding="utf-8")
    assert 'receipt="$receipt_dir/paper-ai-canary.json"' in text
    assert 'rm -f "$receipt"' in text
    assert '"sha": sha' in text
    assert '"recorded_at": int(time.time())' in text
    assert '"output_sha256": data["output_sha256"]' in text
    assert '"publishing": False' in text
    assert "summary" not in text.split('receipt = {', 1)[1].split('}', 1)[0]
