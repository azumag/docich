# VERIFY-5522 — [半熟英雄] 野営放置: ch6 ミルフィーユ (3ヶ月)

対象: docich#5522（`bug` / `auto-generated` の監査自動起票）
ラン `g1342-1fe0734c` / generation `1342` /
`hanjuku-chart-v1495-recall-scene-covered-dispatch-audit` / 月 `ch6 11-4`
（観測 1回）
fingerprint `33dd0a166cda78be` / incident_id `13cbd1a04d8b691fad82`
将軍 ミルフィーユ / 野営継続 3ヶ月 / 初回 11-2 (tick 87306) / 最終観測
tick 87515 / HP 39/39 / 卵 None / 壊れ False / 状態「キャンプしています」/
城候補 不明
flow.recall_verification: `stage=await_dispatch / status=arrival_unconfirmed
/ steps=6`

判定: **(d) 判定不能** — ただし #5431 より一段深く切り分けられている。
ch6 には意図的野営の免除機構が存在しないため、ミルフィーユの3ヶ月野営は
**設計上「意図的な配置」ではあり得ない**こと（滞留であること）は確定
できる。確定できないのは滞留の原因が (a) 実欠陥か (b) 設計された有界の
スキップ・再試行が尽きた結果かで、切り分けにはラン成果物
`hanjuku_decisions.jsonl` が必要。`flow.recall_verification` の存在は
「帰還機構が一度も動かなかった」仮説を弱めるが、投影が `general` を落とす
ため本人の試行とは証明できない（#5431 §8 と同じ限界）。

調査基盤: worktree `/home/hatch/work/docich-5522`（detached HEAD
`45c36f2db` = origin/main、BOT_VERSION `hanjuku-chart-v1509-card-at-hp1-
audit`）。read-only 検証: 本番 SSH なし / ラン成果物（`hanjuku_decisions.
jsonl`・`hanjuku_events.jsonl`・`hanjuku_frames/`）は VM 側で直接参照せず /
秘密情報なし / コード変更なし（成果物は本ファイルの追加のみ）。issue 本文は
GitHub 公開ページ経由で取得（`gh` は本環境で拒否されたため不使用）。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため**未読**（AGENTS.md
§1）。fingerprint の sha256 再計算は実行環境が計算コマンドを拒否したため
**未実施**。`_fingerprint`（`month_audit.py` L3312-3314 の式
`sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]`）をコード上確認し、
camp_abandoned の起票呼出が `_fingerprint(chapter, kind, [kind])` 形で
あることから、本件の入力は `6|camp_abandoned|camp_abandoned` に確定する
（入力が同じなら出力も同じ）。incident_id は
`sha256(runtime_id|generation|kind|resources|general|step|int(now))[:20]`
形で、`now` は起票時刻依存のため後追い再計算は原理的に不能（一意性担保
用で照合キーではない）。

---

## 結論

**(d) 判定不能。**

確定できること:

1. **野営は「意図的な配置」ではない。** ch6 には camp を帰還対象から
   外す免除機構が存在しない。
   - `recall.py camp_stays`（L131-138）は `boss_phase(mem)` のみを返す。
     `boss_phase` は `MOVING_BOSS_CHAPTER=9`（`hanjuku_chart.py` L1458
     付近）でのみ立つため ch9 限定。ch6 は対象外。
   - ch10 限定の `_whitehouse_camp`（`recall.py` L154-184: White House
     開放中のボス行き行軍の残骸とみなし帰還させない）も ch6 では不発。
   - ボス包囲野営の免除 `_boss_camp_holds_siege`（L116-132 付近）は
     `castle_candidate` が `is_boss_castle` を満たすことを要件とする。
     ミルフィーユの `castle_candidate` は `不明`（`post_month_status.
     _marker` L1082-1163: marker_castle のアイコン数・8px 内の一意な近城
     が確定できなかった場合の未解決値）のため免除不成立。HP 39/39・
     壊れ False・卵 None で、戦力温存や回復待ちのサインもない。
   - したがって ch6 の通常経路では、月次処理 `post_month_status._tasks`
     （L225-308）が野営中の将軍へ `{'kind':'recall'}` タスクを毎月積む
     契約の対象であり、3ヶ月滞留は設計意図外の「滞留」に確定する。
   - 誤検知（(c)）でもない: `camp_status_seen.actors` の状態行 tick が
     87515 で、最終観測月 11-4 の世界状態・在城一覧（アルブラーカ=
     ヘーゼル、アルマムーン=どうし・タピオカ・ヘスティア、イカリア=
     マンゴスチン、カバルッサ=ユイートル）にミルフィーユは含まれず、
     野営継続の観測自体は実在する。

