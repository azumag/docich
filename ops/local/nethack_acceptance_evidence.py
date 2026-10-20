#!/usr/bin/env python3
"""Judge NetHack corner acceptance evidence from a read-only diagnostics capture.

Issue #2271 requires production evidence that a resumed NetHack run actually
processes a text message and keeps acting, rather than emitting an endless run
of "0 件のアクションを実行しました". The owner-only gateway's read-only
``diagnostics`` operation is the only sanctioned way to observe production, and
it returns one sanitized JSON document; nothing else here is read from the VM.

This helper turns that document into an explicit, reproducible verdict so the
acceptance call does not depend on how someone reads the JSON by hand. It is
read-only by construction: it parses a file (or stdin) and prints a summary.

What the current collector can and cannot prove (checked 2026-10-20):

- ``nethack_panes.agent`` is the ``agent-g{generation}`` pane tail, so the
  trailing run of ``nethack: N 件のアクションを実行しました`` lines is visible.
  This is the decisive signal for "the corner kept acting".
- ``nethack_panes.game`` is the game pane tail (last 14 non-blank lines). The
  ``--More--`` prompt renders on the top screen row, so the pager text is
  structurally outside that window. A capture is therefore never failed for a
  missing pager string; it is reported as unavailable, which is a collector
  limitation rather than a corner defect.
- The capture must be taken *while* the corner is active. After the run ends the
  panes are empty and ``active_game`` returns to the previous game, so a
  post-run capture cannot attest the run's action counts.

Unicode masking: the GitHub Actions log redacts some numeric substrings to
``***``, which also makes the captured JSON invalid. Repairs are conservative —
a masked number becomes ``null`` (every field this helper reads is Optional
already) and nothing is ever invented from a mask, so a masked capture degrades
to "unknown" instead of a false pass.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: The corner under test, and the acceptance statuses this helper can return.
CORNER = "nethack"
STATUS_ACTING = "acting"
STATUS_STALLED = "stalled"
STATUS_UNKNOWN = "unknown"

#: ``[agent] nethack: 12 件のアクションを実行しました (brain=nethack)`` as emitted
#: by the agent loop. Only the count is needed; the brain tag is not asserted.
_ACTION_RE = re.compile(r"nethack:\s*(?P<count>\d+)\s*件のアクション")
#: NetHack's own pager/catch-all prompt, when it lands inside the pane window.
_MORE_RE = re.compile(r"--\s*more\s*--", re.IGNORECASE)
#: A redaction marker inside a value that should be numeric.
_MASK_RE = re.compile(r"\*{2,}")
#: Status-line vitals, used only as corroboration that a real game screen is up.
_VITALS_RE = re.compile(r"\b(?:Dlvl|HP|Xp)\s*:", re.IGNORECASE)

#: A trailing run this long that is entirely zero-count is a stall, not a pause.
STALL_RUN = 3


def _repair_masked_json(raw):
    """Make a log-masked diagnostics payload parseable without inventing data.

    GitHub's log redaction rewrites numeric substrings inside otherwise valid
    JSON, e.g. ``"at":1792176165.***7505``, so the document no longer parses.
    Every masked value is replaced with ``null``: the mask means the real value
    is unknown, and a fabricated number could turn a stall into a pass. The
    substitution only touches ``***`` runs adjacent to digits, so genuine
    string content elsewhere is left alone.
    """
    # A masked ``\uXXXX`` escape must become a *valid* escape: rewriting it to
    # ``null`` yields ``\unull``, which is not parseable. U+FFFD is the honest
    # placeholder for "a character was redacted", and this helper only ever
    # reads fixed ASCII keys, so no decision depends on the substituted glyph.
    raw = re.sub(r"\\u[\dA-Fa-f]*\*{2,}[\dA-Fa-f]*", "\\\\ufffd", raw)
    # A masked number: a numeric token containing at least one mask run. The
    # mask can appear more than once inside one number (``179***3***1.0318093``)
    # or swallow the leading digits entirely (``"age_sec":***728``), and digits
    # survive on either side, so the whole token has to be matched rather than
    # the mask alone. Rewriting only the mask leaves fragments such as
    # ``1792176165.null`` or ``null***1.0318093``, which are still invalid JSON.
    # ``-?`` and the bare-``***`` alternative cover masked negatives and values
    # that are nothing but a mask.
    return re.sub(r"(?:-?\d[\d.eE+-]*)?(?:\*{2,}[\d.eE+-]*)+", "null", raw)


def _dig(data, *path):
    """Walk nested dicts, returning None instead of raising on any gap."""
    node = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _action_counts(lines):
    """Return (counts, masked) parsed from agent pane lines, in order."""
    counts, masked = [], False
    for line in _as_lines(lines):
        if _MASK_RE.search(line) and "件のアクション" in line:
            masked = True
            continue
        match = _ACTION_RE.search(line)
        if match is not None:
            counts.append(int(match.group("count")))
    return counts, masked


def _as_lines(value):
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _trailing_zero_run(counts):
    run = 0
    for count in reversed(counts):
        if count != 0:
            break
        run += 1
    return run


def _generation(data):
    """Read the active generation, which the log mask can render unreadable."""
    for path in (("game_switch", "active_generation"),
                 ("game_switch", "generation"),
                 ("corners", "game_switch", "active_generation")):
        value = _dig(data, *path)
        if type(value) is int:
            return value
        if isinstance(value, str):
            if _MASK_RE.search(value):
                return None
            if value.isdigit():
                return int(value)
    return None


def evaluate(doc):
    """Return the acceptance judgement for one diagnostics document.

    The verdict distinguishes three outcomes rather than collapsing them:

    - ``acting``: the agent pane shows a run of counts where the latest action
      count is positive, i.e. the resumed game kept taking actions.
    - ``stalled``: the capture caught a trailing run of zero-count lines, which
      is exactly the #2271 symptom.
    - ``unknown``: the capture cannot decide (corner inactive, panes empty, or
      the only useful numbers were masked by log redaction).
    """
    diag = doc.get("diagnostics") if isinstance(doc, dict) else None
    if not isinstance(diag, dict):
        diag = doc if isinstance(doc, dict) else {}

    panes = diag.get("nethack_panes") or {}
    active_game = _dig(diag, "nethack_panes", "active_game")
    agent_lines = _as_lines(_dig(diag, "nethack_panes", "agent"))
    game_lines = _as_lines(_dig(diag, "nethack_panes", "game"))
    windows = _as_lines(_dig(diag, "nethack_panes", "windows"))

    counts, masked = _action_counts(agent_lines)
    zero_run = _trailing_zero_run(counts)

    if counts:
        status = STATUS_ACTING if counts[-1] > 0 else STATUS_STALLED
    elif masked:
        status = STATUS_UNKNOWN
    else:
        status = STATUS_UNKNOWN

    # Pager text is expected to be absent by construction; report, never fail.
    pager_seen = any(_MORE_RE.search(line) for line in game_lines + agent_lines)
    vitals_seen = any(_VITALS_RE.search(line) for line in game_lines)

    reasons = []
    if status is STATUS_UNKNOWN:
        if active_game != CORNER:
            reasons.append(
                f"corner not active for this capture (active_game={active_game!r}); "
                "panes are empty after a run ends"
            )
        elif masked:
            reasons.append("action counts were redacted by log masking")
        elif not agent_lines:
            reasons.append("agent pane captured but no action lines present")
    if status is STATUS_STALLED:
        reasons.append(
            f"trailing run of {zero_run} zero-action line(s); "
            f"#2271 stall threshold is {STALL_RUN}"
        )
        if zero_run < STALL_RUN:
            status = STATUS_UNKNOWN
            reasons.append("zero run is shorter than the stall threshold")

    return {
        "status": status,
        "corner_active_at_capture": active_game == CORNER,
        "active_game": active_game,
        "generation": _generation(diag),
        "docich_head": _dig(diag, "meta", "docich_head"),
        "action_counts": counts,
        "action_count_latest": counts[-1] if counts else None,
        "trailing_zero_run": zero_run,
        "pane_windows": windows,
        "game_pane_lines": len(game_lines),
        "pager_text_visible": pager_seen,
        "pager_text_expected_visible": False,
        "vitals_visible": vitals_seen,
        "reasons": reasons,
    }


def render(verdict):
    """Render a short human summary; no pane bodies are echoed."""
    lines = [
        f"status: {verdict['status']}",
        f"corner active at capture: {verdict['corner_active_at_capture']} "
        f"(active_game={verdict['active_game']!r})",
        f"generation: {verdict['generation']}",
        f"deployed head: {verdict['docich_head']}",
        f"action counts: {verdict['action_counts']}"
        f" (latest={verdict['action_count_latest']},"
        f" trailing_zero_run={verdict['trailing_zero_run']})",
        f"game pane lines: {verdict['game_pane_lines']}"
        f" (vitals visible: {verdict['vitals_visible']})",
        f"pager text visible: {verdict['pager_text_visible']}"
        " (not expected: top row is outside the 14-line tail)",
    ]
    for reason in verdict["reasons"]:
        lines.append(f"note: {reason}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Judge NetHack #2271 acceptance evidence from a diagnostics capture.")
    parser.add_argument(
        "path", nargs="?", help="diagnostics JSON file; omit or use '-' to read stdin")
    parser.add_argument(
        "--json", action="store_true", help="print the verdict as JSON")
    args = parser.parse_args(argv)

    if args.path in (None, "-"):
        raw = sys.stdin.read()
    else:
        raw = Path(args.path).read_text(encoding="utf-8")

    try:
        doc = json.loads(raw)
    except ValueError:
        # A capture pasted from a GitHub Actions log carries redaction masks.
        try:
            doc = json.loads(_repair_masked_json(raw))
        except ValueError as exc:
            print(f"invalid diagnostics JSON: {exc}", file=sys.stderr)
            return 2

    verdict = evaluate(doc)
    if args.json:
        print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    else:
        print(render(verdict))
    return 0 if verdict["status"] != STATUS_UNKNOWN else 3


if __name__ == "__main__":
    raise SystemExit(main())
