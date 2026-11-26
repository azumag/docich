# VERIFY-5702 — ch12 月内出撃ゼロ（出撃開始後に発射されない）

## 結論

**(a) 実欠陥**。監査の `zero_sortie` 自体は誤検知ではない。ch12 25-9 は観測 1505 回・305.4 秒で、敵向け確定出撃 0、発射 0、`order_start` 2、`order_launched` 0 だった。翌 5 分では同じランから `order_launched` 4・初出撃 70.8 秒が出ているため、発射パイプライン全体の恒久的破損ではなく、月内では出撃手順が前景の月次処理・家来/状態確認・防衛戦とイベント会話に押し出されて完走しなかった、という経路である。

ただし、少なくとも `order_start` 2 件のうち 1 件はワフェルダ将軍一覧を読む read-only の roster probe（`A:a5973e2d:M81b091`）であり、発射を生まない指示だった。もう 1 件の step は issue に保存された末尾証拠だけでは特定できないが、`sortie_input` / `sortie_confirm` / `sortie_departure_requested` / `order_launched` がいずれも 0 で、`castle_jump_failed` 2、`sortie_hold_abandoned` 1、`post_month_sortie_floor` 1、`month_sub_step` 116、`house_scan_started` 2、防衛戦・戦闘（`defense_observed` 8 / `battle_start` 9）と `event_text_advance` 27 が同じ月に残っている。出撃元への到達・将軍/札選択・目標確定へ進む連続した画面所有を確保できなかった構造は証拠と一致する。

## fingerprint / occurrence の再計算

- fingerprint raw: `12|zero_sortie|departure_failure`
  - sha256 先頭16 hex: `5ed9f5e477638b89` — issue の fingerprint と一致
- occurrence raw: `g1190-ch1v9|1190|12|25-9`
  - sha256 先頭16 hex: `741aa96a4e0c1a15` — issue の occurrence と一致

## 既存修正でのカバー確認

| 修正 | 対象機構 | #5702 をカバーするか |
| --- | --- | --- |
| #5645 / v1529（#5623/#5624） | 奪取済み城への stale 攻撃を done で閉じ、activate/reopen ループを止める | **しない**。本件の対象候補は失城ラグナグへの retake で、target-owned の stale 攻撃ではない。`sortie_settled_owned_target` もない。 |
| #5647 / v1530系（#5626） | 実測終了した `launched_unconfirmed` を bounded reopen しゾンビ化を止める | **しない**。本件は `order_launched` 0・`sortie_departure_requested` 0 で、launched_unconfirmed の実測終了 reopen 以前に進んでいない。 |
| #5689 / v1556（#5681/#5155） | boss-only phase で `order_start` 前の dispatch 枯渇を月次占有から回復する | **しない**。本件は boss-only 出撃ではなく ch12 のラグナグ争奪系で、`order_start` には到達している。通常/争奪月では starvation の記録だけで前景譲りは発火しない設計だった。 |
| #5707 / v1567 および #5709 / v1568 | `sortie_launch_pending` 中は築城・家来/状態探索を見送る | **現行 main では不十分**。ラッチは `_commit_departure` でしか立たず、本件のように `sortie_departure_requested` 前に押し出される経路では発火しない。さらにラッチを消す経路がなく、一度立つと章末まで築城・家来探索を見送り続ける退行があった。 |

## ラン時版と現行 main の差異

- ラン時版: `hanjuku-chart-v1525-sortie-cards-eligible`（runtime `g1190-ch1v9` / generation 1190）
- 検証開始時の main: `6f8bedd95` / `hanjuku-chart-v1568-launch-priority-house`
- v1525 から v1568 までに #5645・#5647・#5689 は入っているが、上表のとおり本件経路はカバーしない。
- v1567/v1568 は本件系へ向けた修正だが、優先ラッチの開始点が遅く、終了判定もなかった。したがって現行 main をそのまま「カバー済み」とは判定しない。

## 実修正（この検証で特定した残りの実欠陥）

`hanjuku-chart-v1569-launch-priority-start` として次を実装した。

1. 敵向け出撃（`attack` / `retake` / `boss` / `invade`、read-only probe と move を除く）の `order_start` 時点で `sortie_launch_pending` を立てる。従来どおり departure commit でも再適用する。
2. 家来/状態探索と築城スクロールは、ラッチの文字列を無条件信用せず `active_sortie_launch_priority()` で検証する。
   - post-commit では `launch_probe` が未 kept の間だけ有効。
   - pre-commit では plan active、`sortie_attempt`、`departure_request` のいずれかが同一步を示す間だけ有効。
   - 完了・失敗・確認後・rollback 後・kept portrait march ではラッチを消す。
3. read-only/move の指示では優先ラッチを立てない。
4. `ops/vm_actions/collect_diagnostics.py` の hotload version allowlist に v1569 を登録した。

## テスト

実行環境は Python 3.10 のため、repo 既定の `$HOME/py311shim` が無かったので `tomli` による最小 shim を作成して実行した。

- `tests/test_hanjuku_launch_priority.py tests/test_hanjuku_house_defer_to_launch.py tests/test_hanjuku_chikujou_scroll.py tests/test_hanjuku_house.py tests/test_hanjuku_house_idle_escape.py tests/test_hanjuku_key_inventory.py tests/test_hanjuku_departure_request.py tests/test_hanjuku_order_start_backoff.py tests/test_hanjuku_boss_dispatch_recovery.py` — **168 passed**
- `tests/test_hanjuku_overwhelming_egg_conservation.py tests/test_hanjuku_hotload.py tests/test_hanjuku_chart_bot.py` — **408 passed**
- `tests/test_hanjuku_state_regions.py tests/test_hanjuku_launch_probe_region.py tests/test_hanjuku_month_audit.py` — 228 passed / 1 failed。失敗は既存の Python 3.12 相当 f-string 構文を Python 3.10 の AST が parse できない `soren_corner_text.py` の環境制約で、今回の差分とは無関係。

## 残りの未確認事項

- issue に保存された月末末尾だけでは `order_start` 2 件目の step は特定できない。完全特定には runtime `g1190-ch1v9` の `hanjuku_decisions.jsonl` 全行が必要。
- v1569 の実機確認（争奪月で order_start 後に家来/築城を見送り、発射確認後に再開すること）は次回該当月のランで必要。
- 防衛戦そのものはゲーム側の強制割り込みなので無視しない。修正が対象にするのは、戦闘後の自由画面で月次・家来/築城が出撃手順を再度押し出す優先順位逆転である。
