import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "normalize_soren91_opencode_success.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("soren91_opencode_success", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def text_event(text):
    return json.dumps({"type": "text", "part": {"type": "text", "text": text}}, separators=(",", ":"))


class Soren91OpenCodeSuccessNormalizerTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_helper()

    def test_selects_complete_decide_block_not_preliminary_helper(self):
        raw = (
            text_event("Here is a helper first.\n```js\nconst helperOnly = true;\n```\n")
            + "\n"
            + text_event(
                "Then the complete module.\n```JavaScript\n"
                "const helper = 1;\n"
                "export function decide(boardState) { return { x: 0, reason: 'ok' }; }\n"
                "```\n"
            )
            + "\n"
        ).encode()
        normalized = self.helper.normalize(raw)
        lines = normalized.decode().splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        code = event["part"]["text"]
        self.assertIn("export function decide(boardState)", code)
        self.assertIn("const helper = 1", code)
        self.assertNotIn("helperOnly", code)

    def test_accepts_mjs_fence_and_fragmented_text_events(self):
        raw = (
            text_event("```mjs\nconst a = 1;\n")
            + "\n"
            + text_event("export function decide(boardState) { return { x: 0, reason: 'mjs' }; }\n```\n")
            + "\n"
        ).encode()
        normalized = self.helper.normalize(raw)
        event = json.loads(normalized.decode())
        self.assertIn("const a = 1", event["part"]["text"])
        self.assertIn("export function decide(boardState)", event["part"]["text"])

    def test_no_complete_fenced_candidate_is_preserved_for_fail_closed_parser(self):
        raw = (text_event("analysis only, no strategy") + "\n").encode()
        self.assertEqual(self.helper.normalize(raw), raw)

    def test_raw_unfenced_decide_is_not_rewritten(self):
        raw = (text_event("export function decide(boardState) { return { x: 0, reason: 'raw' }; }") + "\n").encode()
        self.assertEqual(self.helper.normalize(raw), raw)

    def test_tool_event_is_never_removed_by_normalization(self):
        tool = json.dumps({"type": "step_finish", "part": {"reason": "tool-calls"}}, separators=(",", ":"))
        raw = (
            tool
            + "\n"
            + text_event("```javascript\nexport function decide(boardState) { return { x: 0, reason: 'bad' }; }\n```\n")
            + "\n"
        ).encode()
        self.assertEqual(self.helper.normalize(raw), raw)

    def test_error_and_unexpected_events_are_preserved(self):
        for event in (
            {"error": {"message": "private"}},
            {"type": "mystery", "part": {}},
            {"type": "text", "part": {"error": {"message": "private"}}},
        ):
            raw = (json.dumps(event, separators=(",", ":")) + "\n").encode()
            self.assertEqual(self.helper.normalize(raw), raw)

    def test_invalid_json_is_preserved(self):
        raw = b"not-json\n"
        self.assertEqual(self.helper.normalize(raw), raw)


if __name__ == "__main__":
    unittest.main()
