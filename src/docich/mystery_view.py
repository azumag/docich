"""Read-only broadcast board for the mystery corner (loopback HTML view).

The corner manager writes one bounded payload (``mystery_view.json``) into the
state directory; this view server only reads it and renders. It never talks to
Twitch, never mutates any file, and binds to the generation it was started
with so a stale board can never be served to a newer runtime (the
``tsuitate_show`` binding rule).

Same loopback-only contract as the other docich broadcast views: no
``--host`` switch, Host header check, no-store cache headers, and the shared
Japanese-page markers (``tests/test_broadcast_view_standard.py``).
"""
from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import time
import uuid

VIEW_NAME = "mystery-view"
DEFAULT_PORT = 8807
PAYLOAD_FILENAME = "mystery_view.json"
MAX_PAYLOAD_BYTES = 256 * 1024
READY_PATH = "/api/mystery"
PHASES = ("intro", "investigate", "interrogate", "deduction", "prediction", "reveal", "done")


class MysteryViewError(ValueError):
    """A fixed, non-sensitive view rejection."""


def payload_path(state_dir) -> Path:
    return Path(state_dir) / PAYLOAD_FILENAME


def read_payload(path) -> dict | None:
    """The bounded current payload, or ``None`` when absent/unreadable.

    Fail-soft by design: a missing or invalid payload is an "unavailable"
    board, never a hidden write and never raw disk contents on air.
    """
    try:
        path = Path(path)
        if not path.exists() or path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_PAYLOAD_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != "mystery-view":
        return None
    if data.get("phase") not in PHASES:
        return None
    case = data.get("case")
    if not isinstance(case, dict) or not isinstance(case.get("commitment"), str):
        return None
    return data


def write_payload(path, payload: dict) -> None:
    """Atomic, bounded write from the corner manager (owner side)."""
    path = Path(path)
    blob = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(blob) > MAX_PAYLOAD_BYTES:
        raise MysteryViewError("view payload exceeds the size bound")
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".mystery_view.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(blob)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


