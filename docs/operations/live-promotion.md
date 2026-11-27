# PAPER → LIVE の自動昇格（Issue #3623 §2.6）

2026-11-27 のオーナー決定で、PAPER の銘柄別スロットの戦略を、条件を満たしたら LIVE へ自動で入れ替える。
判定は [`src/docich/trading/live/promotion.py`](../../src/docich/trading/live/promotion.py)、
適用は LIVE ワーカー（[`runner.py`](../../src/docich/trading/live_worker/runner.py) の `_promotion_step`）が行う。

## 条件（すべて満たしたときだけ）

1. **ルール v3**（[`promotion_shadow.py`](../../src/docich/trading/promotion_shadow.py)）：セル（スロット実験 × 銘柄）の e-process が E ≥ 5、日ブロック 5 以上。2026-11-17 以降の日だけを使う。
2. **LIVE を上回る**：そのセルの日ブロックと同じ日について、`候補の日ブロック − LIVE の日ブロック` の平均が正。LIVE の日ブロックは、その銘柄の LIVE の売り 1 件ごとの純リターン（手数料込み、平均取得原価）の日平均。LIVE がその日に売っていなければ 0。
   LIVE がまだ扱っていない銘柄は、1 だけで追加する。
3. **頻度**：その銘柄の LIVE 方針が変わってから 7 日未満なら変えない（初回登録・昇格のどちらも数える）。つまり 1 銘柄 7 日に 1 回まで。
4. **停止**：`<state_dir>/trading-live/owner/promotion.disabled` があれば判定も適用もしない。新規の買いを止める安全装置が作動中も昇格しない。
5. **ルールの出所**：セルの実験そのもの（スロットの `paper_strategy_experiment.json`、交代済みならスロットの評価履歴）。厳密な往復検査と LIVE の範囲（JPY 建て、1 回の買いは資金の 30% まで）を通ったものだけ。

## 動作

- ワーカーは起動直後と、その後 1 時間ごとに判定する（サイクルの合間）。
- 昇格は LIVE 台帳に `reason=promotion`・`actor=promotion_judge` の方針の有効化として記録され、根拠の digest も残る。ワーカーはその場で新しい方針に切り替える（再起動しない）。
- 昇格で銘柄を外すことはない。置き換えか追加だけ。
- 保有中の建玉は、**買ったときの方針の売り条件**で手じまいする（`decide.entry_exit_policies`）。その銘柄の新規の買いは、旧建玉が無くなるまで止まる（既存の `legacy_position_open`）。
- 判定の結果は `<state_dir>/trading-live/promotion_status.json`（0600）。`status`（`ok` / `stopped` / `safety` / `unavailable` / `failed`）、昇格した銘柄と根拠（E、ブロック数、候補と LIVE の平均）、見送った理由（`not_better_than_live` / `min_hold` / `rules_unavailable` など）。
- 判定が壊れても売買は止めない（状態ファイルに `failed` を書き、今の方針で続ける）。

## 止め方

```bash
touch <state_dir>/trading-live/owner/promotion.disabled
```

再開はこのファイルを消す。ワーカーの再起動は要らない（次の判定から効く）。

## 既知の限界

- 比較の単位は「売り 1 件あたりの純リターンの日平均」で、資金あたりの損益ではない。
- PAPER の日ブロックは買い手数料を補正した値（`cell_metrics`）、LIVE は実際の手数料。
- ルール v3 の保証は「実質トントン以下のセルが昇格する確率はセルごとに 1/5 以下」まで。多くのセルを順に見るので、偶然の昇格は混ざる。
