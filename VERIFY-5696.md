# VERIFY-5696 — [半熟英雄] 出撃キャンセル: ch9 ベーグル

検証日: 2026-11-27 JST  
検証 base: `origin/main` `942d0acd6ed925b453afc10ea03da2136fe23a97` / `hanjuku-chart-v1568-launch-priority-house`  
対象ラン: `g1190-ch1v9` / generation 1190 / `hanjuku-chart-v1525-sortie-cards-eligible`

## 結論

**(b) 設計どおり。追加の実修正は不要。**

ただし、open PR #5714 は本件の `move` 出撃を対象外にしているため、#5714 が本件を直接カバーするわけではない。カバー不要と判断する理由は、本件が敵城向けの攻撃・奪還・ボス出撃ではなく、自城 `イデ！` への中継 `move` であり、#5714 の優先化設計でも `move` / read-only probe は意図的に housekeeping 優先化の対象外に置かれているからである。

## ハッシュ照合

issue 埋め込み値と sha256 先頭桁を再計算した。

- fingerprint: raw `9|sortie_cancelled|sortie_cancelled` → `17e34cc026e4a7b9`（一致）
- occurrence: raw `g1190-ch1v9|1190|sortie_cancelled|sortie_unit_stayed|Mf0523b|ベーグル|None|1795691511` → `ea2ca9cb616874f95a61`（一致）

`fingerprint` には issue JSON の全 signals ではなく、primary signal 1件だけが渡される（現行 `month_audit.py` L3394-L3396）。

## 経路の特定

1. issue の月は ch9 `19-7`、55観測 / 11.3秒 / first_tick 148647 / last_tick 148701。
2. 対象 sortie `A:3814542a:Mf0523b` の `age_ticks` は 154。last_tick から逆算すると sortie 作成 tick はおよそ 148547 で、月開始より 100 tick（約20秒）前である。したがって、月内に新規開始して 2秒で取消されたのではなく、**前月末期から持ち越された `launched_unconfirmed` の照合**が月内に走った経路と読める。
3. issue の counts には `order_launched_unconfirmed: 2`、`sortie_target_resumed: 1`、`sortie_confirm: 1` が残る。直近30行だけでは初回中断行は落ちているが、これらは visible な `order_start` より前に出撃先選択・未確定化があったことを示す。
4. 現行 `map.py` L2608-L2613 は `launched_unconfirmed` の sortie を通常の `next_order` より先に選び、同じ step の照合へ戻す。`order_start` はこの照合再入でも記録される（`map.py` L2696-L2700）。したがって issue の `order_start 1 / order_launched 0` は「初回 dispatch が月内に始まり発射しなかった」ことの直接証拠ではない。
5. `_verify_sortie_source` は出撃元の将軍一覧を開き、本人が一覧にいる場合だけ「在城＝取消」の実測証拠として `cancelled` にする（`deploy.py` L127-L182）。issue の `garrison_seen` → `sortie_cancelled_observed`、理由文「将軍一覧で本人の在城を確認したため再試行可能」はこの設計経路と一致する。誤検知ではない。
6. 取消後は order を `pending` に戻し、取消回数ゲートでbounded retryになる（`orders.py` L415-L438）。無限に即時再送する経路ではない。

## `order_start_without_launch` 補助シグナルの意味

対象 order は `purpose: move`、出撃元 `イソ`、対象 `イデ！` で、issue 世界状態でも `イデ！` は奪取済みの自城である。監査の `_departures` は自城への `move` を敵向け発射から明示的に除外する（`month_audit.py` L3198-L3200）。そのため `departures=0` はこの `move` については正常で、`order_start_without_launch` はあくまで補助シグナルである。

primary の `sortie_cancelled` / `sortie_unit_stayed` は、将軍一覧でベーグルの在城を実測した事実に基づく。監査の誤検知 (c) ではない。

## 既存検証・修正との関係

