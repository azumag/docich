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
    assert parent == "2b0ae5b8402c7142df4a37e3948677940aa7c052", parent
    for path, expected in SOURCES.items():
        assert git_blob(path) == expected, path
    entries = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    rules = [path for path in entries if Path(path).name in {"AGENTS.md", "AGENTS.override.md"}]
    print("RULE_FILES=" + json.dumps(rules), flush=True)
    assert rules == ["AGENTS.md"], "Re-review additional repository instructions"
    print("VERIFIED_BASE=" + BASE, flush=True)


def prepare_tests():
    assert git_blob(TEST) == "a9041664c6a52e4f77c603f026a7b0107e164c86"
    path = Path(TEST)
    source = path.read_text()
    source = source.replace('from html.parser import HTMLParser\n',
                            'from html.parser import HTMLParser\nfrom types import SimpleNamespace\n\nfrom docich.adapters.base import Observation\nfrom docich.agent.brains import build_brain\n')
    before = 'def test_recognized_pager_does_not_require_a_visible_status_block():\n    startup = NethackStartup(enabled=True, clock=lambda: 0.0)\n'
    after = 'def test_recognized_pager_after_restore_does_not_require_visible_status():\n    startup = NethackStartup(enabled=True, clock=lambda: 0.0)\n    assert [a.text for a in startup.consider(\n        normalize_tty("Restoring save file...--More--")\n    )] == [" "]\n'
    assert source.count(before) == 1
    source = source.replace(before, after)
    source += '\n\ndef test_playerless_pager_without_resume_evidence_holds():\n    startup = NethackStartup(enabled=True, clock=lambda: 0.0)\n    obs = normalize_tty(tty_frame("multiline", status=False))\n    assert obs.prompt == "more"\n    assert startup.consider(obs) == []\n    assert startup.actions == 0\n\n\n@pytest.mark.parametrize("status", [\n    "Dlvl:1 HP:0(10)",\n    "Dlvl:1 HP:8(0)",\n    "Dlvl:1 Xp:1",\n    "HP:8(10) Xp:1",\n])\ndef test_incomplete_or_dead_vitals_do_not_prove_resume(status):\n    lines = tty_frame().splitlines()\n    lines[23] = status\n    startup = NethackStartup(enabled=True, clock=lambda: 0.0)\n    assert startup.consider(normalize_tty("\\n".join(lines))) == []\n    assert startup.actions == 0\n\n\ndef test_restore_evidence_does_not_rearm_exhaustion():\n    clock = [0.0]\n    startup = NethackStartup(enabled=True, clock=lambda: clock[0])\n    assert [a.text for a in startup.consider(\n        normalize_tty("Restoring save file...--More--")\n    )] == [" "]\n    clock[0] = startup.TIMEOUT_S\n    assert startup.consider(normalize_tty(tty_frame(status=False))) == []\n    assert startup.state == "exhausted" and startup.actions == 1\n\n\ndef startup_brain():\n    game = SimpleNamespace(name="nethack", adapter="cli", raw={\n        "cli": {"cols": 80, "rows": 24},\n        "nethack": {"startup": {"enabled": True}},\n    }, agent=SimpleNamespace(brain="nethack"))\n    brain = build_brain(SimpleNamespace(), game)\n    brain.startup.clock = lambda: 0.0\n    return brain\n\n\ndef observation(text):\n    return Observation(game="nethack", title="NetHack", adapter="cli",\n                       ts=1, kind="text", text=text)\n\n\ndef test_rejected_restore_does_not_authorize_unproven_pager():\n    brain = startup_brain()\n    before = dict(brain.startup.__dict__)\n    actions = brain.decide(observation("Restoring save file...--More--"))\n    assert [a.text for a in actions] == [" "]\n    different = observation("Restore save? [yn]")\n    assert not brain.validate_action(actions[0], different)\n    brain.discard_action_plan()\n    assert brain.startup.__dict__ == before\n    assert not getattr(brain.startup, "_restore_seen", False)\n    assert brain.decide(observation(tty_frame(status=False))) == []\n    assert brain.startup.actions == 0\n\n\ndef test_accepted_restore_allows_pager_but_revalidates_frame():\n    brain = startup_brain()\n    restore = observation("Restoring save file...--More--")\n    action = brain.decide(restore)[0]\n    assert brain.validate_action(action, restore)\n    brain.action_sent(action)\n    assert brain.startup.actions == 1\n    page = observation(tty_frame("multiline", status=False))\n    actions = brain.decide(page)\n    assert [a.text for a in actions] == [" "]\n    assert not brain.validate_action(actions[0], observation("Really save? [yn] (n)"))\n    brain.discard_action_plan()\n    assert brain.startup.actions == 1\n    action = brain.decide(page)[0]\n    assert brain.validate_action(action, page)\n    brain.action_sent(action)\n    assert brain.startup.actions == 2\n    assert brain.decide(observation(tty_frame("gameplay", player=True)))\n    assert brain.startup.state == "gameplay"\n'
    path.write_text(source)
    assert git_blob(TEST) == "b7a4dab09cf2aadc076c625f13f516b455562823"


