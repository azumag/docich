# VERIFY-5507 — [半熟英雄] 月内出撃ゼロ: ch6 月次処理がマップを占有

- 対象: docich#5507（`bug` / `auto-generated` の監査自動起票）/ fingerprint `4ba82a1c75855bfe` / occurrence(incident_id) `8d72ea69aab0b8d05231`
- 判定: `zero_sortie`（confidence `high`）/ signature `monthly_scan`（issue 記載シグネチャ: 月次処理がマップを占有 / 戦闘が長引く）
- ラン: `g1342-1fe0734c` / generation `1342` / `hanjuku-chart-v1492-boss-only-card-miss-eligibility`
- 月: ch6 10-9（観測 1067回 / 1427.7秒 / max gap 4.0秒 / started_clean）
- 検証ベース: worktree `/home/hatch/work/wt/docich/devincli-5507` detached HEAD `425f1c38a`（`BOT_VERSION = 'hanjuku-chart-v1493-interim-order-purpose'`。v1492 は祖先として含まれる）
- 検証範囲: read-only。本ファイルの作成以外のコード変更なし。実装変更・本番 SSH なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照できず、issue 埋め込みの決定表・世界状態・現行コードを照合した。VM 原本の時系列は未取得（下レビュー 7150317567 の指摘を根拠に再判定）。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(d) 判定不能 — 月次処理占有の root cause を利用可能な証拠から一意に帰属できない**

発火自体は監査契約どおり（誤検知ではない）で、fingerprint / incident_id は現行式で再計算一致する。#5463 型の probe ループや #5455 型の本国発ボス行軍、#5487 型の合法出撃源ゼロとは、決定表の範囲で異なることも確認できる。

しかし #5520 の Devin Web 下レビュー（7150317567）が示した 3 点により、前版の (b) 帰属は支持できない:

1. **order_start の集計矛盾**: issue の決定表には `sortie_input` → `card_pick` → `sortie_confirm` → `y_jump_open` → `y_jump_unknown_hold` の系列が現れ、`order_start` が 1 回発生した証拠がある。一方で集計値 `order_start=0` と矛盾する。`month_audit._decision_counts` が `Counter.most_common(30)` で切り捨てているため（L2962-2966）、1 回だけの `order_start` が集計から漏れた可能性が高い。`monthly_scan` signature 自体がこの集計上限の影響を受けた可能性がある。
2. **idle-reclaim 発火の証拠なし**: 月内の最後は `y_jump_unknown_hold` で終わり、その後に月内 `post_month_*` へ戻った証拠もない。前版の「試行停滞後の idle reclaim で月次 scan が map を保持」は証拠に反する。
3. **翌月回復の帰属未支持**: 翌 5 分窓の出撃 1 件を「月次処理完了」とする記述も未支持。月境界後に別の要因で出撃が成立した可能性を排除できない。

したがって、利用可能な証拠では monthly_scan を root cause と断定できず、(d) 判定不能とする。fingerprint 一致と zero_sortie 発火自体は否定しない。

### 1. 発火の正当性とハッシュ照合

- `zero_sortie` は閉じた月について `confirmed == 0` かつ `departures == 0` で、月を開始から完全観測した場合に発火。issue 値は 確定 0 / 発射 0 で契約どおり（order_start は集計上 0 だが、決定表には 1 件の `order_start` 系列が現れる）。
- 起票閾値（obs≥20、duration≥`MIN_MONTH_SECONDS`=120s、max_gap≤60s、coverage≥0.9）を本件は 1067回 / 1427.7s / 4.0s / started_clean で充足。confidence `high` も契約どおり。
- fingerprint を現行 `month_audit.py` の `_fingerprint`（L3112-3115: `sha256("chapter|kind|primary")`）で再計算: `sha256("6|zero_sortie|monthly_scan")[:16]` = `4ba82a1c75855bfe` で一致。
- incident_id を `_incident_id`（L3107-3110: `sha256("runtime_id|generation|chapter|month")[:20]`）で再計算: `sha256("g1342-1fe0734c|1342|6|10-9")[:20]` = `8d72ea69aab0b8d05231` で一致。

### 2. #5463 / #5455 / #5487 型ではない限定切り分け（下レビュー同意）

- #5463（ch1）の機構は `boss_target_locked` / `plan_map_activate` / `castle_jump_*` の activate→hold ループ。本件の決定内訳にはこれらの繰り返しは現れない。
- #5455（ch12）の機構は暫定ボス order を本国発で出撃させる長距離行軍の吸収・野営化。本件の pending はアルマムーン→イカリア（通常城）であり、ボス行軍ではない。
- #5487（ch1）の機構は合法出撃源ゼロ（max staffed ≤ 1）。本件はアルマムーンに 4 名在城で `no_source` は不成立。

### 3. 事実のみに限定した記述

- 月内の決定系列には `sortie_input` → `card_pick` → `sortie_confirm` → `y_jump_open` → `y_jump_unknown_hold` が現れる。Y ジャンプ中の unread 画面で保留が入った。
- 戦闘 grind: `battle_draw_hold` 11 件（契約どおり `battle_grind` 発火）。
- 翌 5 分窓: 決定 86 件 / 出撃 1 件 / 回復 yes / 初出撃 78.1 秒。ただし「月次処理完了」による回復とは断定できない。
- idle-reclaim 発火・月次処理完了・`order_start=0` は根拠としない。

### 4. なぜ (a)・(b)・(c) ではないか

- (a) 実欠陥ではない（本 issue の範囲）: 月次処理占有それ自体が欠陥である証拠はない。ただし下記の `_decision_counts` 切り捨ては別途 (a) 候補として扱う。
- (b) 設計どおりではない: idle-reclaim 発火の証拠がなく、設計どおりの占有と断定できない。
- (c) 誤検知ではない: 月内 confirmed/departures が 0 で、監査は契約どおり発火した。

### 残件 / 別タスク候補

- **`month_audit._decision_counts` の top-30 切り捨て**（L2962-2966）により、低頻度の `order_start` が集計・シグナル計算から漏れる。これは監査の帰属誤りを招く可能性があり、実欠陥 (a) として別途タスク化を検討する。本 VERIFY の範囲外。
- VM 原本（`hanjuku_decisions.jsonl`）の時系列が取得できれば、`order_start` / `post_month_sortie_floor` / `post_month_sortie_floor_reclaimed` の発火を直接確認し、(d) を解消できる可能性がある。
- 本検証ではコード変更しない。PR #5520 の更新・CI 確認は検品ゲート（メイン Muse）が行う。

### 付録: 判定シグネチャ

- kind: `zero_sortie` / signature: `monthly_scan` / confidence: `high`
- fingerprint: `sha256("6|zero_sortie|monthly_scan")[:16]` = `4ba82a1c75855bfe`
- incident_id: `sha256("g1342-1fe0734c|1342|6|10-9")[:20]` = `8d72ea69aab0b8d05231`
