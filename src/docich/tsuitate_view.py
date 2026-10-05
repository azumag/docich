"""Loopback-only broadcast view for the Tsuitate beta bot.

The browser never receives the beta-control capability.  This process calls the
existing HMAC bridge server-side and projects only its reviewed status fields.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path

from .tsuitate_beta_control import ControlError, call_beta_control

DEFAULT_PORT = 8804
VIEW_NAME = "tsuitate-view"

HTML = """<!doctype html><html lang="ja"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI衝立将棋</title>
<style>
html,body{margin:0;background:#101317;color:#f4f5f7;font-family:system-ui,-apple-system,sans-serif}
main{width:960px;height:540px;box-sizing:border-box;padding:38px 48px;background:linear-gradient(135deg,#151a20,#0d1014)}
h1{font-size:42px;margin:0 0 8px}.sub{color:#aeb7c2;font-size:18px}
.state{font-size:54px;font-weight:800;margin:70px 0 18px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 32px;font-size:22px}
.k{color:#9da8b5}.v{font-weight:650}.note{margin-top:60px;color:#9da8b5;font-size:18px}
.bad{color:#ffb1b1}
</style>
<main><h1>AI衝立将棋</h1><div class="sub">相手の駒はAIにも見えていません</div>
<div id="state" class="state">接続確認中…</div>
<div class="grid"><div><span class="k">Brain</span><br><span id="brain" class="v">-</span></div>
<div><span class="k">対局</span><br><span id="game" class="v">-</span></div>
<div><span class="k">予約局数</span><br><span id="reserved" class="v">-</span></div>
<div><span class="k">完了局数</span><br><span id="completed" class="v">-</span></div></div>
<div class="note">1コーナー1局。終局後は自動で元のゲームへ戻ります。</div></main>
<script>
const labels={stopped:"待機中",queued:"対戦相手を待っています",playing:"対局中",draining:"終局後に停止します",finished:"対局終了",queue_timeout:"対戦相手が見つかりませんでした",paused:"結果確認待ち"};
async function refresh(){try{const r=await fetch("/api/tsuitate",{cache:"no-store"});const d=await r.json();
 const el=document.getElementById("state");el.textContent=d.ok?(labels[d.state]||"状態確認中"):"制御接続を確認できません";el.className="state"+(d.ok?"":" bad");
 document.getElementById("brain").textContent=d.brainVersion||"-";
 document.getElementById("game").textContent=d.gameActive?"進行中":(d.readyForNextRun?"次局開始可":"待機");
 document.getElementById("reserved").textContent=String(d.reservedGames??"-");
 document.getElementById("completed").textContent=String(d.completedGames??"-");
}catch(e){document.getElementById("state").textContent="状態取得待ち";}}
refresh();setInterval(refresh,1000);
</script></html>"""


def status_projection(runtime_id: str, generation: int, lease_id: str) -> dict:
    base = {
        "runtime_id": runtime_id,
        "generation": generation,
        "lease_id": lease_id,
    }
    try:
        status = call_beta_control("status")
    except ControlError as exc:
        return {**base, "ok": False, "error": exc.code}
    return {
        **base,
        "ok": True,
        "state": status["state"],
        "brainVersion": status.get("brainVersion"),
        "completedGames": status["completedGames"],
        "reservedGames": status["reservedGames"],
        "stopRequested": status["stopRequested"],
        "readyForNextRun": status["readyForNextRun"],
        "gameActive": status["state"] in {"playing", "draining"},
    }


def handler_for(runtime_id: str, generation: int, lease_id: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in {"/", "/broadcast"}:
                body = HTML.encode("utf-8")
                kind = "text/html; charset=utf-8"
                status = 200
            elif self.path == "/api/tsuitate":
                data = status_projection(runtime_id, generation, lease_id)
                body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                kind = "application/json"
                status = 200
            else:
                self.send_error(404)
                return
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_error(405)

        def log_message(self, *_args):
            pass

    return Handler


def serve(*, port: int, runtime_id: str, generation: int, lease_id: str) -> None:
    HTTPServer(("127.0.0.1", port), handler_for(runtime_id, generation, lease_id)).serve_forever()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runtime-id", required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--lease-id", required=True)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535 or args.generation < 1:
        parser.error("invalid runtime arguments")
    serve(port=args.port, runtime_id=args.runtime_id, generation=args.generation, lease_id=args.lease_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
