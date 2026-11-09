# 半熟英雄: v604 の重大バグ修正（出撃監視の重複キーワードで brain が落ちる）（v605、2026-11-10）

## 症状（実測・02:0x）

本番の bot が **`brain が異常終了しました (code=2)`** を繰り返し、**コーナーが停止**しました。

```
TypeError: docich.hanjuku.decision_log._record() got multiple values for keyword argument
  at deploy.py:695:_selected_actor_guard < deploy.py:1687:deploy_step
```

## 原因

v604（PR #3856）で `_record(mem, 'sortie_actor_ocr_slip', **_deploy_context(order, mem),
general=selected, ...)` と書きましたが、`_deploy_context` は既に `general` を返すため、
**キーワード引数の重複**で例外になっていました。出撃画面の観測ごとに落ちるため、
エージェントが実質停止しました。

## 修正（v605 `hanjuku-chart-v605-actor-slip-crash`）

- `general=selected` を削除（文脈の `general` が予定将軍を持つ）。
- 回帰テストを追加: 該当 `_record` が `general=` を再指定しないことを検査。
- **復旧**: 本番 checkout に緊急適用 → エージェントを tmux で再起動 → 稼働を実測。

## 確認

- `tests/test_hanjuku_sortie_evidence.py` に1件追加。出撃・証跡・診断 221件 passed。
- 本番: 再起動後 `[agent] ... 1 件のアクションを実行しました` が継続し、
  `observations` が増加（121,216 時点）することを実測。
