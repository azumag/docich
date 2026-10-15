"""One-shot, fixed-source regression verification; never updates a Git ref."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

BASE = "6fecdaad1f8cfb1f26f3f2054fe12d44c590f4cf"
SOURCES = {
    "src/docich/nethack_startup.py": "7a82eb0bab21d69605882daa5a9dfddde3859679",
    "src/docich/nethack_spectator.py": "9dfee95d009a96114b212172d467670fa6183509",
}
TEST = "tests/test_nethack_resume_pager_and_translate.py"


def git_blob(path):
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def check_base():
    parent = subprocess.check_output(["git", "rev-parse", "HEAD^"], text=True).strip()
    assert parent == BASE, (parent, BASE)
    for path, expected in SOURCES.items():
        assert git_blob(path) == expected, path
    entries = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    rules = [path for path in entries if Path(path).name in {"AGENTS.md", "AGENTS.override.md"}]
    print("RULE_FILES=" + json.dumps(rules), flush=True)
    assert rules == ["AGENTS.md"], "Re-review additional repository instructions"
    print("VERIFIED_BASE=" + BASE, flush=True)


def baseline():
    check_base()
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", TEST,
                             "--junitxml=/tmp/nethack-2271-red.xml"], check=False)
    cases = list(ET.parse("/tmp/nethack-2271-red.xml").getroot().iter("testcase"))
    failures = [case for case in cases if case.find("failure") is not None]
    errors = [case for case in cases if case.find("error") is not None]
    skips = [case for case in cases if case.find("skipped") is not None]
    assert result.returncode == 1 and len(cases) == 28
    assert len(failures) == 18 and not errors and not skips
    print("BASELINE_REPRODUCED: 18 failed, 10 passed, 0 errors, 0 skipped", flush=True)


def patch():
    check_base()
    path = Path("src/docich/nethack_startup.py")
    source = path.read_text()
    before = '''No save/restore, recovery, gameplay prompt, or new-run restart automation lives here,
with one exception: the ``--More--`` of NetHack's ``Restoring save file...`` banner is
advanced (see ``_RESTORE_MORE_RE``), because a resumed run never shows a creation prompt
and would otherwise sit on it forever.'''
    after = '''No save/restore, recovery, or new-run restart automation lives here.
A normalized ``more`` pager can precede the first complete map of a resumed
run, with the player hidden by text and no character-creation prompt seen.
Advance it within the same startup budget; retain the legacy restore-banner
fallback below for captures that the normalizer cannot classify.'''
    assert source.count(before) == 1
    source = source.replace(before, after)
    before = '''        if key is None and not has_gameplay and _RESTORE_MORE_RE.search(text):
'''
    after = '''        if key is None and obs.prompt == "more":
            # Resume pagers can cover '@' before we ever observe a complete
            # map. They do not require character creation in this session.
            # Use the normalizer's prompt classification, not a substring:
            # questions/selection/ambiguous wraps remain blocked. The common
            # action, observation, time and duplicate-frame limits still apply.
            key = " "
        if key is None and not has_gameplay and _RESTORE_MORE_RE.search(text):
'''
    assert source.count(before) == 1
    path.write_text(source.replace(before, after))
    path = Path("src/docich/nethack_spectator.py")
    source = path.read_text()
    assert source.count('<html lang="ja">') == 2
    assert source.count('<meta charset="utf-8">') == 2
    source = source.replace('<html lang="ja">', '<html lang="ja" translate="no">')
    source = source.replace('<meta charset="utf-8">',
                            '<meta charset="utf-8">\n<meta name="google" content="notranslate">')
    path.write_text(source)
    subprocess.run(["git", "diff", "--check"], check=True)
    subprocess.run(["git", "diff", "--stat"], check=True)


def publish():
    # Only immutable public source blobs are stored. No commits, refs, PRs,
    # production workflows, VM requests, secrets or runtime data are modified.
    assert os.environ.get("GITHUB_REPOSITORY") == "azumag/docich"
    assert os.environ.get("GITHUB_REF") == "refs/heads/verify/nethack-2271-20261015"
    changed = subprocess.check_output(["git", "diff", "--name-only"], text=True).splitlines()
    assert set(changed) == set(SOURCES), changed
    subprocess.run(["git", "diff", "--check"], check=True)
    blobs = {}
    for path in [*SOURCES, TEST]:
        expected = git_blob(path)
        payload = json.dumps({"content": Path(path).read_text(), "encoding": "utf-8"})
        response = subprocess.run(
            ["gh", "api", "--method", "POST", "repos/azumag/docich/git/blobs", "--input", "-"],
            input=payload, text=True, capture_output=True, check=False,
        )
        if response.returncode:
            raise RuntimeError("source blob publication failed")
        actual = json.loads(response.stdout)["sha"]
        assert actual == expected, path
        blobs[path] = actual
    print("VERIFIED_SOURCE_BLOBS=" + json.dumps(blobs, sort_keys=True), flush=True)


if __name__ == "__main__":
    {"red": baseline, "patch": patch, "publish": publish}[sys.argv[1]]()
