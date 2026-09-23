"""End-of-corner STRUCTURAL improvement for nInvaders: the LLM rewrites the policy code.

One job per corner end:

1. evaluate the incumbent policy on the real game (``arena``) -> stats + death context;
2. ask the LLM for a complete replacement policy (Python source), given measured
   game facts, the incumbent source, its evaluation and the recent attempt history;
3. static gate + sandbox smoke test of the candidate (never imported in-process);
4. evaluate the candidate with the same arena settings;
5. promotion gate: enough completed matches, no policy faults, a real margin AND a
   one-sided permutation test — otherwise the incumbent stays (fail closed).

Promotion writes an immutable, hash-named version into the ``PolicyStore`` and
flips ``current.json``; the live player picks it up at the next match start.
Numeric weights are deliberately not the unit of improvement: the LLM may change
targeting, firing, movement planning and state tracking.
"""
from __future__ import annotations

import itertools
import math
import random
import re
import statistics
import tempfile
from pathlib import Path

from . import arena as _arena
from .sandbox import PolicyProcess, PolicyRejected, policy_sha, validate_policy_source
from .store import PolicyStore

MAX_LLM_SOURCE_BYTES = 24_000
CORNER_EVAL_MATCHES = 6
CORNER_EVAL_PARALLEL = 3
CORNER_EVAL_MAX_SECONDS = 240.0

GAME_FACTS = """\
GAME FACTS (measured on the real nInvaders in an 80x24 tmux pane; you decide 10 times per second):
- The cannon '/-^-\\' is on row 22 (obs["player"] = [x, y], x = the '^' column, usable range about 2..77).
  Each Left/Right key moves it about 2 columns. Only ONE of your missiles can be in flight; Space while one
  exists does nothing. Your missile is '!' (obs["missiles"]) and rises ~25 rows/s. It never hurts you.
- Alien bombs are ':' (obs["bombs"]) and fall ~8 rows/s (0.8 rows per decision). They are the only thing that
  kills you; from the alien block (rows ~2-10) to the cannon row takes ~1.5-2.5 s.
- Barriers '#' (obs["barriers"], rows 16-19, 4 groups of ~7 columns). Bombs and your missiles erode them.
  A missile fired from directly under a barrier column is wasted (it dies within ~0.1 s) and erodes your cover.
- Aliens (obs["aliens"] = centres, 3 chars wide) start as 5 rows x 10 columns, march sideways and step DOWN 2
  rows at each edge. The match ends (block-letter GAME OVER, then the title screen) when they reach the cannon
  row ("invasion") or you run out of lives. Points from the title legend: bottom-row alien 100, middle 150,
  top 200, UFO ('< oo>' in obs["ufo"], top rows) 500.
- The trusted parser gives you: kind, tick, score, level, lives, player, player_hit (cannon exploding),
  bombs, missiles, aliens, barriers, ufo, text (the raw 80x24 pane). Coordinates are [x, y] with y = row from the top.
- Measured baseline behaviour: it almost never dies to bombs (dodging works) but NEVER clears level 1: every
  match ends by invasion after ~90 s at ~6000 points. The weakness is killing speed / target choice, not survival.
"""

CONTRACT = """\
CONTRACT (violations are rejected before anything runs):
- The file must define `def decide(obs, state):` returning a list of keys chosen from "Left", "Right", "Space"
  (at most 3; Left+Right cancel out). `state` is a dict that persists for ONE match only - use it for tracking
  (previous positions, alien speed, direction, counters, ...).
- Only the ``math`` module may be imported. No file/network/OS access, no eval/exec/open/getattr/setattr/globals,
  no attribute names starting with double underscore. print() is a no-op.
- Each call must finish in well under 50 ms. Keep the file under ~20 KB.
"""


class ImproveError(RuntimeError):
    pass


# --------------------------------------------------------------------- statistics

def permutation_p_value(a: list[float], b: list[float], *, iterations: int = 20000, seed: int = 1) -> float:
    """One-sided P(mean(b) - mean(a) >= observed) under random relabeling (exact when small)."""
    if not a or not b:
        return 1.0
    pool = [float(x) for x in a] + [float(x) for x in b]
    n_b, total = len(b), len(pool)
    observed = sum(pool[len(a):]) / n_b - sum(pool[:len(a)]) / len(a)
    grand = sum(pool)
    if math.comb(total, n_b) <= 100_000:
        hits = combos = 0
        for idx in itertools.combinations(range(total), n_b):
            combos += 1
            sb = sum(pool[i] for i in idx)
            diff = sb / n_b - (grand - sb) / (total - n_b)
            if diff >= observed - 1e-9:
                hits += 1
        return hits / combos
    rng = random.Random(seed)
    hits = 0
    for _ in range(iterations):
        rng.shuffle(pool)
        sb = sum(pool[:n_b])
        if sb / n_b - (grand - sb) / (total - n_b) >= observed - 1e-9:
            hits += 1
    return (hits + 1) / (iterations + 1)


