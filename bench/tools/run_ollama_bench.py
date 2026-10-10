#!/usr/bin/env python3
"""RTX 3060 実機での本計測ランナー (#1263).

`bench/jev_bench.py`（PR #1933）の**計測ロジックをそのまま再利用**し、
輸送層だけを Ollama ネイティブ `/api/chat` に差し替える。理由は実測:

- Ollama の OpenAI 互換 `/v1/chat/completions` は **`num_ctx` を無視**する
  （`options.num_ctx` も top-level `num_ctx` も効かず、常にモデル既定長で
  ロードされる → モデル間で VRAM 条件が揃わない）。
- 同エンドポイントは **Qwen3 の `think:false` も無視**する（思考トークンで
  `max_tokens` を使い切り `content` が空になる）。

ネイティブ `/api/chat` は両方を受け付ける（実測: ctx=8192 が `/api/ps` の
`context_length` に反映、Qwen3 は `think:false` で JSON を返す）。よって
「全モデル同一プロンプト・同一生成パラメータ、`num_ctx` 明示、モデル差は
chat template と Qwen3 の思考モードのみ」という #1263 の契約はこの経路で満たす。

再利用する `jev_bench` の部品:
  load_suite / build_system_prompt / build_user_prompt / render_prompt /
  parse_answer / run_once / summarize / summary_row / write_csv /
  PER_CASE_FIELDS / SUMMARY_FIELDS / slug

追加するもの:
  OllamaBackend       ネイティブ /api/chat のストリーミング計測
  OllamaVramSampler   /api/ps の `size_vram` をポーリングしたピーク VRAM

使い方::

    python3 bench/tools/run_ollama_bench.py \\
        --base-url http://desktop-9j2it17:11434 \\
        --model qwen2.5:7b-instruct-q4_K_M --model qwen3:8b-q4_K_M \\
        --num-ctx 8192 --runs 3 --warmup 3 \\
        --out bench/runs --results-dir bench/results \\
        --stamp 2026-10-09_rtx3060_jev
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path, PureWindowsPath
import shlex
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

import csv

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "bench"))

import jev_bench as jb  # noqa: E402

NO_THINK_FAMILIES = ("qwen3",)


def http_json(url, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class OllamaBackend:
    """Streaming native ``/api/chat`` client (stdlib only)."""

    name = "ollama"

    def __init__(self, base_url, model, num_ctx, *, temperature=0.0, top_p=1.0,
                 max_tokens=64, seed=42, think=None, timeout=300.0, extra_options=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.seed = seed
        self.think = think  # None -> runtime default; False -> thinking off
        self.timeout = timeout
        self.extra_options = extra_options or {}

    def generate(self, payload):
        options = {"temperature": self.temperature, "top_p": self.top_p,
                   "num_predict": self.max_tokens, "seed": self.seed,
                   "num_ctx": self.num_ctx}
        options.update(self.extra_options)
        body = {"model": self.model,
                "messages": payload["messages"],
                "stream": True,
                "options": options}
        if self.think is not None:
            body["think"] = self.think
        request = urllib.request.Request(
            self.base_url + "/api/chat", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        started = time.monotonic()
        ttft = None
        chunks = []
        final = {}
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            for raw in response:
                raw = raw.strip()
                if not raw:
                    continue
                event = json.loads(raw.decode("utf-8", "ignore"))
                if event.get("error"):
                    raise RuntimeError(event["error"])
                piece = (event.get("message") or {}).get("content") or ""
                if piece and ttft is None:
                    ttft = time.monotonic() - started
                if piece:
                    chunks.append(piece)
                if event.get("done") is True:
                    final = event
                    break
        if not final:
            raise RuntimeError("Ollama stream ended without done:true")
        total = time.monotonic() - started
        text = "".join(chunks)
        eval_count = measured_token_count(final.get("eval_count"))
        eval_ns = final.get("eval_duration") or 0
        prompt_eval = measured_token_count(final.get("prompt_eval_count"))
        output_tokens = eval_count
        tokens_per_second = None
        if output_tokens is not None:
            duration = eval_ns / 1e9 if eval_ns > 0 else total
            tokens_per_second = output_tokens / duration if duration > 0 else None
        usage = {"input_tokens": prompt_eval, "output_tokens": output_tokens}
        if prompt_eval is not None and eval_count is not None:
            usage["total_tokens"] = prompt_eval + eval_count
        return text, {"ttft_ms": (ttft if ttft is not None else total) * 1000.0,
                      "total_ms": total * 1000.0,
                      "output_tokens": output_tokens,
                      "tokens_per_second": tokens_per_second,
                      "usage": usage,
                      "finish_reason": final.get("done_reason"),
                      "load_ms": (final.get("load_duration") or 0) / 1e6,
                      "prompt_eval_ms": (final.get("prompt_eval_duration") or 0) / 1e6}


def measured_token_count(value):
    """Unknown usage stays unknown; booleans and negative counts are invalid."""
    return value if type(value) is int and value >= 0 else None


def per_run_summary(reports, run_ids):
    return [{"run": run_id, "n": report.get("all", {}).get("n"),
             "accuracy": report.get("all", {}).get("accuracy"),
             "accuracy_live_log": report.get("live_log", {}).get("accuracy"),
             "macro_f1": report.get("all", {}).get("macro_f1"),
             "peak_vram_mib": report.get("peak_vram_mib"),
             "ttft_p50_ms": report["latency"]["ttft_ms"].get("p50"),
             "ttft_p95_ms": report["latency"]["ttft_ms"].get("p95"),
             "total_p50_ms": report["latency"]["total_ms"].get("p50"),
             "total_p95_ms": report["latency"]["total_ms"].get("p95"),
             "tokens_per_second_median": report["latency"]["tokens_per_second"]["median"]}
            for run_id, report in zip(run_ids, reports)]


def rebuild_entry(records, cases, previous, suite_dir):
    """Recompute a native result from saved records, with no model or API calls."""
    run_ids = sorted({row["run"] for row in records})
    # VRAM cannot be recovered from classification raw. Preserve each saved
    # run's observation by ID; unknown peaks must not inherit the pooled peak.
    saved_peaks = {run["run"]: run.get("peak_vram_mib")
                   for run in previous.get("per_run", [])}
    args = argparse.Namespace(**previous["generation"])
    common = dict(model=previous["model"], quantization=previous["quantization"],
                  backend=previous["backend"], args=args,
                  suite_dir=suite_dir,
                  run_dir=previous["run_dir"], started_at=previous["started_at"])
    per_run = [jb.summarize([row for row in records if row["run"] == run_id],
                            cases, runs=1, peak_vram=saved_peaks.get(run_id), **common)
               for run_id in run_ids]
    pooled = {**previous, **jb.summarize(records, cases, runs=len(run_ids),
                                       peak_vram=previous["peak_vram_mib"], **common),
              "per_run": per_run_summary(per_run, run_ids)}
    return {"model": pooled["model"], "quantization": pooled["quantization"],
            "num_ctx": pooled["num_ctx"], "think": pooled["think"],
            "pooled": pooled, "per_run": per_run,
            "peak_vram_mib": pooled["peak_vram_mib"],
            "vram_samples": pooled["vram_samples"]}


def public_suite_reference(suite_dir, suite_digest):
    """Use a repo-relative suite path or a content identity for external suites."""
    try:
        if PureWindowsPath(suite_dir).is_absolute():
            raise ValueError("external Windows path")
        return Path(suite_dir).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return "suite:" + (suite_digest or "external")


def public_report(report, *, suite_reference, run_reference):
    """Project location fields without mutating the local report."""
    return {**report, "suite": suite_reference, "run_dir": run_reference}


def public_metadata(metadata, *, suite_reference):
    """Keep measurements, and reconstruct a portable command from their settings."""
    generation = metadata["generation"]
    command = ["python3", "bench/tools/run_ollama_bench.py",
               "--base-url", "<ollama-base-url>", "--suite", suite_reference,
               "--num-ctx", str(metadata["num_ctx"]),
               "--runs", str(metadata["runs"]), "--warmup", str(metadata["warmup"])]
    for flag, key in (("--temperature", "temperature"), ("--top-p", "top_p"),
                      ("--max-tokens", "max_tokens"), ("--seed", "seed")):
        command.extend([flag, str(generation[key])])
    for model in metadata["models"]:
        command.extend(["--model", model])
    command.extend(["--out", "bench/runs", "--results-dir", "bench/results"])
    return {**metadata, "suite": suite_reference, "command": shlex.join(command)}


class OllamaVramSampler:
    """Peak VRAM (MiB) from ``/api/ps`` ``size_vram`` (Windows host: no nvidia-smi)."""

    def __init__(self, base_url, interval=0.2):
        self.base_url = base_url.rstrip("/")
        self.interval = interval
        self.peak_mib = None
        self.peak_sample = None
        self.enabled = True

    def _read(self):
        try:
            ps = http_json(self.base_url + "/api/ps", timeout=15)
        except Exception:  # noqa: BLE001
            return None
        best = None
        for model in ps.get("models", []):
            vram = model.get("size_vram")
            if isinstance(vram, int) and (best is None or vram > best):
                best = vram
        return best

    def __enter__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        while not self._stop.is_set():
            value = self._read()
            if value is not None:
                self.peak_mib = value / 1048576.0 if self.peak_mib is None else max(self.peak_mib, value / 1048576.0)
            self._stop.wait(self.interval)

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)

    def snapshot(self):
        try:
            ps = http_json(self.base_url + "/api/ps", timeout=15)
        except Exception:  # noqa: BLE001
            return None
        for model in ps.get("models", []):
            return {"name": model.get("name"), "size": model.get("size"),
                    "size_vram": model.get("size_vram"),
                    "context_length": model.get("context_length")}
        return None


def unload_all(base_url):
    loaded = []
    try:
        ps = http_json(base_url.rstrip("/") + "/api/ps", timeout=15)
        loaded = [m.get("model") or m.get("name") for m in ps.get("models", [])]
    except Exception:  # noqa: BLE001
        pass
    for name in loaded:
        for path in ("/api/generate", "/api/chat"):
            try:
                http_json(base_url.rstrip("/") + path, {"model": name, "keep_alive": 0}, timeout=30)
            except Exception:  # noqa: BLE001
                pass
    # wait until the GPU is idle (no model resident)
    for _ in range(30):
        try:
            ps = http_json(base_url.rstrip("/") + "/api/ps", timeout=15)
            if not ps.get("models"):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    return False


def quant_from_tag(tag, override=None):
    if override:
        return override
    suffix = tag.split(":")[-1]
    for known in ("q4_k_m", "q5_k_m", "q4_k_s", "q5_k_s", "q8_0", "q6_k", "q3_k_m", "fp16"):
        if suffix == known:
            return known.upper().replace("_K_", "_K_")
    return suffix


def vram_mib(sample):
    """``size_vram`` (bytes) of a /api/ps snapshot as MiB, or None."""
    if not sample:
        return None
    value = sample.get("size_vram")
    return value / 1048576.0 if isinstance(value, int) else None


def think_for(model, no_think_families):
    family = model.split("/")[-1].split(":")[0]
    return False if family in no_think_families else None


def run_model(args, model, cases):
    quant = quant_from_tag(model, args.quantization)
    no_think = tuple(f.strip() for f in args.think_off.split(",") if f.strip())
    think = think_for(model, no_think)

    if not unload_all(args.base_url):
        raise SystemExit("GPU is still busy (a model is resident) before starting %s" % model)

    backend = OllamaBackend(args.base_url, model, args.num_ctx,
                            temperature=args.temperature, top_p=args.top_p,
                            max_tokens=args.max_tokens, seed=args.seed,
                            think=think, timeout=args.request_timeout)
    vram = OllamaVramSampler(args.base_url, interval=args.vram_poll_seconds)
    system = jb.build_system_prompt()

    started_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.out) / f"{stamp}_{jb.slug(model)}" / (args.label or "default")
    run_dir.mkdir(parents=True, exist_ok=True)

    records = []
    per_run = []
    run_vram = []
    for run_index in range(1, args.runs + 1):
        records.extend(jb.run_once(backend, cases, system=system, prompt_format="chat",
                                   chat_template=None, vram=vram, run_index=run_index,
                                   warmup=args.warmup))
        snap = vram.snapshot()
        run_vram.append(snap)
        rows = [row for row in records if row["run"] == run_index]
        run_args = argparse.Namespace(temperature=args.temperature, top_p=args.top_p,
                                      max_tokens=args.max_tokens, seed=args.seed)
        report = jb.summarize(rows, cases, model=model, quantization=quant,
                              backend="ollama", args=run_args, peak_vram=vram.peak_mib,
                              suite_dir=Path(args.suite).resolve(), run_dir=run_dir.resolve(),
                              started_at=started_at, runs=1)
        per_run.append(report)
        all_block = report.get("all", {})
        print("[%s run %d/%d] acc=%.4f macro_f1=%.4f ttft_p50=%.0f tok/s=%.1f vram=%sMiB" % (
            model, run_index, args.runs, all_block.get("accuracy") or 0.0,
            all_block.get("macro_f1") or 0.0,
            report["latency"]["ttft_ms"].get("p50") or 0.0,
            report["latency"]["tokens_per_second"]["median"] or 0.0,
            vram.peak_mib), flush=True)

    run_args = argparse.Namespace(temperature=args.temperature, top_p=args.top_p,
                                  max_tokens=args.max_tokens, seed=args.seed)
    pooled = jb.summarize(records, cases, model=model, quantization=quant,
                          backend="ollama", args=run_args, peak_vram=vram.peak_mib,
                          suite_dir=Path(args.suite).resolve(), run_dir=run_dir.resolve(),
                          started_at=started_at, runs=args.runs)
    pooled["suite_digest"] = pooled.get("suite_digest")
    pooled["prompt"] = {"prompt_format": "chat", "chat_template": None,
                        "system": system, "output_contract": jb.OUTPUT_CONTRACT}
    pooled["transport"] = "ollama-native:/api/chat"
    pooled["transport_reason"] = ("Ollama /v1 ignores num_ctx and Qwen3 think:false; "
                                  "native /api/chat honours both (measured)")
    pooled["num_ctx"] = args.num_ctx
    pooled["think"] = think
    pooled["vram_samples"] = run_vram
    pooled["size_vram_mib"] = vram_mib(run_vram[-1] if run_vram else None)
    pooled["per_run"] = per_run_summary(per_run, range(1, args.runs + 1))

    (run_dir / "config.json").write_text(json.dumps(
        {**vars(args), "model": model, "quantization": quant, "num_ctx": args.num_ctx,
         "think": think, "started_at": started_at}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps(pooled, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    from docich.eval import corpus  # noqa: E402  (canonical jsonl writer)
    corpus.write_jsonl(run_dir / "raw.jsonl", records)
    corpus.write_jsonl(run_dir / "outputs.jsonl",
                       [{"case_id": row["case_id"], "category": row["choice"],
                         "parse_ok": row["parse_ok"], "usage": row["usage"],
                         "ttft_ms": row["ttft_ms"], "total_ms": row["total_ms"]}
                        for row in records if not row["warmup"]])
    jb.write_csv(run_dir / "per_case.csv", records, jb.PER_CASE_FIELDS)
    jb.write_csv(run_dir / "summary.csv", [jb.summary_row(r) for r in per_run], jb.SUMMARY_FIELDS)

    unload_all(args.base_url)
    return {"model": model, "quantization": quant, "num_ctx": args.num_ctx, "think": think,
            "run_dir": str(run_dir), "pooled": pooled, "per_run": per_run,
            "peak_vram_mib": vram.peak_mib, "vram_samples": run_vram}


AGG_FIELDS = ["model", "quantization", "num_ctx", "think", "runs", "cases_per_run", "pooled_n",
              "accuracy_median", "accuracy_min", "accuracy_max", "accuracy_std",
              "accuracy_live_log_median", "macro_f1_median", "coverage_all",
              "parse_failures_total", "errors_total",
              "ttft_p50_ms_median", "ttft_p50_ms_max", "ttft_p95_ms_median",
              "total_p50_ms_median", "total_p95_ms_median",
              "tokens_per_second_median", "peak_vram_mib", "size_vram_mib",
              "ctx_len", "started_at"]


def _stats(values):
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return None, None, None, None
    return (statistics.median(values), min(values), max(values),
            statistics.pstdev(values) if len(values) > 1 else 0.0)


def aggregate_row(entry):
    runs = entry["per_run"]
    pooled = entry["pooled"]
    acc_m, acc_min, acc_max, acc_std = _stats([r.get("all", {}).get("accuracy") for r in runs])
    live_m, _, _, _ = _stats([r.get("live_log", {}).get("accuracy") for r in runs])
    f1_m, _, _, _ = _stats([r.get("all", {}).get("macro_f1") for r in runs])
    ttft_m, _, ttft_max, _ = _stats([r["latency"]["ttft_ms"].get("p50") for r in runs])
    ttft95_m, _, _, _ = _stats([r["latency"]["ttft_ms"].get("p95") for r in runs])
    tp50_m, _, _, _ = _stats([r["latency"]["total_ms"].get("p50") for r in runs])
    tp95_m, _, _, _ = _stats([r["latency"]["total_ms"].get("p95") for r in runs])
    tps_m, _, _, _ = _stats([r["latency"]["tokens_per_second"]["median"] for r in runs])
    all_block = pooled.get("all", {})
    case_counts = {r.get("all", {}).get("n") for r in runs}
    if len(case_counts) != 1:
        raise ValueError("cases_per_run requires equal scored counts across runs")
    last_vram = entry["vram_samples"][-1] if entry["vram_samples"] else None
    return {
        "model": entry["model"], "quantization": entry["quantization"],
        "num_ctx": entry["num_ctx"], "think": entry["think"], "runs": len(runs),
        "cases_per_run": next(iter(case_counts)), "pooled_n": all_block.get("n"),
        "accuracy_median": acc_m, "accuracy_min": acc_min, "accuracy_max": acc_max,
        "accuracy_std": acc_std, "accuracy_live_log_median": live_m, "macro_f1_median": f1_m,
        "coverage_all": all_block.get("coverage"),
        "parse_failures_total": sum(r.get("all", {}).get("parse_failures") or 0 for r in runs),
        "errors_total": sum(r.get("all", {}).get("errors") or 0 for r in runs),
        "ttft_p50_ms_median": ttft_m, "ttft_p50_ms_max": ttft_max, "ttft_p95_ms_median": ttft95_m,
        "total_p50_ms_median": tp50_m, "total_p95_ms_median": tp95_m,
        "tokens_per_second_median": tps_m, "peak_vram_mib": entry["peak_vram_mib"],
        "size_vram_mib": vram_mib(last_vram),
        "ctx_len": (last_vram or {}).get("context_length"),
        "started_at": pooled.get("started_at"),
    }


def collect_metadata(base_url, args, models, suite_manifest):
    version = {}
    try:
        version = http_json(base_url.rstrip("/") + "/api/version", timeout=20)
    except Exception:  # noqa: BLE001
        pass
    tags = {}
    try:
        info = http_json(base_url.rstrip("/") + "/api/tags", timeout=30)
        tags = {m["name"]: m.get("size") for m in info.get("models", [])}
    except Exception:  # noqa: BLE001
        pass
    return {
        "measured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "host": "desktop-9j2it17 (Windows, Tailscale)",
        "gpu": "NVIDIA GeForce RTX 3060 12GB (Ampere)",
        "driver_version": None,
        "cuda_version": None,
        "driver_cuda_note": ("nvidia-smi は実機でしか実行できず、Windows 側 SSH:22 が閉じて"
                             "いるため取得不可（既知のブロッカー）。代替証拠は /api/ps の "
                             "size_vram == size（100% GPU オフロード）"),
        "runtime": "Ollama %s" % version.get("version", "unknown"),
        "ollama_version": version.get("version"),
        "api": "native /api/chat (streaming)",
        "transport_reason": pooled_reason(),
        "suite": str(Path(args.suite).resolve()),
        "suite_digest": suite_manifest.get("corpus_digest"),
        "suite_case_count": suite_manifest.get("case_count"),
        "generation": {"temperature": args.temperature, "top_p": args.top_p,
                       "max_tokens": args.max_tokens, "seed": args.seed},
        "num_ctx": args.num_ctx, "runs": args.runs, "warmup": args.warmup,
        "models": models, "model_disk_bytes": {m: tags.get(m) for m in models},
        "command": "python3 bench/tools/run_ollama_bench.py " + " ".join(
            ["--base-url", args.base_url, "--num-ctx", str(args.num_ctx),
             "--runs", str(args.runs), "--warmup", str(args.warmup),
             "--temperature", str(args.temperature), "--top-p", str(args.top_p),
             "--max-tokens", str(args.max_tokens), "--seed", str(args.seed)]
            + [item for m in models for item in ("--model", m)]
            + ["--out", str(args.out), "--results-dir", str(args.results_dir)]),
    }


def pooled_reason():
    return ("Ollama /v1 は num_ctx と Qwen3 think:false を無視するため、計測は native "
            "/api/chat で行う（同一プロンプト・同一生成パラメータは jev_bench 側で固定）")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", default=str(REPO_ROOT / "bench" / "jev_eval_v1"))
    parser.add_argument("--base-url", default="http://desktop-9j2it17:11434")
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--quantization", default=None, help="override; default derived from the tag")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--temperature", type=float, default=jb.GENERATION_DEFAULTS["temperature"])
    parser.add_argument("--top-p", type=float, default=jb.GENERATION_DEFAULTS["top_p"])
    parser.add_argument("--max-tokens", type=int, default=jb.GENERATION_DEFAULTS["max_tokens"])
    parser.add_argument("--seed", type=int, default=jb.GENERATION_DEFAULTS["seed"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--think-off", default=",".join(NO_THINK_FAMILIES),
                        help="comma list of model families that need think:false")
    parser.add_argument("--request-timeout", type=float, default=300.0)
    parser.add_argument("--vram-poll-seconds", type=float, default=0.2)
    parser.add_argument("--max-cases", type=int, default=0, help="truncate the suite (smoke only)")
    parser.add_argument("--out", default=str(REPO_ROOT / "bench" / "runs"))
    parser.add_argument("--results-dir", default=str(REPO_ROOT / "bench" / "results"))
    parser.add_argument("--label", default=None)
    parser.add_argument("--stamp", default=None, help="results subdirectory name")
    args = parser.parse_args(argv)

    models = args.model or [
        "qwen2.5:7b-instruct-q4_K_M", "qwen3:8b-q4_K_M", "llama3.1:8b-instruct-q4_K_M",
        "schroneko/llama-3.1-swallow-8b-instruct-v0.1:q4_k_m",
        "MHKetbi/sbintuitions-sarashina2.2-3b-instruct-v0.1:q4_K_S"]

    loaded = jb.load_suite(args.suite)
    cases = loaded["cases"]
    if args.max_cases and args.max_cases < len(cases):
        cases = cases[:args.max_cases]
    print("suite=%s cases=%d models=%d num_ctx=%d runs=%d warmup=%d" % (
        args.suite, len(cases), len(models), args.num_ctx, args.runs, args.warmup), flush=True)

    if not unload_all(args.base_url):
        raise SystemExit("GPU busy before the run started")

    entries = []
    for model in models:
        print("\n=== %s ===" % model, flush=True)
        try:
            entries.append(run_model(args, model, cases))
        except Exception as exc:  # noqa: BLE001
            print("FAILED %s: %s: %s" % (model, type(exc).__name__, exc), flush=True)
            entries.append({"model": model, "error": "%s: %s" % (type(exc).__name__, exc)})

    stamp = args.stamp or dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = Path(args.results_dir) / stamp
    results_dir.mkdir(parents=True, exist_ok=True)

    ok_entries = [e for e in entries if "pooled" in e]
    rows = [aggregate_row(e) for e in ok_entries]
    jb.write_csv(results_dir / "summary.csv", rows, AGG_FIELDS)

    suite_reference = public_suite_reference(args.suite, loaded["manifest"]["corpus_digest"])
    local_metadata = {**collect_metadata(args.base_url, args, models, loaded["manifest"]),
                      "failed_models": [{"model": e["model"], "error": e["error"]}
                                        for e in entries if "error" in e]}
    (results_dir / "metadata.json").write_text(json.dumps(
        public_metadata(local_metadata, suite_reference=suite_reference),
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    raw_dir = results_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    for entry in ok_entries:
        src = Path(entry["run_dir"]) / "raw.jsonl"
        if src.is_file():
            (raw_dir / (jb.slug(entry["model"]) + ".raw.jsonl")).write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8")
        (results_dir / (jb.slug(entry["model"]) + ".report.json")).write_text(
            json.dumps(public_report(
                entry["pooled"], suite_reference=suite_reference,
                run_reference=f"results:{jb.slug(results_dir.name)}/{jb.slug(entry['model'])}"),
                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n=== SUMMARY (%s) ===" % results_dir, flush=True)
    for row in rows:
        print("%-60s acc=%.4f (min %.4f max %.4f std %.4f) ttft_p50=%.0f tok/s=%.1f vram=%sMiB" % (
            row["model"], row["accuracy_median"] or 0.0, row["accuracy_min"] or 0.0,
            row["accuracy_max"] or 0.0, row["accuracy_std"] or 0.0,
            row["ttft_p50_ms_median"] or 0.0, row["tokens_per_second_median"] or 0.0,
            row["size_vram_mib"]), flush=True)
    print("summary_csv=%s" % (results_dir / "summary.csv"), flush=True)
    print("metadata=%s" % (results_dir / "metadata.json"), flush=True)
    print("raw_logs=%s" % raw_dir, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
