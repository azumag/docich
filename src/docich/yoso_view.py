"""Read-only, loopback-only 予想王 leaderboard presentation.

The view is the display half of the 予想王 corner (docs/features/yosou-oh-corner.md).
It never writes the ledger, never talks to Twitch and never starts anything: the
corner owner records resolved predictions through ``prediction_leaderboard`` and
this page only projects the already-stored ranking to the broadcast frame.

Only the fields Twitch publishes on a prediction's ``top_predictors`` reach the
screen (public id/login/display name and channel points). Diagnostics emit counts
only; this module emits no viewer identity outside the board payload.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import time
import unicodedata
import uuid

from .prediction_leaderboard import (
    DEFAULT_LIMIT,
    DEFAULT_MIN_ROUNDS,
    FILE as LEDGER_FILE,
    LeaderboardError,
    load as load_ledger,
    ranking,
    stats,
)

DEFAULT_PORT = 8805
VIEW_NAME = "yosou-view"
MAX_NAME_CHARS = 24
MAX_MIN_ROUNDS = 100
MAX_LIMIT = 50

HTML = r'''<!doctype html>
<html lang="ja" translate="no"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="google" content="notranslate">
<title>予想王</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0b1017;color:#f2f5f8;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:12px 22px 12px;background:radial-gradient(ellipse at 30% 20%,#1b2a3c 0,#101823 58%,#0a0f15 100%);overflow:hidden}
header{height:58px;display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #2c3d50;padding-bottom:5px}
h1{font-size:28px;line-height:1.05;letter-spacing:.06em;margin:0;font-weight:800}.eyebrow{font-size:8px;letter-spacing:.3em;color:#7fb4c9;display:block;margin-bottom:1px}
.sub{font-size:11px;color:#a9b8c6;margin-top:1px}.tags{display:flex;gap:6px;align-items:center}.tag{border:1px solid #3c5a70;background:#12202e;color:#9fd3dc;padding:4px 8px;border-radius:99px;font-size:10px;letter-spacing:.04em;white-space:nowrap}
#status-line{height:22px;display:flex;align-items:center;justify-content:space-between;color:#93a8b8;font-size:11px;white-space:nowrap;overflow:hidden}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#63d0ac;margin-right:6px}
#board{height:calc(100% - 100px);border:1px solid #263a4c;border-radius:10px;background:linear-gradient(150deg,#13213199,#0d1620cc);overflow:hidden}
table{width:100%;border-collapse:collapse;table-layout:fixed}
thead th{background:#13202d;color:#8ea6b6;font-size:10px;letter-spacing:.1em;font-weight:600;text-align:left;padding:6px 10px;border-bottom:1px solid #263a4c;white-space:nowrap;overflow:hidden}
tbody td{padding:6px 10px;border-bottom:1px solid #1b2937;font-size:17px;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
tbody tr:nth-child(1) td{background:linear-gradient(90deg,#3a2f12aa,#13202d00)}
.rank{width:58px;font-weight:800;color:#ffd67a;font-size:20px}.name{width:auto;font-weight:700}.rate{width:118px;color:#9ff0c8;font-weight:750}.rounds{width:86px;color:#c8d6e2}.won{width:132px;color:#ffd9a8}.net{width:112px}
.up{color:#9ff0c8}.down{color:#ffb1b1}.zero{color:#8fa3b3}
#empty{padding:38px 20px;text-align:center;color:#9fb0bf;font-size:18px;line-height:1.7}
#unavailable{position:absolute;inset:0;z-index:5;display:flex;align-items:center;justify-content:center;background:#0b1017ee;color:#f0d9ad;text-align:center;font-size:20px;line-height:1.7;padding:40px}
#unavailable[hidden]{display:none}
#footer{position:absolute;left:22px;right:22px;bottom:5px;color:#6d8494;font-size:9px;line-height:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style>
<div id="stage">
<header>
<div><span class="eyebrow">PREDICTION KING</span><h1>予想王</h1><div class="sub">チャンネルポイント予想・的中率ランキング</div></div>
<div class="tags"><span class="tag">上位予想者のみ集計</span><span class="tag" id="window-tag">最小参加数 -</span></div>
</header>
<div id="status-line"><span><i class="dot"></i><span id="state">台帳を確認中</span></span><span id="totals"></span></div>
<section id="board">
<table><thead><tr><th class="rank">順位</th><th class="name">表示名</th><th class="rate">的中率</th><th class="rounds">参加数</th><th class="won">獲得ポイント</th><th class="net">純増</th></tr></thead><tbody id="rows"></tbody></table>
<div id="empty" hidden>確定した予想がまだありません</div>
</section>
<div id="footer">出典：Twitch Helix GET /helix/predictions の top_predictors（RESOLVEDのみ）／ 半熟英雄・Sorenの予想 ／ 上位予想者だけを集計したランキングです（全参加者の的中率ではありません）</div>
</div>
<div id="unavailable" hidden>予想台帳を確認できないため休止中</div>
<script>
'use strict';
function fit(){const el=document.getElementById('stage');const s=Math.min(innerWidth/960,innerHeight/540);el.style.transform=`scale(${s})`;el.style.left=`${(innerWidth-960*s)/2}px`;el.style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',fit);fit();
function pct(v){return `${(v*100).toFixed(1)}%`;}
function points(v){return String(v);}
function net(v){const sign=v>0?'+':'';return `${sign}${v}`;}
function showUnavailable(on){document.getElementById('unavailable').hidden=!on;}
function render(data){
 document.getElementById('state').textContent='確定済みの予想を集計したランキング';
 document.getElementById('totals').textContent=`確定 ${data.rounds} 予想 ／ 集計対象 ${data.viewers} 名`;
 document.getElementById('window-tag').textContent=`最小参加数 ${data.min_rounds}`;
 const body=document.getElementById('rows');body.replaceChildren();
 for(const row of data.ranking){
  const tr=document.createElement('tr');
  const cells=[[String(row.rank),'rank'],[row.user_name,'name'],[pct(row.hit_rate),'rate'],
   [points(row.rounds),'rounds'],[points(row.won),'won'],[net(row.net),'net '+(row.net>0?'up':row.net<0?'down':'zero')]];
  for(const [text,className] of cells){const td=document.createElement('td');td.className=className;td.textContent=text;tr.append(td);}
  body.append(tr);
 }
 document.getElementById('empty').hidden=data.ranking.length>0;
 document.getElementById('empty').textContent=data.rounds>0
  ?`確定 ${data.rounds} 予想のうち、最小参加数（${data.min_rounds}）を満たす予想者はいません`
  :'確定した予想がまだありません';
 showUnavailable(false);
}
async function poll(){
 try{
  const response=await fetch('/api/yosou',{cache:'no-store',signal:AbortSignal.timeout(2500)});
  if(!response.ok)throw new Error('unavailable');
  const data=await response.json();
  if(data.ok!==true||!Array.isArray(data.ranking))throw new Error('invalid');
  render(data);
 }catch(_){showUnavailable(true);document.getElementById('state').textContent='台帳を再確認中';}
}
poll();setInterval(poll,2000);
</script></html>'''


class YosoViewError(ValueError):
    """The view request or the ledger projection violates a fixed bound."""


def _int_bound(value, low: int, high: int, what: str) -> int:
    if type(value) is not int or isinstance(value, bool) or not low <= value <= high:
        raise YosoViewError(f"invalid {what}")
    return value


def display_name(value: str) -> str:
    """One bounded, control-free display string for the broadcast frame."""
    text = unicodedata.normalize("NFC", str(value))
    text = "".join(" " if ch.isspace() else ch for ch in text
                   if not unicodedata.category(ch).startswith("C"))
    text = " ".join(text.split())
    return text[:MAX_NAME_CHARS] or "（表示名なし）"


def board(state_dir, *, min_rounds: int = DEFAULT_MIN_ROUNDS,
          limit: int = DEFAULT_LIMIT, clock=time.time) -> dict:
    """Project the recorded leaderboard to the fixed broadcast payload."""
    min_rounds = _int_bound(min_rounds, 1, MAX_MIN_ROUNDS, "min_rounds")
    limit = _int_bound(limit, 1, MAX_LIMIT, "limit")
    ledger = load_ledger(Path(state_dir) / LEDGER_FILE)
    counts = stats(ledger)
    rows = ranking(ledger, min_rounds=min_rounds, limit=limit)
    return {
        "ok": True,
        "updated_at": int(clock()),
        "rounds": counts["rounds"],
        "viewers": counts["viewers"],
        "min_rounds": min_rounds,
        "limit": limit,
        "ranking": [
            {
                "rank": index,
                "user_name": display_name(row["user_name"] or row["user_login"]),
                "hit_rate": row["hit_rate"],
                "rounds": row["rounds"],
                "hits": row["hits"],
                "used": row["used"],
                "won": row["won"],
                "net": row["net"],
            }
            for index, row in enumerate(rows, 1)
        ],
    }


def handler_for(state_dir, runtime_id: str = "preview", *, generation=None,
                lease_id=None, min_rounds: int = DEFAULT_MIN_ROUNDS,
                limit: int = DEFAULT_LIMIT, clock=time.time):
    state_dir = Path(state_dir)
    if not isinstance(runtime_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", runtime_id) is None:
        raise YosoViewError("invalid runtime id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise YosoViewError("invalid generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise YosoViewError("invalid lease id") from None
    min_rounds = _int_bound(min_rounds, 1, MAX_MIN_ROUNDS, "min_rounds")
    limit = _int_bound(limit, 1, MAX_LIMIT, "limit")

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
            if self.path != "/api/yosou":
                self.reply(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                data = board(state_dir, min_rounds=min_rounds, limit=limit, clock=clock)
            except (OSError, LeaderboardError, YosoViewError):
                data, status = {"ok": False, "reason": "leaderboard-unavailable"}, 503
            else:
                status = 200
            data.update(runtime_id=runtime_id, generation=generation, lease_id=lease_id)
            self.reply(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    return Handler


def serve(state_dir, *, port: int = DEFAULT_PORT, runtime_id: str = "preview",
          generation=None, lease_id=None, min_rounds: int = DEFAULT_MIN_ROUNDS,
          limit: int = DEFAULT_LIMIT) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise YosoViewError("invalid port")
    # No --host switch: the listener can never bind publicly by configuration.
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(state_dir, runtime_id, generation=generation, lease_id=lease_id,
                    min_rounds=min_rounds, limit=limit),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="予想王ランキングの読み取り専用画面")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runtime-id", default="preview")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--lease-id")
    parser.add_argument("--min-rounds", type=int, default=DEFAULT_MIN_ROUNDS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)
    try:
        serve(args.state_dir, port=args.port, runtime_id=args.runtime_id,
              generation=args.generation, lease_id=args.lease_id,
              min_rounds=args.min_rounds, limit=args.limit)
    except (OSError, YosoViewError):
        print(json.dumps({"ok": False, "reason": "view-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
