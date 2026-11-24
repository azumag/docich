"""Regression coverage for the NetHack #2271 acceptance-evidence judge.

These fixtures are synthetic diagnostics documents shaped like the owner-only
gateway output. They are not captures of the 2026-10-15 incident or of any
production run; they pin how the helper classifies a capture.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nethack_acceptance_evidence import (  # noqa: E402
    STATUS_ACTING,
    STATUS_STALLED,
    STATUS_UNKNOWN,
    _repair_masked_json as _repair,
    evaluate,
    main,
    render,
)

TOOL = Path(__file__).resolve().parents[1] / "nethack_acceptance_evidence.py"


def agent_line(count, masked=False):
    body = "***" if masked else str(count)
    return f"[agent] nethack: {body} 件のアクションを実行しました (brain=nethack)"


def game_pane(*, with_pager=False, with_vitals=True):
    lines = [" |..@....|", " ---------"]
    if with_vitals:
        lines.append("Dlvl:3 $:474 HP:8(10) Pw:2(2) AC:10 Xp:1")
    if with_pager:
        lines.append("Welcome back to the dungeon. --More--")
    return lines


#: Distinguishes "this document predates the head capture" from "the head rows
#: were captured and are empty".
NO_GAME_HEAD = object()


def doc(*, agent=None, game=None, active_game="nethack", generation=888,
        head="128d5c6e09c8409a5b8ac5fa4ea40d4fe6171093", game_head=NO_GAME_HEAD):
    panes = {
        "present": True,
        "phase": "ready",
        "active_game": active_game,
        "windows": ["nethack", "agent-g888"],
        "game": game if game is not None else game_pane(),
        "agent": agent if agent is not None else [],
    }
    if game_head is not NO_GAME_HEAD:
        panes["game_head"] = game_head
    return {
        "status": "diagnosed",
        "sha": head,
        "diagnostics": {
            "meta": {"docich_head": head},
            "game_switch": {"active_game": active_game, "active_generation": generation},
            "nethack_panes": panes,
        },
    }


@pytest.mark.parametrize("counts", [[1], [0, 1], [0, 0, 0, 4], [7, 9, 12]])
def test_positive_action_count_is_acting(counts):
    verdict = evaluate(doc(agent=[agent_line(c) for c in counts]))
    assert verdict["status"] == STATUS_ACTING
    assert verdict["action_count_latest"] == counts[-1]
    assert verdict["generation"] == 888
    assert verdict["corner_active_at_capture"] is True
    assert verdict["reasons"] == []


@pytest.mark.parametrize("counts", [[0, 0, 0], [0, 0, 0, 0, 0]])
def test_trailing_zero_run_is_stalled(counts):
    verdict = evaluate(doc(agent=[agent_line(c) for c in counts]))
    assert verdict["status"] == STATUS_STALLED
    assert verdict["trailing_zero_run"] == len(counts)
    assert verdict["action_count_latest"] == 0
    assert any("#2271 stall threshold" in reason for reason in verdict["reasons"])


def test_zero_run_below_threshold_is_unknown_not_stalled():
    verdict = evaluate(doc(agent=[agent_line(0), agent_line(0)]))
    assert verdict["status"] == STATUS_UNKNOWN
    assert any("shorter than the stall threshold" in r for r in verdict["reasons"])


def test_zeros_before_a_positive_action_do_not_stall():
    # A resumed game may sit on a pager for a couple of frames, then act: that
    # is the fixed behaviour, so only the *trailing* run decides.
    verdict = evaluate(doc(agent=[agent_line(0), agent_line(0), agent_line(0), agent_line(5)]))
    assert verdict["status"] == STATUS_ACTING
    assert verdict["trailing_zero_run"] == 0


def test_inactive_corner_capture_is_unknown_with_reason():
    verdict = evaluate(doc(agent=[], game=[], active_game="sorengame"))
    assert verdict["status"] == STATUS_UNKNOWN
    assert verdict["corner_active_at_capture"] is False
    assert any("corner not active" in r for r in verdict["reasons"])


def test_masked_action_counts_never_become_a_false_pass():
    verdict = evaluate(doc(agent=[agent_line(0, masked=True), agent_line(0, masked=True)]))
    assert verdict["status"] == STATUS_UNKNOWN
    assert verdict["action_counts"] == [None, None]
    assert any("redacted" in r for r in verdict["reasons"])


def test_masked_generation_is_reported_as_unknown():
    verdict = evaluate(doc(agent=[agent_line(3)], generation=888))
    assert verdict["generation"] == 888

    masked = doc(agent=[agent_line(3)])
    masked["diagnostics"]["game_switch"]["active_generation"] = "***5"
    assert evaluate(masked)["generation"] is None


def test_pager_text_is_reported_but_never_required():
    without = evaluate(doc(agent=[agent_line(2)]))
    assert without["status"] == STATUS_ACTING
    assert without["pager_text_visible"] is False
    assert without["pager_text_expected_visible"] is False

    with_pager = evaluate(doc(agent=[agent_line(2)],
                              game=game_pane(with_pager=True)))
    assert with_pager["status"] == STATUS_ACTING
    assert with_pager["pager_text_visible"] is True


def test_message_row_head_capture_makes_the_pager_decidable():
    """#2271: the top row is where ``--More--`` lives, so the head decides it."""
    head = ["Restoring save file...--More--", "Welcome back to the dungeon."]
    verdict = evaluate(doc(agent=[agent_line(0)] * 3, game_head=head))
    assert verdict["status"] == STATUS_STALLED  # the capture still reports the stall
    assert verdict["game_head_captured"] is True
    assert verdict["game_head_lines"] == 2
    assert verdict["pager_text_visible"] is True
    assert verdict["pager_text_expected_visible"] is True
    assert "message row captured: 2 head line(s)" in render(verdict)


