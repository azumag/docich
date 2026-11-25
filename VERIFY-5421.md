# VERIFY-5421 — [半熟英雄] 野営放置: ch4 キッシュ (3ヶ月)

- 対象: docich#5421 / fingerprint `3bd148295593ce20` / occurrence `876cc932ae553d32be64`
- 判定: `camp_abandoned` (confidence high) / シグネチャ「野営放置 / 3ヶ月以上野営」
- ラン: `g1342-1fe0734c` / generation 1342 / `hanjuku-chart-v1467-status-panel-owner-unreadable-egg`
- 月: ch4 8-1（観測 1回 / 0.0秒） / 将軍 キッシュ / 継続 3ヶ月 / 初回 7-11 (tick 55227)
- 検証ベース: `origin/main` = `4f7d93e59`（**v1481**。ランの v1467 から
  v1469（#5424）・v1477（#5442）・v1481（#5450）の3段の関連修正がすべて
  マージ済みで、その差分を直接確認できる）
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装変更・本番 SSH なし。
  ラン成果物 `hanjuku_decisions.jsonl` / frames は VM 側のため直接参照不可）
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読と記録する（AGENTS.md §1）
- 同型: #5418（リーキ）/ #5419（レモン）/ #5420（デュオニソス）— 全件同一
  fingerprint `3bd148295593ce20`・同一ラン・同月。fingerprint は
  `sha256("4|camp_abandoned|camp_abandoned")[:16]`（`month_audit.py:3089`）で
  章×kind 粒度のため、ch4 の camp_abandoned は将軍違いでも同一になる（計算で確認済み）

## 結論: **(a) 実欠陥 — 修正済み**

発火自体は監査契約どおり（誤検知ではない）。実欠陥は解消経路側にあり、
**#5418 と同じ「包囲免除 → 月次 `boss_move`」経路**である。v1469（#5424）が
boss_move の goal 未解決クラッシュを直したが、キッシュの野営が残ったままに
なった直接の残存原因は次の2点で、**v1477（#5442 = `911ffbb55`）＋
v1481（#5450 = `4f7d93e59`）で main にマージ済み**である:

1. **ボス至近野営の免除月数カウント消失**（stale な `field` 行を `arm` が読み
   カウントを破棄 → 毎月 `boss_move` が選ばれ `recall` にならない）
2. **古い退却再確認行が月次タスクの野営メニューを乗っ取る**
   （attempts 使い切りの `retreat_rechecks` 行が匿名再確認へ変換し B で閉じて
   タスクを移動なしに消費）

#5442 の障害記録 `docs/operations/incidents/hanjuku-boss-camp-recall-20261125.md`
は、本 issue と同一のラン `g1342-1fe0734c`・将軍 キッシュ・野営地 四季王城(北東)・
初回 tick 55227（7-11）を実測対象として明示し、#5421 を直接引用している。

### 1. 発火の正当性: 契約どおり（誤検知ではない）

- 監査契約（`docs/operations/hanjuku-month-audit.md`「野営放置（3ヶ月以上連続）」）:
  `general_statuses.statuses` で `location=camp` の将軍を月またぎで数え、
  3ヶ月以上連続なら1回保存する。
- `CAMP_ABANDON_MONTHS = 3`（`month_audit.py:55`）、`_camp_generals`（同:1454）、
  `_camp_age_months`（同:1488）。キッシュは first_tick 55227（7-11）以降、
  7-12・8-1 の2つの月開始をまたいだため `months=1+2=3` で発火。数え方は正しい。
- `reported` フラグ（`_track_camp_abandon`、`month_audit.py:2502`）で
  同一滞在の再発行は抑止される設計。城候補・HP・卵も名簿行の実測値を
  そのまま投影しており、静的雛形ではない。

### 2. 野営の性質: #5418 と同じく「包囲免除 → boss_move」経路

- 城候補は `四季王城(北東)`。ch4 のボスは4つの四季王城に分かれ、
  `boss_sites(4)`（`hanjuku_chart.py:1357` → `SOURCE_BOSS_SITES[4]`、
  `hanjuku_map_sources.py:97`）と `is_boss_castle`（同:1365）がこれを
  ボスサイトとして扱う。
- 世界状態は通常城7城すべて確保済み（奪取6+本国アルマムーン）で、残る敵目標は
  四季王サイトのみ = ch4 ボスフェーズ。
- キッシュは HP 26/26・スーパーエッグ・`broken: false` →
  `_boss_camp_holds_siege`（`post_month_status.py:116`）が成立し、
  `BOSS_CAMP_EXEMPT_MONTHS = 3`（同:77）の包囲免除と `_boss_move_ready`
  （同:135、HP > max/2 で無傷判定）による `boss_move` 起票の対象。
  #5418（リーキ HP30/30・無傷・北東）と同じ型。castle_candidate 無しの
  #5419（判定不能の recall 経路）、broken の #5420（免除外の recall 先頭）
  とは分岐する。

### 3. 実欠陥: 免除カウント消失 + 古い退却再確認の乗っ取り（#5442 で確定）