def baseline():
    check_base()
    prepare_tests()
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", TEST,
                             "--junitxml=/tmp/nethack-2271-red.xml"], check=False)
    cases = list(ET.parse("/tmp/nethack-2271-red.xml").getroot().iter("testcase"))
    failures = [case for case in cases if case.find("failure") is not None]
    errors = [case for case in cases if case.find("error") is not None]
    skips = [case for case in cases if case.find("skipped") is not None]
    assert result.returncode == 1 and len(cases) == 36
    assert len(failures) == 19 and not errors and not skips
    print("BASELINE_REPRODUCED: 19 failed, 17 passed, 0 errors, 0 skipped", flush=True)


def patch():
    check_base()
    assert git_blob(TEST) == "b7a4dab09cf2aadc076c625f13f516b455562823"
    path = Path("src/docich/nethack_startup.py")
    source = path.read_text()
    before = '''No save/restore, recovery, gameplay prompt, or new-run restart automation lives here,
with one exception: the ``--More--`` of NetHack's ``Restoring save file...`` banner is
advanced (see ``_RESTORE_MORE_RE``), because a resumed run never shows a creation prompt
and would otherwise sit on it forever.'''
    after = '''No save/restore, recovery, or new-run restart automation lives here.
Resume pagers can hide the player before the first complete map. Advance a
normalized ``more`` only with visible living-game vitals or a previously
accepted exact restore banner, within the existing startup budgets. The
legacy restore-banner and character-creation paths remain separate.'''
    assert source.count(before) == 1
    source = source.replace(before, after)
    before = '        self._creation_seen = False\n'
    after = before + '        self._restore_seen = False\n'
    assert source.count(before) == 1
    source = source.replace(before, after)
    before = '        if key is None and not has_gameplay and _RESTORE_MORE_RE.search(text):\n            key = " "\n'
    after = '        restore_more = not has_gameplay and bool(_RESTORE_MORE_RE.search(text))\n        if key is None and restore_more:\n            key = " "\n        if key is None and obs.prompt == "more" and (\n            self._restore_seen\n            or (\n                obs.vitals.dungeon_level is not None\n                and obs.vitals.hp is not None and obs.vitals.hp > 0\n                and obs.vitals.hp_max is not None and obs.vitals.hp_max > 0\n            )\n        ):\n            # A resumed game\'s text can cover \'@\' without character creation\n            # in this session. Only recognized pagers with resume evidence\n            # qualify; arbitrary pre-game \'--More--\' remains blocked. Common\n            # budgets and duplicate-frame suppression still apply below.\n            key = " "\n'
    assert source.count(before) == 1
    source = source.replace(before, after)
    before = '        self._last_answered = text\n'
    after = '        if restore_more and key == " ":\n            # The brain rolls this evidence back if fresh-frame validation\n            # rejects the action before transport (startup checkpoint).\n            self._restore_seen = True\n        self._last_answered = text\n'
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
    assert set(changed) == set(SOURCES) | {TEST}, changed
    assert git_blob(TEST) == "b7a4dab09cf2aadc076c625f13f516b455562823"
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
