# VERIFY-5507 — [半熟英雄] 月内出撃ゼロ: ch6 月次処理がマップを占有

- 対象: docich#5507（`bug` / `auto-generated` の監査自動起票）/ fingerprint `4ba82a1c75855bfe` / occurrence(incident_id) `8d72ea69aab0b8d05231`
- 判定: `zero_sortie`（confidence `high`）/ signature `monthly_scan`（シグナル: 月次処理がマップを占有 / 戦闘が長引く）
- ラン: `g1342-1fe0734c` / generation `1342` / `hanjuku-chart-v1492-boss-only-card-miss-eligibility`
- 月: ch6 10-9（観測 1067回 / 1427.7秒 / max gap 4.0秒 / started_clean）
- 検証ベース: worktree `/home/hatch/work/wt/docich/devincli-5507` detached HEAD `425f1c38a`（`BOT_VERSION = 'hanjuku-chart-v1493-interim-order-purpose'`。v1492 は祖先として含まれる）
- 検証範囲: read-only。本ファイルの作成以外のコード変更なし。実装変更・本番 SSH なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照せず、issue 埋め込みの決定表・世界状態・コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。

## 結論: **(b) 設計どおり — 月次処理の占有は監査シグナルの範囲内で、#5463 型の probe ループや #5455 型の本国発ボス行軍の再発ではない**

発火自体は監査契約どおり（誤検知ではない）。ただし ch6 10-9 の占有機構は、#5463 で修正済みになった「読み取り専用 probe がボスロックで再活性化するループ」でも、#5455 で修正済みになった「暫定ボス order を本国発で出撃させる長距離行軍」でもない。決定内訳に `boss_target_locked` / `plan_map_activate` / `castle_jump_*` の繰り返しは現れず、世界状態も在城 4 名のアルマムーンを出撃元に持つ（`no_source` は不成立）。

月次処理（`post_month_status`）がマップを占有したのは、当月に成立した出撃がなく開始可能な指示（アルマムーン→イカリア、デーメーテール）が存在する間、#4642/#4888 の譲渡ロジックが bounded にマップを出撃計画へ明け渡し、試行が停滞すると `SORTIE_FLOOR_IDLE_YIELDS` で月次側へ戻す設計どおりの振る舞いである。戦闘 grind（`battle_draw_hold` 11 件）と Y ジャンプ中の unread hold（`y_jump_unknown_hold`）が重なった結果、月内発射は 0 になったが、翌 5 分窓で 1 件の出撃が成立し 78.1 秒で回復しており、恒久的なフロー破綻ではない。

### 1. 発火の正当性とハッシュ照合

- `zero_sortie` は閉じた月について `confirmed == 0` かつ `departures == 0` で、月を開始から完全観測した場合に発火。issue 値は 確定 0 / 発射 0 / order_start 0 / order_launched 0 で契約どおり。
- 起票閾値（obs≥20、duration≥`MIN_MONTH_SECONDS`=120s、max_gap≤60s、coverage≥0.9）を本件は 1067回 / 1427.7s / 4.0s / started_clean で充足。confidence `high`（reasons 空）も契約どおり。
- fingerprint を現行 `month_audit.py` の `_fingerprint`（L3112-3115: `sha256("chapter|kind|primary")`）で再計算: `sha256("6|zero_sortie|monthly_scan")[:16]` = `4ba82a1c75855bfe` で一致。
- incident_id を `_incident_id`（L3107-3110: `sha256("runtime_id|generation|chapter|month")[:20]`）で再計算: `sha256("g1342-1fe0734c|1342|6|10-9")[:20]` = `8d72ea69aab0b8d05231` で一致。

### 2. #5463 / #5455 型ではない根拠

- #5463（ch1）の機構は `boss_target_locked` / `plan_map_activate` / `castle_jump_open` / `castle_jump_confirm` / `source_not_under_cursor` の activate→hold ループ。本件の決定内訳にはこれらの繰り返しは現れない。
- #5455（ch12）の機構は暫定ボス order を本国発で出撃させる長距離行軍の吸収・野営化。本件の pending はアルマムーン→イカリア（通常城）であり、ボス行軍ではない。
- 世界状態はアルマムーンに 4 名在城で、`no_source`（max staffed ≤ 1）は不成立。合法出撃源ゼロ家族（#5487）とも異なる。

### 3. 月次処理の振る舞い

- `post_month_status.step` の譲渡条件（#4642/#4888）は、当月に成立した出撃がなく開始可能な指示がある場合に `SORTIE_FLOOR_YIELDS=48` までマップを出撃計画へ明け渡し、`SORTIE_FLOOR_IDLE_YIELDS=3` の idle で月次側へ戻す。v1492 はこのロジックを含む。
- 本件の決定表には `sortie_input` → `card_pick` → `sortie_confirm` → `y_jump_open` → `y_jump_unknown_hold` が現れ、Y ジャンプ中の unread 画面で保留が入った。月次 scan が map を保持したのはこの試行が停滞した後の idle reclaim であり、設計どおりである。
- 翌 5 分窓: 決定 86 件 / 出撃 1 件 / 回復 yes / 初出撃 78.1 秒。恒久的な破綻ではなく、月境界後に月次処理が完了して出撃が成立した。

### 4. なぜ (a)・(c)・(d) ではないか

- (a) 実欠陥ではない: #5463/#5455 型の決定列が現れず、v1492 で修正された card_miss 系（cb1e21dc8）とも無関係。月次譲渡ロジックは v1492 に既に含まれており、本件で再発した証拠はない。
- (c) 誤検知ではない: 月内 confirmed/departures/order_start/order_launched がすべて 0 で、監査は契約どおり発火した。
- (d) 判定不能ではない: 決定表・世界状態・コード照合で、月次処理の占有が設計どおりの譲渡/idle-reclaim であることを帰属できる。

### 残件

- Y ジャンプ中の unread hold（`y_jump_unknown_hold`）が sortie の成立を遅らせた可能性があり、月次処理とは独立に unread 画面対策の改善余地がある。ただし本件の primary signal は monthly_scan であり、unread_hold は発火していない。
- 本検証ではコード変更しない。PR 化・CI green 確認は検品ゲート（Muse）が行う。

### 付録: 判定シグネチャ

- kind: `zero_sortie` / signature: `monthly_scan` / signals: `monthly_scan_hold` + `battle_grind` / confidence: `high`
- fingerprint: `sha256("6|zero_sortie|monthly_scan")[:16]` = `4ba82a1c75855bfe`
- incident_id: `sha256("g1342-1fe0734c|1342|6|10-9")[:20]` = `8d72ea69aab0b8d05231`
