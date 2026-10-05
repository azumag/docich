"""Loopback-only broadcast view for the Tsuitate beta bot.

The browser never receives the beta-control capability.  This process calls the
existing HMAC bridge server-side and projects only its reviewed status fields.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path

from .tsuitate_beta_control import ControlError, call_beta_control, project_player_view

DEFAULT_PORT = 8804
VIEW_NAME = "tsuitate-view"

HTML = """<!doctype html><html lang="ja"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI衝立将棋</title>
<style>
html,body{margin:0;background:#101317;color:#f4f5f7;font-family:system-ui,-apple-system,sans-serif}
main{width:960px;height:540px;box-sizing:border-box;padding:22px 28px;display:grid;grid-template-columns:470px 1fr;gap:28px;background:linear-gradient(135deg,#151a20,#0d1014)}
h1{font-size:34px;line-height:1.05;margin:0 0 5px}.sub{color:#aeb7c2;font-size:15px;margin-bottom:12px}
.board{width:430px;height:430px;display:grid;grid-template-columns:repeat(9,1fr);grid-template-rows:repeat(9,1fr);border:2px solid #6e5c3b;background:#c9ab70}
.sq{box-sizing:border-box;border-right:1px solid rgba(55,42,21,.68);border-bottom:1px solid rgba(55,42,21,.68);display:flex;align-items:center;justify-content:center;color:#342814;font-size:20px;font-weight:700;position:relative}
.sq.own{background:#e1c17d;color:#15110b}.sq small{position:absolute;right:2px;bottom:1px;font-size:8px;color:rgba(35,27,14,.55);font-weight:500}
.state{font-size:34px;font-weight:800;line-height:1.15;margin:4px 0 16px}.bad{color:#ffb1b1}.wait{color:#c5cbd3}
.panel{border-top:1px solid #2a3038;padding-top:12px;margin-top:12px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:11px 18px;font-size:17px}.wide{grid-column:1/-1}
.k{color:#98a3af;font-size:13px}.v{font-weight:650;line-height:1.35}.hand{min-height:28px}.note{margin-top:14px;color:#98a3af;font-size:13px;line-height:1.45}
.check{color:#ffd58a}.foul{color:#ffb7a8}
</style>
<main>
<section><h1>AI衝立将棋</h1><div class="sub">盤面はAI自身が観測できる駒だけを表示</div><div id="board" class="board" aria-label="AIから見えている盤面"></div></section>
<section>
<div id="state" class="state wait">接続確認中…</div>
<div class="grid">
<div><span class="k">Brain</span><br><span id="brain" class="v">-</span></div>
<div><span class="k">手数</span><br><span id="move" class="v">-</span></div>
<div><span class="k">AIの先後</span><br><span id="color" class="v">-</span></div>
<div><span class="k">手番</span><br><span id="turn" class="v">-</span></div>
<div><span class="k">反則</span><br><span id="fouls" class="v foul">-</span></div>
<div><span class="k">王手情報</span><br><span id="checks" class="v check">-</span></div>
<div class="wide"><span class="k">持ち駒</span><br><span id="hand" class="v hand">-</span></div>
<div class="wide"><span class="k">時計</span><br><span id="clocks" class="v">-</span></div>
</div>
<div class="panel"><span class="k">対局状態</span><br><span id="game" class="v">-</span></div>
<div class="note">相手の隠し駒・公開棋譜から復元した盤面は表示しません。1コーナー1局、終局後は元のゲームへ戻ります。</div>
</section>
</main>
<script>
const labels={stopped:"待機中",queued:"対戦相手を待っています",playing:"対局中",draining:"終局後に停止します",finished:"対局終了",queue_timeout:"対戦相手が見つかりませんでした",paused:"結果確認待ち"};
const role={pawn:"歩",lance:"香",knight:"桂",silver:"銀",gold:"金",bishop:"角",rook:"飛",king:"玉",tokin:"と",promotedlance:"成香",promotedknight:"成桂",promotedsilver:"成銀",horse:"馬",dragon:"龍"};
const colorLabel={sente:"先手",gote:"後手"};
function boardOrder(v){return v?.yourColor==="gote"?{files:[1,2,3,4,5,6,7,8,9],ranks:["i","h","g","f","e","d","c","b","a"]}:{files:[9,8,7,6,5,4,3,2,1],ranks:["a","b","c","d","e","f","g","h","i"]};}
function renderBoard(v){
 const board=document.getElementById("board"), order=boardOrder(v), pieces=new Map((v?.yourPieces||[]).map(p=>[p.square,p]));
 board.replaceChildren();
 for(const rank of order.ranks)for(const file of order.files){const square=String(file)+rank,p=pieces.get(square),cell=document.createElement("div");cell.className="sq"+(p?" own":"");cell.setAttribute("aria-label",square+(p?" "+(role[p.role]||p.role):" 不明"));if(p)cell.textContent=role[p.role]||p.role;const tag=document.createElement("small");tag.textContent=square;cell.appendChild(tag);board.appendChild(cell);}
}
function clock(ms){if(typeof ms!=="number")return "-";const sec=Math.max(0,Math.floor(ms/1000)),m=Math.floor(sec/60),s=sec%60;return m+":"+String(s).padStart(2,"0");}
function handText(v){const xs=Object.entries(v?.yourHand||{}).filter(([,n])=>n>0).map(([r,n])=>(role[r]||r)+"×"+n);return xs.length?xs.join("  "):"なし";}
function apply(d){
 const state=document.getElementById("state");state.textContent=d.ok?(labels[d.state]||"状態確認中"):"制御接続を確認できません";state.className="state "+(d.ok?"":"bad");
 document.getElementById("brain").textContent=d.brainVersion||"-";document.getElementById("game").textContent=d.gameActive?"進行中":(d.readyForNextRun?"次局開始可":"待機");
 const v=d.playerView||null;renderBoard(v);
 document.getElementById("move").textContent=v?String(v.moveNumber):"-";
 document.getElementById("color").textContent=v?(colorLabel[v.yourColor]||v.yourColor):"-";
 document.getElementById("turn").textContent=v?(colorLabel[v.turn]||v.turn):"-";
 document.getElementById("fouls").textContent=v?("AI "+v.fouls.you+" / 相手 "+v.fouls.opponent):"-";
 document.getElementById("checks").textContent=v?(v.youInCheck?"王手を受けています":(v.opponentInCheck?"相手玉に王手":"なし")):"-";
 document.getElementById("hand").textContent=v?handText(v):"-";
 document.getElementById("clocks").textContent=v?("先手 "+clock(v.clocks.senteMs)+" / 後手 "+clock(v.clocks.goteMs)):"-";
}
async function refresh(){try{const r=await fetch("/api/tsuitate",{cache:"no-store"});apply(await r.json());}catch(e){apply({ok:false});}}
renderBoard(null);refresh();setInterval(refresh,1000);
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
        "playerView": player_view,
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