2. **帰還・再配置の機構はコードに存在し、少なくとも一部は動いていた。**
   - 月次タスク経路: `post_month_status._tasks`（L225-308）→
     `wait_recall` ステージ（L2495-2544 付近、
     `RECALL_TASK_RETRIES=2`、`RECALL_INTERRUPT_GRACE=240`）→
     `_recall_goal`（`strategy/orders.py` L2991 付近。v1478 で goal 未解決
     時は本城フォールバック追加、
     `docs/operations/hanjuku-boss-camp-recall-20261125.md` L71-82）。
   - 発射・検証経路: `_recall_dispatch_step`（`recall.py` L2591 付近〜）
     で `await_dispatch` 検証を駆動し、終端は `dispatched` /
     `arrival_unconfirmed` / `dispatch_unconfirmed`（L2640-2644 付近）。
     本ラン版 v1495（commit `05b0363a1`、PR #5514 / issue #5437）には
     `scene_covered` 刻印が既に入っており、送信済み dispatch が非マップ
     画面に遮られても `picker_closed`/`goal_failed` の誤計上を抑制する。
   - 到着確認の保険: `camp_arrival_check`（v1367、
     `docs/operations/hanjuku-camp-arrival-timeout-20261122.md`）が
     帰還後も野営継続なら 600 tick で `camp_recall_arrival_timeout` を
     記録して goal failure に積む。`CAMP_ROW_STALE_TICKS=600`
     （`post_month_status` L99・L488 付近）が古い状態行の再読を強制する。
   - `flow.recall_verification = await_dispatch / arrival_unconfirmed /
     steps=6` は、`input_sent` 確定・`picker_closed` 未達のまま検証
     window（6 ステップ）を使い切った終端 — つまり **ある帰還要求が
     送信 receipt の照合まで進み、受理（ピッカー閉鎖）が観測できずに
     終端した** 実在の試行が少なくとも1件あったことを示す。
     #5431（この層が一切見えなかった）とは情報量が違う。

確定できないこと（(a) と (b) の分岐点）:

3. **その終端がミルフィーユ本人のものか。** flow 投影
   `_summarize_flow`（`month_audit.py` L441-481）は
   `recall_verification` を汎用ループ（L469-480）で
   `stage/kind/status/result/attempts/reads/steps` のみに縮小し、
   `general`・`goal`・`camp` を落とす。同ラン内に他の野営者・砦帰還が
   あり得るため本人帰属は証明不能。
4. **月次タスクが本人に対して実際に走ったか。** issue の直前決定窓は
   終端前 120 秒（11-4 月次処理部）のみで、11-2・11-3 の両月に
   `post_month_status` がミルフィーユへ recall タスクを積み・実行・
   再キューしたかは窓外。11-4 月次部の決定列に本人への指示・帰還決定は
   無いが、他の出撃（タピオカのセレーネ奪還 order_start、バーミセリ
   発射）は継続しており、タスクが「積まれたが有界スキップ/予算切れ」
   「実行されたが終端 unconfirmed」「そもそも積まれなかった」は全て
   両立する。
5. **到着失敗後の回復層が発火したか。** `arrival_unconfirmed` 終端は
   `roster_read_due[goal]` をアームし（L2746-2754 付近）、
   `scene_covered` でなければ `recall_goal_failures` を計上
   （L2767-2768 付近）。また野営継続なら `camp_recall_arrival_timeout`
   が 600 tick で発火する設計。これらが本人へ発火したか、発火後の
   再試行がどう終わったかは issue からは読めない。

→ 「設計された有界の帰還試行・スキップ・再キューが3ヶ月尽きた」
((b) 側) と「帰還機構のどこかの層で実欠陥が繰り返し発生した」((a) 側)
を切り分ける証拠が不足。**(d) 判定不能。**（不足証拠は §5）

## 1. fingerprint / incident_id の式照合

