"""#970 regression: the presentation poll guard survives in-flight children.

Review of #2395 found that a single tab refresh can start more than one request
(stream = ``/api/stream`` + ``/api/workers``, corners = ``/api/corners`` + the
special/beta/renderer child panels). The first version released the in-flight
guard as soon as the *outer* call returned, so a slow child request was stacked
by the next 10s tick.

These tests execute the shipped ``pollGate``/``startTabPolling`` block and the
real ``loadStream`` / ``loadWorkersControl`` / ``loadCorners`` functions in
``node`` with controllable leaf requests. They pin:

* a pending child keeps the guard held across ticks,
* the guard is released only after every child settles,
* one failing child does not free the guard while the other is still pending.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import webui  # noqa: E402

POLL_START = "// webui-poll-visibility:start"
POLL_END = "// webui-poll-visibility:end"
LOADERS = ("loadStream", "loadWorkersControl", "loadCorners")

HARNESS = r"""
const out = {};
const calls = {};
function bump(key) { calls[key] = (calls[key] || 0) + 1; }
function resetCounts() { for (const key of Object.keys(calls)) delete calls[key]; }
let pending = {};
function resetPending() { pending = {}; }
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

let hidden = false;
const listeners = {};
const timers = new Map();
let seq = 0;
globalThis.setInterval = (fn) => { const id = ++seq; timers.set(id, fn); return id; };
globalThis.clearInterval = (id) => { timers.delete(id); };
globalThis.document = {
  get hidden() { return hidden; },
  addEventListener(type, fn) { listeners[type] = fn; },
  querySelector() { return { getAttribute() { return "stream"; } }; },
  getElementById() { return null; },
};
function lastTimer() {
  const ids = [...timers.keys()];
  return ids.length ? timers.get(ids[ids.length - 1]) : null;
}
let statusTimer = null, cornersTimer = null, streamTimer = null, audioTimer = null, dashTimer = null;
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// Leaf dependencies of the real loaders, all controllable from the test.
function toast() {}
function renderStream() {}
function renderWorkersControl() {}
function renderCorners() {}
function renderCornerCatalogMessage() {}
let predictionWorkerStatusCache = null;
function loadPredictionWorkerStatus() { return Promise.resolve(null); }
function loadSpecial() { bump("special"); return pending.special ? pending.special.promise : Promise.resolve(); }
function loadBetaStatus() { bump("beta"); return pending.beta ? pending.beta.promise : Promise.resolve(); }
function loadRenderer() { bump("renderer"); return pending.renderer ? pending.renderer.promise : Promise.resolve(); }
function api(path) {
  bump("api:" + path);
  return pending[path] ? pending[path].promise : Promise.resolve({});
}

__POLL__
__LOAD_STREAM__
__LOAD_WORKERS__
__LOAD_CORNERS__

