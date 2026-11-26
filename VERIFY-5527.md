# VERIFY-5527 — [半熟英雄] 月内出撃ゼロ: ch6 出撃開始後に発射されない

## 対象

| 項目 | 値 |
|---|---|
| Issue | docich#5527「[半熟英雄] 月内出撃ゼロ: ch6 出撃開始後に発射されない」 |
| 判定 | `zero_sortie` (confidence: high)、signature `departure_failure`（月次処理占有 `monthly_scan_hold` を副次シグナルとして併発） |
| 章 / 月 | ch6 / 月 6-8 |
| run | g1190-ch1v9 / generation 1190 / `hanjuku-chart-v1496-card-open-limit` |
| fingerprint | `986141b2fd81018d` |
| occurrence (incident_id) | `6c9b37d37490230fa43a` |
| 観測 | 1123回 / 243.6秒 / max gap 0.7秒 / started_clean |
| 出撃 | 確定 0 / 発射 0 / `order_start` 22 / `order_launched` 0 |

## 調査基盤

- リポジトリ `azumag/docich`、worktree `/home/hatch/work/wt/docich/devincli-5525`、HEAD `2fe1b66f8`（`BOT_VERSION='hanjuku-chart-v1497-boss-audit-live-sortie-defer'`、v1496 コミット `3871e3e9e` を祖先として含む）。
- read-only 検証。本ファイルの作成以外のコード変更なし。実装変更・本番 SSH なし。
- ラン成果物（`hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/`）は VM 側のため直接参照できず、issue 埋め込みの決定表・世界状態・現行コードを照合した。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）。
- 検証に用いたハッシュ再計算: `sha256("6|zero_sortie|departure_failure")[:16]` = `986141b2fd81018d`（一致）、`sha256("g1190-ch1v9|1190|6|6-8")[:20]` = `6c9b37d37490230fa43a`（一致）。

## 結論: **(b) 設計どおり —— 月次処理と戦闘が月を占有し、22 回の `order_start` はいずれも発射を伴わない読み取り/検証の開始だった**

`order_start` 22 回は発射を目的とした指示ではなく、月次処理（`post_month_status` / `month_sub_*`）と戦闘が月を占有する中で、計画が発行した読み取り専用の probe / 検証指示が開始→完了（`order_done`）を繰り返した痕跡である。発射ゼロは `sender_min` 制約や `no_source` ではなく、月次処理占有による設計どおりの挙動であり、翌 5 分窓で 18.1 秒で回復している。#5324 型のデッドロック（probe 対象選定の構造的欠陥）は本件では観測されない。

### 結論の根拠（要約）

1. **発射不可能な状態ではなかった**: 月末時点で自城は複数将軍を擁する（アルマムーン 9 名など）。`sender_min`=2（`hanjuku_chart.py:1303-1305`、`guard_free` は ch9 のみ `:1308-1318`）に対し `garrison` は充足しており、`_source_spare_missing`（`strategy/orders.py:2414-2418`）で全出撃元が遮断される状態ではない。`no_source` シグナルも発火していない。#5332 型の「城に 1 将軍しかいない」機構は本件に当てはまらない。
2. **月を占有したのは月次処理と戦闘**: `monthly_scan_hold` シグナルが発火（`month_sub_step`=74、`month_sub_open`=11、`post_month_status_close_probe`=29）。`post_month_status` が地図操作をブロックする経路（`flows/map.py:2395-2422`）が月内に繰り返し発火し、出撃の発射機会を奪った。戦闘も 350 件開始（勝 291 / 敗 48）で月内を消費したが、`battle_grind` 閾値（draw_hold≥10 または battles≥20）は `battle_draw_hold`=5 で未達、署名は `departure_failure` が優先された。
3. **22 回の `order_start` は読み取り/検証の開始**: `order_done`=22 と一致し、`order_launched`=0 / `sortie_departure_requested`=0 / `order_failed`=0。`_finish_order(mem, 'done')` が呼ばれる経路（`deploy.py:1185/1228/1472/2427`）は読み取り専用 probe / 検証 / ch9 の一覧判定であり、発射を伴わない。`roster_probe_complete`=22（issue の before 集計）も probe 完走を示し、#5324 型の probe デッドロック（対象選定の構造的欠陥）は存在しない。
4. **翌月に自然回復**: after 区間 355 決定 / 出撃 1 件 / 回復 yes / 初出撃 18.1 秒。月境界を越えれば発射が成立するため、月内のゼロは恒久的破綻ではなく占有による設計どおりの挙動である。
5. **#5525 の top-30 切り捨ては本件に影響しない**: #5507 では `order_start`=1（低頻度）が切り捨てられて集計矛盾を生んだが、本件の `order_start`=22 は高頻度で切り捨て対象外。`monthly_scan_hold` も `month_sub_step`=74 で高頻度。また `confirmed`（launches）は `month_sorties` から独立に算出され 0 で、切り捨てとは無関係に発射ゼロが確定している。シグネチャ・指紋は影響を受けない。