| 項目 | コード | issue 値 | 照合 |
|---|---|---|---|
| fingerprint 式 | `_fingerprint` `month_audit.py` L3312-3314: `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]` | `33dd0a166cda78be` | 呼出は `_fingerprint(chapter, kind, [kind])` 形 → 入力 `6|camp_abandoned|camp_abandoned` に確定。sha256 再計算は本環境が計算コマンドを拒否したため**未実施**（式と入力値の照合のみ明記） |
| incident_id 式 | `sha256(runtime_id|generation|kind|resources|general|step|int(now))[:20]`（camp_abandoned ビルダ `_build_camp_abandon_incident` L1638-1727 内） | `13cbd1a04d8b691fad82` | `int(now)` が起票時刻依存のため後追い再計算は原理的に不能。一意性担保用で照合キーではない |
| 検知月数 | `CAMP_ABANDON_MONTHS = 3`（L61） | 継続 3ヶ月 | 一致（閾値ちょうどで発火） |
| 月数の数え方 | `_camp_age_months`/`_camp_month_starts`（L1609-1635）: `1 + #(month_starts > first_tick)`、上限 24 | 初回 11-2 (tick 87306) → 発火月 11-4 | 11-3・11-4 の month start が 87306 以後に2回 → 3ヶ月で整合 |
| 追跡状態 | `_track_camp_abandon`（L2674-2748）: `camp_watch.generals`・`camp_status_seen.actors` の tick・`reported` フラグで一度だけ発火 | 観測 1回・判定理由 なし | 契約どおり |

なお issue ヘッダの `order_start 0` は投影欠陥の既知パターン: v1495 の
`_decision_counts` は `most_common(30)` で top-30 に切り詰められており
（6c1fd176f で全件化に修正、#5525/#5530）、決定窓内に `order_start` 行が
存在しても counts に残らず 0 と表示される（本 issue の `decisions.before`
には タピオカの order_start 行が実在する）。**サイン自体は投影 artifact
であり検知ロジックの欠陥ではない。**

## 2. 帰還・再配置経路（v1495 時点）

- **月次タスク**: `post_month_status._tasks`（L225-308）が野営中の将軍へ
  `{'kind':'recall'}` を積む。ch9（`camp_stays` = `boss_phase`）のみ本城
  帰還を止め、代わりに最終回廊城への集合移動を使う（`gather_castle`
  L141-151、ch9 は イデ！）。ch6 は通常帰還対象。
- **実行**: `wait_recall` ステージ（L2495-2544 付近）で
  `RECALL_TASK_RETRIES=2`・`RECALL_INTERRUPT_GRACE=240` の有界再試行。
  `_recall_goal`（`strategy/orders.py` L2991 付近）は城候補不明でも本城へ
  フォールバック（v1478）。ch6 本城は `アルマムーン`
  （`HOME_CASTLES[6]`）、`BOSS_CASTLES[6]='ボス'`。
- **検証**: `_recall_dispatch_step`（`recall.py` L2591 付近〜）が
  `await_dispatch` で送信 receipt 照合→`picker_closed` 観測を 6 ステップ
  で打ち切る。終端は `dispatched`/`arrival_unconfirmed`/
  `dispatch_unconfirmed`。v1495 で `scene_covered` が追加され、送信確定後に
  非マップ画面に被られた試行は goal_failed 誤計上を免れる。
- **到着後の保険**: `camp_arrival_check` が野営継続を 600 tick で
  `camp_recall_arrival_timeout` として記録（v1367）。
- **再検出**: `retreat_rechecks`/`camp_observed` は pending 検証中の actor
  の重複キューイングを抑止（L385-392）、`release_for_task`（L427 付近）が
  月次タスクに所有権を渡す（v1477）。

## 3. `arrival_unconfirmed` と 3ヶ月滞留の関係

`stage=await_dispatch / status=arrival_unconfirmed / steps=6` は
「送信 receipt（`input_sent`）まで照合できたが、受理の画面証拠
（`picker_closed` = 帰還先ピッカー閉鎖）が 6 ステップ内に取れず終端」
を意味する。VERIFY-5515（同一ラン・ch6・#5515 の検証、結論 (a)）は
v1493 で await_dispatch の月またぎ無保護窓消費と receipt 証明機会喪失を
実欠陥と切り分けた。**v1495 は #5437 経由で `scene_covered` 刻印を持つ
ため、送信確定後の場面被りによる誤終端は v1493 より緩和されているが、
`arrival_unconfirmed` 自体は依然発生しうる設計どおりの終端形**である。

本件との関係で言えるのは:
- 帰還機構が完全に死んでいたわけではない（送信照合まで進んだ試行が
  flow に残っている）。
- しかし試行の本人帰属と、11-2〜11-4 の各月での試行回数・終端・
  再キュー・arrival_timeout 発火が読めないため、「設計どおりの終端が
  積み上がった」か「どこかの層で実欠陥が繰り返された」かは分からない。

## 4. 同型 family との切り分け

