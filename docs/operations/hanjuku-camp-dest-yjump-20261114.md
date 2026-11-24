# 通常野営帰還の屋根なしピッカーを Y ジャンプで目的城へ合わせる（v836、2026-11-14）

## 背景

issue #3978（月初場面に切られた野営帰還が成立しない: g1163 で1時間6回試行・0回成功）の
対策として、開始抑制（v802-804 の `MONTH_END_MARGIN`）と場面越しの意図保持（v693 の
`scene_pending`）は入っているが、`dest` 段の通常野営帰還は自軍城の屋根が画面に無いと

- `picker_forced`（オーナー 2026-11-05「どこでも良いから指定する」）でマーカー位置へ
  確定し、狙いではない城へ送るか、
- `camp_recall_skipped` で B 取消しになる

しかなかった。主人公・砦経路では `_start_y_jump`（全体マップで目的城を直接選ぶ）が
実測済みで、砦帰還は「砦から最も近い自軍城」へジャンプできている。

## 変更

`src/docich/hanjuku/flows/recall.py`:

1. `dest` 段のジャンプ経路を `hero`/`fort` 限定から全帰還へ拡張。通常野営でも
   `state['goal']`（`_recall_goal` = 帰還する本人の現在地に最も近い自軍城）が決まり、
   自軍屋根が見えないとき `RECALL:target` 予算内で `_start_y_jump` する。
   ジャンプ済み（`y_jumped['RECALL'] == goal`）で屋根が隠れたままなら、その城の上に
   マーカーがあるので A で確定する（`_finish_hero_recall` 経路）。
   屋根が見えれば従来どおり実測ステップで屋根へ寄せて確定する。
2. 新規帰還の開始時に `y_jumps['RECALL:target']` と `y_jumped['RECALL']` を必ず
   リセット（`_reset_boss_jump`、boss/fort 経路と同じペア）。前回帰還のジャンプ受領が
   次のピッカーのマーカーを誤確定しないようにする。
   ジャンプ予算を使い切ったとき・goal が決まらないときは従来どおり
   `picker_forced` / `camp_recall_skipped` へ落ちる（後方互換のフォールバック維持）。

## 守ったもの

- 実カーソルを毎回観測して1押しずつ進める契約（メニュー・マーカー歩行）は不変。
- 計画しただけでは送信しない `await_dispatch` / 受領分離の契約は不変。
- `y_jump_unreachable`・`y_jumps` 予算・`_close_target_jump` の失敗処理は既存のまま。
- boss redirect・砦経路の振る舞いは不変（リセットを新規帰還へ一般化しただけ）。

## テスト

`tests/test_hanjuku_recall_dest_jump.py` 新規6件:

- 通常野営・屋根なし・goal 既知 → Y（全体マップを開く）を返す
- ジャンプ受領済み・屋根なし → A で確定し `await_dispatch` へ
- ジャンプ予算消尽（`RECALL:target=2`）→ `picker_forced` フォールバック
- goal 不明 → `picker_forced` フォールバック
- 新規帰還で `y_jumped`/`y_jumps` がリセットされる
- マーカー無し → 従来どおり B 取消し

関連: `recall_dest_jump` + `recall_month_scene` + `recall_interrupt` +
`recall_destination` + `recall_scene_resume` + `recall_picker_native` +
`dome_camp` + `fort_camp` = 118 passed。

## 未確認・残件

- 実機での `y_jump_open`（RECALL:target, 通常野営）と `camp_recall_requested` の
  発生・成立率は未実測。VM 切替直後のため rotation は `recovery_required`、
  半熟英雄は非表示のまま。反映後に `camp_recall_interrupted` の再発有無と
  帰還成功率を再計測する。
- ジャンプ失敗（`y_jump_failed`）時のフォールバック遷移は経路上の
  `picker_forced` に任せている; 実機で確認する。