def test_legacy_document_without_a_head_capture_reports_unavailable():
    verdict = evaluate(doc(agent=[agent_line(1)]))
    assert verdict["game_head_captured"] is False
    assert verdict["game_head_lines"] == 0
    assert verdict["pager_text_visible"] is False
    assert verdict["pager_text_expected_visible"] is False
    assert "no message-row head capture" in render(verdict)


def test_empty_head_capture_is_evidence_of_no_message_row():
    verdict = evaluate(doc(agent=[agent_line(1)], game_head=["", ""]))
    assert verdict["game_head_captured"] is True
    assert verdict["game_head_lines"] == 2
    assert verdict["pager_text_visible"] is False
    assert verdict["pager_text_expected_visible"] is True


def test_vitals_are_corroboration_only():
    blind = evaluate(doc(agent=[agent_line(4)], game=[" |..@....|"]))
    assert blind["status"] == STATUS_ACTING
    assert blind["vitals_visible"] is False


def test_empty_agent_pane_is_unknown_not_a_pass():
    verdict = evaluate(doc(agent=[]))
    assert verdict["status"] == STATUS_UNKNOWN
    assert any("no action lines" in r for r in verdict["reasons"])


@pytest.mark.parametrize("bad", [{}, {"diagnostics": None}, {"diagnostics": {}},
                                 {"diagnostics": {"nethack_panes": None}}])
def test_missing_sections_do_not_raise(bad):
    verdict = evaluate(bad)
    assert verdict["status"] == STATUS_UNKNOWN
    assert verdict["action_counts"] == []


def test_render_omits_pane_bodies():
    # Pane text can carry game content; the summary must not echo it.
    agent = [agent_line(1)]
    verdict = evaluate(doc(agent=agent))
    text = render(verdict)
    assert "件のアクション" not in text
    assert "status: acting" in text
    assert "generation: 888" in text


