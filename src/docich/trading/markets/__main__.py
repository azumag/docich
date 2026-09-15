"""Market worker, show runner and improvement entry points (all paper-only)."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import time
import tomllib

from .core import JST, Limits, PaperBook, TITLES, dt, report_window
from .feeds import FeedUnavailable, fx_week_open, jpx_open, read_news, read_quotes
from .lab import active_policy, advance_challenger, promotion_assessment, propose, write_json
from .selector import active_selector_policy, read_file_candidates, select_universe


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

    def _selector_state(self) -> dict:
        path = self.root / "selector-state.json"
        try:
            raw = json.loads(path.read_text())
            return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _stock_quote_config(self, *, now: float, begin: float) -> tuple[dict, dict]:
        selector_config = self.config.get("selector", {})
        if not isinstance(selector_config, dict) or not selector_config.get("enabled", False):
            return self.config, {"enabled": False, "ready": True, "status": "disabled",
                                 "symbols": self.config.get("symbols", [])}
        held = sorted(self.book.state().get("positions", {}).keys())
        previous = self._selector_state()
        try:
            if selector_config.get("feed", "file") != "file":
                raise FeedUnavailable("dynamic stock selector provider is not configured")
            policy = active_selector_policy(self.root)
            candidates = read_file_candidates(selector_config, self.data_root, now, policy)
            last_replaced = float(previous.get("last_replaced_at", 0) or 0)
            if not math.isfinite(last_replaced) or last_replaced < 0 or last_replaced > now:
                last_replaced = 0
            selection = select_universe(
                candidates, policy, now=now,
                current_symbols=previous.get("focused_symbols", []),
                held_symbols=held,
                last_replaced_at=last_replaced,
                session_start=begin,
            )
            selection.update(enabled=True, status="ok")
        except (FeedUnavailable, OSError, ValueError, KeyError, TypeError, ArithmeticError):
            # Scanner failure must never open new positions, but a held position
            # remains pinned so its exit quote can still be refreshed.
            selection = {
                "enabled": True, "ready": False, "status": "unavailable",
                "symbols": held, "focused_symbols": [], "held_symbols": held,
                "ranking": [], "changed": False,
                "last_replaced_at": previous.get("last_replaced_at", 0),
                "policy": previous.get("policy", "unavailable"), "as_of": now,
            }
        write_json(self.root / "selector-state.json", selection)
        quote_config = dict(self.config)
        quote_config["symbols"] = selection["symbols"]
        return quote_config, selection

    def tick(self, *, clock=time.time) -> dict:
        if not self.config.get("enabled", False):
            return {"status": "disabled", "mode": "paper"}
        with guard(self.root / "worker.lock"):
            now = clock()
            begin, end = report_window(self.market, now)
            policy = active_policy(self.root)
            open_market = (fx_week_open(now) if self.market == "fx" else
                           jpx_open(self.data_root / self.config.get("calendar_file", "jpx-calendar.json"), now))
            reason, quotes = "market_closed", []
            selector = {"enabled": False, "ready": True, "status": "disabled",
                        "symbols": self.config.get("symbols", [])}
            quote_config = self.config
            if open_market:
                try:
                    if self.market == "stocks":
                        quote_config, selector = self._stock_quote_config(now=now, begin=begin)
                    if quote_config.get("symbols"):
                        quotes = read_quotes(quote_config, self.market, self.data_root, now)
                        if self.market == "stocks" and selector.get("status") == "unavailable":
                            reason = "selector_unavailable"
                        else:
                            reason = "ok" if quotes else "no_tradeable_quotes"
                    else:
                        if self.market == "stocks" and selector.get("status") == "unavailable":
                            reason = "selector_unavailable"
                        else:
                            reason = "no_candidates" if selector.get("enabled") else "price_feed_unavailable"
                except (FeedUnavailable, OSError, ValueError, KeyError, TypeError):
                    reason = "price_feed_unavailable"
                    if self.market == "stocks":
                        selector["ready"] = False
            now = clock()
            begin, end = report_window(self.market, now)
            allow_entries = open_market
            force_flat = False
            entry_cutoff = end
            selector_policy_version = selector.get("policy", "disabled")
            if self.market == "stocks":
                try:
                    lease = json.loads(self.corner_path.read_text())
                except (OSError, ValueError):
                    lease = {}
                try:
                    selector_policy = active_selector_policy(self.root)
                    selector_policy_version = selector_policy.version
                    entry_cutoff = max(begin, end - policy.max_hold_s - selector_policy.exit_buffer_s)
                except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
                    # Invalid explicit selector policy is a hard no-entry state.
                    entry_cutoff = begin
                    selector["ready"] = False
                    selector_policy_version = "invalid"
                allow_entries = (open_market and selector.get("ready", True) and begin <= now < entry_cutoff
                                 and lease.get("status") == "active" and lease.get("ends_at") == end
                                 and 0 <= now - lease.get("heartbeat", 0) <= 20)
                force_flat = not allow_entries
            result = self.book.process(quotes, policy, now=now, allow_entries=allow_entries, force_flat=force_flat)
            try:
                advance_challenger(self.book, self.root, quotes, now=now, allow_entries=allow_entries, force_flat=force_flat)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                write_json(self.root / "experiment-status.json", {"status": "failed", "error": type(exc).__name__, "as_of": now})
            cutoff = end if self.market == "stocks" else begin
            if now >= cutoff and (self.market == "fx" or lease.get("ends_at") == cutoff):
                self.book.report(cutoff)
            result.update(status=reason, market=self.market, mode="paper", allow_entries=allow_entries)
            if self.market == "stocks":
                result["selector"] = {
                    "enabled": bool(selector.get("enabled")),
                    "ready": bool(selector.get("ready", True)),
                    "status": selector.get("status", "unknown"),
                    "focused_count": len(selector.get("focused_symbols", selector.get("symbols", []))),
                    "held_count": len(selector.get("held_symbols", [])),
                    "policy": selector_policy_version,
                    "entry_cutoff_at": entry_cutoff,
                }
            write_json(self.root / "health.json", result)
            return result

    def improve(self, *, now: float) -> dict:
        if not self.config.get("enabled", False):
            return {"status": "disabled"}
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
        for key, filename in (("health", "health.json"), ("news", "news.json"), ("experiment", "experiment-status.json"),
                              ("selector", "selector-state.json")):
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
