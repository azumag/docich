"""Market worker, show runner and improvement entry points (all paper-only)."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import time
import tomllib

from .core import JST, Limits, PaperBook, TITLES, dt, report_window
from .feeds import FeedUnavailable, fx_week_open, jpx_open, read_news, read_quotes
from .lab import active_policy, advance_challenger, promotion_assessment, propose, write_json


@contextmanager
def guard(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Runtime:
    def __init__(self, g, market: str, settings: Path):
        self.g, self.market = g, market
        self.settings_path = settings
        self.settings = tomllib.loads(settings.read_text())
        self.config = self.settings[market]
        if self.config.get("mode", "paper") != "paper":
            raise ValueError("only paper mode is available")
        if type(self.config.get("enabled", False)) is not bool:
            raise ValueError("enabled must be a boolean")
        self.limits = Limits(**{**{"lot": 100 if market == "stocks" else 1000}, **self.config.get("limits", {})})
        self.root = g.state_dir / "market-paper" / market
        self.data_root = g.state_dir / "market-data"
        self.corner_path = g.state_dir / f"market-{market}-corner.json"
        self.book = PaperBook(self.root / "paper.sqlite3", market, self.limits)

    def tick(self, *, clock=time.time) -> dict:
        if not self.config.get("enabled", False):
            return {"status": "disabled", "mode": "paper"}
        with guard(self.root / "worker.lock"):
            now = clock()
            open_market = (fx_week_open(now) if self.market == "fx" else
                           jpx_open(self.data_root / self.config.get("calendar_file", "jpx-calendar.json"), now))
            reason, quotes = "market_closed", []
            if open_market:
                try:
                    quotes = read_quotes(self.config, self.market, self.data_root, now)
                    reason = "ok" if quotes else "no_tradeable_quotes"
                except (FeedUnavailable, OSError, ValueError, KeyError, TypeError):
                    reason = "price_feed_unavailable"
            now = clock()  # assess freshness and the session AFTER network I/O
            begin, end = report_window(self.market, now)
            allow_entries = open_market
            force_flat = False
            if self.market == "stocks":
                try:
                    lease = json.loads(self.corner_path.read_text())
                except (OSError, ValueError):
                    lease = {}
                allow_entries = (open_market and begin <= now < end - 15 and lease.get("status") == "active"
                                 and lease.get("ends_at") == end and 0 <= now - lease.get("heartbeat", 0) <= 20)
                # Exit management continues even if the display crashes or a
                # quote is absent at 10:00. No new position is allowed then.
                force_flat = not allow_entries
            policy = active_policy(self.root)
            result = self.book.process(quotes, policy, now=now, allow_entries=allow_entries, force_flat=force_flat)
            try:
                advance_challenger(self.book, self.root, quotes, now=now, allow_entries=allow_entries, force_flat=force_flat)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                # An experiment failure must not stop primary FX execution.
                write_json(self.root / "experiment-status.json", {"status": "failed", "error": type(exc).__name__, "as_of": now})
            cutoff = end if self.market == "stocks" else begin
            if now >= cutoff and (self.market == "fx" or lease.get("ends_at") == cutoff):
                self.book.report(cutoff)
            result.update(status=reason, market=self.market, mode="paper", allow_entries=allow_entries)
            write_json(self.root / "health.json", result)
            return result

    def improve(self, *, now: float) -> dict:
        if not self.config.get("enabled", False):
            return {"status": "disabled"}
        # A separate lock/process: a slow AI call never stalls the resident FX bot.
        with guard(self.root / "improve.lock"):
            news = read_news(self.settings.get("news", {}).get("rss_urls", []), now)
            write_json(self.root / "news.json", {"as_of": now, "items": news, "status": "ok" if news else "unavailable"})
            agents = self.settings.get("ai", {}).get("agents", "")
            if not agents:
                profile = tomllib.loads(self.g.config_path.read_text())
                agents = profile.get("paper_corner", {}).get("improve_agents", "")
            result = propose(self.book, self.root, self.g, agents=agents, news=news, now=now)
            write_json(self.root / "improvement-status.json", {**result, "as_of": now})
            return result

    def status(self) -> dict:
        now = time.time()
        result = self.book.snapshot(now=now)
        for key, filename in (("health", "health.json"), ("news", "news.json"), ("experiment", "experiment-status.json")):
            path = self.root / filename
            try:
                result[key] = json.loads(path.read_text())
            except (OSError, ValueError):
                result[key] = {}
        row = self.book.db.execute("SELECT body FROM reports ORDER BY id DESC LIMIT 1").fetchone()
        result["report"] = json.loads(row[0]) if row else None
        result["chart"] = [json.loads(r[0])["equity_jpy"] for r in reversed(list(self.book.db.execute("SELECT body FROM metrics ORDER BY ts DESC LIMIT 120")))]
        result["promotion"] = promotion_assessment(self.book, active_policy(self.root))
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="docich stocks/FX paper-only programs")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--markets-config", type=Path)
    parser.add_argument("--market", required=True, choices=tuple(TITLES))
    parser.add_argument("command", choices=("worker", "tick", "corner", "improve", "status", "dashboard"))
    args = parser.parse_args(argv)
    from ...config import load_global
    root = Path(__file__).resolve().parents[4]
    g = load_global(root, args.config)
    runtime = Runtime(g, args.market, args.markets_config or root / "config/market-paper.toml")
    try:
        if args.command == "worker":
            while True:
                try:
                    runtime.tick()
                except (OSError, ValueError, RuntimeError) as exc:
                    write_json(runtime.root / "health.json", {"status": "failed", "error": type(exc).__name__, "as_of": time.time(), "mode": "paper"})
                time.sleep(5)
        elif args.command == "tick":
            print(json.dumps(runtime.tick(), ensure_ascii=False))
        elif args.command == "improve":
            print(json.dumps(runtime.improve(now=time.time()), ensure_ascii=False))
        elif args.command == "corner":
            from .program import MarketCorner
            print(MarketCorner(runtime).tick())
        elif args.command == "dashboard":
            from .program import serve
            serve(runtime)
        else:
            print(json.dumps(runtime.status(), ensure_ascii=False))
    except BlockingIOError:
        print("already-running")
    finally:
        runtime.book.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
