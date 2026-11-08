# 出撃元フォールバックの記録を回数に変える（v421、2026-11-05）

## 実測（切り分けの結論）

v420 の `code_probe`（モジュール自身の版）で**実行中のコードが新しいことを確認**した。
`module:hanjuku-chart-v420-code-probe` のタリーが立ち、v419 の `source_giveup_probe` も
5分で **94件** 出ている。つまり「新しい記録が出ない」のはコードが古いからではなく、
その時間に `map_step` が動いていなかっただけだった。

`source_giveup_probe` 224件の内容はすべて同じ:

- `override: null`（`_give_up_source` が設定した上書きが残っていない）
- `source_miss: 5`（pop が残っていない）
- `source: オケアナ` / `home: アルマムーン` / `home_garrison: []`（本城に将軍の記録なし）
- 5分で `order_source_changed` 94件 = 同じ観測のたびに再発。

## 変更

- `_give_up_source` の `order_source_changed`（source_fallback）記録を `_tally` に置き換え。
  状態が残らない経路では毎観測同じ記録が出るだけで、判断ログを埋める価値がない。
- フォールバックの判断自体は変えない（上書きが効く環境では従来どおり本城へ切り替わる）。

## 検証

- `tests/test_hanjuku_off_chart.py` の該当テストを新仕様に更新（記録なし・`source_fallback` の回数1）。
- 関連（`boss_sortie_overdue` / `castle_panel` / `source_content_key` / `source_mismatch` /
  `giveup_fallback` / 層 / `chart_bot` / `chart_adjust`）538 passed。

## 未確認・残件

- 状態が残らない根本原因（ランタイムのコピー実行経路）は Issue #3417 のまま。
- 本城の将軍が空のため、たとえ上書きが効いても本城からは出撃できない。
- 第6話ボス城への入城は依然として未実測。
