"""Check the test image's narrow COPY inputs without requiring a Docker daemon."""
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "containers/discord-chat/Dockerfile"


def _test_image_copies():
    # The current Dockerfile uses exact COPY paths and inherits runtime in test.
    allowed = [line[1:] for line in DOCKERFILE.with_suffix(".dockerignore").read_text().splitlines()
               if line.startswith("!")]
    assert all(not any(char in path for char in "*?[") for path in allowed)
    for line in DOCKERFILE.read_text().replace("\\\n", " ").splitlines():
        words = shlex.split(line)
        if not words or words[0] != "COPY":
            continue
        sources, target = words[1:-1], Path(words[-1])
        if target.is_absolute():
            target = target.relative_to("/opt/docich")
        for source in sources:
            paths = [path for path in allowed
                     if path == source or path.startswith(source.rstrip("/") + "/")]
            assert paths, f"COPY input excluded from build context: {source}"
            for path in paths:
                if source.endswith("/"):
                    destination = target / Path(path).relative_to(source.rstrip("/"))
                elif words[-1].endswith("/") or len(sources) > 1:
                    destination = target / Path(path).name
                else:
                    destination = target
                yield ROOT / path, destination


def test_classifier_dependency_is_exact_and_test_target_only():
    runtime, test = DOCKERFILE.read_text().split("FROM runtime AS test", 1)
    dependency = "src/docich/comment_classifier/active_game.py"
    assert "comment_classifier" not in runtime
    assert dependency in shlex.split(test.replace("\\\n", " "))
    assert (ROOT / dependency, Path(dependency)) in list(_test_image_copies())


def test_narrow_test_image_can_collect_all_copied_tests(tmp_path):
    for source, destination in _test_image_copies():
        copied = tmp_path / destination
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, copied)
    # -I ignores ambient Python paths; the repository cannot mask missing copies.
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); import pytest; "
        "raise SystemExit(pytest.main(['--collect-only', '-q', '-p', "
        "'no:cacheprovider', sys.argv[2]]))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script,
         str(tmp_path / "src"), str(tmp_path / "tests")],
        cwd=tmp_path, env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout
