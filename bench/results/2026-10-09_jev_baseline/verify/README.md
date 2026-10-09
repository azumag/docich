# Independent verification of the current-Jev baseline (t_7d465767)

This directory re-derives every headline number in PR #1974 and PR #1970 from the
committed artifacts alone, with a scorer written from scratch that mirrors
`src/docich/eval/metrics.py::score_predictions` (an unavailable prediction stays a
miss, so a runner cannot raise accuracy by answering less often).

    python3 verify/score_independent.py   # writes verify/independent_summary.json

Nothing here touches a production service. The inputs are the four committed
files below; the VM-side measurement that produced them is not repeated.

## Reproduction status

Every published figure reproduces exactly:

| Figure | PR #1974 / #1970 published | reproduced here |
| --- | --- | --- |
| Jev ungated accuracy (106) | 0.6698 | 0.6698 |
| Jev ungated macro F1 | 0.7899 | 0.7899 |
| Jev ungated coverage | 0.9434 | 0.9434 (6 × `probability_sum=0.990000`) |
| Jev ungated latency p50 / p95 | 154 ms / 204 ms | 153.9 / 204.4 |
| Jev pipeline accuracy / macro F1 | 0.6698 / 0.6303 | 0.6698 / 0.6303 |
| Jev pipeline notification FP/100 | 0.94 | 0.94 |
| llama accuracy / macro F1 / live-log | 0.6286 / 0.7339 / 0.5867 | 0.6286 / 0.7339 / 0.5867 |
| llama notification P / R | 0.882 / 0.882 | 0.882 / 0.882 |
| llama total p50 / p95 | 554 / 693 ms | 554 / 693 |
| llama TTFT p50 / p95 | 285 / 409 ms | 285 / 409 ms |
| llama parse failures | 0 | 0 |

## Two corrections this verification establishes

### 1. The llama notification FP rate is 1.90/100, not 0.6

The parent task's judgement criteria quote "notif FP 0.6 per 100". That figure is
one run's 2 false positives divided by the 3-run pooled denominator:

    2 FP / 315 pooled attempts × 100 = 0.63   <- the "0.6"

Per run the llama bench scored 105 cases and produced exactly 2 notification FP
(`jev-0066` other→card_gacha, `jev-0102` chitchat→raid, identical in all 3 runs
because generation is deterministic at temp 0.0 / seed 42):

    2 FP / 105 cases × 100 = 1.90/100

Issue comments 6072072408 and 6072161395 already corrected the FP *count* to 1.90
on this same definition; the 0.6 rate survived in the parent handoff and in this
card's criteria. The correct llama baseline for the notification FP comparison is
**1.90/100**, and the comparison below uses it.

### 2. The two benchmarks did not score the same case set

`bench/jev_bench.py` loads a suite through `corpus.load_public_cases`, which reads
`public_cases.jsonl` **plus** `critical_cases.jsonl`. The llama bench therefore
scored 105 cases = 106 public − 3 warmup (`jev-0001..0003`) + 2 critical
(`jev-0093`, `jev-0094`). The Jev baseline scored exactly the 106 public cases.

So the naive accuracy comparison (Jev 0.6698 on 106 vs llama 0.6286 on 105) mixes
two different case sets. On the **matched 103 public cases both sides ran**:

| | accuracy | macro F1 | live-log | coverage | notif P | notif R | notif FP/100 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Jev ungated (103) | 0.6699 | 0.7922 | 0.5867 | 0.9515 | 0.8421 | 0.9412 | 2.91 |
| Jev pipeline (103) | 0.6602 | 0.6303 | 0.6133 | 1.0000 | 0.9286 | 0.7647 | 0.97 |
| llama3.1:8b (103) | 0.6408 | 0.7389 | 0.5867 | 1.0000 | 0.8824 | 0.8824 | 1.94 |

Delta (Jev ungated − llama): accuracy **+0.0291**, macro F1 **+0.0533**,
live-log **+0.0000**, notification precision **−0.0402**, notification recall
**+0.0588**, notification FP/100 **+0.97**.

Neither correction changes the direction of the GO/NOGO verdict — both were
already "keep current Jev" — but the notification-FP leg of the criteria moves
from "llama 0.6 vs Jev 2.83 (llama 4.7× better)" to "llama 1.90 vs Jev 2.83
(1.5× better)", and on the matched subset Jev's pipeline view (0.97/100) is
actually *better* than llama.

## GO / NOGO

The card's criteria were GO = current Jev ≥ llama's 0.629 accuracy **and**
notification FP at llama's level; NOGO = current Jev significantly higher.

On the matched 103-case subset current Jev is higher on every accuracy-like
metric (accuracy +0.029, macro F1 +0.053, notification recall +0.059) and lower
on notification precision. The gate's binary framing does not hold, and the
answer does not depend on which of the two FP numbers is used:

**NOGO — keep current Jev.** There is no accuracy to buy by replacing it, and the
pipeline view already beats llama on notification FP once the correct denominator
is used. This matches the parent task's conclusion.

The genuinely actionable finding is unchanged and remains a production defect
rather than a model-selection question: the strict `probabilities` validator
rejects 2-decimal provider responses at a measured 12.7%, and one such rejection
cascades into a 10 s then 300 s cooldown that pushed 64 of 106 pipeline batches
off the provider entirely.
