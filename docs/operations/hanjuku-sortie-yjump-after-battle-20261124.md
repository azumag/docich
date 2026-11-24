# 半熟英雄: 戦闘割り込み後の出撃先Yジャンプやり直し（v1431、2026-11-24）

## 事象

出撃先ピッカーでYジャンプを確定したあと、出撃先のAを送る前に割り込み戦闘が
入ると、戦闘明けのピッカーはマーカーをゲーム側の都合の位置へ復元する。一方
ボット側にはジャンプ確定時の `near_goal` / `y_jumped` / `cursor` が残っており、
`target_step` が復元マーカーを「到着済み」と読んでその場でAを送っていた。
計画とは違うセルへ出撃する原因になる。

v1427 は `battle_end` で配置を捨てていたが、戦闘終了判定が2観測必要なこと、
`observe_sortie_transition` が `battle_end` より先に走ることから、戦闘明けの
最初の出撃先ピッカーでは古い到着記録がまだ生きていた。

## 変更

- `flows/map.observe_sortie_transition`: 出撃指示中（`sortie_attempt` が現在の
  order と一致）に戦闘画面（`SORTIE_INTERRUPTING_BATTLE_KINDS`）を観測したら
  `battle_interrupt` と時刻を記録し、`forget_target_placement` で割り込み前の
  到達記録・カーソルを捨てる。割り込みで消費した目標ジャンプ予算も戻し、
  やり直しの1回目が予算切れにならないようにする。
- `flows/map.target_step`: `battle_interrupt` が残っている出撃先ピッカーでは、
  割り込み後に新しいYジャンプで置き直されていない限り、古い到着記録を捨てて
  Yジャンプをやり直す（`sortie_target_rejump` を記録）。割り込み後のYジャンプ
  が既にマーカーを置き直している場合は、その配置を尊重して通常のAへ進む。
- `BOT_VERSION` v1431-sortie-yjump-after-battle。診断の既知バージョン一覧へ追加。

## 検証

- `tests/test_hanjuku_sortie_rejump_after_battle.py`（新規）4 passed。
- 出撃・Yジャンプ・遷移・戦闘終了に関係するテスト 165 passed。
- 変更フロー（`target_step` / `observe_sortie_transition` / `battle_end` /
  `_want_y_jump` / `_drop_stale_y_jump`）を参照するテスト群: 2030 passed, 5 skipped。

## 未確認

- 本番デプロイと実機での再発確認は未実施。