(async () => {
  // 1) stream: both children stay pending across the next tick.
  resetCounts(); resetPending();
  pending["/api/stream"] = deferred();
  pending["/api/workers"] = deferred();
  startTabPolling("stream");
  const streamTick = lastTimer();
  streamTick();
  await tick(); await tick();
  streamTick();
  await tick(); await tick();
  out.stream_while_pending = { stream: calls["api:/api/stream"] || 0, workers: calls["api:/api/workers"] || 0 };
  pending["/api/stream"].resolve({});
  pending["/api/workers"].resolve({ workers: [] });
  await tick(); await tick();
  streamTick();
  await tick(); await tick();
  out.stream_after_settle = { stream: calls["api:/api/stream"] || 0, workers: calls["api:/api/workers"] || 0 };

  // 2) corners: the parent resolves fast and the three child panels stay slow.
  resetCounts(); resetPending();
  pending.special = deferred();
  pending.beta = deferred();
  pending.renderer = deferred();
  startTabPolling("corners");
  const cornersTick = lastTimer();
  cornersTick();
  await tick(); await tick();
  cornersTick();
  await tick(); await tick();
  out.corners_while_pending = {
    special: calls["special"] || 0, beta: calls["beta"] || 0,
    renderer: calls["renderer"] || 0, corners: calls["api:/api/corners"] || 0,
  };
  pending.special.resolve();
  pending.beta.resolve();
  pending.renderer.resolve();
  await tick(); await tick();
  cornersTick();
  await tick(); await tick();
  out.corners_after_settle = { special: calls["special"] || 0, corners: calls["api:/api/corners"] || 0 };

  // 3) stream: one child fails while the other is still pending.
  resetCounts(); resetPending();
  pending["/api/stream"] = deferred();
  pending["/api/workers"] = deferred();
  startTabPolling("stream");
  const failTick = lastTimer();
  failTick();
  await tick(); await tick();
  pending["/api/stream"].reject(new Error("stream failed"));
  await tick(); await tick();
  failTick();
  await tick(); await tick();
  out.fail_while_other_pending = { stream: calls["api:/api/stream"] || 0, workers: calls["api:/api/workers"] || 0 };
  pending["/api/workers"].resolve({ workers: [] });
  await tick(); await tick();
  failTick();
  await tick(); await tick();
  out.fail_after_both_settle = { stream: calls["api:/api/stream"] || 0, workers: calls["api:/api/workers"] || 0 };

  process.stdout.write(JSON.stringify(out));
})();
"""


def _block(html, start, end):
    assert start in html and end in html, f"missing markers {start!r}/{end!r}"
    return html.split(start, 1)[1].split(end, 1)[0]


class WebuiPollInflightTest(unittest.TestCase):
    def _results(self):
        if shutil.which("node") is None:
            self.skipTest("JS 挙動検証には node が必要 (CI runner には同梱)")
        html = webui.INDEX_HTML
        script = (
            HARNESS.replace("__POLL__", _block(html, POLL_START, POLL_END))
            .replace("__LOAD_STREAM__", _block(html, f"// webui-loader:loadStream:start", f"// webui-loader:loadStream:end"))
            .replace("__LOAD_WORKERS__", _block(html, f"// webui-loader:loadWorkersControl:start", f"// webui-loader:loadWorkersControl:end"))
            .replace("__LOAD_CORNERS__", _block(html, f"// webui-loader:loadCorners:start", f"// webui-loader:loadCorners:end"))
        )
        completed = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, timeout=15, check=False
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_stream_child_keeps_guard_across_ticks(self):
        out = self._results()
        self.assertEqual(out["stream_while_pending"], {"stream": 1, "workers": 1}, out)
        self.assertEqual(out["stream_after_settle"], {"stream": 2, "workers": 2}, out)

    def test_corners_children_keep_guard_across_ticks(self):
        out = self._results()
        self.assertEqual(
            out["corners_while_pending"],
            {"special": 1, "beta": 1, "renderer": 1, "corners": 1},
            out,
        )
        self.assertEqual(out["corners_after_settle"], {"special": 2, "corners": 2}, out)

    def test_one_failing_child_does_not_release_guard_early(self):
        out = self._results()
        self.assertEqual(out["fail_while_other_pending"], {"stream": 1, "workers": 1}, out)
        self.assertEqual(out["fail_after_both_settle"], {"stream": 2, "workers": 2}, out)

    def test_stream_composite_returns_a_promise(self):
        html = webui.INDEX_HTML
        block = _block(html, POLL_START, POLL_END)
        self.assertIn("Promise.allSettled([loadStream(), loadWorkersControl()])", block)
        self.assertNotIn("load=()=>{ loadStream(); loadWorkersControl(); }", block)

    def test_loaders_await_their_children(self):
        html = webui.INDEX_HTML
        corners = _block(html, "// webui-loader:loadCorners:start", "// webui-loader:loadCorners:end")
        self.assertIn("Promise.allSettled([loadSpecial(), loadBetaStatus(), loadRenderer()])", corners)
        self.assertIn("await children;", corners)


if __name__ == "__main__":
    unittest.main()