HTML = '''<html lang="ja" translate="no"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="google" content="notranslate">
<title>AI探偵ミステリー</title>
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0d0b12;color:#f2f0f8;font-family:"Noto Sans CJK JP","Yu Gothic",sans-serif}
#stage{position:absolute;width:960px;height:540px;transform-origin:top left;padding:12px 22px;background:radial-gradient(ellipse at 70% 10%,#241a33 0,#141020 58%,#0a080f 100%);overflow:hidden}
header{height:54px;display:flex;align-items:flex-end;justify-content:space-between;border-bottom:1px solid #3a2c50;padding-bottom:4px}
h1{font-size:26px;line-height:1.05;letter-spacing:.06em;margin:0;font-weight:800}.eyebrow{font-size:8px;letter-spacing:.3em;color:#c9a7ff;display:block;margin-bottom:1px}
.sub{font-size:11px;color:#b3a8c6;margin-top:1px}.tag{border:1px solid #55406e;background:#1c1430;color:#d9c2ff;padding:4px 8px;border-radius:99px;font-size:10px;white-space:nowrap}
#status-line{height:20px;display:flex;align-items:center;justify-content:space-between;color:#a99fc0;font-size:11px;white-space:nowrap;overflow:hidden}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#8f7bd9;margin-right:6px}
#case-line{margin:6px 0 4px;font-size:15px;line-height:1.4;color:#efe9ff}
#case-line b{color:#ffd67a}
#board{display:flex;gap:10px;height:330px}
#board[hidden],#matrix[hidden],#revealboard[hidden]{display:none}
.column{flex:1;border:1px solid #332748;border-radius:10px;background:linear-gradient(150deg,#1a132999,#120d1bcc);padding:8px;overflow:hidden;display:flex;flex-direction:column}
.column h2{font-size:11px;letter-spacing:.12em;color:#a08cc9;margin:0 0 6px;font-weight:700}
.card{border:1px solid #2c2242;border-radius:8px;background:#171126;padding:6px 8px;margin-bottom:6px;font-size:13px;line-height:1.35}
.card .name{font-weight:800;color:#e8e2ff}.card .role{color:#9d8fc0;font-size:11px;margin-left:6px}
.card .clue{color:#ffe6a8;font-size:12px;margin-top:2px}.card .alibi{color:#a8ffd0;font-size:12px;margin-top:2px}
.card.dim{opacity:.45}.card.hit{border-color:#7b5fd0;background:#1d1533}
.poll{font-size:13px;line-height:1.5;color:#dcd4f2;white-space:pre-wrap}
.poll .q{font-weight:800;color:#ffd67a}
.predict{font-size:13px;line-height:1.5;color:#dcd4f2}
#matrix{display:flex;gap:10px;height:330px}
#matrix .mrooms{flex:0 0 38%}#matrix .msusp{flex:1}
.mrow{border:1px solid #2c2242;border-radius:8px;background:#171126;padding:5px 7px;margin-bottom:5px;font-size:11.5px;line-height:1.35}
.mrow .who{font-weight:800;color:#e8e2ff}.mrow .role{color:#9d8fc0;font-size:10px;margin-left:5px}
.mrow .line{color:#cdc4e4;margin-top:1px}.mrow .say{color:#a8ffd0}.mrow .trace{color:#ffe6a8}
.mrow .cl{font-size:11px;color:#ffe6a8;margin-top:2px}
#revealboard{display:flex;gap:9px;height:330px}
#revealboard .rcol{flex:1;border:1px solid #4a3768;border-radius:10px;background:linear-gradient(160deg,#221736cc,#140f21cc);padding:7px 9px;overflow:hidden}
#revealboard .rcol:nth-child(1){flex:.8}#revealboard .rcol:nth-child(2){flex:1.3}#revealboard .rcol:nth-child(3){flex:1.1}
#revealboard h3{font-size:10px;letter-spacing:.14em;color:#c9a7ff;margin:0 0 5px;font-weight:800}
.blk{transition:opacity .4s}.blk.pending{opacity:.22}
.who-big{font-size:21px;font-weight:800;color:#ffd67a;line-height:1.2}
.kv{font-size:11.5px;color:#efe9ff;margin-top:3px;line-height:1.4}.kv b{color:#c9a7ff;font-weight:700}
.li{font-size:11px;line-height:1.4;color:#e4dcf6;margin:0 0 3px;padding-left:9px;text-indent:-9px}
.li:before{content:"・";color:#8f7bd9}
.hint{font-size:10px;color:#9d8fc0;margin:-2px 0 5px}
.hit-line{font-size:12px;color:#a8ffd0;margin-top:4px}
.commit{font-size:10px;color:#8f84ad;margin-top:5px;word-break:break-all}
#footer{position:absolute;left:22px;right:22px;bottom:4px;color:#6d6488;font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#unavailable{position:absolute;inset:0;z-index:5;display:flex;align-items:center;justify-content:center;background:#0d0b12ee;color:#e8d9ad;font-size:20px}
#unavailable[hidden]{display:none}
</style>
<div id="stage">
<header>
<div><span class="eyebrow">AI DETECTIVE MYSTERY</span><h1>AI探偵ミステリー</h1><div class="sub">みんなで探偵・参加型推理コーナー</div></div>
<div class="tags"><span class="tag" id="phase-tag">事件を準備中</span></div>
</header>
<div id="status-line"><span><i class="dot"></i><span id="state">事件を確認中</span></span><span id="participation"></span></div>
<section id="case-line"></section>
<section id="board">
 <div class="column" id="rooms-col"><h2>現場の部屋（アンケートで調査）</h2><div id="rooms"></div></div>
 <div class="column" id="suspects-col"><h2>容疑者（アンケートで尋問）</h2><div id="suspects"></div></div>
 <div class="column" id="action-col"><h2>参加状況</h2><div id="action"></div></div>
</section>
<section id="matrix" hidden>
 <div class="column mrooms"><h2>手がかり（全室）</h2><div id="mrooms"></div></div>
 <div class="column msusp"><h2>容疑者とアリバイ（照合）</h2><div class="hint">時刻つきの手がかりの部屋と、証言の部屋をくらべてください</div><div id="msusp"></div></div>
</section>
<section id="revealboard" hidden>
 <div class="rcol">
  <h3>① 判定</h3>
  <div class="blk" data-step="1" id="rv-verdict"></div>
 </div>
 <div class="rcol">
  <h3>② 事件の全容</h3>
  <div class="blk" data-step="2" id="rv-story"></div>
  <h3 style="margin-top:6px">③ 動機</h3>
  <div class="blk" data-step="3" id="rv-motive"></div>
 </div>
 <div class="rcol">
  <h3>④ 推理</h3>
  <div class="blk" data-step="4" id="rv-reason"></div>
 </div>
</section>
<div id="footer">事件の答えは開始時にハッシュで確定済みです。終了後にシードを開示し、誰でも公開コードで検証できます。</div>
</div>
<div id="unavailable" hidden>事件の配信を準備しています</div>
<script>
'use strict';
function fit(){const el=document.getElementById('stage');const s=Math.min(innerWidth/960,innerHeight/540);el.style.transform=`scale(${s})`;el.style.left=`${(innerWidth-960*s)/2}px`;el.style.top=`${(innerHeight-540*s)/2}px`;}
addEventListener('resize',fit);fit();
const PHASE_LABELS={intro:'事件発生',investigate:'現場調査（アンケート投票中）',interrogate:'容疑者尋問（アンケート投票中）',deduction:'推理タイム・資料公開',prediction:'犯人予想（チャンネルポイント）',reveal:'答えの開示',done:'おしまい'};
function el(id){return document.getElementById(id);}
function div(cls,text){const d=document.createElement('div');if(cls)d.className=cls;if(text!=null)d.textContent=text;return d;}
function allSearched(c){const rooms=c.rooms||[];return rooms.length>0&&rooms.every(r=>r.searched);}
function allAlibis(c){const s=c.suspects||[];return s.length>0&&s.every(x=>x.alibi);}
function traceRooms(c,name){const out=[];for(const r of (c.rooms||[])){if(r.clue&&r.clue.indexOf(name)>=0)out.push(r.name);}return out;}
function renderBoard(c,data){
 const rooms=el('rooms');rooms.replaceChildren();
 for(const room of (c.rooms||[])){
  const d=div('card'+(room.searched?'':' dim'));
  d.append(div('name',room.name));
  if(room.searched)d.append(div('clue',room.clue?room.clue:'手がかりなし'));
  rooms.append(d);
 }
 const suspects=el('suspects');suspects.replaceChildren();
 for(const s of (c.suspects||[])){
  const d=div('card'+(s.alibi?'':' dim'));
  const n=document.createElement('div');
  const nm=document.createElement('span');nm.className='name';nm.textContent=s.name;
  const ro=document.createElement('span');ro.className='role';ro.textContent=s.role;
  n.append(nm);n.append(ro);d.append(n);
  if(s.alibi)d.append(div('alibi',s.alibi));
  suspects.append(d);
 }
 const action=el('action');action.replaceChildren();
 if(c.intro&&c.intro.length){for(const t of c.intro){action.append(div('poll','・'+t));}}
 if(data.poll){
  const p=div('poll');
  p.append(div('q','🗳 '+data.poll.question));
  for(const ch of (data.poll.choices||[])){
   const line=div(null,ch.label+(ch.winner?' ✅':(ch.active?' ▸':''))+(ch.votes!=null?`（${ch.votes}票）`:''));
   if(ch.winner)line.style.color='#a8ffd0';
   p.append(line);
  }
  action.append(p);
 }
 if(data.prediction){
  const label={active:'投票受付中',locked:'投票締切',resolved:'精算済み',canceled:'不成立・返金',skipped:'予想なし'}[data.prediction.status]||data.prediction.status;
  action.append(div('predict',`💰 犯人予想：${label}`));
 }
}
function renderMatrix(c){
 const mr=el('mrooms');mr.replaceChildren();
 for(const room of (c.rooms||[])){
  const d=div('mrow');
  d.append(div('who',room.name));
  d.append(div('cl',room.clue?room.clue:'手がかりなし'));
  mr.append(d);
 }
 const ms=el('msusp');ms.replaceChildren();
 for(const s of (c.suspects||[])){
  const d=div('mrow');
  const n=document.createElement('div');
  const nm=document.createElement('span');nm.className='who';nm.textContent=s.name;
  const ro=document.createElement('span');ro.className='role';ro.textContent=s.role;
  n.append(nm);n.append(ro);d.append(n);
  d.append(div('line say','証言：'+(s.alibi||'未聴取')));
  const traces=traceRooms(c,s.name);
  d.append(div('line trace','時刻つきの手がかり：'+(traces.length?traces.join('・'):'なし')));
  ms.append(d);
 }
}
function renderReveal(c,data){
 const r=c.reveal,v=el('rv-verdict');v.replaceChildren();
 v.append(div('who-big','犯人は '+(r.culprit?r.culprit.name:'')+'（'+(r.culprit?r.culprit.role:'')+'）'));
 const kv=document.createElement('div');kv.className='kv';
 const mk=(k,val)=>{const d=document.createElement('div');const b=document.createElement('b');b.textContent=k;d.append(b);d.append(document.createTextNode(val));return d;};
 kv.append(mk('手法：',r.method||''));
 kv.append(mk('動機：',r.motive||''));
 v.append(kv);
 const st=el('rv-story');st.replaceChildren();
 for(const line of (r.timeline||[]))st.append(div('li',line));
 st.append(div('kv','事件を起こした手口：'+(r.method_how||'')));
 st.append(div('kv','現場に残った証拠：'+(r.method_evidence||'')));
 const mo=el('rv-motive');mo.replaceChildren();
 mo.append(div('kv',(r.motive_reason||'')+'。'));
 mo.append(div('kv','証拠：'+(r.motive_room||'')+'の「'+(r.motive_evidence||'')+'」（'+(r.culprit?r.culprit.name:'')+'の名あり）'));
 const rs=el('rv-reason');rs.replaceChildren();
 rs.append(div('kv','決定打：'+(r.contradiction||'')));
 for(const line of (r.exclusions||[]))rs.append(div('li',line));
 rs.append(div('commit',`commit ${String(r.commitment||'').slice(0,16)}… seed ${r.seed}`));
 const step=data.reveal_step||99;
 for(const blk of document.querySelectorAll('#revealboard .blk')){
  const need=parseInt(blk.getAttribute('data-step'),10)||0;
  blk.className='blk'+(need>step?' pending':'');
 }
}
function render(data){
 el('phase-tag').textContent=PHASE_LABELS[data.phase]||data.phase;
 el('state').textContent='事件ボードを表示中';
 const c=data.case||{};
 el('case-line').replaceChildren();
 const line=document.createElement('div');
 const b=document.createElement('b');b.textContent=c.headline||'';
 line.append(b);line.append(document.createTextNode(`（${c.time||''}）`));
 el('case-line').append(line);
 const revealing=!!c.reveal;
 const matrix=!revealing&&allSearched(c)&&allAlibis(c);
 el('board').hidden=revealing||matrix;
 el('matrix').hidden=!matrix;
 el('revealboard').hidden=!revealing;
 if(revealing)renderReveal(c,data);
 else if(matrix)renderMatrix(c);
 else renderBoard(c,data);
 el('unavailable').hidden=true;
}
async function poll(){
 try{
  const response=await fetch('/api/mystery',{cache:'no-store',signal:AbortSignal.timeout(2500)});
  if(!response.ok)throw new Error('unavailable');
  const data=await response.json();
  if(data.ok!==true||!data.case)throw new Error('invalid');
  render(data);
 }catch(_){el('unavailable').hidden=false;}
}
poll();setInterval(poll,2000);
</script></html>'''


