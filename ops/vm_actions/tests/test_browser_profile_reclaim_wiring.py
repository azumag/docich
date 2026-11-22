"""Wiring tests for the browser_profile_reclaim vm-operations operation.

The operation is intentionally limited to phase 1 of the reviewed two-phase
wrapper (docich#3122, helper #3144, wrapper #3146): piping
browser_profile_reclaim_now.sh with no arguments can only emit the dry-run
plan JSON. The --apply phase needs the owner-supplied EXPECT_PLAN digest and
must stay unwired until owner approval, so these tests also pin the absence
of any apply path in the workflow step.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations.yml"


def _step_block(text: str) -> str:
    start = text.index("Run browser-profile reclaim dry-run plan (phase 1 only)")
    return text[start:text.index("- name:", start)]


class BrowserProfileReclaimWiringTests(unittest.TestCase):
    def test_operation_is_exposed_and_gated(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("browser_profile_reclaim]", text)
        self.assertIn(
            "steps.auth.outputs.operation == 'browser_profile_reclaim'", text
        )

    def test_step_pipes_reviewed_wrapper_with_no_arguments(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        step = _step_block(text)
        self.assertIn(
            "cat control/ops/vm_actions/browser_profile_reclaim_now.sh", step
        )
        self.assertIn('"exec docich production $SHA"', step)
        # Phase 1 only: no apply flag, no plan digest, no positional argument
        # may reach the wrapper through this step. Comment lines may name the
        # deferred --apply/EXPECT_PLAN contract, so only the executable lines
        # are pinned.
        executable = "\n".join(
            line for line in step.splitlines() if not line.lstrip().startswith("#")
        )
        for forbidden in ("--apply", "EXPECT_PLAN", "expect_plan", "apply="):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, executable)
        # The generic reclaim `apply` input must not be reused for this op.
        self.assertNotIn("inputs.apply", executable)

    def test_wrapper_preconditions_are_not_relaxed(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        step = _step_block(text)
        # The step must not bypass the wrapper's own fail-closed checks
        # (state dir / game_switch.json / game-switch.lock non-symlink).
        self.assertNotIn("rm ", step)
        self.assertNotIn("rmtree", step)


if __name__ == "__main__":
    unittest.main()
