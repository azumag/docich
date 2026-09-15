"""Shared coordinator/output integration, fixed JST windows, read-only dashboard."""
from __future__ import annotations

import json
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .core import TITLES, report_window
from .lab import write_json
from .__main__ import guard

VIEW_MARKETS = {"stock-paper-view": "stocks", "fx-paper-view": "fx"}
PORTS = {"stocks": 8801, "fx": 8802}


def make_market_view_adapter(g, spec, *, settings_path=None):
    from ...adapters.program import ProgramViewAdapter, paper_view_game_config
    market = VIEW_MARKETS[spec.game]

    class MarketViewAdapter(ProgramViewAdapter):
        def __init__(self):
            game = replace(paper_view_game_config(), name=spec.game, title=TITLES[market])
            super().__init__(g, game, spec)
            self.dashboard_kind = "html"
            self.dashboard_port = PORTS[market]

        def _game_command(self):
            return [str(g.repo_root / "bin/docich-market-paper"), "--config", str(g.config_path),
                    "--markets-config", str(settings_path or g.repo_root / "config/market-paper.toml"),
                    "--market", market, "dashboard"]

    return MarketViewAdapter()


def result_text(report: dict) -> str:
    total = report.get("period_pnl_jpy")
    pnl = f'{float(total):+,.0f}円' if total is not None else "集計待ち"
    incomplete = "集計対象のデータが不足しているため暫定値です。" if not report.get("complete") else ""
    remaining = len((report.get("last") or {}).get("positions", {}))
    tail = (f"未決済は{remaining}件です。" if remaining else "")
    return (f'{report["title"]}の結果です。期間損益は{pnl}、決済は{report["closed_trades"]}回、'
            f'決済取引の費用控除後損益は{float(report["closed_net_pnl_jpy"]):+,.0f}円です。{tail}{incomplete}')


