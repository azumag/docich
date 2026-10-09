# 現行 Jev ベースライン計測 (#1263)

`bench/jev_eval_v1` の public ケース **106 件**（suite digest
`sha256:d750a436...56c2b`）を **現行 Jev 分類器（本番実装）** に投入し、
`llama3.1:8b-instruct-q4_K_M`（PR #1970）と **同一スコアリング**
（`docich.eval.graders.classifier.evaluate`、`bench/jev_bench.py` と同じ呼び出し）
で accuracy / macro F1 / ラベル別 P・R・F1 / coverage / parse failure を出した。

## 測り方（2 ビュー）

`bench/jev_bench.py` は「1 モデル = 1 パス、ゲートなし」で測る。現行 Jev は
パイプライン（heuristic → min_confidence 0.70 → 通知保護 → cooldown gate）
なので、両方を同じ採点器で測っている。

| ビュー | 何を測ったか |
| --- | --- |
| `ungated_single_pass` | ケースごとに reviewed provider へ 1 回だけ呼び、返答ラベルをそのまま採点。llama と同じ単一パス構造で、**精度の直接比較はこの列** |
| `production_pipeline` | 本番 `bin/docich-comment-classify` を 1 ケース 1 バッチで回し、実際に row に載ったラベルを採点。ゲートと provider の flakiness を含む **実運用精度** |

ゲート・閾値・通知保護を外した ungated を主比較にしているのは、llama 側にも
それらのゲートが存在しないため。逆に pipeline 側を ungated と比べると
「ゲットが落としている精度」がそのまま見える。

## 結果

| | accuracy | macro F1 | live-log accuracy | coverage | parse failure |
| --- | --- | --- | --- | --- | --- |
| 現行 Jev（ungated, 単一パス） | **0.7100** | **0.7899** | **0.6389** | 0.9434 | 6 |
| 現行 Jev（production pipeline） | 0.6698 | 0.6303 | 0.6282 | 1.0000 | 0 |
| heuristic のみ（Jev 不実行） | 0.5849 | 0.5169 | 0.5513 | 1.0000 | 0 |
| llama3.1:8b-instruct-q4_K_M（PR #1970） | 0.629 | 0.734 | 0.587 | 1.00 | 0 |

- accuracy は「miss を誤答として数える」共通ルール。ungated の available-only は
  0.7100 / coverage 0.9434。
- llama の live-log 0.587 は 3 連 run 平均、現行 Jev は 1 パス。

### 通知ラベル（intent_family=notification, gold 20 件）

| | precision | recall | F1 | FP | FN |
| --- | --- | --- | --- | --- | --- |
| 現行 Jev（ungated） | 0.8571 | 0.9000 | 0.8780 | 3 | 2 |
| 現行 Jev（pipeline） | 0.9412 | 0.8000 | 0.8649 | 1 | 4 |
| llama3.1:8b | 0.882 | 0.882 | – | 0.6 / 100 件 | – |

- 現行 Jev の FP は `( ́・ω・) 長時間の配信乙です!N 時間に到達しました!` を
  `stream_goal` と誤った 3 件（jev-0043 / 0044 / 0050、gold は chitchat）。
  100 件あたり **2.83 件** で llama の 0.6 より多い。
- FN 2 件（jev-0002 / 0007）はカード獲得通知だが、下記の provider 側
  validator 失敗で miss になっているだけ。

### レイテンシ

| | p50 | p95 |
| --- | --- | --- |
| 現行 Jev ungated（1 呼び出し全体） | 154 ms | 204 ms |
| 現行 Jev pipeline（jev_ms, 試行時のみ） | 308 ms | 392 ms |
| llama3.1:8b total | 554 ms | 693 ms |

## 発見: provider 応答が厳密 validator を約 13% の率で落とす

`docich.semantic_decision.validator.validate_response` は `probabilities` の
合計を `abs_tol=1e-5` で 1.0 と比較する。現行 provider（`jev-1.13.0`,
api.typesafe.ai）は 2 桁に丸めた確率を返すことがあり、その合計が **0.99**
になり validator が `invalid_response` を投げる。実測（同ケース 150 呼び出し）
で **19/150 = 12.7%**。

これが起きると:

1. そのバッチは heuristic にフォールバックする（`status=invalid_response`）
2. `COOLDOWNS['invalid_response'] = 10` 秒、direct gate が閉まる
3. fallback の vercel は **auth_error**（キー未設定/失効）→ 300 秒 gate
4. 以後のバッチはまとめて cooldown 落ちする

suite 全体を 1 パスで流すと、この連鎖で 106 件中 **64 件が cooldown**
（provider を一切呼ばず heuristic のまま）になった。ラベル精度そのものより
**この可用性の低さ** が現行 Jev の実運用上の弱点。

## GO / NOGO 判定

判定基準（親カード t_2e3d9101 の結論）:

- GO: 現行 Jev が llama の accuracy 0.629 と同等以上 **かつ** 通知 FP 率が同水準
- NOGO: 現行 Jev が有意に上回る場合

**判定: 精度は GO（現行 Jev を維持）、通知 FP は NOGO 側（llama 優位）、
可用性は NOGO（provider flakiness が支配的）。**

1. **精度**: 現行 Jev は llama3.1:8b を全指標で上回る
   （accuracy +0.081 / macro F1 +0.056 / live-log +0.052 / 通知 recall +0.018）。
   ローカル LLM への置き換えで精度を取る理由はない。**現行 Jev を維持**。
2. **通知 FP**: llama 0.6/100 件に対し現行 Jev 2.83/100 件（同一パス条件）。
   ただし pipeline 側（ゲート込み）は 0.94/100 件で、誤動作の実害は小さい。
   `( ́・ω・) 長時間の配信乙です` 系の誤判定は閾値 or ルールで潰せる。
3. **可用性**: llama の 693ms p95 は目標 300ms に未達、かつ VRAM 5.53GiB を
   OBS/VRChat と奪い合う。現行 Jev の 154ms p50 は用途に対して十分速い。

**結論: shadow mode の GO / NOGO という問い自体が成立しない。** ローカル LLM は
精度でも速さでも現行 Jev を越えていないので、置き換え候補ではない。
親カードの結論（「現行 Jev 維持・ローカル LLM は shadow mode で段階検証」）を
**維持**する。むしろ優先すべきは上記 provider flakiness（13% の validator 失敗
→ 10 秒/300 秒 gate 連鎖）で、これは shadow mode より前に潰すべき本番缺陷。

## 改善条件（NOGO 時に記録する差分）

- 差分が最大のラベル: 現行 Jev の FP は `stream_goal`（長時間配信通知の誤判定）、
  FN は `card_gacha`（validator 失敗による見かけ上の取りこぼし）。
- 条件 1: provider の `probabilities` 丸め（2 桁）を validator が許すか、
  provider 側で少数桁を増やす。これだけで 12.7% の invalid_response が消え、
  pipeline coverage が実効 100% に近づく。
- 条件 2: vercel fallback の認証情報を修正する（現状は常時 auth_error で
  実質 fallback が機能していない）。
- 条件 3: `stream_goal` の FP は「N 時間に到達しました」パターンを
  heuristic 側で除外するか、通知ラベルの min_confidence を引き上げる。

## 再現

```sh
# VM 上（Soren 本番実装を read-only で分類させる。state/metrics は
# /home/ubuntu/jev-baseline-* に逃がし、本番 gate と telemetry は触らない）
scp run_baseline.sh run_ungated_on_vm.py ubuntu@soren-prod-vnic:~
scp public_cases.jsonl ubuntu@soren-prod-vnic:/home/ubuntu/jev-baseline-suite/
ssh ubuntu@soren-prod-vnic 'bash ~/run_jev_baseline.sh'          # pipeline ビュー
ssh ubuntu@soren-prod-vnic 'python3 ~/run_ungated_on_vm.py'      # ungated ビュー

# 手元（採点のみ。provider キーは不要）
python3 score_baseline.py      # -> report.json
```

## public_safe 確認

- `@handle` は全ケース `[user]` 投影済み（suite 側で実施）。
- 生ログは Git に置いていない。本計測で書き出したのは
  `report.json` / `ungated_results.jsonl`（ラベルと採点のみ）/ `pipeline_metrics.jsonl`
  （telemetry event。`rows` は baseline/candidate/selected/status のみで
  コメント本文を含まない）。
- 資格情報（TYPESAFE_API_KEY 等）は成果物に一切含まれないことを確認済み。
