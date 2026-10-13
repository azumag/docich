#!/usr/bin/env python3
"""Ungated single-pass live-Jev baseline (#1263), run on the Soren VM.

One reviewed provider call per case, scored by the same grader the bench
harness uses. A response the core's strict validator rejects is recorded as a
miss with its reason, so the coverage / parse-failure numbers are honest. The
results file carries no credential and no raw log line.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import ssl
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/home/ubuntu/docich/src")

from docich.comment_classifier import heuristic, jev  # noqa: E402
from docich.semantic_decision import transport as T  # noqa: E402
from docich.semantic_decision.routes import resolve_route  # noqa: E402
from docich.semantic_decision.validator import number  # noqa: E402

PROBE_USER = "azumagbanjo"


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> int:
    suite = Path("/home/ubuntu/jev-baseline-suite/public_cases.jsonl")
    out_path = Path("/home/ubuntu/jev-ungated-results.jsonl")
    env = load_env(Path("/home/ubuntu/soren/.env"))
    cfg = jev.Config.from_env(env)
    profile = resolve_route("direct")
    key = env[profile.credential_env]
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), T._NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    cases = [json.loads(line) for line in suite.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    for case in cases:
        rows = heuristic.baseline([f"{PROBE_USER}: {case['input']['comment']}"])
        payload = T.encode_request(jev.build_request(rows, cfg.model), profile)
        request = urllib.request.Request(
            profile.endpoint, data=payload, method="POST",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        started = time.monotonic()
        record = {"case_id": case["case_id"],
                  "expected": case["expected"]["category"],
                  "intent_family": case["expected"]["intent_family"],
                  "tags": list(case.get("tags") or [])}
        try:
            with opener.open(request, timeout=2.0) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            record.update(category=None, status="http_error", detail=str(exc.code))
            exc.close()
        except Exception as exc:  # noqa: BLE001
            record.update(category=None, status="network_error", detail=type(exc).__name__)
        else:
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            record["latency_ms"] = latency_ms
            try:
                data = T.validate_response(T.strict_json(raw), T.strict_json(payload), profile)
            except Exception as exc:  # noqa: BLE001
                # Diagnose which strict check fired, without logging the body.
                detail = str(exc)
                try:
                    parsed = T.strict_json(raw)
                    answer = parsed["answers"]["c1"]
                    probs = answer.get("probabilities") or {}
                    total = sum(probs.values())
                    if not math.isclose(total, 1, abs_tol=1e-5):
                        detail = f"probability_sum={total:.6f}"
                    elif not number(answer.get("confidence")):
                        detail = "confidence_not_a_number"
                    else:
                        detail = f"argmax_mismatch choice={answer.get('choice')}"
                except Exception:  # noqa: BLE001
                    pass
                record.update(category=None, status="invalid_response", detail=detail)
            else:
                answer = data["answers"]["c1"]
                record.update(category=answer["choice"],
                              confidence=answer["confidence"], status="ok")
        results.append(record)
        status = record["status"] if record["status"] == "ok" else f"{record['status']}:{record.get('detail')}"
        print(f"{record['case_id']} expected={record['expected']} "
              f"got={record.get('category')} {status}")

    out_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n",
                        encoding="utf-8")
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\ncases={len(results)} ok={ok} invalid={len(results)-ok}")
    print(f"out={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