def test_cli_reads_a_file_and_reports_acting(tmp_path):
    path = tmp_path / "diag.json"
    path.write_text(json.dumps(doc(agent=[agent_line(3)])), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(TOOL), str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "status: acting" in proc.stdout


def test_cli_json_mode_and_unknown_exit_code(tmp_path):
    path = tmp_path / "diag.json"
    path.write_text(json.dumps(doc(agent=[], active_game="sorengame")), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(TOOL), "--json", str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 3
    payload = json.loads(proc.stdout)
    assert payload["status"] == STATUS_UNKNOWN


def test_cli_rejects_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(TOOL), str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    assert "invalid diagnostics JSON" in proc.stderr


def test_main_accepts_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(
        json.dumps(doc(agent=[agent_line(0), agent_line(0), agent_line(0)]))))
    assert main(["--json"]) == 1


# --- log-redaction repair -------------------------------------------------
# GitHub rewrites numeric substrings with ``***`` inside the captured JSON, so a
# real capture is not valid JSON as pasted. These shapes are taken from actual
# captures of the two diagnostics runs on 2026-10-20.

@pytest.mark.parametrize("masked", [
    '"at":1792176165.***7505',           # trailing digits masked
    '"at":179***3***1.0318093',          # mask repeated inside one number
    '"age_sec":***728',                  # leading digits masked
    '"scanned_entries":***',             # value is only a mask
    '"completed_at":179***95121.0718646',
])
def test_masked_numbers_repair_to_null(masked):
    repaired = json.loads("{" + _repair(masked) + "}")
    assert list(repaired.values()) == [None]


@pytest.mark.parametrize("masked,expected", [
    (r'"detail":"PAPER\u6***6\u7565\u5b9f\u9a13"', "PAPER\ufffd略実験"),
    (r'"d":"ok\u6***6"', "ok\ufffd"),
])
def test_masked_unicode_escapes_repair_to_a_valid_escape(masked, expected):
    # ``\unull`` is not parseable, so a masked escape must become a real escape.
    repaired = json.loads("{" + _repair(masked) + "}")
    assert list(repaired.values()) == [expected]


def test_repair_leaves_unmasked_json_untouched():
    original = '{"a":1,"b":"plain text","c":true,"d":null,"e":-2.5}'
    assert _repair(original) == original


def test_masked_capture_parses_end_to_end(tmp_path):
    # A masked capture of an inactive corner: repair must yield "unknown", and
    # must never be read as a pass just because parsing had to be salvaged.
    payload = ('{"status":"diagnosed","diagnostics":{"meta":{"docich_head":"128d5c6e09c8409a5b8ac5fa4ea40d4fe6171093"},'
               '"game_switch":{"active_game":"sorengame","active_generation":***},'
               '"nethack_panes":{"active_game":"sorengame","agent":[],"game":[]}}}')
    path = tmp_path / "masked.json"
    path.write_text(payload, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(TOOL), "--json", str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 3, proc.stderr
    result = json.loads(proc.stdout)
    assert result["status"] == STATUS_UNKNOWN
    assert result["generation"] is None
    assert result["docich_head"] == "128d5c6e09c8409a5b8ac5fa4ea40d4fe6171093"


def test_unrecoverable_json_still_fails_loudly(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"a": ', encoding="utf-8")
    proc = subprocess.run([sys.executable, str(TOOL), str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 2


@pytest.mark.parametrize('counts,status,zero_run', [
    ([5, None], STATUS_UNKNOWN, 0),
    ([0, None, 0, 0], STATUS_UNKNOWN, 2),
    ([None, 0, 0, 0], STATUS_STALLED, 3),
    ([None, 5], STATUS_ACTING, 0),
])
def test_unknown_observations_preserve_order(counts, status, zero_run):
    verdict = evaluate(doc(agent=[agent_line(c, masked=c is None) for c in counts]))
    assert verdict['status'] == status
    assert verdict['action_counts'] == counts
    assert verdict['action_count_latest'] == counts[-1]
    assert verdict['trailing_zero_run'] == zero_run


@pytest.mark.parametrize('counts', [[5], [0, 0, 0]])
@pytest.mark.parametrize('active_game', ['sorengame', None, ''])
def test_inactive_corner_never_uses_residual_logs(counts, active_game):
    verdict = evaluate(doc(agent=[agent_line(c) for c in counts], active_game=active_game))
    assert verdict['status'] == STATUS_UNKNOWN


def test_numeric_repair_preserves_quoted_masks_and_escaped_strings():
    strings = ['***', '123***45', 'quote " ***', r'literal \u6***6',
               r'backslash \\ then ***']
    raw = json.dumps({'text': strings})[:-1] + ', "age":12***3}'
    fixed = json.loads(_repair(raw))
    assert fixed == {'text': strings, 'age': None}


def test_repair_does_not_salvage_arbitrary_invalid_tokens():
    with pytest.raises(ValueError):
        json.loads(_repair('{"age":garbage***123}'))


def test_numeric_repair_does_not_erase_latest_unknown_action(tmp_path):
    payload = json.dumps(doc(agent=[agent_line(5), agent_line(None, masked=True)]))
    payload = payload.replace('"active_generation": 888', '"active_generation": 8***8')
    path = tmp_path / 'masked.json'
    path.write_text(payload, encoding='utf-8')
    proc = subprocess.run([sys.executable, str(TOOL), '--json', str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 3, proc.stderr
    result = json.loads(proc.stdout)
    assert result['status'] == STATUS_UNKNOWN
    assert result['action_counts'] == [5, None]
    assert result['action_count_latest'] is None


def test_stalled_cli_is_nonzero(tmp_path):
    path = tmp_path / 'stalled.json'
    path.write_text(json.dumps(doc(agent=[agent_line(0)] * 3)), encoding='utf-8')
    proc = subprocess.run([sys.executable, str(TOOL), '--json', str(path)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 1
    assert json.loads(proc.stdout)['status'] == STATUS_STALLED
