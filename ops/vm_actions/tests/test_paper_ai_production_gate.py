from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
import time
import tempfile
import unittest
from unittest.mock import patch

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
    return root.resolve(), sha


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


class PaperAiProductionGateTests(unittest.TestCase):
    def temp(self):
        return Path(tempfile.mkdtemp(prefix="paper-ai-gate-"))

    def test_enable_requires_fresh_exact_sha_receipt_and_writes_only_fixed_capabilities(self):
        tmp_path = self.temp()
        root, sha = _repo(tmp_path)
        home, soren = tmp_path / "home", tmp_path / "soren"
        home.mkdir()
        _production_env(soren)
        _receipt(home, sha)

        run = _run_fixed(ENABLE, root=root, soren=soren, home=home, sha=sha)
        self.assertEqual(run.returncode, 0, run.stderr)
        target = home / ".config" / "docich" / "paper-ai.env"
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        values = dict(
            line.split("=", 1)
            for line in target.read_text(encoding="utf-8").splitlines()
            if line
        )
        self.assertEqual(values, {
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
        })
        body = target.read_text(encoding="utf-8")
        self.assertNotIn("OPENCODE", body)
        self.assertNotIn("DISCORD", body)
        self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
        self.assertEqual(list(target.parent.glob(".paper-ai.env.*")), [])
        previous_inode = target.stat().st_ino
        target.write_text("previous-capability\n", encoding="utf-8")
        again = _run_fixed(ENABLE, root=root, soren=soren, home=home, sha=sha)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotEqual(target.stat().st_ino, previous_inode)
        self.assertEqual(target.read_text(encoding="utf-8"), body)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(list(target.parent.glob(".paper-ai.env.*")), [])

    def test_enable_rejects_stale_or_wrong_sha_receipt_without_capability_file(self):
        for age, wrong in ((86401, False), (0, True)):
            with self.subTest(age=age, wrong=wrong):
                case = self.temp()
                root, sha = _repo(case)
                home, soren = case / "home", case / "soren"
                home.mkdir()
                _production_env(soren)
                _receipt(home, "f" * 40 if wrong else sha, age=age)
                run = _run_fixed(ENABLE, root=root, soren=soren, home=home, sha=sha)
                self.assertNotEqual(run.returncode, 0)
                self.assertFalse((home / ".config" / "docich" / "paper-ai.env").exists())

    def test_enable_rejects_nonregular_and_symlink_destinations_without_writing(self):
        for kind in ("directory", "directory-symlink", "file-symlink", "dangling-symlink", "fifo"):
            with self.subTest(kind=kind):
                case = self.temp()
                root, sha = _repo(case)
                home, soren = case / "home", case / "soren"
                home.mkdir()
                _production_env(soren)
                _receipt(home, sha)
                target = home / ".config/docich/paper-ai.env"
                outside = case / "outside"
                if kind == "directory":
                    target.mkdir()
                elif kind == "directory-symlink":
                    outside.mkdir()
                    target.symlink_to(outside, target_is_directory=True)
                elif kind == "file-symlink":
                    outside.write_text("keep\n", encoding="utf-8")
                    target.symlink_to(outside)
                elif kind == "dangling-symlink":
                    target.symlink_to(outside)
                else:
                    os.mkfifo(target)

                before = target.lstat()
                run = _run_fixed(ENABLE, root=root, soren=soren, home=home, sha=sha)
                self.assertNotEqual(run.returncode, 0)
                self.assertNotIn("paper_ai_enabled", run.stdout)
                self.assertEqual(target.lstat().st_ino, before.st_ino)
                self.assertEqual(target.lstat().st_mode, before.st_mode)
                self.assertEqual(list(target.parent.glob(".paper-ai.env.*")), [])
                if kind == "directory":
                    self.assertEqual(list(target.iterdir()), [])
                elif kind == "directory-symlink":
                    self.assertEqual(list(outside.iterdir()), [])
                elif kind == "file-symlink":
                    self.assertEqual(outside.read_text(encoding="utf-8"), "keep\n")
                elif kind == "dangling-symlink":
                    self.assertFalse(outside.exists())

    def test_enable_rename_failure_preserves_old_file_and_cleans_temporary_file(self):
        case = self.temp()
        root, sha = _repo(case)
        home, soren = case / "home", case / "soren"
        home.mkdir()
        _production_env(soren)
        _receipt(home, sha)
        target = home / ".config/docich/paper-ai.env"
        target.write_text("previous-capability\n", encoding="utf-8")
        bin_dir = case / "bin"
        bin_dir.mkdir()
        fake_mv = bin_dir / "mv"
        fake_mv.write_text("#!/usr/bin/env bash\nexit 91\n", encoding="utf-8")
        fake_mv.chmod(0o700)
        with patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"}):
            run = _run_fixed(ENABLE, root=root, soren=soren, home=home, sha=sha)
        self.assertEqual(run.returncode, 91)
        self.assertNotIn("paper_ai_enabled", run.stdout)
        self.assertEqual(target.read_text(encoding="utf-8"), "previous-capability\n")
        self.assertEqual(list(target.parent.glob(".paper-ai.env.*")), [])

    def test_enable_rename_never_treats_destination_as_directory(self):
        case = self.temp()
        root, sha = _repo(case)
        home, soren = case / "home", case / "soren"
        home.mkdir()
        _production_env(soren)
        _receipt(home, sha)
        target = home / ".config/docich/paper-ai.env"
        real_mv = shutil.which("mv")
        self.assertIsNotNone(real_mv)
        bin_dir = case / "bin"
        bin_dir.mkdir()
        fake_mv = bin_dir / "mv"
        fake_mv.write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            'mkdir -- "${@: -1}"\n'
            f'exec "{real_mv}" "$@"\n',
            encoding="utf-8",
        )
        fake_mv.chmod(0o700)
        with patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"}):
            run = _run_fixed(ENABLE, root=root, soren=soren, home=home, sha=sha)
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn("paper_ai_enabled", run.stdout)
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(list(target.parent.glob(".paper-ai.env.*")), [])

    def test_disable_removes_only_regular_capability_file_and_is_idempotent(self):
        tmp_path = self.temp()
        root, sha = _repo(tmp_path)
        home = tmp_path / "home"
        target = home / ".config" / "docich" / "paper-ai.env"
        target.parent.mkdir(parents=True)
        target.write_text("DOCICH_PAPER_SCRIPT_DIRECT_ENABLED=1\n", encoding="utf-8")
        target.chmod(0o600)
        unrelated = target.parent / "keep"
        unrelated.write_text("yes", encoding="utf-8")

        run = _run_fixed(DISABLE, root=root, soren=None, home=home, sha=sha)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(target.exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "yes")
        again = _run_fixed(DISABLE, root=root, soren=None, home=home, sha=sha)
        self.assertEqual(again.returncode, 0)

    def test_improvement_wrapper_sources_fixed_file_without_putting_secret_in_argv(self):
        tmp_path = self.temp()
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
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "WRAPPED_SECRET")
        self.assertNotIn("WRAPPED_SECRET", " ".join(run.args))

    def test_rotation_and_legacy_units_load_only_optional_paper_ai_capability_file(self):
        for name in (
            "docich-corner-rotation.service",
            "docich-retro-corner.service",
            "docich-paper-corner.service",
        ):
            with self.subTest(name=name):
                text = (ROOT / "scripts" / "systemd" / name).read_text(encoding="utf-8")
                self.assertIn("EnvironmentFile=-%h/.config/docich/paper-ai.env", text)
                self.assertNotIn("CLOUDFLARE_API_TOKEN=", text)

    def test_canary_runner_invalidates_old_receipt_and_writes_safe_exact_sha_receipt(self):
        text = CANARY.read_text(encoding="utf-8")
        self.assertIn('receipt="$receipt_dir/paper-ai-canary.json"', text)
        self.assertIn('unlink "$receipt"', text)
        self.assertIn('"sha": sha', text)
        self.assertIn('"recorded_at": int(time.time())', text)
        self.assertIn('"output_sha256": data["output_sha256"]', text)
        self.assertIn('"publishing": False', text)
        receipt_block = text.split('receipt = {', 1)[1].split('}', 1)[0]
        self.assertNotIn("summary", receipt_block)


if __name__ == "__main__":
    unittest.main()