class MarketCorner:
    def __init__(self, runtime, *, clock=time.time, sleep=time.sleep, coordinator=None, overlay=None, speech=None):
        self.r, self.clock, self.sleep = runtime, clock, sleep
        self.g, self.path = runtime.g, runtime.corner_path
        self.view = next(k for k, v in VIEW_MARKETS.items() if v == runtime.market)
        from ...game_switch import GameSwitchCoordinator, GameSwitchStore
        from ...adapters import make_coordinator_adapter
        from ..soren_output import send_overlay, enqueue_speech
        self.store = GameSwitchStore(self.g.state_dir)
        def factory(spec):
            if spec.game in VIEW_MARKETS:
                return make_market_view_adapter(self.g, spec, settings_path=runtime.settings_path)
            if spec.game == "paper-view":
                # Crypto views belong to an explicit corner factory, not the game catalog.
                from ...adapters.program import make_program_view_adapter
                return make_program_view_adapter(self.g, spec)
            return make_coordinator_adapter(self.g, spec)
        self.coordinator = coordinator or GameSwitchCoordinator(self.store, factory)
        self.overlay, self.speech = overlay or send_overlay, speech or enqueue_speech

    def current(self):
        state, _ = self.store.canonical.load()
        return (state.get("active") or {}).get("game") if state.get("phase") == "ready" else None

    def save(self, state):
        write_json(self.path, state)

    def announce(self, state, key, text):
        delivery = state.setdefault("deliveries", {}).setdefault(key, {"text": text, "overlay": False, "speech": False})
        event = f'market-{self.r.market}:{int(state["ends_at"])}:{key}'
        self.save(state)
        if not delivery["overlay"]:
            self.overlay(self.g, {"ts": int(self.clock()), "category": "system", "level": "info",
                                  "title": TITLES[self.r.market], "body": delivery["text"], "source_id": event})
            delivery["overlay"] = True
            self.save(state)
        if not delivery["speech"]:
            self.speech(self.g, delivery["text"], event_id=event)
            delivery["speech"] = True
            self.save(state)

    @staticmethod
    def require(result):
        if result.status != "succeeded":
            raise RuntimeError("market program switch not completed")

    def restore(self, state):
        state.update(status="restoring", heartbeat=0)
        self.save(state)
        current, previous = self.current(), state.get("previous_game")
        if current == self.view or current is None:
            if previous and previous != self.view:
                self.require(self.coordinator.switch(previous))
            else:
                self.require(self.coordinator.stop())
        # If the operator chose another game, never pull it back.
        state.update(status="completed", completed_at=self.clock())
        self.save(state)
        return "completed"

    def tick(self):
        from ...corner_boundary import CornerWaitExpired, program_slot
        with guard(self.g.state_dir / "locks" / f"market-{self.r.market}-corner.lock"):
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            now = self.clock()
            begin, end = report_window(self.r.market, now)
            recovery = state.get("status") in ("starting", "active", "failed", "restoring")
            if not recovery:
                if not self.r.config.get("enabled", False):
                    return "disabled"
                if not begin <= now < end or state.get("ends_at") == end:
                    return "not_due"
                if self.r.market == "stocks":
                    from .feeds import jpx_open
                    if not jpx_open(self.r.data_root / self.r.config.get("calendar_file", "jpx-calendar.json"), now):
                        return "market_closed_or_calendar_missing"
                state = {"status": "waiting", "requested_at": now, "ends_at": end, "started_at": None}
                self.save(state)
            try:
                with program_slot(self.g, self.path, requested_at=state.get("requested_at", now),
                                  wait_deadline_ts=max(state["ends_at"], now + 60) if recovery else end,
                                  wait_boundary=not recovery, sleep=self.sleep, now=self.clock):
                    if recovery and (self.clock() >= state["ends_at"] or state["status"] in ("restoring", "failed")):
                        if self.clock() >= state["ends_at"] and state.get("started_at"):
                            cutoff = state["ends_at"] if self.r.market == "stocks" else state["ends_at"] - 1800
                            self.announce(state, "result", result_text(self.r.book.report(cutoff)))
                        return self.restore(state)
                    if self.clock() >= state["ends_at"]:
                        state["status"] = "expired"
                        self.save(state)
                        return "expired"
                    if state["status"] != "active":
                        state.update(status="starting")
                        if "previous_game" not in state:
                            state["previous_game"] = self.current()
                        self.save(state)
                        result = self.coordinator.switch(self.view) if state["previous_game"] else self.coordinator.start(self.view)
                        self.require(result)
                        if self.current() != self.view:
                            raise RuntimeError("market view is not canonical")
                        state.update(status="active", started_at=self.clock(), heartbeat=self.clock())
                        self.save(state)
                        self.announce(state, "opening", TITLES[self.r.market] + "です。実資金を使わず、相場とBOTの判断、損益を見ていきます。")
                    frozen = self.r.book.report(begin) if self.r.market == "fx" else None
                    while self.clock() < state["ends_at"]:
                        if self.current() != self.view:
                            return self.restore(state)
                        state["heartbeat"] = self.clock()
                        self.save(state)
                        slot = int((self.clock() - state["started_at"]) // 120)
                        snapshot = self.r.book.snapshot(now=self.clock())
                        if frozen:
                            text = result_text(frozen)
                        elif snapshot.get("stale"):
                            text = "価格データの更新を確認できないため、新しい売買は見送っています。"
                        else:
                            text = (f'確定分は{float(snapshot.get("realized_jpy", 0)):+,.0f}円、'
                                    f'含み損益は{float(snapshot.get("unrealized_jpy", 0)):+,.0f}円です。')
                        self.announce(state, f"slot-{slot}", text)
                        for fill in snapshot.get("recent_fills", []):
                            if fill["kind"] == "close" and fill["ts"] >= state["started_at"]:
                                self.announce(state, f'close-{fill["ts"]}-{fill["symbol"]}',
                                              f'{fill["symbol"]}を決済しました。費用込み損益は{float(fill["net_pnl_jpy"]):+,.0f}円です。')
                        self.sleep(min(5, max(0, state["ends_at"] - self.clock())))
                    report = frozen or self.r.book.report(state["ends_at"])
                    self.announce(state, "result", result_text(report))
                    return self.restore(state)
            except CornerWaitExpired:
                state.update(status="expired", heartbeat=0)
                self.save(state)
                return "expired"
            except BaseException:
                state.update(status="failed", heartbeat=0)
                self.save(state)
                raise


HTML = '''<!doctype html><html lang="ja"><meta charset="utf-8"><title>docich market</title>
<style>body{margin:18px;background:#102132;color:#edf2f7;font:20px sans-serif}h1{font-size:30px;margin:8px 0}
small{color:#9fb4c8}#metrics{white-space:pre-wrap;font-size:23px}canvas{width:900px;height:140px}#fills{font-size:17px;white-space:pre-wrap}</style>
<h1 id="title">取引データ確認中</h1><small>実資金を使わないシミュレーション / 費用・スリッページ込み</small>
<p id="metrics"></p><canvas id="chart" width="900" height="140"></canvas><div id="fills"></div>
<script>
async function refresh(){try{
 const r=await fetch('/api/trading/dashboard',{cache:'no-store'});if(!r.ok)throw Error();const d=await r.json();
 document.getElementById('title').textContent=d.title;
 const fmt=v=>v==null?'確認待ち':Number(v).toLocaleString('ja-JP',{maximumFractionDigits:0})+'円';
 const report=d.report;
 document.getElementById('metrics').textContent=(d.stale?'価格更新を確認できません。売買停止中。\\n':'')+
 '確定分 '+fmt(d.realized_jpy)+' / 含み '+fmt(d.unrealized_jpy)+' / 保有 '+Object.keys(d.positions||{}).length+'件'+
 (report?'\\n直近発表の期間損益 '+fmt(report.period_pnl_jpy)+(report.complete?'':'（暫定）'):'');
 document.getElementById('fills').textContent=(d.recent_fills||[]).slice(0,5).map(f=>f.symbol+' '+(f.kind==='close'?'決済 '+fmt(f.net_pnl_jpy):'新規')+' / '+f.qty).join('\\n');
 const c=document.getElementById('chart').getContext('2d'),xs=(d.chart||[]).map(Number);c.clearRect(0,0,900,140);
 if(xs.length>1){const lo=Math.min(...xs),hi=Math.max(...xs);c.strokeStyle='#61d4b3';c.lineWidth=2;c.beginPath();xs.forEach((v,i)=>{const x=i*900/(xs.length-1),y=125-(v-lo)/(hi-lo||1)*110;i?c.lineTo(x,y):c.moveTo(x,y)});c.stroke();}
}catch(e){document.getElementById('metrics').textContent='データを確認できません。';}}
refresh();setInterval(refresh,3000);
</script></html>'''


def serve(runtime):
    # HTTPServer is single-threaded: its SQLite connection stays on this thread.
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body, kind = HTML.encode(), "text/html; charset=utf-8"
            elif self.path == "/api/trading/dashboard":
                body, kind = json.dumps(runtime.status(), ensure_ascii=False).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_error(405)

        def log_message(self, *args):
            pass
    HTTPServer(("127.0.0.1", PORTS[runtime.market]), Handler).serve_forever()
