"""#970 regression: main Web UI presentation polling pauses while hidden.

The main operator console refreshed its dashboard (6 read-only endpoints) and
the active tab every 10s even while the browser tab was hidden, and a slow
response could stack another round on top. This pins the presentation-only
contract that replaces that:

* ``document.hidden`` stops the timers and suppresses new refreshes,
* becoming visible again refreshes once immediately and restarts the timer,
* an in-flight request is never stacked for the same key,
* the trading dashboard's decision/risk/fill paths are not involved.

The assertions execute the real shipped snippet in ``node`` (the same harness
style as ``tests/test_webui.py``); the surrounding 3.2k-line console script is
not re-implemented here.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import webui  # noqa: E402

START = "// webui-poll-visibility:start"
END = "// webui-poll-visibility:end"

HARNESS = r"""
const out = {};
const calls = {dashboard: 0, status: 0, corners: 0, stream: 0, audio: 0};
let hidden = false;
const listeners = {};
const timers = new Map();
let seq = 0;
globalThis.setInterval = (fn, ms) => { const id = ++seq; timers.set(id, fn); return id; };
globalThis.clearInterval = (id) => { timers.delete(id); };
globalThis.document = {
  get hidden() { return hidden; },
  addEventListener(type, fn) { listeners[type] = fn; },
  querySelector() { return { getAttribute() { return "status"; } }; },
};
async function loadDashboard() { calls.dashboard++; }
async function loadStatus() { calls.status++; }
async function loadCorners() { calls.corners++; }
async function loadStream() { calls.stream++; }
async function loadWorkersControl() {}
async function loadVoiceChain() { calls.audio++; }
let statusTimer = null, cornersTimer = null, streamTimer = null, audioTimer = null, dashTimer = null;
const setTimeout = globalThis.setTimeout;
__SNIPPET__
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
(async () => {
  startDashboardPolling({refresh: true});
  await tick();
  out.visible_refresh = calls.dashboard;
  out.visible_timer = dashTimer !== null;

  hidden = true;
  listeners.visibilitychange();
  const before = calls.dashboard;
  out.hidden_timer = dashTimer === null;
  pollGate("dashboard", loadDashboard);
  await tick();
  out.hidden_suppressed = calls.dashboard === before;

  hidden = false;
  listeners.visibilitychange();
  await tick();
  out.resume_refresh = calls.dashboard === before + 1;
  out.resume_timer = dashTimer !== null;

  let releaseSlow = null;
  let slowCalls = 0;
  const slow = () => { slowCalls++; return new Promise((resolve) => { releaseSlow = resolve; }); };
  pollGate("slow", slow);
  pollGate("slow", slow);
  await tick();
  out.inflight_deduped = slowCalls === 1;
  releaseSlow();
  await tick();
  pollGate("slow", slow);
  await tick();
  out.inflight_released = slowCalls === 2;

  startTabPolling("status");
  out.tab_timer_visible = statusTimer !== null;
  hidden = true;
  listeners.visibilitychange();
  out.tab_timer_hidden = statusTimer === null;

  process.stdout.write(JSON.stringify(out));
})();
"""


class WebuiPollingVisibilityTest(unittest.TestCase):
    def _snippet(self):
        html = webui.INDEX_HTML
        self.assertIn(START, html)
        self.assertIn(END, html)
        return html.split(START, 1)[1].split(END, 1)[0]

    def test_polling_pauses_while_hidden_and_dedupes_in_flight(self):
        if shutil.which("node") is None:
            self.skipTest("JS 挙動検証には node が必要 (CI runner には同梱)")
        script = HARNESS.replace("__SNIPPET__", self._snippet())
        completed = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, timeout=15, check=False
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        out = json.loads(completed.stdout)
        self.assertEqual(out["visible_refresh"], 1, out)
        self.assertTrue(out["visible_timer"], out)
        self.assertTrue(out["hidden_timer"], out)
        self.assertTrue(out["hidden_suppressed"], out)
        self.assertTrue(out["resume_refresh"], out)
        self.assertTrue(out["resume_timer"], out)
        self.assertTrue(out["inflight_deduped"], out)
        self.assertTrue(out["inflight_released"], out)
        self.assertTrue(out["tab_timer_visible"], out)
        self.assertTrue(out["tab_timer_hidden"], out)

    def test_dashboard_timer_is_not_started_unconditionally(self):
        # The old code armed the dashboard refresh with a bare
        # ``setInterval(loadDashboard,10000)`` outside the visibility manager.
        html = webui.INDEX_HTML
        self.assertNotIn("dashTimer=setInterval(loadDashboard", html)
        self.assertNotIn("setInterval(loadDashboard,10000)", html)
        self.assertIn("startDashboardPolling({refresh:true})", html)
        self.assertIn('document.addEventListener("visibilitychange"', html)


if __name__ == "__main__":
    unittest.main()
