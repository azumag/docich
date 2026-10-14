import fnmatch
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations-ci.yml"


def _event_paths(text: str, event: str) -> list[str]:
    lines = text.splitlines()
    event_marker = f"  {event}:"
    start = lines.index(event_marker) + 1
    paths_start = next(
        index for index in range(start, len(lines)) if lines[index] == "    paths:"
    ) + 1

    paths = []
    for line in lines[paths_start:]:
        if not line.startswith("      - "):
            break
        paths.append(line.removeprefix("      - ").strip("'"))
    return paths


def _path_selected(patterns: list[str], path: str) -> bool:
    selected = False
    for pattern in patterns:
        excluded = pattern.startswith("!")
        candidate = pattern[1:] if excluded else pattern
        if fnmatch.fnmatchcase(path, candidate):
            selected = not excluded
    return selected


def _workflow_runs(patterns: list[str], changed_paths: list[str]) -> bool:
    return any(_path_selected(patterns, path) for path in changed_paths)


class VmOperationsCiPathContractTest(unittest.TestCase):
    def test_discord_only_container_and_compose_changes_are_excluded(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        discord_only = [
            "containers/discord-chat/Dockerfile",
            "containers/discord-chat/verify.py",
            "compose.discord-chat.yml",
            "compose.discord-chat.prod.yml",
        ]

        for event in ("pull_request", "push"):
            patterns = _event_paths(text, event)
            self.assertFalse(_workflow_runs(patterns, discord_only), event)

    def test_other_vm_paths_and_mixed_changes_still_run(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        cases = [
            ["containers/paper-strategy/Dockerfile"],
            ["compose.yml"],
            ["containers/discord-chat/Dockerfile", "ops/vm_actions/gateway.py"],
            ["compose.discord-chat.yml", "containers/paper-strategy/Dockerfile"],
        ]

        for event in ("pull_request", "push"):
            patterns = _event_paths(text, event)
            for changed_paths in cases:
                with self.subTest(event=event, changed_paths=changed_paths):
                    self.assertTrue(_workflow_runs(patterns, changed_paths))

    def test_discord_exclusions_follow_their_broad_includes(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for event in ("pull_request", "push"):
            patterns = _event_paths(text, event)
            self.assertLess(
                patterns.index("containers/**"),
                patterns.index("!containers/discord-chat/**"),
            )
            self.assertLess(
                patterns.index("compose*.yml"),
                patterns.index("!compose.discord-chat*.yml"),
            )


if __name__ == "__main__":
    unittest.main()