def promotion_decision(incumbent: dict, candidate: dict, *, matches: int, margin_pct: float = 10.0,
                       alpha: float = 0.10, max_fault_rate: float = 0.02) -> dict:
    """Both args are ``arena.evaluate(...)["summary"]`` dicts.  Fails closed."""
    need = max(4, (matches * 3) // 4)
    inc_mean, cand_mean = float(incumbent["mean"]), float(candidate["mean"])
    out = {"promote": False, "incumbent_mean": round(inc_mean, 1), "candidate_mean": round(cand_mean, 1),
           "diff": round(cand_mean - inc_mean, 1), "p_value": None, "reasons": []}
    if incumbent["played"] < need or candidate["played"] < need:
        out["reasons"].append(f"completed matches too few (need {need}: incumbent {incumbent['played']}, candidate {candidate['played']})")
    if candidate.get("policy_fault_rate", 0.0) > max_fault_rate:
        out["reasons"].append(f"candidate policy faults {candidate['policy_fault_rate']:.1%} > {max_fault_rate:.0%}")
    if out["reasons"]:
        return out
    threshold = margin_pct / 100.0 * max(inc_mean, 1.0)
    if cand_mean - inc_mean < threshold:
        out["reasons"].append(f"improvement {cand_mean - inc_mean:.0f} < margin {threshold:.0f}")
    p = permutation_p_value(incumbent["scores"], candidate["scores"])
    out["p_value"] = round(p, 4)
    if p > alpha:
        out["reasons"].append(f"not significant (p={p:.3f} > {alpha})")
    out["promote"] = not out["reasons"]
    return out


# --------------------------------------------------------------------- prompt / extraction

def _trim_frame(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines())


def build_prompt(*, incumbent_source: str, incumbent_summary: dict, incumbent_matches: list[dict],
                 attempts: list[dict], live_stats: dict | None) -> str:
    causes: dict[str, int] = {}
    for m in incumbent_matches:
        causes[str(m.get("cause"))] = causes.get(str(m.get("cause")), 0) + 1
    worst = min((m for m in incumbent_matches if m.get("last_frames")),
                key=lambda m: m.get("score") or 0, default=None)
    history = "\n".join(
        f"- {a.get('event')}: {a.get('change') or a.get('reason') or ''} "
        f"(incumbent {a.get('incumbent_mean')} -> candidate {a.get('candidate_mean')}, p={a.get('p_value')})"
        for a in attempts if a.get("event") in ("promoted", "kept", "rejected")
    ) or "- (no earlier attempts)"
    live = ""
    if live_stats and live_stats.get("n"):
        live = f"\nToday's live corner: {live_stats['n']} matches, mean {live_stats['mean']:.0f}, best {live_stats['best']}.\n"
    frame_block = ""
    if worst is not None:
        frame_block = (f"\nLast live frame of the WORST evaluated match (score {worst.get('score')}, "
                       f"ended by {worst.get('cause')}):\n```\n{_trim_frame(worst['last_frames'][-1])}\n```\n")
    s = incumbent_summary
    return f"""You improve the strategy of an automatic nInvaders (Space Invaders) player by REWRITING ITS CODE.
Do not just tune constants: change the structure (target selection, when to fire, movement planning,
lead/prediction with `state`, use of barriers) wherever the evidence says the current logic is weak.

{GAME_FACTS}
{CONTRACT}
Current policy evaluation on the real game ({s['played']} completed matches, all measured the same way):
scores {s['scores']}  mean {s['mean']:.0f}  median {s['median']:.0f}  stdev {s['stdev']:.0f}
match endings: {causes}
{live}{frame_block}
Earlier attempts (newest last):
{history}

CURRENT POLICY SOURCE:
```python
{incumbent_source}
```

Write a complete replacement file. Requirements:
1. The first line must be `# CHANGE: <one sentence describing the structural change>`.
2. Output exactly one ```python fenced block containing the whole file, and nothing else of substance.
3. Prefer one or two well-reasoned structural changes over many random ones. A candidate is only adopted if it
   beats the current policy by a clear margin over repeated real matches; otherwise it is discarded.
"""


_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL)


def extract_policy(text: str) -> tuple[str, str]:
    """(source, change note) from the LLM output; raises ImproveError."""
    blocks = _FENCE.findall(text or "")
    source = max(blocks, key=len) if blocks else (text or "")
    source = source.strip("\n")
    if not source.strip() or "def decide" not in source:
        raise ImproveError("LLM出力に decide() を含むPythonコードがありません")
    if len(source.encode("utf-8")) > MAX_LLM_SOURCE_BYTES:
        raise ImproveError("LLM出力のコードが大きすぎます")
    m = re.search(r"^#\s*CHANGE:\s*(.+)$", source, re.MULTILINE)
    return source + "\n", (m.group(1).strip()[:200] if m else "(no CHANGE note)")


