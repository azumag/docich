# VERIFY-5463 — [半熟英雄] 月内出撃ゼロ: ch1 月次処理がマップを占有

- 対象: docich#5463（`bug` / `auto-generated` の監査自動起票）/ fingerprint `f29ac36b2bedefde` / occurrence(incident_id) `519c6a1876ad8ecbeb48`
- 判定: `zero_sortie`（confidence `high`）/ signature `monthly_scan`（signals: `monthly_scan_hold` + `battle_grind` + `no_source`）
- ラン: `g1190-ch1v9` / generation `1190` / `hanjuku-chart-v1483-ch12-boss-staging`（v1483）
- 月: ch1 1-5（観測 1178回 / 237.350秒 / max gap 0.7秒 / coverage 0.97 / started_clean）
- created_at: 2026-11-24T17:37:26Z
- 検証ベース: `/home/ubuntu/repos/docich` の `origin/main` = `acda6c36d`（v1486 系。ランの v1483 から +2〜3 版。修正 v1485 = PR #5468 がマージ済みで差分を直接確認できる）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装変更・本番 SSH なし。ラン成果物 `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl` / `hanjuku_frames/` は VM 側のため直接参照不可）
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）

## 結論: **(a) 実欠陥 — 修正済み（v1485 = PR #5468 が本件機構を修正）**

発火自体は監査契約どおり（誤検知ではない）。実欠陥は v1483 の2点:

1. **`_boss_target_unlocked` が probe を免除していなかった**。機械計画の読み取り専用
   roster probe（`source_probe`/`roster_probe`）は移動先をボス城 `けっかい` に置く
   合法形で、実際には出撃せず出撃元の将軍一覧を読んで B,B で戻るだけだが、v1483 では
   `boss_target_locked` で毎観測 hold された（城メニュー段階で `_hold_locked_boss`
   が発火し一覧を読む前に打ち切られた）。
2. **`plan_starved_rearm` がボス step をロック中でも active にした**。通常城未制圧の
   間ボス注文を active 化する skip が v1483 には無く、hold → pending に戻った同じ step
   を次の map 観測で再活性化し、`plan_map_activate` → `castle_jump` →
   `boss_target_locked` の無限ループになった（月内 7 サイクル: `plan_map_activate` 7 /
   `boss_target_locked` 7 / `castle_jump_open` 6 / `castle_jump_confirm` 5 /
   `source_not_under_cursor` 4）。

この2点が城ジャンプ・メニュー開閉・Y ビュー再測定を繰り返させ、月次処理
（post_month 系・month_sub・house_scan）と防衛戦 9 件と合わせてマップを占有
し続けた結果、月内発射 0 となった。本 issue のシグネチャはその下流症状である。

修正は **v1485（commit `8c346ca01`、PR #5468「通常城が未制圧の間は機械ボス計画を
active にしない」）** で main にマージ済み。PR 本文のオーナー実機診断は本ラン
（第1話 fresh ラン、steady）の症状をそのまま記述している（「実機: 10分で
`boss_target_locked` 6回、`plan_map_activate` 6回、通常城への攻撃注文なし」）。

### 1. 発火の正当性: 契約どおり（誤検知ではない）

- 監査契約（`docs/operations/hanjuku-month-audit.md`）: `zero_sortie` は
  confirmed==0 かつ departures==0 で、月を開始から完全に観測した場合に発火。
  issue 値は 確定 0 / 発射 0 / order_start 0 / order_launched 0 / moves 0 で
  契約どおり。
- 起票閾値（obs≥20、duration≥`MIN_MONTH_SECONDS`=120s、max_gap≤60s、
  coverage≥0.9）を本件は 1178回 / 237.35s / 0.7s / 0.97 で全て充足。
  confidence `high`（`reasons` 空）も契約どおり。