v1469（#5424）が boss_move の Yジャンプ goal 未解決クラッシュ（`'ボス'` ラベルが
ch4 に存在しない）を直した後も、キッシュの野営は解消しなかった。#5442 の
障害記録が実測した2原因:

1. **免除月数のリセット**: `boss_camp_exempt` は 8-5 に 6 あったが、8-6 の
   `arm` が「前月の戦闘で location が field のまま残った」キャッシュを読み、
   野営なしとしてカウントを破棄。`boss_camp_exempt_scope` が同月のため
   再カウントもされず、3ヶ月を超えても毎月 `boss_move` が選ばれ続けた
   （本来は免除切れで `recall` home になるべき）。
2. **古い退却再確認が帰還タスクを乗っ取る**: `retreat_rechecks` に 7-11 のままの
   キッシュ行が残り attempts 上限済み。`camp_recheck._step_flow` が月次タスクの
   野営メニューを匿名再確認へ変換し、上限到達済みのため B で閉じて
   `boss_move` タスクを消費（実測 2026-11-24 23:41 JST:
   `camp_enter` → メニュー → A → B + `retreat_recheck_unconfirmed` →
   `post_month_task_result {"task":"boss_move"}`、移動なし）。

本 incident の `flow` 投影はこの機構と整合する:
`recall: {stage: 'to_camp', steps: 0}`（帰還が開始されるが進まない）、
`recall_retry: {kind: 'dome'}`、`retreat_anonymous_budget: {reads: 2}`
（匿名再確認予算が消費されている）、直前決定の `camp_recall_interrupted`
（月初の場面による帰還先ピッカー中断）。

つまり: 包囲免除の**保持**は設計どおりだが、その解消経路（免除切れ →
`recall` home、または月次 `boss_move` の実移動）が2箇所で壊れており、
野営は「ホールドされている」のではなく「解消手段が壊れて放置された」
状態だった。#5418 と同じ (a) 型で、発火契約は正当・残存原因は別層。

### 4. 修正状況: v1477（#5442）+ v1481（#5450）でマージ済み

- `_count_boss_camps`（`post_month_status.py:312`）: 免除月数を (将軍, 月)
  単位で冪等に数える。stale な読みは野営を追加できても解消はできず、
  削除は完了済み fresh scan のみ（:1435 からスキャン完了時に再カウント）。
  カウント消失時は同 run・同章の退却再確認行の経過月数、および月次監査の
  `camp_watch_hint`（`month_audit.py:2723`、完全な名簿から数えた連続野営
  月数の bounded hint）でシードし、免除窓超なら `recall` home になる。
- 月次タスク帰還 state に `task_recall: True`（`post_month_status.py:2337`）を
  付け、`camp_recheck.release_for_task`（`camp_recheck.py:427`）で同将軍の
  古い再確認行を破棄。`task_recall` の野営メニューは匿名再確認へ変換しない
  （`camp_recheck.py:1025`）。
- v1481（#5450）追補: `_apply_camp_watch_hint`（`brains/hanjuku/bot.py:629`）が
  hint を `state['policy']` 配下へ置く（`decide` は policy のみを渡すため
  v1477 では hint が届いていなかった）。また `task_recall` で `_recall_goal`
  が None のとき章の本城（所有城）へフォールバック（`orders.py`、
  キッシュ 01:11 の「移動なし dispatched」実測への対応）。
- 回帰テスト `tests/test_hanjuku_boss_camp_months.py`・
  `test_hanjuku_boss_camp_remarch.py` は本検証環境で 23 件 pass を確認。
- 関連: v1480（#5453）は第4話の四季王出撃停止（失敗 step 放置・代役移動）を
  別機構として修正。ボス攻撃の進行側の欠陥であり、野営解消機構とは別層。

### 5. 再発コメントの評価（bot_version 不明のため残存経路は断定不可）

issue への再発コメントはいずれも `bot_version` を含まないため、発生時点の
稼働版をコメント単体では確定できない:

| コメント時刻 (JST) | incident_id | 将軍・月 | 状況 |
|---|---|---|---|
| 11-24 22:09 | `2519afe4…` レモン / `207ef50e…` キッシュ / `e261a0ca…` リーキ | ch4 8-1, first 56569, `monthly_scan_hold` 付き | v1469 マージ（23:24 JST）前。v1469 以降の残存経路かは断定不可 |
| 11-25 00:22 | `3e036ace…` キッシュ 8ヶ月 | ch4 8-8, first 61796 | v1469 後・v1477（01:02 JST）前。#5442 が直した「免除消失 + 再確認乗っ取り」の残存経路と整合（監査が数えた8ヶ月は hint の根拠として引用済み） |
| 11-25 01:28 | `6717bce9…` キッシュ 4ヶ月 / `856d7609…` ブカティーニ 4ヶ月 | ch4 8-12, first 64439, `monthly_scan_hold` + 行軍停滞 | v1477 後・v1481（01:48 JST）前。v1481 が修正した hint 未着・goal fallback 欠落の窓内。稼働版不明のため post-v1477 残存かは断定不可 |