def _smoke_observations() -> list[dict]:
    def obs(**kw):
        base = {"tick": 0, "kind": "play", "cols": 80, "rows": 24, "player": [40, 22], "player_hit": False,
                "bombs": [], "missiles": [], "aliens": [[10 + 3 * i, 2 + 2 * r] for r in range(5) for i in range(10)],
                "barriers": [[8, 16], [9, 16], [10, 16]], "ufo": None, "score": 0, "level": 1, "lives": 3, "text": ""}
        base.update(kw)
        return base
    return [obs(), obs(bombs=[[40, 15], [43, 10]], tick=1), obs(missiles=[[40, 12]], tick=2),
            obs(player=None, player_hit=True, tick=3), obs(aliens=[], ufo=[30, 0], tick=4)]


def smoke_test(path) -> None:
    """Run the candidate on synthetic frames in the sandbox; raise ImproveError on any fault."""
    with PolicyProcess(path, tick_timeout_s=1.0) as proc:
        for o in _smoke_observations():
            keys = proc.tick(o)
            if proc.errors or proc.timeouts or proc.dead:
                raise ImproveError(f"スモークテスト失敗: {proc.last_error}")
            if not isinstance(keys, list):
                raise ImproveError("decide() がリストを返しません")


# --------------------------------------------------------------------- the job

def improve_once(store: PolicyStore, *, llm=None, arena_eval=None, matches: int = 6, parallel: int = 3,
                 margin_pct: float = 10.0, alpha: float = 0.10, max_seconds: float = 240.0,
                 live_stats: dict | None = None, dry_run: bool = False) -> dict:
    arena_eval = arena_eval or _arena.evaluate
    incumbent = store.current()
    inc_source = store.source_of(incumbent["sha"])
    if dry_run:
        prompt = build_prompt(
            incumbent_source=inc_source,
            incumbent_summary={"played": 0, "scores": [], "mean": 0.0, "median": 0.0, "stdev": 0.0},
            incumbent_matches=[], attempts=store.recent_attempts(5), live_stats=live_stats)
        return {"status": "dry-run", "incumbent": incumbent["sha"], "prompt_chars": len(prompt)}
    if llm is None:
        raise ImproveError("llm が必要です")

    inc_eval = arena_eval(incumbent["path"], matches, parallel=parallel, max_seconds=max_seconds)
    inc_sum = inc_eval["summary"]
    need = max(4, (matches * 3) // 4)
    if inc_sum["played"] < need:
        store.log({"event": "skipped", "reason": "incumbent-eval-incomplete", "played": inc_sum["played"]})
        return {"status": "skipped", "reason": "incumbent-eval-incomplete", "incumbent": incumbent["sha"],
                "played": inc_sum["played"], "needed": need}

    prompt = build_prompt(incumbent_source=inc_source, incumbent_summary=inc_sum,
                          incumbent_matches=inc_eval["matches"], attempts=store.recent_attempts(5),
                          live_stats=live_stats)
    raw = llm(prompt)
    try:
        source, change = extract_policy(raw)
        validate_policy_source(source)
    except (ImproveError, PolicyRejected) as exc:
        store.log({"event": "rejected", "reason": f"static-gate: {exc}"[:200], "incumbent": incumbent["sha"]})
        return {"status": "rejected", "reason": f"static-gate: {exc}"[:200], "incumbent": incumbent["sha"]}
    sha = policy_sha(source)
    if sha == incumbent["sha"]:
        store.log({"event": "rejected", "reason": "identical-to-incumbent", "incumbent": incumbent["sha"]})
        return {"status": "rejected", "reason": "identical-to-incumbent", "incumbent": incumbent["sha"]}

    with tempfile.TemporaryDirectory(prefix="docich-ninvaders-cand-") as tmp:
        cand_path = Path(tmp) / "candidate.py"
        cand_path.write_text(source, encoding="utf-8")
        try:
            smoke_test(cand_path)
        except ImproveError as exc:
            store.log({"event": "rejected", "reason": str(exc)[:200], "change": change, "candidate": sha})
            return {"status": "rejected", "reason": str(exc)[:200], "candidate": sha, "change": change}
        cand_eval = arena_eval(cand_path, matches, parallel=parallel, max_seconds=max_seconds)

    decision = promotion_decision(inc_sum, cand_eval["summary"], matches=matches,
                                  margin_pct=margin_pct, alpha=alpha)
    summary = {"incumbent": incumbent["sha"], "candidate": sha, "change": change, "matches": matches,
               "incumbent_mean": decision["incumbent_mean"], "candidate_mean": decision["candidate_mean"],
               "p_value": decision["p_value"], "incumbent_scores": inc_sum["scores"],
               "candidate_scores": cand_eval["summary"]["scores"], "reasons": decision["reasons"]}
    if decision["promote"]:
        current = store.promote(source, {"origin": "llm", "change": change, "matches": matches,
                                         "incumbent_mean": decision["incumbent_mean"],
                                         "candidate_mean": decision["candidate_mean"],
                                         "p_value": decision["p_value"]})
        return {"status": "promoted", "current": current["sha"], **summary}
    store.log({"event": "kept", **{k: summary[k] for k in ("incumbent", "candidate", "change", "incumbent_mean",
                                                            "candidate_mean", "p_value")},
               "reason": "; ".join(decision["reasons"])[:200]})
    return {"status": "kept", **summary}
