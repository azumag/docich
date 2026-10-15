"""Read-only, loopback-only 同志カードランキング presentation.

The view is the display half of the 同志カード corner
(``docs/features/doushi-card-corner.md`` §4). It renders one already-fetched,
validated ranking payload to four rotating pages and never fetches again while
on air: the program adapter writes the payload before the switch commits and a
missing or invalid payload keeps this process from serving at all (fail-closed,
no stale board).

Only the fields design §6 allows out of TwiCa reach this frame. The endpoint
itself, the overlay URL it was derived from and any Twitch identity beyond the
published display name stay out of argv, logs and state.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import uuid

from .doushi_ranking import DoushiRankingError, validate_payload

DEFAULT_PORT = 8806
VIEW_NAME = "doushi-view"
#: design §7-3: 1 ページ 15 秒 × 4 ページ（提出値のまま）。
PAGE_SECONDS = 15
PAGE_COUNT = 4
MAX_RUNTIME_ID_CHARS = 128

HTML = r'''<!doctype html>
<html lang="ja" translate="no"><meta charset="utf-8"><meta name="google" content="notranslate"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>同志カード大賞</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0b1017;color:#f2f5f8;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:12px 22px 14px;background:radial-gradient(ellipse at 30% 20%,#1b2a3c 0,#101823 58%,#0a0f15 100%);overflow:hidden}
header{height:56px;display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #2c3d50;padding-bottom:5px}
h1{font-size:27px;line-height:1.05;letter-spacing:.05em;margin:0;font-weight:800}.eyebrow{font-size:8px;letter-spacing:.3em;color:#7fb4c9;display:block;margin-bottom:1px}
.sub{font-size:11px;color:#a9b8c6;margin-top:1px}.tags{display:flex;gap:6px;align-items:center}.tag{border:1px solid #3c5a70;background:#12202e;color:#9fd3dc;padding:4px 8px;border-radius:99px;font-size:10px;letter-spacing:.04em;white-space:nowrap}
#status-line{height:22px;display:flex;align-items:center;justify-content:space-between;color:#93a8b8;font-size:11px;white-space:nowrap;overflow:hidden}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#63d0ac;margin-right:6px}
#board{height:calc(100% - 100px);border:1px solid #263a4c;border-radius:10px;background:linear-gradient(150deg,#13213199,#0d1620cc);overflow:hidden}
table{width:100%;border-collapse:collapse;table-layout:fixed}
thead th{background:#13202d;color:#8ea6b6;font-size:10px;letter-spacing:.1em;font-weight:600;text-align:left;padding:6px 10px;border-bottom:1px solid #263a4c;white-space:nowrap;overflow:hidden}
tbody td{padding:5px 10px;border-bottom:1px solid #1b2937;font-size:16px;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
tbody tr:nth-child(1) td{background:linear-gradient(90deg,#3a2f12aa,#13202d00)}
.rank{width:58px;font-weight:800;color:#ffd67a;font-size:19px}.name{width:auto;font-weight:700}.value{width:210px;color:#9ff0c8;font-weight:750;text-align:right}
.value.right{color:#c8d6e2}
#empty{padding:38px 20px;text-align:center;color:#9fb0bf;font-size:18px;line-height:1.7}
#unavailable{position:absolute;inset:0;z-index:5;display:flex;align-items:center;justify-content:center;background:#0b1017ee;color:#f0d9ad;text-align:center;font-size:20px;line-height:1.7;padding:40px}
#unavailable[hidden]{display:none}
#footer{position:absolute;left:22px;right:22px;bottom:5px;color:#6d8494;font-size:9px;line-height:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#page-dots{position:absolute;right:24px;bottom:26px;display:flex;gap:6px}.page-dot{width:7px;height:7px;border-radius:50%;background:#2c3d50}.page-dot.on{background:#7fb4c9}
</style>
<div id="stage">
<header>
<div><span class="eyebrow">DOUSHI CARD AWARDS</span><h1>同志カード大賞</h1><div class="sub" id="page-sub">ランキング</div></div>
<div class="tags"><span class="tag" id="period-tag">集計期間 -</span><span class="tag">配信者本人とBOTを除く</span></div>
</header>
<div id="status-line"><span><i class="dot"></i><span id="state">ランキングを確認中</span></span><span id="totals"></span></div>
<section id="board">
<table><thead><tr><th class="rank">順位</th><th class="name" id="th-name">表示名</th><th class="value" id="th-value">件数</th></tr></thead><tbody id="rows"></tbody></table>
<div id="empty" hidden>該当するデータがまだありません</div>
</section>
<div id="page-dots"></div>
<div id="footer"></div>
</div>
<div id="unavailable" hidden>ランキングを確認できないため休止中</div>
<script>
'use strict';
const PAGES=[
 {key:'by_count',title:'排出枚数 TOP10',sub:'カードの排出枚数（N連はN件）',nameTh:'同志',valueTh:'枚',
  note:'排出カード枚数（N連はN件）。2026-07-04 以前のN連2枚目以降は含みません',row:r=>[`${r.username}`,`${r.redemption_count} 枚`]},
 {key:'by_points',title:'使用チャネルポイント TOP10',sub:'ガチャに使用したチャンネルポイント',nameTh:'同志',valueTh:'ポイント',
  note:'使用チャネルポイント合計',row:r=>[`${r.username}`,`${r.total_points} pt`]},
 {key:'by_unique',title:'蒐集カード数 TOP10',sub:'現在蒐集しているカードの種類数（全期間）',nameTh:'同志',valueTh:'種類',
  note:'現在蒐集しているカードの種類数（全期間）',row:r=>[`${r.username}`,`${r.unique_count} 種類`]},
 {key:'popular_cards',title:'人気カード TOP10',sub:'期間内に引かれたカード',nameTh:'カード名',valueTh:'枚数',
  note:'期間内に引かれた枚数と人数',row:r=>[`${r.card_name}`,`${r.actual_count} 枚（${r.drawer_count}人が獲得）`]}
];
function fit(){const el=document.getElementById('stage');const s=Math.min(innerWidth/960,innerHeight/540);el.style.transform=`scale(${s})`;el.style.left=`${(innerWidth-960*s)/2}px`;el.style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',fit);fit();
function showUnavailable(on){document.getElementById('unavailable').hidden=!on;}
function pageNote(data,page){return `${data.note} ／ ${page.note}`;}
function render(data,index){
 const page=PAGES[index];
 document.getElementById('page-sub').textContent=page.title;
 document.getElementById('th-name').textContent=page.nameTh;
 document.getElementById('th-value').textContent=page.valueTh;
 document.getElementById('state').textContent=page.sub;
 const rows=data[page.key];
 document.getElementById('totals').textContent=page.key==='by_unique'
  ?`全 ${data.card_type_total} 種類`
  :`${data.period==='all'?'全期間':data.period==='7d'?'直近7日':'直近30日'} ／ 上位 ${rows.length} 件`;
 document.getElementById('period-tag').textContent=data.period==='all'?'集計期間: 全期間':data.period==='7d'?'集計期間: 直近7日':'集計期間: 直近30日';
 const body=document.getElementById('rows');body.replaceChildren();
 for(const row of rows){
  const tr=document.createElement('tr');
  const cells=[String(row.rank),...page.row(row)];
  for(let i=0;i<cells.length;i++){
   const td=document.createElement('td');
   td.className=i===0?'rank':i===1?'name':'value';
   td.textContent=cells[i];tr.append(td);
  }
  body.append(tr);
 }
 document.getElementById('empty').hidden=rows.length>0;
 document.getElementById('empty').textContent=`${page.title} の該当データがありません`;
 document.getElementById('footer').textContent=pageNote(data,page);
 const dots=document.getElementById('page-dots');dots.replaceChildren();
 for(let i=0;i<PAGES.length;i++){const d=document.createElement('i');d.className='page-dot'+(i===index?' on':'');dots.append(d);}
 showUnavailable(false);
}
let payload=null;let page=0;let timer=null;
async function load(){
 try{
  const response=await fetch('/api/doushi',{cache:'no-store',signal:AbortSignal.timeout(2500)});
  if(!response.ok)throw new Error('unavailable');
  const data=await response.json();
  if(data.ok!==true||!Array.isArray(data.by_count)||!Array.isArray(data.popular_cards))throw new Error('invalid');
  payload=data;showUnavailable(false);render(payload,page);
 }catch(_){showUnavailable(true);document.getElementById('state').textContent='ランキングを再確認中';}
}
function schedule(){if(timer)clearInterval(timer);timer=setInterval(()=>{page=(page+1)%PAGES.length;if(payload)render(payload,page);},__PAGE_SECONDS__*1000);}
load().then(schedule);
</script></html>'''.replace("__PAGE_SECONDS__", str(PAGE_SECONDS))


class DoushiViewError(ValueError):
    """The view request or the stored payload violates a fixed bound."""


def load_payload(payload_file) -> dict:
    """Read and re-validate the adapter-written payload (fail-closed).

    The payload file is transient runtime state: it exists only while the
    corner is starting and is regenerated on every start. Nothing here writes
    it back out.
    """
    try:
        raw = Path(payload_file).read_text(encoding="utf-8")
    except OSError:
        raise DoushiRankingError("doushi ranking payload missing") from None
    try:
        stored = json.loads(raw)
    except ValueError:
        raise DoushiRankingError("doushi ranking payload unreadable") from None
    if (not isinstance(stored, dict)
            or not isinstance(stored.get("payload"), dict)
            or not isinstance(stored.get("limit"), int)
            or not isinstance(stored.get("period"), str)):
        raise DoushiRankingError("doushi ranking payload invalid")
    period = stored["period"]
    limit = stored["limit"]
    # Re-run the same validator the adapter used: a tampered or truncated file
    # must not reach the broadcast frame.
    validated = validate_payload(stored["payload"], period=period, limit=limit)
    return {
        "ok": True,
        "period": validated["period"],
        "generated_at": validated["generated_at"],
        "metric_labels": validated["metric_labels"],
        "note": validated["note"],
        "card_type_total": validated["card_type_total"],
        "by_count": validated["by_count"],
        "by_points": validated["by_points"],
        "by_unique": validated["by_unique"],
        "popular_cards": validated["popular_cards"],
        "limit": limit,
    }


def handler_for(payload: dict, runtime_id: str = "preview", *, generation=None,
                lease_id=None, page_seconds: int = PAGE_SECONDS):
    if not isinstance(runtime_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", runtime_id) is None:
        raise DoushiViewError("invalid runtime id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise DoushiViewError("invalid generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise DoushiViewError("invalid lease id") from None
    if type(page_seconds) is not int or isinstance(page_seconds, bool) or not 1 <= page_seconds <= 300:
        raise DoushiViewError("invalid page seconds")

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
            if self.path != "/api/doushi":
                self.reply(404, b"not found", "text/plain; charset=utf-8")
                return
            data = dict(payload)
            data.update(runtime_id=runtime_id, generation=generation, lease_id=lease_id,
                        page_seconds=page_seconds)
            self.reply(200, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    return Handler


def serve(payload: dict, *, port: int = DEFAULT_PORT, runtime_id: str = "preview",
          generation=None, lease_id=None) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise DoushiViewError("invalid port")
    # No --host switch: the listener can never bind publicly by configuration.
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(payload, runtime_id, generation=generation, lease_id=lease_id),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="同志カードランキングの読み取り専用画面")
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
    except (OSError, DoushiViewError, DoushiRankingError):
        # Fixed, non-sensitive line only: argv may not carry the endpoint and
        # the payload may carry display names.
        print(json.dumps({"ok": False, "reason": "view-unavailable"}))
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
