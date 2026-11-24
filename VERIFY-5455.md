# VERIFY-5455 — [半熟英雄] 月内出撃ゼロ: ch12 月次処理がマップを占有

- 対象: docich#5455（`bug` / `auto-generated` の監査自動起票）/ fingerprint `bbf112c182d10da1` / occurrence(incident_id) `84f8276679d0bc48ae6b`
- 判定: `zero_sortie`（confidence `high`）/ signature `monthly_scan`（シグナル: 月次処理がマップを占有 / 野営帰還を繰り返す / 戦闘が長引く）
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1468-battle-menu-bounded-episode`（コミット `00c2b6503`）
- 月: ch12 22-12（観測 1439回 / 217.675秒 / max gap 0.8秒 / coverage 0.999 / started_clean）
- created_at: 2026-11-24T16:56:52Z
- 検証ベース: `/home/ubuntu/repos/docich` の `origin/main` = `54e6c11c3`（**v1484**。ランの v1468 から +16。関連修正 v1477（#5442）・v1481（#5450）・v1483（#5451）がすべてマージ済みで、その差分を直接確認できる）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装変更・本番 SSH なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照不可）
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）

## 結論: **(a) 実欠陥 — 修正済み（v1483 = PR #5451 が本件機構を修正）**

発火自体は監査契約どおり（誤検知ではない）。実欠陥はシグネチャの下層にあり、
**v1468 の計画層が第12話の暫定ボス order を本国アルマムーン発で出撃させ続けた**
（速攻チャート廃止後の interim planner が、チャート記載の「12-A3 主人公をディランダへ
拠点集結 → 12-B1 ディランダ発ボス」を使っていなかった）点にある。
アルマムーン発の長距離ボス行軍は途中で吸収・敗北・野営化し（オーナー診断済み、
PR #5451 本文）、行軍枠と将軍を占有したまま野営を累積させた。その野営帰還タスクが
月次キューを飽和させ、防衛戦・行軍戦闘と合わせてマップを占有し続けた結果、
月内発射 0 となった。本 issue の `monthly_scan_hold` シグネチャはその下流症状である。

修正は **v1483（commit `a4af58faa`、PR #5451「第12話はボス近くの拠点へ主人公を
先に進めてから出撃する」）** で main にマージ済み。既存野営の解消側は
v1477（#5442 = `911ffbb55`）＋ v1481（#5450 = `4f7d93e59`）の boss_camp_recall
機構が既にカバーしている。

### 1. 発火の正当性: 契約どおり（誤検知ではない）

- 監査契約（`docs/operations/hanjuku-month-audit.md`）: `zero_sortie` は
  confirmed==0 かつ departures==0 で、月を開始から完全に観測した場合に発火。
  issue 値は 確定 0 / 発射 0 / order_start 0 / order_launched 0 で契約どおり。
- issue 起票閾値（obs≥20、duration≥`MIN_MONTH_SECONDS`=120s、max_gap≤60s、
  coverage≥0.9）を本件は 1439回 / 217.675s / 0.8s / 0.999 で全て充足。
  confidence `high` の条件（完全観測＋出撃機会の存在）も満たす。
- fingerprint を `month_audit.py` の `_fingerprint`（L3105-3108:
  `sha256("chapter|kind|signals")[:16]`）で再計算:
  `sha256("12|zero_sortie|monthly_scan")[:16]` = `bbf112c182d10da1` で一致。
  signature `monthly_scan` は `PRIMARY_SIGNALS`（L253-281）の先勝ち順で妥当。
- incident_id も `_incident_id`（L3100-3102:
  `sha256("runtime_id|generation|chapter|month")[:20]`）で再計算:
  `sha256("g1190-ch1v9|1190|12|22-12")[:20]` = `84f8276679d0bc48ae6b` で一致。

### 2. シグネチャの内訳: 占有の実体

`_signals`（`month_audit.py` L2990-3039）の定義と月内決定 419件の内訳:

- `monthly_scan_hold`（月次処理がマップを占有）: `post_month_status` 系・
  `close_probe`・`month_sub_step`・flow フラグ。本件は post_month ~110件
  （status_close_probe 18 / task_result 12 / roster_marker_mapped 13 /
  location_confirmed 6 / recall_requeued 8）＋ month_sub ~82件。
- `camp_recall_loop`（野営帰還を繰り返す）: camp 系決定 ≥10 → 本件 ~70件
  （camp_enter 9 / camp_fort_sortie 9 / camp_recall_dispatched 9 /
  input_sent 10 / requested 7 / status_recall_pending 5 / recall_requeued 8）。
- `battle_grind`（戦闘が長引く）: battle 決定 ≥20 → 本件 ~123件
  （okunote_select 33 / summon_preempt 21 / melee 15 ほか）。
- `post_month_sortie_floor`（明け渡し）は一度も発火していない
  （yield も `post_month_sortie_floor_reclaimed` も無し）→ 明け渡し可能な
  stage＋map の隙間で `next_order` が None だった、あるいはその隙間自体が
  無かった（§4）。
- 月をまたいだ直後（翌5分）: 決定 799件 / 出撃 3件 / 回復 yes / 初出撃まで
  178.1秒。帰還・整備の継続のあと出撃が再開しており、監査が想定する
  「一時的占有 → 回復」には合致する。

### 3. 帰属: なぜ (d) でなく (a) か

#5391・#5351・#5339 が (d) 判定不能だったのは、集計値だけでは占有の機構を
一意に帰属できなかったため。本件は incident JSON の `sortie_history`・
`sorties`・`garrison` が機構を直接示し、さらに修正 PR のオーナー診断が
本ランの症状をそのまま記述しているため帰属が成立する。

- `sortie_history`: **`attack→ボス` は全5件すべて outcome `lost`**
  （ポセイドン 29775t前 / カペリーニ 28379t / ベーグル 24035t /
  ミルフィーユ 13053t / ドリアン 9961t）。対して非ボス攻撃は全て `won`
  （アルファルファ→ファンゴル / マラスキーノ→ヘリオポリス /
  グリッシーニ→アファニア）。ボス行軍だけが系統的に失われている。
- `sorties`（月末時点の行軍中）: 5件すべて出撃元=アルマムーン、年齢
  1254–1329 tick で全件 overdue 相当（4件 `attack→ボス`: カペリーニ /
  タピオカ / アルテミス / タルタル、1件 `move→ディランダ`: ラズベリー）。
  月内先頭でも行軍中ボス行軍 `A:5f77e647:M11247b` が敵突入で戦闘開始
  （16:51:40 `battle_start`）しており、行軍が途中で吸収されている実態が
  決定ログに残る。
- `garrison`: アルマムーンに将軍10名が滞留。奪取済みの拠点城ディランダには
  1名のみ → チャートが想定する拠点集結が進んでいない。
- 野営の累積: ポセイドンは #5431 により 19-8 以来 3ヶ月以上の野営。
  バランタインの `retreat_recheck` 未確認など帰還未収束も残る。
- 月次キューの飽和: 野営帰還タスクが dispatch 9 / requeue 8。requeue は
  `RECALL_INTERRUPT_GRACE`（240tick、post_month_status.py L2484-2503）内の
  戦闘中断なら `RECALL_TASK_RETRIES`=2 の予算を消費しない設計で、
  戦闘密度が高い月では帰還タスクが実質尽きない。

これらが繋がった機構: **暫定ボス order を本国アルマムーンから出撃 →
長距離行軍が途中で吸収・敗北・野営化 → 行軍枠を overdue のまま占有し
将軍は野営へ → 野営帰還タスクが月次キューを飽和させ、守備戦・行軍戦闘と
合わせてマップを占有し続ける → 出撃可能な隙間が無く月内発射 0**。

### 4. 実欠陥と main の修正

- v1468 の計画層: `chart_plan` は暫定 `attack→ボス` order をアルマムーン発で
  保持（ココット M771577 / ヘーゼル M9913fa / ブラウニー Mb24c88 が
  provisional。launched_orders では M9913fa→タピオカ、Mb24c88→アルテミスと
  別将軍へ割り当て直し = 計画将軍は利用不能だった）。
- PR #5451 本文のオーナー診断が本ランの症状をそのまま記述:
  「12話はアルマムーンから直接ボスを目指すと障害物に当たってキャンプになる」
  「ボス order がすべて暫定(アルマムーン等が出撃元)になり、boss_sortie_overdue
  とキャンプが続いていた」。
- 修正 v1483（`a4af58faa`、PR #5451）の内容:
  - `hanjuku_chart.boss_staging(chapter)`: 拠点（12-B1 の出撃元）と主人公の
    中継経路（12-A3 を逆にたどる）をチャートから導出。城名ハードコードなし。
  - `strategy/routes.py`: 拠点集結の状態機械 `boss_staging`
    （gathering → ready。拠点将軍一覧に主人公が実読みで載ったときだけ ready。
    ready 後は拠点だけがボス出撃を出す）。
  - `strategy/replan.py`: 主人公が中継を1城ずつ進め（同伴者付き）、
    他の将軍が追従。集結済み将軍を他生成器が引き戻さない。
  - `strategy/orders.py`: `_ready` と `_relaunch_overdue_boss` で
    `staging_blocks` により集結中・拠点以外発のボス出撃・出し直しを抑止。
  - 有界フォールバック: 拠点/中継城の喪失、ready 一覧の反復未確認、
    6000tick 進捗なし、開始から 24000tick で `boss_staging_abandoned` を記録して
    従来挙動に戻す（空 action の永続保留なし）。
- 既存野営の解消側は v1477（#5442、免除月数カウント消失＋古い退却再確認行の
  修正）＋ v1481（#5450、月次帰還hint配置＋goal未解決フォールバック）の
  boss_camp_recall 機構がカバー。v1483 はその機構を複製せず再利用する。
- よって本件の生起機構は、将来分の防止（v1483: アルマムーン発ボス行軍の抑止
  → 新規野営の供給遮断）と既存野営の解消（v1477/v1481）の両面で
  main にカバー済み。

### 5. なぜ (b) ではないか

個々の占有要素（防衛戦、行軍戦闘、野営帰還タスク、名簿・買い物等の有界
サブ処理、close_probe 再試行、interrupt grace requeue）はそれぞれ設計どおりで、
明け渡し機構（post_month_status.py L1790-1841、`SORTIE_FLOOR_YIELDS`=48 /
`SORTIE_FLOOR_IDLE_YIELDS`=3）も仕様どおり動いた（startable order が無い間、
または yieldable な隙間が無い間は発火しない）。

ただし占有の原因（野営の累積と overdue 行軍枠の占有）は実欠陥の産物であり、
「月次処理の占有」は欠陥の下流症状であって設計所望の定常状態ではない。
欠陥そのものは main で修正済みのため **(a) 実欠陥 — 修正済み** とする。

### 6. #5391・#5351 系との異同

- #5339・#5351・#5391: いずれも **(d) 判定不能**。集計値では占有の機構を
  一意に帰属できず、確定に必要な run 成果物（per-observation の flow.stage /
  next_order 準備状態など）を列挙して閉じた。
- 本件: incident JSON の `sortie_history`（ボス行軍全滅 vs 非ボス全勝）、
  `sorties`（アルマムーン発 overdue 行軍5件）、`garrison`（アルマムーン滞留10 /
  ディランダ1）が機構を直接示し、修正 PR #5451 のオーナー診断が本ランの症状を
  明記しているため帰属が成立 → (d) ではなく (a)。
- 同型・同一家族:
  - #5448（前月 21-12 `boss_month_zero_sortie`、同一ラン。signals も
    monthly_scan_hold + camp_recall_loop + battle_grind（+stuck_march）。
    22-3 への再発コメントあり）— 同一機構のボスフェーズ版。
  - #5431（ポセイドン `camp_abandoned` 3ヶ月、同一ラン）— 本件の野営累積の一端。

### 残件

- v1483 は本番ランでの実測が未確認（PR 自身が「ディランダからのボス突入、
  camp_found が出ないことは未確認」と明記）。効果の実測は次ラン以降の
  観測に委ねられる。
- 帰還到着未確認の別系統欠陥（#5452 で (a) 実欠陥・未修正と検証済み:
  戦闘中断後の dest-stage いどうモード喪失 → view モードの全体マップへ
  帰還ディスパッチ）は本件の野営帰還 churn にも寄与しうるが、v1483 の
  カバー範囲外で残存する。
- 本件の月内占有そのものには bound 違反はない（占有期の長さは設計内）。
  監査が検出するべき実害（出撃ゼロ月）が実際に発生しており、issue の
  因果記述「月次処理がマップを占有し出撃が無かった経緯」は事実として正しい。

### 付録: 判定シグネチャ

- kind: `zero_sortie` / signature: `monthly_scan` / signals:
  `monthly_scan_hold` + `camp_recall_loop` + `battle_grind` / confidence: `high`
- fingerprint: `sha256("12|zero_sortie|monthly_scan")[:16]` = `bbf112c182d10da1`
  （`month_audit.py` `_fingerprint` L3105-3108、再計算一致）
- incident_id: `sha256("g1190-ch1v9|1190|12|22-12")[:20]` = `84f8276679d0bc48ae6b`
  （`_incident_id` L3100-3102、再計算一致）
