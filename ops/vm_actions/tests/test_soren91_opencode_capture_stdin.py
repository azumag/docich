from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
OUTER_SHIM = ROOT / "ops" / "vm_actions" / "soren91_opencode_capture_shim.sh"


class Soren91OpenCodeCaptureStdinTests(unittest.TestCase):
    def test_async_child_receives_original_prompt_stdin_without_argv_relaxation(self):
        text = OUTER_SHIM.read_text(encoding="utf-8")
        duplicate = text.index("exec 8<&0")
        group_start = text.index("{\n", duplicate)
        child = text.index(
            '"$inner" run --format json --model "$5" >"$child_out" 2>"$child_err" &',
            group_start,
        )
        group_redirect = text.index("} <&8", child)
        close_fd = text.index("exec 8<&-", group_redirect)
        wait_child = text.index('wait "$child_pid"', close_fd)
        self.assertLess(duplicate, group_start)
        self.assertLess(group_start, child)
        self.assertLess(child, group_redirect)
        self.assertLess(group_redirect, close_fd)
        self.assertLess(close_fd, wait_child)
        self.assertNotIn("$@", text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("prompt", text[text.index("child_out="):duplicate].lower())


if __name__ == "__main__":
    unittest.main()