### 1. 発火の正当性とハッシュ照合

- `zero_sortie` は閉じた月について `confirmed==0` かつ `departures==0` で、月を開始から完全観測した場合に発火。issue 値は 確定 0 / 発射 0 で契約どおり。
- 起票閾値（obs≥20、duration≥`MIN_MONTH_SECONDS`=120s、max_gap≤60s、coverage≥0.9）を本件は 1123回 / 243.6s / 0.7s / started_clean で充足。confidence `high` も契約どおり。
- fingerprint を現行 `_fingerprint`（`month_audit.py` L3112-3115: `sha256("chapter|kind|primary")`）で再計算: `sha256("6|zero_sortie|departure_failure")[:16]` = `986141b2fd81018d` で一致。
- incident_id を `_incident_id`（L3107-3110: `sha256("runtime_id|generation|chapter|month")[:20]`）で再計算: `sha256("g1190-ch1v9|1190|6|6-8")[:20]` = `6c9b37d37490230fa43a` で一致。

### 2. #5324 / #5332 / #5507 との異同

- **#5507（ch6 10-9, fp 4ba82a1c）**: 同章・別月。`order_start` が実質 1 回で top-30 切り捨ての影響を受け、idle-reclaim 発火の証拠もなく (d) 判定不能となった。本件は `order_start`=22 で切り捨ての影響を受けず、月次処理占有が主因と帰属できる点で異なる。
- **#5332（ch11, fp 81b4920b）**: 同じ `departure_failure` 署名。`order_start`=12 / `order_launched`=0 で (b) 設計どおり（城に 1 将軍しかいない → `_source_spare_missing` で発射遮断、翌月回復）。本件は城に複数将軍がおり `no_source` ではないが、月次処理占有という別経路で同じ「発射ゼロは設計どおり」に帰結する。回復パターンも一致。
- **#5324（ch3, fp 054e5049）**: `order_start`=6 / `order_launched`=0 で (a) 実欠陥（`_defense_gate` の probe 擬似解除バグ + probe 対象選定の #4689 再発）。本件では `defense_wait` / `chart_defense_wait` の記録がなく、probe 完走（`roster_probe_complete`=22）も観測されるため、#5324 型のデッドロックは存在しない。

### 3. v1496 での再発か

- v1496（コミット `3871e3e9e`）は召喚獣戦の切り札オープン回数を `SUMMON_CARD_OPEN_LIMIT`=2 に制限する変更（`flows/battle_menus.py`）。戦闘メニューの無限スクロール防止が目的で、出撃・発射経路には関与しない。
- 本件のシグナルは `order_start_without_launch` + `monthly_scan_hold` であり、`battle_grind` は閾値未達で発火していない。v1496 が本件の departure failure を引き起こした証拠はない。
- 月次処理占有（`monthly_scan_hold`）は v1496 以前から観測される既存パターンであり、v1496 での「再発」とは言えない。v1496 は本件の原因ではない。

### 4. なぜ (a)・(c)・(d) ではないか

- **(a) 実欠陥ではない**: #5324 型の `defense_wait` probe 擬似解除や probe 対象選定の構造的欠陥は本件では観測されない。`roster_probe_complete`=22 が probe の正常完走を示す。月次処理占有は `post_month_status` の設計どおりの挙動である。
- **(c) 誤検知ではない**: `confirmed`=0 は `month_sorties` から独立算出され、発射ゼロが事実である。監査契約どおり発火。
- **(d) 判定不能ではない**: #5507 と異なり `order_start`=22 は高頻度で切り捨ての影響を受けず、月次処理占有が主因として帰属できる。VM 原本がなくても結論を支持する証拠が揃っている。

### 付録: 判定シグネチャ

- kind: `zero_sortie` / signature: `departure_failure` / confidence: `high`
- fingerprint: `sha256("6|zero_sortie|departure_failure")[:16]` = `986141b2fd81018d`
- incident_id: `sha256("g1190-ch1v9|1190|6|6-8")[:20]` = `6c9b37d37490230fa43a`
- signals: `order_start_without_launch`, `monthly_scan_hold`