- **#5635**: ch3 ミュスカデの `sortie_unit_stayed` も、中断された `launched_unconfirmed` を出撃元 roster で照合し、在城なら cancel・pending戻し・bounded retryへ進む同じ機構で、結論は (b)。本件は章と目的は違うが、取消判定機構は同じであり、#5635 の検証でカバーされる。
- **#5623/#5624 と修正 PR #5645**: 奪取済み城への stale `attack` が activate→取消を繰り返し計画再構築を飢餓させた欠陥。本件は `attack` ではなく自城への `move` で、`sortie_route_unverified` / attack-on-owned loop の証拠がない。PR #5645 は本件経路をカバーする必要もなく、実際カバーしない。
- **#5702 と修正 PR #5714**: #5702 は敵向け retake/attack が `order_start` 後に housekeeping へ押し出された欠陥。PR #5714（open、CI 12/12 green確認）は `order_start` 時点で優先窓を張るが、`LAUNCH_PRIORITY_PURPOSES` は `attack` / `retake` / `boss` / `invade` だけで、read-only probe と `move` は明示的に除外する（branch `verify/5702` の `orders.py` L1005-L1020、test `test_read_only_and_move_orders_do_not_arm_launch_priority`）。したがって **#5714 は本件を直接カバーしない**。一方で、この除外は #5714 の優先化設計上意図的なので、本件を実欠陥とする根拠にはならない。
- **現行 main の v1567/v1568**: `sortie_launch_pending` は `_commit_departure` で立ち、築城・家来/状態探索を遅らせる（`map.py` L3044-L3053、`month.py` L1227-L1236）。本件は counts・sortie age・target=None の未確定化記録から、picker が送信確定前に閉じた `launched_unconfirmed` 経路と読め、`_commit_departure` に到達していないため、この latch 有無は取消結果を変えない。仮に latch があっても発射確認を早めるだけで、在城実測による cancel 自体は同じである。

## ラン時版 v1525 と現行 main v1568 の差異

関連範囲では、v1525 から現行 main までに以下が入っている。

- v1529 / PR #5645: 奪取済み城への stale attack を done で閉じる（本件とは別経路）
- v1530: 実測終了した `launched_unconfirmed` の bounded reopen
- v1532: cancel時の `cancel_state` を issue 証拠へ残す（判定材料の追加であって、cancel 判定自体は同じ）
- v1556: boss-only phase の dispatch 枯渇回復（本件は boss 出撃ではない）
- v1557: order_start backoff を実開始まで消費しない
- v1562: ch9 の道中/移動ボス効率化（経路計画には影響しうるが、cancel 判定機構は同じ）
- v1567/v1568: 発射待ちの間の築城・家来/状態探索を抑制（ただし latch は commit後、かつ現行 main の partial実装）

`_verify_sortie_source` の中核（roster presence を cancel の実測証拠とし、pendingへ戻す）は v1525 から現行 main まで変わっていない。差分は主に証拠出力・retry/bounded recovery・優先化で、本件の結論を変えない。

## 残る未確定事項

- issue の `decisions.before` は直近30行に切られ、初回の picker中断を直接示す行（月境界または他画面遷移）は残っていない。完全な中断原因特定には runtime `g1190-ch1v9` の `hanjuku_decisions.jsonl` 全行が必要。
- ただし、sortie age、counts、現行コードの照合再入経路、roster presence の実測は揃っているため、取消判定が設計どおりという結論には影響しない。

## 設計判断メモ

`move` であっても主人公の中継移動を敵向け出撃と同じく housekeeping へ優先させるかどうかは、実装欠陥の修正ではなく別の policy 変更である。ch9 では move が連続するため、全 move を優先化すると築城・家来/状態確認が長時間 starvation する恐れがある。主人公中継だけを対象にする場合は `hero_hub_move` / corridor 文脈を限定的に識別する設計が必要であり、#5714 の `move` 一律除外は現時点では意図的な安全側の範囲設定と読める。