| issue | 章 | ラン/版 | 城候補 | flow | 意図的野営の可能性 |
|---|---|---|---|---|---|
| #5545 | ch9 カシス | g1190-ch1v9 / v1498 | ボス | `{}`（空） | **あり**: ch9 は `camp_stays` 設計（ボスフェーズ集合待機） |
| #5546 | ch9 ソーピニヨン | g1190-ch1v9 / v1498 | 不明 | — | 同上（ch9 設計） |
| #5547 | ch9 バタール | g1190-ch1v9 / v1498 | ボス | — | 同上（ch9 設計、HP 1/99 で温存サインもある） |
| #5559 | ch10 ビーツ | g1190-ch1v9 / v1500 | 不明（null） | `await_dispatch/dispatched/steps=1` | **ありえ得る**: `_whitehouse_camp`（ch10 White House 開放中のボス行軍残骸）が該当するかはラン状態次第 |
| **本件 #5522** | **ch6 ミルフィーユ** | **g1342-1fe0734c / v1495** | **不明** | `await_dispatch/arrival_unconfirmed/steps=6` | **なし**: ch6 に免除機構は存在しない |

- ch9 の3件（#5545/#5546/#5547）は `camp_stays` 設計で**野営継続自体が
  意図どおり**であり、camp_abandoned 監査が設計上のサインを拾った
  偽陽性側の family。#5547 は castle_candidate=ボスでボス包囲免除とも
  両立する。
- ch10 の #5559 は `_whitehouse_camp` の条件（White House 開放済み・
  ボスリング近傍）に一致すれば同じく設計どおり。
- **本件 #5522 はこの family を代表しない**: ch6 には対応する免除機構が
  なく、野営継続は設計意図外の滞留に確定する。signature（野営放置/
  3ヶ月）が共通するだけで機構は別。family の切り分けとしては、ch9/ch10
  のサインは「監査の偽陽性範囲」、ch6 の本件は「帰還機構の失敗か有界
  スキップか」を問う別レイヤーに分かれる。

## 5. 不足する証拠（(d) の根拠、確定に必要なもの）

一意確定にはラン成果物（VM 側 `hanjuku_decisions.jsonl` /
`hanjuku_events.jsonl` / `hanjuku_frames/`）が必要:

- 11-2・11-3・11-4 各月の `post_month_status` 系決定
  （`post_month_status_armed`/`post_month_task_result`/
  `camp_recall_requested`/`camp_recall_input_sent`/
  `camp_recall_unconfirmed`/`camp_recall_dispatched`/
  `post_month_recall_requeued`/`camp_recall_arrival_timeout`/
  `camp_recall_goal_failed`）で ミルフィーユ が general/goal として
  現れるか。本人のタスクが月ごとに積まれたか・実行されたか・
  どう終端したか。
- `flow.recall_verification` 本体（`general`/`goal`/`camp`/
  `scene_covered`/`requested_tick`）— 投影が落とすため issue からは
  本人帰属できない。
- 本人の状態行の tick 履歴（`camp_status_seen`）と各月の月またぎの
  相対位置 — 最終観測 tick 87515 が 11-3 後半の再読によるものか、
  帰還試行直前の観測か。
- 同ラン内の他の野営者の有無（`recall_verification` が他者の終端で
  ないことの確認）。
- 本人の試行に `scene_covered` が立っていたか（立っていれば v1495 の
  goal_failed 免責が滞留を延ばす設計どおりの働きをした可能性）。

## 6. v1495 → 現行 main（v1509）の差分

`05b0363a1..HEAD` で `month_audit.py`/`post_month_status.py`/`recall.py`
に入った関連コミット:

| commit | 内容 | 本件への影響 |
|---|---|---|
| `0193891d3` (v1498, #5452/#5518) | 目的地操舵中の戦闘中断でいどうモード喪失を `camp_recall_mode_lost` として扱う（`recall.py` +158 行等） | 帰還行軍が戦闘で途切れた場合の検出・免責を追加。本ラン（v1495）には無い。**本件の直前決定列に防衛戦闘のサインは無い**ため適用場面の証拠はない |
| `6c1fd176f` (#5525/#5530) | `_decision_counts` の top-30 切り詰めを全件化 | 投影 artifact の修正（本 issue の `order_start 0` が該当）。検知・帰還機構は無変更 |
| `2fe1b66f8`, `1b13aeb0d`, `dae1516e4`, `5e41759ff`, `cd6d7a24d` | 監査・戦闘系の別修正 | `_track_camp_abandon`/`_tasks` の recall キューイングへの変更なし（確認した範囲） |

→ 現行 main までに **camp_abandoned の検知機構と ch6 の通常帰還経路の
仕組みに変更は入っていない**。v1498 の mode-lost は「戦闘で帰還が途切れ
た」場合の新たな終端形を追加するのみで、本件のような
`arrival_unconfirmed` 滞留の回復機構は変わらない。

## 7. #5431 との異同

| | #5431 (ch12 ポセイドン, 結論 (d)) | 本件 #5522 (ch6 ミルフィーユ) |
|---|---|---|
| 意図的野営の免除機構 | ch12 にも無し（滞留は確定） | ch6 にも無し（同じく確定） |
| flow.recall_verification | issue に無し | **あり**（`await_dispatch/arrival_unconfirmed/steps=6`） |
| 帰還機構の実動 | issue から不明 | **少なくとも1件の送信照合済み試行が存在** |
| 切り分けの限界 | 機構が動いたか自体が不明 | 本人帰属・各月の試行履歴が不明 |

→ #5431 より一段深いが、本人帰属と月次履歴が読めない時点で (a)/(b) の
分岐は残り、結論は同じく **(d)**。

## 付録: issue 事実 → コード対応表

| issue の値 | 対応箇所（`45c36f2db` = 現行 main 上の行番号） |
|---|---|
| 判定 `camp_abandoned` / confidence high | `month_audit.py` `_build_camp_abandon_incident` L1638-1727、`CAMP_ABANDON_MONTHS=3` L61 |
| 継続 3ヶ月の数え方 | `month_audit.py` `_camp_age_months`/`_camp_month_starts` L1609-1635 |
| 追跡・一度だけ発火 | `month_audit.py` `_track_camp_abandon` L2674-2748 |
| fingerprint 式 | `month_audit.py` L3312-3314（呼出は `_fingerprint(chapter,kind,[kind])`） |
| incident_id 式 | `month_audit.py`（camp_abandoned ビルダ内、`sha256(runtime_id|generation|kind|resources|general|step|int(now))[:20]`） |
| 城候補「不明」 | `post_month_status._marker` L1082-1163（marker_castle アイコン数/8px 内一意近城が未解決） |
| 月次 recall タスク | `post_month_status._tasks` L225-308、`wait_recall` L2495-2544 付近、`RECALL_TASK_RETRIES=2`・`RECALL_INTERRUPT_GRACE=240` |
| 帰還先解決（候補不明→本城） | `strategy/orders.py` `_recall_goal` L2991 付近（v1478）、`docs/operations/hanjuku-boss-camp-recall-20261125.md` L71-82 |
| dispatch 検証・終端形 | `recall.py` `_recall_dispatch_step` L2591 付近〜、終端 L2640-2644 付近 |
| flow 投影が `general` を落とす | `month_audit.py` `_summarize_flow` L441-481（recall_verification は汎用ループ L469-480 で stage/status/steps 等のみ） |
| v1495 scene_covered | commit `05b0363a1`（PR #5514 / #5437）— 本ラン版に搭載済み |
| 到着 timeout 保険 | `camp_arrival_check`（v1367、`docs/operations/hanjuku-camp-arrival-timeout-20261122.md`）、`CAMP_ROW_STALE_TICKS=600` `post_month_status` L99・L488 付近 |
| ch9 免除（本件対象外） | `recall.py camp_stays` L131-138（`boss_phase` = `MOVING_BOSS_CHAPTER=9` `hanjuku_chart.py` L1458 付近）、`gather_castle` L141-151 |
| ch10 免除（本件対象外） | `recall.py _whitehouse_camp` L154-184 |
| ボス包囲免除（城候補 ボス が要件） | `recall.py _boss_camp_holds_siege` L116-132 付近、`BOSS_CAMP_EXEMPT_MONTHS=3` L77 |
| ch6 本城/ボス城 | `hanjuku_chart.py` `HOME_CASTLES[6]='アルマムーン'`、`BOSS_CASTLES[6]='ボス'` |
| `order_start 0` の投影 artifact | `month_audit.py` `_decision_counts`（v1495 は `most_common(30)`、6c1fd176f で全件化 #5525/#5530） |
| 決定窓 120 秒 | `month_audit.py` `_build_camp_abandon_incident` 内 `since=now-120` |
| v1495→v1509 の関連差分 | `0193891d3`(v1498 mode-lost), `6c1fd176f`(counts 全件化) 等 §6 |

記録: issue #5522 への結論コメントは検品ゲートが行うため投稿しない。
本レーンはコード変更・マージを行わない（read-only 検証のみ）。
`git checkout -b verify/5522-camp-abandoned` および `git add`/`git commit`
は本環境で拒否されたため、本ファイルは worktree 直下に**未コミット**の
まま残す（VERIFY-5431 と同じ状況・同じ手順）。拾う場合は
`verify/5522-camp-abandoned` ブランチを切って本ファイルだけをコミット
すればよい（push は不要）。