def handler_for(path, runtime_id: str = "preview", *, generation=None, lease_id=None,
                reader=read_payload):
    path = Path(path)
    if not isinstance(runtime_id, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", runtime_id) is None:
        raise MysteryViewError("invalid runtime id")
    if generation is not None and (type(generation) is not int or generation < 1):
        raise MysteryViewError("invalid generation")
    if lease_id is not None:
        try:
            if not isinstance(lease_id, str) or str(uuid.UUID(lease_id)) != lease_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise MysteryViewError("invalid lease id") from None

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
            if self.path != READY_PATH:
                self.reply(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                data = reader(path)
            except OSError:
                data = None
            # A missing payload is the view server's *normal* state right after
            # the switch: the corner writes the first board only after the
            # GameSwitch readiness gate passes. Reporting it as 503/ok:false
            # would fail every first start (the production 2026-10-18 fire:
            # mystery state interrupted / switch-terminal-before-corner-active
            # with started_at=null). The readiness contract is therefore
            # "server answers with the bound identity", and the board itself
            # shows its preparing screen until the corner publishes the case.
            payload = dict(data) if isinstance(data, dict) else \
                {"case": None, "reason": "case-unavailable"}
            payload.update(ok=True, runtime_id=runtime_id, generation=generation,
                           lease_id=lease_id)
            self.reply(200, json.dumps(payload, ensure_ascii=False,
                                       allow_nan=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    return Handler


def serve(path, *, port: int = DEFAULT_PORT, runtime_id: str = "preview",
          generation=None, lease_id=None) -> None:
    if type(port) is not int or not 1024 <= port <= 65535:
        raise MysteryViewError("invalid port")
    # No --host switch: the listener can never bind publicly by configuration.
    with ThreadingHTTPServer(
        ("127.0.0.1", port),
        handler_for(path, runtime_id, generation=generation, lease_id=lease_id),
    ) as server:
        server.serve_forever(poll_interval=0.2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AI探偵ミステリー事件ボード（読み取り専用）")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runtime-id", default="preview")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--lease-id")
    args = parser.parse_args(argv)
    try:
        serve(payload_path(args.state_dir), port=args.port, runtime_id=args.runtime_id,
              generation=args.generation, lease_id=args.lease_id)
    except (OSError, MysteryViewError):
        print(json.dumps({"ok": False, "reason": "view-unavailable"}))
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
