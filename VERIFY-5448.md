# VERIFY-5448 — [半熟英雄] ボスフェーズで月内ボス出撃ゼロ: ch12 クーモン

- 対象: docich#5448（`bug` / `auto-generated` の監査自動起票）/ fingerprint `a3452e7bc09c8aab` / occurrence(incident_id) `b160ace21d37bd5cd262`
- 判定: `boss_month_zero_sortie`（confidence `high`）/ signature `boss_month_zero_sortie`（signals: `boss_month_zero_sortie` + `monthly_scan_hold` + `camp_recall_loop` + `battle_grind` + `stuck_march`）
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1468-battle-menu-bounded-episode`
- 月: ch12 21-12（観測 1604回 / 235.390秒 / max gap 0.657秒 / coverage 0.998 / started_clean）
- 再発コメント: ch12 22-3 / occurrence `b875ff8e5e392504e5a0`（観測 977回 / 136.3秒 / max gap 0.8秒、確定 0 / 発射 5 / order_start 6、ボス出撃 0）
- created_at: 2026-11-24T16:13:39Z
- 検証ベース: `/home/ubuntu/repos/docich` の `origin/main` = `0d20d07c3`（**v1489**。ランの v1468 から +21。関連修正 v1477（#5442）・v1481（#5450）・v1483（#5451）がすべてマージ済みで、その差分を直接確認できる）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装変更・本番 SSH なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照不可）
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）

## 結論: **(a) 実欠陥 — 修正済み（v1483 = PR #5451 が本件機構を修正）**

発火自体は監査契約どおり（誤検知ではない）。実欠陥は **#5455 と同一**:
v1468 の計画層が第12話の暫定ボス order を本国アルマムーン発で出撃させ続け、
長距離ボス行軍が途中で吸収・敗北・野営化して行軍枠と将軍を占有、野営帰還
タスクが月次キューを飽和させてマップを占有し続けた結果、月内ボス出撃 0
（この月は全出撃も 0）となった。本 issue はその機構の **ボスフェーズ版** で、
VERIFY-5455 §6 が本件を「同一機構のボスフェーズ版」として事前に名指しして
いる。修正 v1483（commit `a4af58faa`、PR #5451「第12話はボス近くの拠点へ
主人公を先に進めてから出撃する」）は ch12 のボス出撃経路そのものを直すため
本件もカバーする。

### 1. 発火の正当性: 契約どおり（誤検知ではない）

- 監査契約（`docs/operations/hanjuku-month-audit.md`「ボスフェーズで月内ボス
  出撃ゼロ（v1410）」）: 月を閉じた時点で `_boss_phase` が成立し（通常城を
  すべて確保しボス城が残る）、その月のボス向け出撃が 0 なら
  `boss_month_zero_sortie` を保存する。全出撃 0 の月でも汎用 `zero_sortie`
  より本 kind を優先する（`month_audit.py` L3465-3467 が L3468 より先に
  評価される）。
- issue 値: `_boss_phase` 成立（奪取10城＋本国アルマムーン、残り=ボス）・
  `boss_month.departures` 0 / `launch_events` 0・sorties 全項目 0 で契約どおり。
- confidence `high` の条件（`started_clean`、章またぎ・月ジャンプなし、
  ledger_ok、max_gap≤`MAX_GAP_SECONDS`、duration≥`MIN_MONTH_SECONDS`=120s、
  obs≥`MIN_OBSERVATIONS`、coverage≥0.9、context 有り）を本件は
  1604回 / 235.390s / 0.657s / 0.998 で全て充足（`confidence_reasons` 空）。
- fingerprint を `_fingerprint`（`month_audit.py` L3105-3107:
  `sha256("chapter|kind|primary")[:16]`）で再計算:
  `sha256("12|boss_month_zero_sortie|boss_month_zero_sortie")[:16]` =
  `a3452e7bc09c8aab` で一致。signature は `PRIMARY_SIGNALS`（L253-281）の
  先勝ち順で `boss_month_zero_sortie` が妥当（`_build_incident` L3145-3147 が
  kind 名を signals 先頭へ固定する設計とも一致）。
- incident_id を `_incident_id`（L3100-3102:
  `sha256("runtime_id|generation|chapter|month")[:20]`）で再計算:
  `sha256("g1190-ch1v9|1190|12|21-12")[:20]` = `b160ace21d37bd5cd262` で一致。
- 再発コメントの incident_id も同式で再計算:
  `sha256("g1190-ch1v9|1190|12|22-3")[:20]` = `b875ff8e5e392504e5a0` で一致。
  同 fingerprint のため新 issue ではなくコメントへ集約される挙動も契約どおり。

### 2. 帰属: 占有の実体は #5455 と同一機構

incident JSON が機構を直接示す（#5339 が (d) だった「集計値だけ」の状況ではない）:

- `plan.chart_plan.orders`: 暫定 `attack→ボス` order 3件、**全件 source=アルマムーン**
  （ココット M771577 / レモン M245d80 / ブラウニー Mb24c88、note「暫定: …でボスを攻撃」）。
- `world.launched_orders` / `world.sorties`: 行軍中8件、**全件 source=アルマムーン**。
  ボス向け5件（ランプータン・セルリアク・パプリカ・カペリーニ・ブリー）、
  move2件（ジキタリス→ディランダ launched_unconfirmed、ビーツ→ファンゴル）。
  `A:5f77e647:M09af94`（アテナ attack→ボス）は 3180tick で `overdue`
  → `stuck_march` シグナルの実体。計画将軍と発射将軍が別名に割り替わる
  パターンも #5455 と同じ（計画のココット→発射はカペリーニ 等）。
- `world.sortie_history`: 直近の `attack→ボス` は全3件 outcome `lost`
  （ポセイドン 12057tick前 / カペリーニ 10661t / ベーグル 6317t）。
  非ボス攻撃は全て `won` → ボス行軍だけが系統的に失われる。
- `world.garrison`: アルマムーンに将軍8名滞留、拠点城ディランダは1名のみ
  → チャートが想定する拠点集結（12-A3/12-B1）が進んでいない。
- 月内決定 492件（coverage 0.998）: post_month 系 ~110件（close_probe 30 /
  roster_marker 22+10 / location_confirmed 18 / task_result 13 /
  recall_requeued 9）＋ month_sub ~50件で `monthly_scan_hold`、
  camp 系 ~50件超で `camp_recall_loop`（`camp` カウント ≥10 の閾値）、
  battle 系 ~140件で `battle_grind`（≥20 の閾値）。
- 翌5分: 決定 912件 / 出撃 6件 / 回復 yes / 初出撃まで 82.2秒 → 監査の想定する
  「一時的占有 → 月またぎ後に回復」に合致（恒久的デッドロックではない）。

機構: **暫定ボス order を本国アルマムーンから出撃 → 長距離行軍が途中で
吸収・敗北・野営化 → overdue のまま行軍枠を占有し将軍は野営へ → 野営帰還
タスクが月次キューを飽和、守備戦・行軍戦闘と合わせてマップを占有 →
ボス出撃可能な隙間が無く月内ボス出撃 0**。VERIFY-5455 と同じ連鎖で、
本件はそれがボスフェーズ（`_boss_phase` 成立）中の月に現れた形。

### 3. 実欠陥と main の修正

- PR #5451 本文のオーナー診断が本ランの症状をそのまま記述（「12話は
  アルマムーンから直接ボスを目指すと障害物に当たってキャンプになる」
  「ボス order がすべて暫定(アルマムーン等が出撃元)になり、
  boss_sortie_overdue とキャンプが続いていた」）。
- 修正 v1483（`a4af58faa`、PR #5451）が main 在籍を確認済み:
  - `hanjuku_chart.boss_staging(chapter)`（`hanjuku_chart.py` L1309-1333）:
    拠点（ボス order の source）と主人公の中継経路をチャート記載から導出。
    城名ハードコードなし。ch12 は STAGING_CHAPTERS 対象。
  - `routes.staging_arm` / `staging_update`（`strategy/routes.py` L313-390）:
    gathering → ready の状態機械。拠点将軍一覧に主人公を実読み確認した
    ときだけ ready。喪失・反復未確認・`STAGING_MAX_TICKS` 停滞・
    `STAGING_MAX_TOTAL` 超過で `boss_staging_abandoned` を記録して従来挙動へ
    戻す有界フォールバック付き。
  - `routes.staging_blocks`（`routes.py` L404-417）: gathering 中は全ボス
    出撃を抑止、ready 後は拠点以外発のボス出撃を抑止（roster_probe は免除）。
    `orders.py` L867（`_ready`）と L1306（overdue ボス出し直し）で適用。
  - `replan.py` L516-536・L1284: 主人公が中継を1城ずつ進め同伴者付き、
    他の将軍が追従。集結済み将軍を他生成器が引き戻さない。
- 既存野営の解消側は v1477（#5442）＋ v1481（#5450、boss_camp_recall の
  hint配置と goal 未解決フォールバック）がカバー。VERIFY-5455 §4 と同じ評価。
- よって本件の生起機構は将来分の防止（v1483: アルマムーン発ボス行軍の抑止
  → 新規野営の供給遮断）と既存野営の解消（v1477/v1481）の両面で
  main にカバー済み。

### 4. なぜ (b)/(c)/(d) ではないか

- (c) 誤検知でない: §1 のとおり契約どおりの発火で、識別子3件も再計算一致。
  実害（ボスフェーズ月にボス出撃ゼロ）は実際に発生した。
- (b) でない: 占有の個々の要素（野営帰還タスク、防衛戦、有界サブ処理）は
  設計どおりだが、占有の原因（暫定ボス order の本国発長距離行軍と野営累積）は
  実欠陥の産物であり、月内ボス出撃ゼロは設計所望の定常状態ではない。
  欠陥は main で修正済みのため (a) 実欠陥・修正済み。
- (d) でない: incident JSON の `chart_plan` / `launched_orders` / `sorties` /
  `sortie_history` / `garrison` が機構を直接示し、修正 PR #5451 のオーナー
  診断が本ランの症状を明記しているため帰属が成立する（#5339 の (d) と違い
  本件は機構の一意帰属が可能）。

### 5. 関連 issue との異同・重複確認

- **#5455**（ch12 22-12 `zero_sortie`、同一ラン v1468、(a) 実欠陥・v1483
  修正済み）: 同一機構の姉妹 issue。22-12 では月閉時に `_boss_phase` が
  不成立だったため汎用 `zero_sortie` 側に出た。本件（21-12 と再発 22-3）は
  `_boss_phase` 成立中のため `boss_month_zero_sortie` 側に出た違いのみ。
- **#5463**（ch1 `zero_sortie`、別ラン v1483、(a) 実欠陥・v1485 修正済み）:
  別章・別 fingerprint・別機構（probe のボスロック抑止）。重複ではない。
- **#5332**（ch11 17-12 `zero_sortie` departure_failure、v1447、(b) 設計
  どおり）: 別章・別 signature・別機構（全自城 sender_min 未満で合法阻害）。
- **#5339**（ch11 18-5 `boss_month_zero_sortie` エッグママ、v1447、(d) 判定
  不能）: 同 kind だが別章・別ボス・別 fingerprint（`b246fc5f40680931`）・
  別ランの別機構。本件は ch12 クーモン・v1468 で、帰属に必要な証拠が揃い
  (a) に辿り着いた。重複ではない。

### 残件

- v1483 は本番ランでの実測が未確認（VERIFY-5455 同様、効果の実測は次ラン
  以降の観測に委ねられる）。
- 帰還到着未確認の別系統欠陥（#5452 で (a)・未修正と検証済み）は本件の
  野営帰還 churn にも寄与しうるが、v1483 のカバー範囲外で残存する。
- 21-12 は全出撃も 0 で `order_start`/`order_launched` も 0 のため、
  `post_month_sortie_floor`（明け渡し）が発火できる startable order + 隙間が
  存在しなかった点は VERIFY-5455 §2/§5 と同じ読み（bound 違反なし、占有期の
  長さは設計内）。

### 付録: 判定シグネチャ

- kind: `boss_month_zero_sortie` / signature: `boss_month_zero_sortie` /
  signals: `boss_month_zero_sortie` + `monthly_scan_hold` + `camp_recall_loop`
  + `battle_grind` + `stuck_march` / confidence: `high`
- fingerprint: `sha256("12|boss_month_zero_sortie|boss_month_zero_sortie")[:16]`
  = `a3452e7bc09c8aab`（`month_audit.py` `_fingerprint` L3105-3107、再計算一致）
- incident_id: `sha256("g1190-ch1v9|1190|12|21-12")[:20]` =
  `b160ace21d37bd5cd262`（`_incident_id` L3100-3102、再計算一致）
- 再発 incident_id: `sha256("g1190-ch1v9|1190|12|22-3")[:20]` =
  `b875ff8e5e392504e5a0`（再計算一致）
