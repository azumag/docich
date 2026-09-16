from pathlib import Path
import os
import subprocess
import tempfile
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
        self.assertNotIn(".soren91-opencode-prompt", text)

    def test_real_noninteractive_bash_child_receives_piped_prompt(self):
        prompt = "Return exactly OK.\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outer = root / "opencode"
            inner = root / "opencode-fixed-exec"
            classifier = root / "classifier.py"

            # Keep the reviewed shim logic intact while redirecting its private
            # /home/ubuntu temp files into this test sandbox.
            outer.write_text(
                OUTER_SHIM.read_text(encoding="utf-8").replace(
                    "/home/ubuntu/", f"{root}/"
                ),
                encoding="utf-8",
            )
            inner.write_text("#!/usr/bin/env bash\nset -euo pipefail\ncat\n", encoding="utf-8")
            classifier.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            outer.chmod(0o700)
            inner.chmod(0o700)
            classifier.chmod(0o600)

            env = os.environ.copy()
            env["SOREN91_OPENCODE_NONZERO_CLASSIFIER"] = str(classifier)
            proc = subprocess.run(
                [str(outer), "run", "--format", "json", "--model", "model/test"],
                input=prompt,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=5,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, prompt)
            self.assertEqual(proc.stderr, "")


if __name__ == "__main__":
    unittest.main()