- fingerprint を `month_audit.py` の `_fingerprint`（L3105-3107:
  `sha256("chapter|kind|signals")[:16]`）で再計算:
  `sha256("1|zero_sortie|monthly_scan")[:16]` = `f29ac36b2bedefde` で一致。
- incident_id も `_incident_id`（L3100-3102:
  `sha256("runtime_id|generation|chapter|month")[:20]`）で再計算:
  `sha256("g1190-ch1v9|1190|1|1-5")[:20]` = `519c6a1876ad8ecbeb48` で一致。

### 2. 世界状態: なぜ出撃できない局面だったか

- 奪取済み: ナキューメラ(ヴィーナス) / キカンドン(ゼウス) / ジョンリギ(ココット)。
  自城は本国アルマムーン(どうし)を含め4城。失城なし。
- **全城とも在城将軍が1名のみ**（`no_source` シグナルの発火条件そのもの:
  `month_audit.py` L3022-3025、`staffed` の最大が 1 以下）。
- 未制圧の通常城は ゴーメン・スペンソニア・カストーラ の3城。ch1 の
  `sender_min` は 2（`chart.sender_min`、guard_free は ch9 のみ）で、
  `startable` は名指し将軍に `source_last_defender` を返す
  （`chart_adjust.py` L232-235）。どの城からも1名を出せば城が空になるため、
  攻撃・移動の実出撃注文は一本も生成できない局面だった。
- sortie_history は奪取3件のみ（ヴィーナス→ナキューメラ / ゼウス→キカンドン /
  ココット→ジョンリギ、すべて `won`）。`sorties`・`launched_orders` は空で、
  行軍占有・overdue 行軍は存在しない（#5455 との相違点）。

### 3. 占有の実体: probe がボスロックでループ

- 機械計画（`chart_plan.source='mechanical'`）の pending は2件のみ:
  - `A:6320e426:M22715a` — move ナキューメラ→けっかい、note
    「暫定: 帰還の到着確認としてナキューメラの将軍一覧を読む」。`replan.py`
    L1887-1891 の `due_orders` probe（`source_probe`+`roster_probe`）で、
    移動先は「どの章の自城からでも合法」なボス城ラベルを置いただけの
    読み取り専用注文（発射しない）。
  - `A:6320e426:M6075a6` — attack アルマムーン→キカンドン、note
    「暫定: アルマムーンの将軍一覧を読んで増援の到着を確認する」。
    `replan.py` L1972-1974 の単独在城城 probe 変換（`roster_probe`）。
- 決定ログの繰り返し: `plan_map_activate`(M22715a) → `castle_jump_open`/`confirm`
  （ナキューメラ）→ castle_menu で `boss_target_locked` → pending へ戻る →
  次の map 観測で再活性化。v1483 には orchestrator.py:662-675 の
  `boss_locked` skip と、orders.py `_boss_target_unlocked` の probe 免除
  （現在の L688-693）が無かった。
- probe の一覧読みは castle_menu 段階の hold で打ち切られるため完了せず、
  `roster_read_due` は消えず、同じ step が mint され続ける構造
  （replan.py L1865-1868 コメントどおり due は complete read でのみ pop）。
- 月次処理の占有: `post_month_status_close_probe` 4 /
  `post_month_location_confirmed` 3 / `post_month_roster_marker_mapped` 3 /
  `month_blind_nav` 4 / `house_scan_started`・`buy` 5。`monthly_scan_hold`
  シグナルは契約どおり発火（L3004-3006）。
- 戦闘: `battles.decisions` 85、stats `battles_started` 9 / `battles_judged` 9 /
  8勝1敗（防衛戦）。`battle_grind`（battle_count≥20）契約どおり発火。
- 翌5分: 136決定 / 出撃0 / recovered no（early seal）。ループは月を跨いでも
  続いていた。

### 4. 実欠陥と main の修正

PR #5468（v1485、`8c346ca01`）の2変更が本件機構の両半分をそのまま直す:

- `orchestrator.plan_starved_rearm`: 章内の通常城がすべて自城になるまで
  （`_normal <= owned`）ボス step を active 化しない —「chapter 1 loop」と
  コメントが明記する本件と同じ症状。
- `orders._boss_target_unlocked`: `source_probe`/`roster_probe` の注文は
  ボスロックの対象外とする（読み取り専用 probe は出撃しないため） —
  「chapter 1: the probe was held as boss_target_locked every observation」と
  コメントが明記する本件そのもの。

さらに、実出撃が不可能な局面そのもの（全自城1名体制）についても:

- probe が一覧を読み終えれば増援の到着を検出して計画が再構成される設計であり、
  ループを止めれば少なくとも名簿確認系の読みは進む。1名制約そのものは
  `sortie_held_last_castle`（deploy.py L1820-1845）と `sender_min` が意図的に
  守る仕様（全城空にして落城・ゲームオーバーを防ぐ）で、こちらは (a) の対象外。
- よって本件の生起機構は v1485 で main にカバー済み: 通常城未制圧の間は
  ボス step が活性化されず、probe はロックされずに一覧を読み終えられる。

### 5. なぜ (b)・(d) ではないか

- (b) 設計どおり・既存修正でカバー: 占有の各要素（probe の一覧読み、城ジャンプ、
  月次サブ処理、防衛戦）は個別には設計どおりだが、「読み取り専用 probe が
  ボスロックで毎観測打ち切られ再活性化する」挙動自体がオーナーが欠陥と
  診断し修正したもの。(b) ではなく **(a) 実欠陥 — 修正済み**。
- (d) 判定不能: #5391・#5351 が (d) だったのは集計値だけでは占有機構を
  一意に帰属できなかったため。本件は決定ログの step・source・target が
  activate→hold ループを直接示し、修正 PR #5468 のオーナー実機診断が本ランの
  症状をそのまま記述しているため帰属が成立する。

### 6. #5455・#5391・#5351 系との異同

- **#5455（ch12）**: 同一ラン `g1190-ch1v9` の別章（第12話、v1468）。機構は
  「暫定ボス order を本国発で出撃 → 長距離行軍が吸収・野営化 → 野営帰還が
  月次キューを飽和」で、v1483（PR #5451）で修正済み。章・fingerprint
  （`bbf112c182d10da1`）・機構とも異なり重複ではない。共通するのは「月次処理の
  占有は出撃を塞いだ欠陥の下流症状」という形だけ。
- **#5391（ch4）・#5351（ch3）**: いずれも (d) 判定不能。本件は機構が
  incident JSON とコード照合で確定するため別系統。
- 同一家族: #5468 が直した「probe のボスロック + ボス step 活性化」の
  chapter-1 ループは本件の実機再現であり、v1485 で防止される。

### 残件

- v1485 は PR #5468 時点で本番デプロイ未実施と記載があり、本番ランでの
  実測効果は次ラン以降の観測に委ねられる。
- 全自城1名体制で未制圧通常城が3城残る局面では、probe の完了と増援到着を
  待つ以外に出撃可能な行動がない。仮にループが無くてもこの月の発射は
  限られていた可能性があるが、監査が検出するべき「probe が読み終わらず
  マップを占有し続けた」実害は実際に発生しており、issue の因果記述は
  事実として正しい。

### 付録: 判定シグネチャ

- kind: `zero_sortie` / signature: `monthly_scan` / signals:
  `monthly_scan_hold` + `battle_grind` + `no_source` / confidence: `high`
- fingerprint: `sha256("1|zero_sortie|monthly_scan")[:16]` = `f29ac36b2bedefde`
  （`month_audit.py` `_fingerprint` L3105-3107、再計算一致）
- incident_id: `sha256("g1190-ch1v9|1190|1|1-5")[:20]` = `519c6a1876ad8ecbeb48`
  （`_incident_id` L3100-3102、再計算一致）
