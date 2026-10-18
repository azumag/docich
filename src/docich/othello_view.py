"""Loopback broadcast view for the participatory othello MVP (Issue #699).

The page polls ``/api/othello``, which reads the spectator snapshot from the
room Worker through the same bounded control bridge the tsuitate view uses.
The whole board is public information in othello, so unlike the tsuitate View
there is no fog constraint — the snapshot this module projects is already the
spectator-safe projection from ``src/docich/othello_corner.py`` /
``workers/othello-bot/src/room.js``.

The page follows the broadcast HTML standard
(``docs/broadcast-html-view-standard.md``): Japanese page, translation off,
960x540 stage scaled to fill the stream frame.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import re
import uuid

DEFAULT_PORT = 8807
VIEW_NAME = "othello-view"
SNAPSHOT_MAX_BYTES = 16 * 1024
#: Poll cadence: a live othello match needs faster refresh than the ranking
#: pages, but slower than the tsuitate status poll since moves are turn-based.
POLL_MILLISECONDS = 1000

PHASE_LABELS = {"IDLE": "ルーム待機中", "ROOM_OPEN": "ルームを開きました",
                "PLAYING": "対局中", "FINISHED": "対局終了",
                "FAILED": "異常終了", "TIMEOUT": "タイムアウト"}
OUTCOME_LABELS = {"viewer_win": "視聴者の勝ち", "ai_win": "AI(docich brain)の勝ち",
                  "draw": "引き分け", "aborted": "中断", "timeout": "タイムアウト"}

HTML = r'''<!doctype html>
<html lang="ja" translate="no"><meta charset="utf-8"><meta name="google" content="notranslate"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>参加型オセロ</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0b1017;color:#f2f5f8;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:16px 26px 18px;background:radial-gradient(ellipse at 30% 20%,#173227 0,#101b16 58%,#0a1210 100%);overflow:hidden}
header{height:54px;display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #2b4238;padding-bottom:4px}
h1{font-size:26px;line-height:1.05;letter-spacing:.05em;margin:0;font-weight:800}.eyebrow{font-size:8px;letter-spacing:.3em;color:#6fc7a1;display:block;margin-bottom:2px}
.sub{font-size:11px;color:#a5bcae;margin-top:1px}
.tags{display:flex;gap:6px;align-items:center}.tag{border:1px solid #35594a;background:#12261f;color:#9fdcc2;padding:4px 8px;border-radius:99px;font-size:10px;letter-spacing:.04em;white-space:nowrap}
#status-line{height:24px;display:flex;align-items:center;justify-content:space-between;color:#8fb2a2;font-size:12px}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#63d0ac;margin-right:6px}
main{display:grid;grid-template-columns:452px 1fr;gap:26px;height:calc(100% - 104px)}
#boardwrap{display:flex;align-items:center;justify-content:center;border:1px solid #24453a;border-radius:12px;background:linear-gradient(150deg,#12251ecc,#0c1712)}
#board{display:grid;grid-template-columns:repeat(8,50px);grid-template-rows:repeat(8,50px);gap:2px;background:#0a1813;border:2px solid #35594a;border-radius:8px;padding:5px}
.sq{background:#123d32;border-radius:4px;position:relative;font-size:11px;color:#7ba396;display:flex;align-items:center;justify-content:center}
.sq .disc{position:absolute;width:34px;height:34px;border-radius:50%}
.sq.black .disc{background:radial-gradient(circle at 35% 30%,#4d5a55,#0c1210 70%);border:1px solid #5a6b64}
.sq.white .disc{background:radial-gradient(circle at 35% 30%,#ffffff,#b9c2be 75%);border:1px solid #7d8a85}
.sq.last{outline:2px solid #ffd67a}
.sq .coord{opacity:0}
#panel{display:flex;flex-direction:column;gap:14px;font-size:14px}
.panel-title{font-size:12px;letter-spacing:.18em;color:#6fc7a1;font-weight:700}
.kv{display:grid;grid-template-columns:96px 1fr;gap:8px 12px}
.kv .k{color:#7d9c8e}.kv .v{font-weight:700;line-height:1.3}
#result{display:none;border:1px solid #4b6a3f;border-radius:10px;padding:12px 14px;background:rgba(120,170,110,.12)}
#result.on{display:block}#result .rk{font-size:12px;color:#9fb59a}#result .rv{font-size:24px;font-weight:800;margin-top:2px}
#log{flex:1;overflow:hidden;border:1px solid #24453a;border-radius:10px;padding:10px 12px;font-size:11px;color:#9ab5a8;line-height:1.9}
#unavailable{position:absolute;inset:0;z-index:5;display:flex;align-items:center;justify-content:center;background:#0b1017ee;color:#f0d9ad;text-align:center;font-size:20px;line-height:1.7;padding:40px}
#unavailable[hidden]{display:none}
</style>
<div id="stage">
<header>
<div><span class="eyebrow">PARTICIPATORY GAMES</span><h1>参加型オセロ</h1><div class="sub">視聴者 vs docich brain（MVP）</div></div>
<div class="tags"><span class="tag" id="room-tag">ルーム -</span><span class="tag">観戦も歓迎</span></div>
</header>
<div id="status-line"><span><i class="dot"></i><span id="state">ルームを確認中</span></span><span id="scoreline"></span></div>
<main>
<section id="boardwrap"><div id="board"></div></section>
<section id="panel">
<div class="panel-title">MATCH</div>
<div class="kv">
<span class="k">手番</span><span class="v" id="turn">-</span>
<span class="k">手数</span><span class="v" id="moves">-</span>
<span class="k">あなた</span><span class="v" id="yourturn">-</span>
</div>
<div id="result"><div class="rk">勝敗</div><div class="rv" id="outcome">-</div>
<div style="font-size:12px;color:#c5d5cc;margin-top:4px"><span id="finalscore">-</span></div></div>
<div id="log">スタート記録がここに出ます</div>
<div style="font-size:10px;color:#5f7d70">othello MVP: ルーム制。他ゲームは後続 issue。</div>
</section>
</main>
</div>
<div id="unavailable" hidden>対戦サーバーに接続できません</div>
<script>
'use strict';
function discClass(cell){return cell===1?'black':cell===2?'white':'';}
function lastMoveFrom(log){ return null; }
function render(data){
 document.getElementById('room-tag').textContent='ルーム '+(data.roomId||'-');
 document.getElementById('state').textContent=data.phase?{IDLE:'ルーム待機中',ROOM_OPEN:'ルームを開きました',PLAYING:'対局中',FINISHED:'対局終了',FAILED:'異常終了',TIMEOUT:'タイムアウト'}[data.phase]||data.phase:'ルーム待機中';
 const board=document.getElementById('board');board.replaceChildren();
 if(Array.isArray(data.board)){
  for(let r=0;r<8;r++)for(let c=0;c<8;c++){
   const sq=document.createElement('div');sq.className='sq'+(data.board[r][c]?' '+discClass(data.board[r][c]):'');
   if(data.board[r][c])sq.innerHTML='<div class="disc"></div>';
   sq.title=String.fromCharCode(97+c)+(r+1);
   board.append(sq);
  }
 }
 document.getElementById('turn').textContent=data.turn==='black'?'視聴者(黒)':data.turn==='white'?'AI(白)':'-';
 document.getElementById('moves').textContent=data.movesPlayed!=null?String(data.movesPlayed):'-';
 document.getElementById('yourturn').textContent=data.yourTurn===true?'手番です':data.yourTurn===false?':AIの手番':'-';
 document.getElementById('scoreline').textContent=data.scores?('黒 '+data.scores.black+' − '+data.scores.white+' 白'):'';
 const result=document.getElementById('result');
 if(data.outcome){result.classList.add('on');document.getElementById('outcome').textContent={viewer_win:'視聴者の勝ち',ai_win:'AI(docich brain)の勝ち',draw:'引き分け',aborted:'中断',timeout:'タイムアウト'}[data.outcome]||data.outcome;
  document.getElementById('finalscore').textContent=data.result?('黒 '+data.result.black+' − '+data.result.white+' 白'):'';
 }else{result.classList.remove('on');document.getElementById('outcome').textContent='-';document.getElementById('finalscore').textContent='-';}
 const log=document.getElementById('log');
 log.textContent=(data.movesPlayed?Math.floor(data.movesPlayed/2)+' 手完了':'')||'スタート記録がここに出ます';
 showUnavailable(false);
}
function showUnavailable(hidden){document.getElementById('unavailable').hidden=!!hidden;}
async function refresh(){
 try{
  const r=await fetch('/api/othello',{cache:'no-store',signal:AbortSignal.timeout(2500)});
  if(!r.ok)throw new Error('unavailable');
  const data=await r.json();
  if(data.ok!==true)throw new Error('unavailable');
  render(data);
 }catch(e){showUnavailable(true);document.getElementById('state').textContent='ルームを再確認中';}
}
refresh();setInterval(refresh,__POLL_MS__);
</script></html>'''.replace("__POLL_MS__", str(POLL_MILLISECONDS))


class OthelloViewError(ValueError):
    """The view request or the control response violates a fixed bound."""


_ALLOWED_URL = re.compile(
    r"^https://[A-Za-z0-9.-]+(:\d+)?/room/snapshot/?$"
    r"|^http://127\.0\.0\.1(:\d+)?/room/snapshot/?$"
)


def validate_control_url(url: str) -> str:
    """The only outbound target: the Workers room snapshot on loopback.

    No argv, no logs, no state. An explicit empty/invalid value fails closed.
    """
    if not isinstance(url, str) or not _ALLOWED_URL.match(url):
        raise OthelloViewError("othello control url invalid")
    return url


def fetch_snapshot(url: str, *, timeout_seconds: float = 2.5) -> dict:
    """One bounded GET of the spectator snapshot. Errors are generic."""
    import urllib.request
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "docich-othello-view"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        if getattr(response, "status", 200) != 200:
            raise OthelloViewError("othello snapshot unavailable (fetch)")
        body = response.read(SNAPSHOT_MAX_BYTES + 1)
    if len(body) > SNAPSHOT_MAX_BYTES:
        raise OthelloViewError("othello snapshot unavailable (size)")
    try:
        snapshot = json.loads(body)
    except ValueError:
        raise OthelloViewError("othello snapshot unavailable (json)") from None
    if not isinstance(snapshot, dict) or snapshot.get("phase") not in PHASE_LABELS:
        raise OthelloViewError("othello snapshot unavailable (shape)")
    return snapshot


def project_snapshot(snapshot: dict, runtime_id: str, generation: int, lease_id: str | None) -> dict:
    """Rebuild the broadcast payload from the snapshot. Additive fields only."""
    if not isinstance(runtime_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", runtime_id) is None:
        raise OthelloViewError("invalid runtime id")
    if type(generation) is not int or generation < 1:
        raise OthelloViewError("invalid generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise OthelloViewError("invalid lease id") from None
    board = snapshot.get("board")
    if snapshot["phase"] != "IDLE":
        if not isinstance(board, list) or len(board) != 8 \
                or not all(isinstance(row, list) and len(row) == 8 for row in board):
            raise OthelloViewError("othello snapshot unavailable (board)")
        if any(cell not in (0, 1, 2) for row in board for cell in row):
            raise OthelloViewError("othello snapshot unavailable (cell)")
    payload = {
        "ok": True,
        "runtime_id": runtime_id,
        "generation": generation,
        "lease_id": lease_id,
        "phase": snapshot["phase"],
        "roomId": snapshot.get("roomId"),
        "turn": snapshot.get("turn"),
        "movesPlayed": snapshot.get("movesPlayed"),
        "outcome": snapshot.get("outcome"),
    }
    if snapshot["phase"] != "IDLE":
        payload["board"] = board
        payload["scores"] = snapshot.get("scores") if isinstance(snapshot.get("scores"), dict) else None
        payload["result"] = snapshot.get("result") if isinstance(snapshot.get("result"), dict) else None
        payload["yourTurn"] = snapshot.get("yourTurn")
    return payload


def handler_for(runtime_id: str, generation: int, lease_id: str, *, control_url: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # never log request paths or viewer identities

        def reply(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            port = self.server.server_port
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.reply(403, b"forbidden", "text/plain; charset=utf-8")
                return
            if self.path in {"/", "/broadcast"}:
                self.reply(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path != "/api/othello":
                self.reply(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                snapshot = fetch_snapshot(control_url)
                data = project_snapshot(snapshot, runtime_id, generation, lease_id)
            except (OSError, TimeoutError, OthelloViewError):
                self.reply(200, json.dumps({"ok": False, "reason": "unavailable"},
                                           ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
                return
            self.reply(200, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_POST(self):
            self.reply(405, b"method not allowed", "text/plain; charset=utf-8")

    return Handler


def serve(*, port: int, runtime_id: str, generation: int, lease_id: str, control_url: str) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise OthelloViewError("invalid port")
    # No --host switch: the listener can never bind publicly by configuration.
    with HTTPServer(("127.0.0.1", port),
                    handler_for(runtime_id, generation, lease_id, control_url=control_url)) as server:
        server.serve_forever(poll_interval=0.2)


def control_url_from_env(environ=None) -> str:
    import os
    env = os.environ if environ is None else environ
    return env.get("DOCICH_OTHELLO_CONTROL_URL", "")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="参加型オセロ MVP 読み取り専用ビュー")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runtime-id", default="preview")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--lease-id", default=None)
    parser.add_argument("--control-url", default=None,
                        help="ルームsnapshot取得URL。機密扱い: argv 未指定時は環境変数")
    args = parser.parse_args(argv)
    if args.generation is not None and (type(args.generation) is not int or args.generation < 1):
        parser.error("invalid generation")
    control_url = args.control_url or control_url_from_env()
    try:
        control_url = validate_control_url(control_url)
        serve(port=args.port, runtime_id=args.runtime_id,
              generation=args.generation or 1, lease_id=args.lease_id,
              control_url=control_url)
    except (OSError, OthelloViewError):
        # Fixed, non-sensitive line only: argv may carry the endpoint.
        print(json.dumps({"ok": False, "reason": "view-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
