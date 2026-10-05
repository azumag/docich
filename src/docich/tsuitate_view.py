"""Loopback-only broadcast view for the Tsuitate beta bot.

The browser never receives the beta-control capability.  This process calls the
existing HMAC bridge server-side and projects only its reviewed status fields.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path

from .tsuitate_beta_control import (
    ControlError,
    call_beta_control,
    project_game_result,
    project_player_view,
)

DEFAULT_PORT = 8804
VIEW_NAME = "tsuitate-view"

HTML = """<!doctype html><html lang="ja"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI衝立将棋</title>
<style>
html,body{margin:0;background:#101317;color:#f4f5f7;font-family:system-ui,-apple-system,sans-serif}
main{width:960px;height:540px;box-sizing:border-box;padding:28px 38px;display:grid;grid-template-columns:430px 1fr;gap:42px;background:linear-gradient(135deg,#151a20,#0d1014)}
h1{font-size:38px;line-height:1.05;margin:0 0 7px}.sub{color:#aeb7c2;font-size:16px;margin-bottom:18px}
.fog{width:400px;height:400px;box-sizing:border-box;border:2px solid #64583f;background:repeating-linear-gradient(0deg,rgba(215,190,137,.07) 0,rgba(215,190,137,.07) 43px,rgba(215,190,137,.18) 44px),repeating-linear-gradient(90deg,rgba(215,190,137,.07) 0,rgba(215,190,137,.07) 43px,rgba(215,190,137,.18) 44px);display:flex;align-items:center;justify-content:center;text-align:center;padding:42px;color:#d6c7a8;font-size:24px;font-weight:750;line-height:1.5}
.state{font-size:38px;font-weight:800;line-height:1.15;margin:8px 0 24px}.bad{color:#ffb1b1}.wait{color:#c5cbd3}
.result{display:none;margin:0 0 18px;border:1px solid #4a5a3f;border-radius:10px;padding:12px 16px;background:rgba(120,170,110,.12)}
.result.on{display:block}.result .rk{font-size:14px;color:#9fb59a}.result .rv{font-size:26px;font-weight:800;margin-top:2px}
.result .rd{font-size:15px;color:#c5cbd3;margin-top:4px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px 22px;font-size:20px}.result+.grid{margin-top:0}.wide{grid-column:1/-1}.k{color:#98a3af;font-size:14px}.v{font-weight:650;line-height:1.35}
.panel{border-top:1px solid #2a3038;padding-top:16px;margin-top:20px}.note{margin-top:18px;color:#98a3af;font-size:14px;line-height:1.55}
</style>
<main>
<section><h1>AI衝立将棋</h1><div class="sub">公開配信向け spectator view</div><div class="fog">対局中の駒配置・持ち駒は<br>公平性のため配信しません</div></section>
<section>
<div id="state" class="state wait">接続確認中…</div>
<div id="result" class="result">
<div class="rk">勝敗</div><div id="rv-outcome" class="rv">-</div>
<div class="rd"><span id="rv-reason">-</span> / <span id="rv-moves">-</span></div>
</div>
<div class="grid">
<div><span class="k">Brain</span><br><span id="brain" class="v">-</span></div>
<div><span class="k">手数</span><br><span id="move" class="v">-</span></div>
<div><span class="k">AIの先後</span><br><span id="color" class="v">-</span></div>
<div><span class="k">手番</span><br><span id="turn" class="v">-</span></div>
<div class="wide"><span class="k">時計（取得時点）</span><br><span id="clocks" class="v">-</span></div>
</div>
<div class="panel"><span class="k">対局状態</span><br><span id="game" class="v">-</span></div>
<div class="note">owner側ではBot自身のPlayerViewを検証済みHMAC statusとして保持できますが、このbroadcast viewには駒配置・持ち駒・raw checkpoint・公開棋譜由来の盤面を渡しません。</div>
</section>
</main>
<script>
const labels={stopped:"待機中",queued:"対戦相手を待っています",playing:"対局中",draining:"終局後に停止します",finished:"対局終了",queue_timeout:"対戦相手が見つかりませんでした",paused:"結果確認待ち"};
const colorLabel={sente:"先手",gote:"後手"};
const outcomeLabel={win:"AIの勝ち",loss:"AIの負け",draw:"引き分け",unknown:"結果未確定"};
const reasonLabel={normal:"通常終了",checkmate:"詰み",stalemate:"持棋子",resign:"投了",timeout:"時間切れ",foul_limit:"反則上限",repetition:"連続王手",draw:"引き分け",aborted:"中止",disconnect:"通信断",transport_error:"通信エラー",protocol_error:"通信エラー",storage_error:"保存エラー",interrupted:"中断",no_move:"指し手なし",unknown:"理由未確定"};
function clock(ms){if(typeof ms!=="number")return "-";const sec=Math.max(0,Math.floor(ms/1000)),m=Math.floor(sec/60),s=sec%60;return m+":"+String(s).padStart(2,"0");}
function apply(d){
 const state=document.getElementById("state");state.textContent=d.ok?(labels[d.state]||"状態確認中"):"制御接続を確認できません";state.className="state "+(d.ok?"":"bad");
 document.getElementById("brain").textContent=d.brainVersion||"-";document.getElementById("game").textContent=d.gameActive?"進行中":(d.readyForNextRun?"次局開始可":"待機");
 const v=d.spectatorView||null;
 document.getElementById("move").textContent=v?String(v.moveNumber):"-";
 document.getElementById("color").textContent=v?(colorLabel[v.yourColor]||v.yourColor):"-";
 document.getElementById("turn").textContent=v?(colorLabel[v.turn]||v.turn):"-";
 document.getElementById("clocks").textContent=v?("先手 "+clock(v.clocks.senteMs)+" / 後手 "+clock(v.clocks.goteMs)):"-";
 // The settled result is public once the match ended, so it is safe to show.
 // Nothing about the board, the moves or the opponent is ever derived here.
 const r=d.gameResult||null;
 document.getElementById("result").className=r?"result on":"result";
 if(r){
  document.getElementById("rv-outcome").textContent=outcomeLabel[r.outcome]||"結果未確定";
  document.getElementById("rv-reason").textContent=(reasonLabel[r.reason]||"理由未確定")+(r.resultConfidence==="verified"?"":"（未検証）");
  document.getElementById("rv-moves").textContent=String(r.moveNumber)+"手まで";
 }
}
async function refresh(){try{const r=await fetch("/api/tsuitate",{cache:"no-store"});apply(await r.json());}catch(e){apply({ok:false});}}
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
    try:
        player_view = project_player_view(status.get("playerView"))
    except ControlError:
        player_view = None
    spectator_view = None
    if player_view is not None:
        spectator_view = {
            "yourColor": player_view["yourColor"],
            "turn": player_view["turn"],
            "moveNumber": player_view["moveNumber"],
            "clocks": player_view["clocks"],
            "status": player_view["status"],
        }
    try:
        game_result = project_game_result(status.get("gameResult"))
    except ControlError:
        game_result = None
    # A result may only appear for a match the Worker already settled, so the
    # viewer never learns the outcome of a game that is still running.
    if game_result is not None and status["state"] != "finished":
        game_result = None
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
        "spectatorView": spectator_view,
        "gameResult": game_result,
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
