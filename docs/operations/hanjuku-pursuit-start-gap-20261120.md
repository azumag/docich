# 半熟英雄: ボス追撃がフィールドを占有し続けるのを、開始間隔でゆずる（v1251、2026-11-20）

## 症状（本番 g1288 / v1249 の実測）

第9話・全12城確保後、ボスへの入城が一度も成立しないまま月が進んでいた。

- `hanjuku_decisions.previous.jsonl`（11:34〜13:14 の100分）:
  `boss_pursuit_queued` 77件 / `boss_pursuit_redirected` 65件 /
  `boss_pursuit_unverified` 12件。一方 `order_start` 2件・`order_launched` 1件。
- 同じ月の監査（`hanjuku_month_audit.jsonl`）: 月 64-3 は
  `launches 0 / departures 0 / order_start 0 / moves 0`。
- `hanjuku_bot.json`: `boss_wave_generals` は7将軍（カシス・ギー・コリアンダー・
  コンポート・バタール・ピオーネ・マスカット）。`plan.active` は null。
- 追撃は約90秒ごとに再開し、1将軍あたり約10分ごとに再照準していた。
  それでもボス入城・交戦の記録は無く、同じ将軍の再照準だけが続いた。

## 原因

`boss_pursuit.step` は「mapフレームで追撃候補が1人でもいれば」他の担当より先に
フィールドを取る。追撃probeは最後にmapへ戻って終わるため、次のmapフレームも
また追撃が取り、野営の再発見（recall）や城からの新規出撃計画（map flow）に
フレームが渡らない。結果として、ボスへ向かう将軍だけが動き続け、新しい波・
城からの出撃・野営将軍の回収がほぼ実行されない。

## 変更

- `flows/boss_pursuit.py`: `PURSUIT_START_GAP = 90` を追加。追撃を開始したtickを
  `boss_pursuit_start_tick` に残し、次の追撃開始まで90 tick（約1.5分）を空ける。
  待つ間は `step()` が None を返し、map flow / recall / 月次などの通常担当へ
  フレームを明け渡す。追撃自体の手順・対象選び・再照準の内容は変えない。
- 追撃開始の記録 `boss_pursuit_queued` に `since_last_start` を追加（間隔の実測用）。
- `state/regions.py`: `boss_pursuit_start_tick` を章スコープ（FLOW）で宣言。
- `version.py`: `hanjuku-chart-v1251-pursuit-start-gap`。
- `ops/vm_actions/collect_diagnostics.py`: hot-load版の許可リストへ追加。

## 検証

- 追加: `tests/test_hanjuku_boss_pursuit_gap.py`（3件）
  - 間隔内の2回目の開始は拒否され、状態を書き換えない
  - 間隔経過後は開始でき、`since_last_start` が記録される
  - 旧buildからhot-loadした（キーが無い/不正な）メモリでも初回は開始できる
- 既存: `test_hanjuku_boss_pursuit_fort` / `_redirect_log` / `_yjump` /
  `_absent_general` / `test_hanjuku_key_inventory` / `test_hanjuku_state_regions` /
  `test_hanjuku_chapter_scope_cleared` が緑。
- CI: 半熟スイートで確認する（本doc時点では未実行）。

## 未確認・残件

- 実機で追撃回数が減り、その間に城からの出撃・野営回収が増えるかは未確認。
  デプロイ後の `boss_pursuit_queued` 間隔と `order_start` 件数で確かめる。
- 本変更は「同じ行動の繰り返しでフィールドを占有しない」ためのもので、
  ボス入城そのものの成否（移動ボスへの追いつき方）は別問題として残る。
  入城が続かない場合は、追撃の狙い位置（先行/待ち伏せ）を別途検討する。