いずれも incident_id が本件 `876cc932…` と異なる別発生であり、fingerprint
共有は「同じ章・同じ kind」を意味するだけで、同じ原因・同じ防止策の適用を
意味しない（VERIFY-5420 §5 と同じ整理）。

## 1. issue 提示値 → コード対応（根拠の照合）

| issue の値 | 対応箇所（`origin/main` = `4f7d93e59`） | 検証結果 |
|---|---|---|
| 判定 `camp_abandoned` / kind / `auto_file: true` | `month_audit.py` L1500-1589（L1529-1530 event/kind, L1588 auto_file） | 一致 |
| `confidence: high` / 判定理由 なし | `month_audit.py` L1531-1532（camp_abandoned は high 固定・理由配列空。`_confidence()` L3032 を通らない） | 一致（記録形の特性） |
| 月 ch4 8-1 / 観測 1 回 / 0.0 秒 / max gap 0.0 秒 | `month_audit.py` L1517-1527（`_month_summary` L3059-3081 をそのまま転記） | 一致 |
| 野営 3 ヶ月 / 初回 7-11 (55227) | `CAMP_ABANDON_MONTHS = 3` L55、`_camp_age_months` L1488、`_track_camp_abandon` L2502 | 一致 |
| HP 26 / max 26 / スーパーエッグ / broken false / status_text「キャンプのなかにいます」 | `month_audit.py` L1535-1548（statuses の row を転記） | 一致 |
| castle_candidate `四季王城(北東)` | 同上（`marker.castle_candidate`）＋ `hanjuku_map_sources.py` L97 | 一致。boss site で intact のため免除→boss_move 経路 |
| 出撃 確定0 / 発射0 / order_start 0 / order_launched 0 | `month_audit.py` L1550-1556（context.month_sorties.launches と決定カウント） | 一致 |
| signals `camp_abandoned` / `camp_abandon_3months` | `month_audit.py` L1510-1516（基本 2 個＋`_signals()` L2974、最大 8 個） | 一致 |
| incident_id `876cc932ae553d32be64` | `month_audit.py` L1582-1584: `sha256(f"{runtime_id}|{generation}|camp_abandoned|{general}|{first_tick}")[:20]` | **再計算一致**（`g1342-1fe0734c|1342|camp_abandoned|キッシュ|55227`） |
| fingerprint `3bd148295593ce20` | `month_audit.py` L1586-1587 → `_fingerprint` L3089: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]` | **再計算一致**（`4|camp_abandoned|camp_abandoned`） |
| issue タイトル格式 | `_issue_title` L4085-4121: `[半熟英雄] 野営放置: ch{章} {将軍} ({months}ヶ月)` | 一致 |

監査が起票する incident はラン成果物から事後生成される記録であり、この一致は
「issue の数値が検出ロジックから改変なく来ていること」を証明する。

## 2. 兄弟 issue との異同（fingerprint `3bd148295593ce20`）

| issue | 将軍 | HP/max | 卵 | broken | castle_candidate | 判定 | 経路 |
|---|---|---|---|---|---|---|---|
| #5418 | リーキ | 30/30 | カラフルエッグ | False | 四季王城(北東) | (a) 実欠陥、v1469 で修正済み | 免除→boss_move（goal 未解決クラッシュ） |
| #5419 | レモン | 27/27 | ワンダーエッグ | False | なし | (d) 判定不能 | 免除外の recall 経路 |
| #5420 | デュオニソス | 6/54 | なし | True | 四季王城(北東) | (b) 設計どおり | broken のため免除外・帰還キュー先頭 |
| **#5421** | **キッシュ** | **26/26** | **スーパーエッグ** | **False** | **四季王城(北東)** | **(a) 実欠陥、v1477+v1481 で修正済み** | **免除→boss_move（免除消失＋再確認乗っ取り）** |

#5418 と本件は同じ「intact なボスサイト隣接野営 → 包囲免除 → boss_move」型だが、
実欠陥の層が異なる: #5418 は Yジャンプ goal の解決不能（v1469 で修正）、
本件はその先の「毎月 boss_move が選ばれ続け、野営メニューが古い再確認に
乗っ取られて移動しない」機構（v1477+v1481 で修正）。

## 3. 残件（read-only では確定不能）

- 8-8・8-12 の再発 incident が稼働していた bot_version は、コメントに
  `bot_version` が無いため確定できない。確定にはラン成果物
  `hanjuku_decisions.jsonl` / `hanjuku_events.jsonl`（または diagnostics の
  hot-load marker）が必要。
- #5442 障害記録の「未確認・残件」に同じく、配備後の実機で次の月次タスクが
  `recall` になりキッシュが城へ戻ることは実観測が必要。テスト成功を
  実機成立とは扱わない。
- 免除カウントを失った別将軍が再確認行を持たない場合はシードできず、
  fresh scan の観測から数え直しになる（#5442 記録の残件）。
