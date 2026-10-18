"""Read-only, loopback-only ぷよぷよクローン self-play presentation.

The view is the display half of the ぷよぷよクローン corner
(``docs/superpowers/specs/2026-10-18-puyo-puyo-clone-corner-design.md``). It
renders one already-played offline match summary to a fixed 960x540 frame and
never fetches, never talks to Twitch and never starts anything: the program
adapter plays the bounded self-play through ``puyo_corner`` before the switch
commits and writes the payload as a transient runtime file; a missing or
invalid payload keeps this process from serving at all (fail-closed, no stale
board). The live corner's own engine functions remain the only rules authority.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import uuid

from .puyo_corner import BOARD_COLS, BOARD_ROWS, OUTCOMES

DEFAULT_PORT = 8807
VIEW_NAME = "puyo-view"
MAX_TEXT_CHARS = 200
MAX_SCORE = 10 ** 9
MAX_CHAIN = 10 ** 6

#: 選択肢は ruleset の colors そのまま。未知の配色は既定の灰色に落ちる。
COLOR_CSS = {
    "red": "#e05a4e",
    "blue": "#4e8fe0",
    "green": "#63c56e",
    "yellow": "#e0c94e",
}

HTML = r'''<!doctype html>
<html lang="ja" translate="no"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="google" content="notranslate">
<title>ぷよぷよクローン</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0b1017;color:#f2f5f8;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:12px 22px 12px;background:radial-gradient(ellipse at 30% 20%,#1b2a3c 0,#101823 58%,#0a0f15 100%);overflow:hidden}
header{height:58px;display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #2c3d50;padding-bottom:5px}
h1{font-size:28px;line-height:1.05;letter-spacing:.06em;margin:0;font-weight:800}.eyebrow{font-size:8px;letter-spacing:.3em;color:#7fb4c9;display:block;margin-bottom:1px}
.sub{font-size:11px;color:#a9b8c6;margin-top:1px}.tags{display:flex;gap:6px;align-items:center}.tag{border:1px solid #3c5a70;background:#12202e;color:#9fd3dc;padding:4px 8px;border-radius:99px;font-size:10px;letter-spacing:.04em;white-space:nowrap}
#status-line{height:22px;display:flex;align-items:center;justify-content:space-between;color:#93a8b8;font-size:11px;white-space:nowrap;overflow:hidden}.dot{display:inline-block;width:6px;border-radius:50%;background:#63d0ac;margin-right:6px}
#boards{display:flex;gap:18px;height:calc(100% - 100px);align-items:flex-start;justify-content:center;padding-top:10px}
.panel{border:1px solid #263a4c;border-radius:10px;background:linear-gradient(150deg,#13213199,#0d1620cc);padding:10px 14px}
.panel h2{margin:0 0 4px;font-size:15px;color:#9fd3dc}
.panel .score{font-size:12px;color:#a9b8c6;margin-bottom:6px}
.grid{display:grid;grid-template-rows:repeat(12,26px);grid-template-columns:repeat(6,26px);gap:2px}
.cell{border-radius:4px;background:#101722;border:1px solid #1b2937}
#result{color:#ffd67a;font-weight:700}
#footer{position:absolute;left:22px;right:22px;bottom:5px;color:#6d8494;font-size:9px;line-height:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#unavailable{position:absolute;inset:0;z-index:5;display:flex;align-items:center;justify-content:center;background:#0b1017ee;color:#f0d9ad;text-align:center;font-size:20px;line-height:1.7;padding:40px}
#unavailable[hidden]{display:none}
</style>
<div id="stage">
<header>
<div><span class="eyebrow">PUYO PUYO CLONE</span><h1>ぷよぷよクローン</h1><div class="sub">オフライン自対戦・4つ揃いで消える</div></div>
<div class="tags"><span class="tag">自己対局の記録表示</span><span class="tag">連鎖ボーナス付き</span></div>
</header>
<div id="status-line"><span><i class="dot"></i><span id="state">対局を確認中</span></span><span id="result"></span></div>
<section id="boards">
<div class="panel"><h2>プレイヤー</h2><div class="score" id="user-score"></div><div class="grid" id="user-grid"></div></div>
<div class="panel"><h2>CPU</h2><div class="score" id="ai-score"></div><div class="grid" id="ai-grid"></div></div>
</section>
<div id="footer">出典：docich ぷよぷよクローン (#698) のオフライン自対戦による対局結果 ─ 6x12 盤・4つ以上同色で消去</div>
</div>
<div id="unavailable" hidden>対局を確認できないため休止中</div>
<script>
'use strict';
const COLORS=__COLORS__;
function fit(){const el=document.getElementById('stage');const s=Math.min(innerWidth/960,innerHeight/540);el.style.transform=`scale(${s})`;el.style.left=`${(innerWidth-960*s)/2}px`;el.style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',fit);fit();
function showUnavailable(on){document.getElementById('unavailable').hidden=!on;}
function drawBoard(gridId,scoreId,label,side,data){
 const grid=document.getElementById(gridId);grid.replaceChildren();
 const board=data&&data.board;
 if(Array.isArray(board)){
  for(const row of board){
   if(!Array.isArray(row))continue;
   for(const color of row){
    const cell=document.createElement('div');cell.className='cell';
    if(color&&COLORS[color]){cell.style.background=COLORS[color];cell.style.borderColor='rgba(255,255,255,.18)';}
    grid.append(cell);
   }
  }
 }
 document.getElementById(scoreId).textContent=`${label} ${data&&typeof data.score==='number'?data.score:0} 点／最大連鎖 ${data&&typeof data.chain==='number'?data.chain:0}`;
}
let lastData=null;
function render(data){
 if(data.ok!==true||!data.user||!data.ai)throw new Error('invalid');
 document.getElementById('state').textContent='対局済みの自対戦を表示中';
 document.getElementById('result').textContent=data.winner==='draw'?'引き分け':
  data.winner==='user'?'プレイヤーの勝ち':data.winner==='ai'?'CPUの勝ち':'';
 drawBoard('user-grid','user-score','得点','user',data.user);
 drawBoard('ai-grid','ai-score','得点','ai',data.ai);
 lastData=data;
 showUnavailable(false);
}
async function poll(){
 try{
  const response=await fetch('/api/puyo',{cache:'no-store',signal:AbortSignal.timeout(2500)});
  if(!response.ok)throw new Error('unavailable');
  render(await response.json());
 }catch(_){showUnavailable(true);document.getElementById('state').textContent='対局を再確認中';}
}
poll();setInterval(poll,2000);
</script></html>'''.replace("__COLORS__", json.dumps(COLOR_CSS, ensure_ascii=False))

SIDE_KEYS = ("user", "ai")
SIDES = frozenset(SIDE_KEYS)
OUTCOME_WINNERS = frozenset({"user", "ai", "draw", None})


class PuyoViewError(ValueError):
    """The view request or the stored payload violates a fixed bound."""


def _side_invalid(side):
    return (not isinstance(side, dict)
            or type(side.get("score")) is not int
            or not 0 <= side["score"] <= MAX_SCORE
            or type(side.get("chain")) is not int
            or not 0 <= side["chain"] <= MAX_CHAIN
            or not isinstance(side.get("board"), list)
            or len(side["board"]) != BOARD_ROWS
            or any(not isinstance(row, list) or len(row) != BOARD_COLS
                   or any(cell is not None and (
                       not isinstance(cell, str) or not (1 <= len(cell) <= 16))
                       for cell in row)
                   for row in side["board"]))


def validate_match_payload(raw) -> dict:
    """Validate the adapter-written match summary. Fail-closed."""
    if not isinstance(raw, dict):
        raise PuyoViewError("match payload must be a mapping")
    if raw.get("schema") != 1:
        raise PuyoViewError("match payload schema must be 1")
    if not isinstance(raw.get("ruleset_id"), str) or not (1 <= len(raw["ruleset_id"]) <= 128):
        raise PuyoViewError("match payload ruleset id is invalid")
    for key in ("started_at", "ended_at"):
        value = raw.get(key)
        if not isinstance(value, str) or not (1 <= len(value) <= 40):
            raise PuyoViewError(f"match payload {key} is invalid")
    if raw.get("outcome") not in OUTCOMES:
        raise PuyoViewError("match payload outcome is invalid")
    if raw.get("winner") not in OUTCOME_WINNERS:
        raise PuyoViewError("match payload winner is invalid")
    for key in ("frames", "actions"):
        value = raw.get(key)
        if type(value) is not int or isinstance(value, bool) or not 0 <= value <= 10 ** 9:
            raise PuyoViewError(f"match payload {key} is invalid")
    for side in SIDE_KEYS:
        if _side_invalid(raw.get(side)):
            raise PuyoViewError(f"match payload {side} summary is invalid")
    return raw


def match_payload_from_state(state) -> dict:
    """Build the view payload from an offline terminal engine state.

    The engine owns the rules and all limits (``copy_board`` re-validates both
    boards fail-closed); this projection only pins the shape the view
    validates against.
    """
    from .puyo_corner import copy_board  # keeps a single board validator

    state = dict(state)
    payload = {
        "schema": 1,
        "ruleset_id": state.get("ruleset_id"),
        "started_at": state.get("started_at"),
        "ended_at": state.get("ended_at"),
        "outcome": state.get("outcome"),
        "frames": state.get("frames", 0),
        "actions": state.get("actions", 0),
        "winner": state.get("winner"),
        "user": {
            "score": state.get("user_score", 0),
            "chain": state.get("user_chain", 0),
            "board": copy_board(state.get("user_board")),
        },
        "ai": {
            "score": state.get("ai_score", 0),
            "chain": state.get("ai_chain", 0),
            "board": copy_board(state.get("ai_board")),
        },
    }
    return validate_match_payload(payload)


def load_payload(payload_file) -> dict:
    """Read and re-validate the adapter-written payload (fail-closed).

    The payload file is transient runtime state: it exists only while the
    corner is starting and is regenerated on every start. Nothing here writes
    it back out.
    """
    try:
        raw = Path(payload_file).read_text(encoding="utf-8")
    except OSError:
        raise PuyoViewError("puyo match payload missing") from None
    try:
        stored = json.loads(raw)
    except ValueError:
        raise PuyoViewError("puyo match payload unreadable") from None
    if not isinstance(stored, dict) or stored.get("schema") != 1:
        raise PuyoViewError("puyo match payload wrapper is invalid")
    payload = validate_match_payload(stored.get("payload"))
    return {"ok": True, **payload}


def handler_for(payload: dict, runtime_id: str = "preview", *,
                generation=None, lease_id=None):
    if not isinstance(runtime_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", runtime_id) is None:
        raise PuyoViewError("invalid runtime id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise PuyoViewError("invalid generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise PuyoViewError("invalid lease id") from None
    body = dict(payload)
    body.update(runtime_id=runtime_id, generation=generation, lease_id=lease_id)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # never log request paths or viewer identities

        def reply(self, status: int, body_bytes: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body_bytes)))
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
                self.wfile.write(body_bytes)
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
            if self.path != "/api/puyo":
                self.reply(404, b"not found", "text/plain; charset=utf-8")
                return
            self.reply(200, json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    return Handler


def serve(payload: dict, *, port: int = DEFAULT_PORT, runtime_id: str = "preview",
          generation=None, lease_id=None) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise PuyoViewError("invalid port")
    # No --host switch: the listener can never bind publicly by configuration.
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(payload, runtime_id, generation=generation, lease_id=lease_id),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ぷよぷよクローン自対戦の読み取り専用画面")
    parser.add_argument("--payload-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runtime-id", default="preview")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--lease-id")
    args = parser.parse_args(argv)
    try:
        payload = load_payload(args.payload_file)
        serve(payload, port=args.port, runtime_id=args.runtime_id,
              generation=args.generation, lease_id=args.lease_id)
    except (OSError, PuyoViewError):
        # Fixed, non-sensitive line only: the payload may carry match details.
        print(json.dumps({"ok": False, "reason": "view-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
