# 半熟英雄: 戦闘・中断後の出撃先セル固定（v1427、2026-11-24）

## 経緯

本番 `/home/ubuntu/docich` の tracked file に未コミットの修正
（`BOT_VERSION=hanjuku-chart-v1423-sortie-cell-after-battle`）が残っており、
gateway の `tracked_vm_drift` で 2026-11-24 の main deploy（#5255 / #5260）が
fail-closed していた（docich#5261）。オーナー承認（2026-11-24）を得て、この
稼働中だった修正を main へ回収した。コード内容は本番で動いていたものと同一で、
バージョンだけ v1427-sortie-cell-placement に付け直した（v1423〜v1426 は既存
PR が使用）。

## 事象

出撃先ピッカーは、戦闘・イベント・メニューで中断されるとマーカーをゲーム側の
都合の位置で復元する。一方ボット側には中断前の `cursor` / `y_jumped` の受領が
残っており、復元マーカーを「到着済み」と誤認して、計画とは違うセルを確定して
しまう（g1163 2026-11-11 12:24:03 にオシロガで崖のセルへ出撃した例）。

## 変更

- `os_layer/navigation.forget_target_placement(mem, step)` を追加。
  `cursor`、`near_goal`（同 step）、`y_jumped[step]`、`y_jumped_res[step]` を捨てる。
- `flows/battle.battle_end`: 戦闘終了時に `forget_target_placement(mem)` を呼び、
  次のピッカーで新しい Y ジャンプから置き直す。
- `flows/map.observe_sortie_transition`: 再開したピッカーの後片付けと、
  ピッカーが閉じた後の再アームで `forget_target_placement(mem, step)` を呼ぶ。
- `flows/map.map_step` の出撃再開時、`y_jumped` / `y_jumped_res` / `near_goal` を
  step 単位で消す。`_drop_jump_state` も `y_jumped_res` を落とす。
- `BOT_VERSION` v1427-sortie-cell-placement、`collect_diagnostics.py` の既知
  バージョン一覧へ追加。

## 検証

- `tests/test_hanjuku_sortie_cell_shift.py`（新規・本番修正に付いていたテスト）
  4 passed。
- 出撃・マップ・Y ジャンプ系 13 ファイル 170 passed。
- CI: 本 PR の必須チェック（full-suite / prediction / retro / security ほか）。

## 復旧時の退避

本番 checkout の未コミット内容は VM 上に退避した（コミット対象外）。

- `/home/ubuntu/backups/drift-20261124/tracked-drift.diff`
  （sha256 `c77f312f05af4a3adbe0fca37c04f632f31623c559fda16a57c7d4895b7fb66c`）
- `/home/ubuntu/backups/drift-20261124/tracked-drift.tar.gz`
  （sha256 `37a1d64bc0d9b3937e50b5f065fdca70628e88b8cad700585c2e07ad823cb3d0`）

## 未確認

- この回収 PR のデプロイ完了（本番 checkout の clean 化 → deploy → ファイル
  SHA 照合）はマージ後に実施し、結果は PR に記録する。
