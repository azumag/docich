# 半熟英雄: 場面中断の記録に推定月長を残す（v809、2026-11-14）

## 症状（実測 g1163 2026-11-14 03:42-03:45）

野営の帰還先選択（dest）が月替わりの場面で中断されました。v801 で
goal/target/dest_target は残るようになりましたが、その中断が月のどのあたり
（長い月の終わり際か、短い月の遷移か）で起きたのかは分かりませんでした。

```
03:42:53 きかん（here）
03:45:29 camp_recall_interrupted {"stage": "dest", "steps": 1, "target": [84, 73]}
```

## 修正（v809 `hanjuku-chart-v809-interrupt-month-estimate`）

`camp_recall_interrupted` の `observed_metric` に `month_estimate`（直近3件の
月の長さの最大）を追加します。月の長さが分かっているときだけ入り、挙動は
変更しません。

## 検証

- 新規 `test_an_interruption_records_the_month_timing`:
  `month_durations=[500]` のとき month_estimate=500 が記録される。
- 月場面・帰還・野営関連の既存テストを実行。
- 修正前の `orchestrator.py` では新テストが失敗することを確認。

## 本番反映後に実測すること

- 中断記録に month_estimate が入り、長い月/短い月の遷移を区別できること。
- その情報で待機余白（v802/v804）の妥当性を判断できること。
- 12話ボス撃破には未到達。目標は active のまま継続します。
