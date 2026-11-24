# 半熟英雄: v837-v841 セッション変更の実機検証チェックリスト（2026-11-14）

第12話クリアへ向けた改善 PR のうち、実機（半熟コーナー）が rotation
`recovery_required` で非表示の間に merge された 5 件の検証手順。
半熟コーナーが再表示されたら、下記の記録が想定どおり出ているか確認する。

## 検証対象

| 版 | PR | Issue | 確認すべき記録 |
|---|---|---|---|
| v837 | #4200 | #3753 | `hanjuku_run.json` の `bot_version` が hotload 世代と一致（state/observation/input_sent の3箇所とも v837 以降の文字列を書く） |
| v838 | #4202 | #3505 | ボス至近の健全野営（HP半分超）が月次帰還を待たず `boss_move` タスク化し、野営メニュー `いどう` でボス城へ Yジャンプ再移動。`camp_recall_requested`(いどう)・`_boss_move_dest_step` 発生を確認 |
| v839 | #4204 | #3008 | 月次帰還キューで計測済みHP比率が低い将軍が名順より先に帰還される。重傷の野営将軍が遭遇死しない |
| v840 | #4207 | #3751 | 国人数の走査中変動（例 32→31）でも `roster_count_mismatch` を記録しつつ、読めた将軍の帰還・修理タスクが `roster_end_unconfirmed` で全滅せず実行される（`general_statuses.complete:false` は維持） |
| v841 | #4210 | #3721 | 章送り直後の HUD・文字・カーソルなし地図で、12観測ごとに `map_blind_advance` + A送信が出て演出を抜ける（従来は174秒無入力） |

## 検証方法

1. 半熟コーナーが回転で表示されるのを待つ（`hanjuku_predictions` の status が
   CANCELED ではなく ACTIVE/LOCKED になる）。
2. diagnostics / decision log で上表の記録名を検索する。
3. v837 は `hanjuku_run.json` の `bot_version` 文字列を現行 hotload 版と突き合わせる。
4. v838/v839/v840 は月次ステータス走査の直後、v841 は章送り直後の観測で確認する。

## 注意

- v838 は健全（HP半分超）な野営将軍のみが `boss_move` 化する。負傷・未計測・
  broken は従来どおり帰還・免除。
- v840 の degraded completion は membership 証明（除籍・prune・
  `recruit_roster`・`receipts.complete`）を行わない。名簿確定までは
  `complete:false` のまま公開されるのが正常。
- v841 の A はカーソル・HUD・本文が全て無い地図でのみ発火する。通常の
  フィールドマップ（カーソルあり）では発火しない。
