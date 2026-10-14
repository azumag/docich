from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github/workflows"


class MergeTopProbeRetiredTests(unittest.TestCase):
    """A one-off experiment must not become a persistent deployment side effect."""

    def test_experiment_launcher_is_removed(self):
        # Removing both workflow_run and workflow_dispatch leaves the current
        # VM experiment alone and prevents this retired launcher being reused.
        self.assertFalse((WORKFLOWS / "soren-merge-top-ab-probe.yml").exists())

    def test_no_workflow_reinstates_merge_top_experiment(self):
        # Cover renamed launchers too. These are the experiment-specific
        # mutations that reset a winner or terminate an unrelated active A/B.
        workflows = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
        self.assertTrue(workflows)
        for path in workflows:
            text = path.read_text()
            for mutation in (
                "ANALYZE_BOARD_MERGE_TOP_MODEL=",
                "superseded_by_adopting_merge_top_probe",
                "merge_top_ab.",
            ):
                with self.subTest(workflow=path.name, mutation=mutation):
                    self.assertNotIn(mutation, text)

    def test_normal_deployment_workflow_remains(self):
        text = (WORKFLOWS / "vm-operations.yml").read_text()
        self.assertIn("name: VM operations", text)
        self.assertIn("branches: [main]", text)
        self.assertIn('deploy docich $TARGET $SHA', text)


if __name__ == "__main__":
    unittest.main()
