# VERIFY-5437 — [半熟英雄] キャンプ帰還未確認: ch12 バジル

対象: docich#5437（`bug` / `auto-generated` の監査自動起票）
ラン `g1190-ch1v9` / generation `1190` /
`hanjuku-chart-v1469-recall-yjump-goal` / 月 `ch12 20-4`（観測 1461回 /
186.4秒 / max gap 0.7秒）
fingerprint `0b1f418d2b3f507c` / occurrence(incident_id) `e4223260f2c348fa78ad`
created_at `1795531466.7193978`
再発コメント（2026-11-24）: ch12 23-1、将軍 シュガー、同一 fingerprint、
incident_id `f592bcd24f5ffe9f9fb1`、観測 488回 / 71.5秒

判定: **(a) 実欠陥** — 終端判定 `camp_recall_unconfirmed` /
`arrival_unconfirmed` 自体は契約どおりの正しい検出（送信 receipt あり・
picker 閉鎖未観測）。実欠陥はその下層で、砦しゅつげき帰還の dispatch
検証窓（await_dispatch・6観測）が、送信直後に前面へ出た text/場面
（おそらく月末場面 — 終端観測が月クローズ時刻と同一 tick）を何の
保護もなく通り抜け、`picker_closed` を観測する機会が構造的に失われた。
送信 A がピッカーへ届いたか場面に食われたかはラン成果物が必要だが、
どちらでも欠陥が成立する（受理済みなら健全ペアへの `camp_recall_goal_failed`
誤計上 / 未受理なら会話への誤入力）。現行 main（v1487 系）でも未修正で、
PR #5442 / #5450 / #5451 のカバー範囲外。#5141（due 城名簿読み起票）は
マージ済みだが到着確認の後始末を改善するだけで、発火自体は防がない。

調査基盤: worktree `/home/hatch/work/docich-5437`（detached HEAD
`acda6c36d` = origin/main = `診断allowlistへv1486-ch4-southern-kingsを戻す`
(#5482)）。read-only 検証: 本番 SSH なし / ラン成果物
`hanjuku_decisions.jsonl`・`hanjuku_events.jsonl`・`hanjuku_frames/` は
VM 側で直接参照せず / 秘密情報なし / コード変更なし（成果物は本ファイルの
追加のみ）。issue 本文は GitHub 公開ページ経由で取得（`gh` 不使用）。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1）。
fingerprint / incident_id の sha256 再計算は実行環境が計算コマンドを
拒否したため **未実施**。ただし `_fingerprint`（`month_audit.py` L3105-3107）
の式 `sha256(f"{chapter}|{kind}|{'+'.join(signals)}")[:16]` をコード上確認し、
呼出側 L931 が `_fingerprint(chapter, 'camp_recall_unconfirmed',
['camp_recall_unconfirmed'])` であることから、本件の入力は
`12|camp_recall_unconfirmed|camp_recall_unconfirmed` に確定する（sha256 は
入力が同じなら出力も同じ。再発コメントが同一 fingerprint で集約されるのも
この章+kind 集約設計どおり）。incident_id は L926-929 で
`runtime_id|generation|kind|reason_code|camp|general|int(now)` の
sha256[:20] であり、`now` は起票時刻依存のため後追い再計算は原理的に不能
（一意性担保用で照合キーではない）。

---

## 結論

**(a) 実欠陥。** 二段に分かれる。

1. **終端判定そのものは契約どおり（誤検知ではない）。**
   `_recall_dispatch_step`（`recall.py` L2389-2533）の受理条件は
   `input_sent && picker_closed && !interrupted`（L2415）。`picker_closed`
   は「送信 A のあと画面が `map` へ戻る」こと（L2403-2404）。
   本件は送信後の実画面が `unknown` → `text`（イベント文面）のまま
   6 観測を使い切り（steps=6、L2405）、`picker_closed=False` で
   `arrival_unconfirmed` に終端した。reason_code
   `camp_arrival_unconfirmed` は `resulting_event` からの決定的写像
   （`month_audit.py` L770-771、#5248 の誤署名は v1435 系で修正済み）で
   **正しい署名**。issue 本文の stage/steps/status/input_receipt_confirmed/
   escape_move/general/camp/castle は終端 metric（L2455-2468）と全て整合。

2. **下層の実欠陥 — dispatch 検証窓が text/場面に無防備。**
   - `await_dispatch` は text hold を明示免除される（`recall.py`
     L1460-1465:「the dispatch wait settles on the next screen, which can
     be a text/month screen」）。text 画面は例外なく `_recall_dispatch_step`
     へ入り、step を消費するが `picker_closed` は `map` でしか立たない。
   - 場面（concert 等）が前面へ出ても `_recall_after_scene`
     （`orchestrator.py` L2057-2079）は `stage != 'await_dispatch'` を条件に
     明示除外する — 送信済み帰還を再開しない設計判断で、結果として
     場面の観測をそのまま 6 ステップ分消費して未確認終端へ落ちる。
   - `interrupted` マーカーは `state.get('input_sent')` が立った後は
     刻印できない（`_mark_recall_interrupted` L448）。text 画面経路の
     呼出（L1466）も `stage != 'await_dispatch'` の内側にあるため
     await_dispatch では恒常的に不発（VERIFY-5248 §3 が指摘したデッド
     パスは現行でも同じ）。よって `interrupted=False` は「場面の横取りが
     無かった」ことを保証しない — 本件では横取りが**実在する**。
   - 月末ゲート `_month_end_soon`（L472-490、`MONTH_END_MARGIN=200` ticks、
     docstring がまさに「場面にピッカーを切られる」g1163 事例を引く）は
     `state is None` の新規フィールド野営開始時（L1241）にしか無い。
     月次タスク帰還（`post_month_status.py` L2365-2390 が `flow.recall` を
     直接作成）も camp_recheck/砦経路も、このゲートを通らない。
     本件の `camp_recall_requested`（14:44:26.03Z）は月クローズ
     （`closed_at` = 終端観測と同一時刻 14:44:26.72Z）の **約0.7秒前
     ≈ 5-6 tick 前** で、margin=200 の意図から見ても検証不能が構造的に
     確定していた。
   - 結果の帰属が真因を反映しない: `arrival_unconfirmed` かつ
     `not picker_closed` で `recall_goal_failures[(132,124)][アルマムーン]`
     +=1 し `camp_recall_goal_failed` を記録（L2510-2531）。コメント自身が
     「the general never moved」＝宛先拒否の学習と明記するが、本件の
     直接因は場面遮蔽（または会話への A 誤送）であり、受理済みなら
     健全な (野営, 城) ペアを失敗として誤計上する。`_goal_after_failures`
     の「次回は別の城を試す」回転ではこの機構の再発を止められない。
   - 逆に A が場面に食われていた場合は、receipt は送信を証明するが
     ピッカーは未確定のまま残り、誤入力で発射されなかった帰還になる
     （#5452 の `castle_menu` 誤入力と同型の入力誤送）。

**発射の成否はラン成果物なしでは確定不能だが、どちらの枝でも欠陥は
成立する**（VERIFY-5248/5306 と同じ二段構造）。`world.garrison` の
`アルマムーン` にバジルが載っている点は到着の証拠に**ならない**:
`_assign`/完全名簿読みによる実測値と `_garrison_move`（orders.py
L3130-3155）等の簿記的移動記録が混在し、かつ彼は 14:44:20 に砦の中で
実観測されているため、月末スナップショットの在城記載は砦入り以前の
陈旧値か別経路の簿記でしかない（§5）。

## 1. issue 提示値 → コード対応（`acda6c36d` 上の行番号）

| issue の値 | 対応箇所 | 検証結果 |
|---|---|---|
| 決定 `camp_recall_unconfirmed` | `recall.py` L2498-2500（`_recall_dispatch_step` 終端の未受理枝、唯一の記録点） | 一致 |
| `resulting_event: arrival_unconfirmed` | L2415-2418: `input_sent` 真・`picker_closed` 偽 → `arrival_unconfirmed` | 一致 |
| `stage: await_dispatch` / `steps: 6` | `_request_recall` L2356（stage 設定）/ L2390（+1）・L2405（`steps >= 6` で終端） | 一致（6 観測上限到達、約0.7秒） |
| `input_receipt_confirmed` / `input_sent: True` | L2391-2402: `_journal['_recall_inputs']` と `request_trace` の同run照合 → `camp_recall_input_sent` 記録 | 一致（**物理送信は実在**。送信先がピッカーか場面かは証明外） |
| `picker_closed: False` | L2403-2404: `input_sent` 後に `screen.kind=='map'` でのみ立つ | 一致（`unknown`→`text` のまま終端） |
| `interrupted: False` | L2406-2407: `_flow['recall_interrupted']` 未検出。L448 の `input_sent` ゲートと L1460 の await_dispatch 免除で**刻印不能** | 一致（機械的には正しいが「横取りなし」の保証ではない） |
| `escape_move: False` / `target_cell: None` | L2420, L2452-2458 | 一致（崖離脱ではない） |
| 将軍 `バジル`（fort のみ記録） | 終端 metric `general = state.get('general') if state.get('fort') else None`（L2467）→ `flow.recall_retry.kind='dome'` とも整合 | **砦（dome）しゅつげき帰還であることを確定** |
| 野営 `[132,124]` / 帰還先 `アルマムーン` | `terminal_metric['camp']`（L2457）/ `state['goal']`。`アルマムーン` = ch12 本城（`hanjuku_chart.py` `HOME_CASTLES[12]`）→ owned | 一致 |
| 理由文 / reason_code `camp_arrival_unconfirmed` | L2495-2497 文言 / `month_audit.py` L770-771（`resulting_event` 決定的） | 正しい署名 |
| `camp_recall_goal_failed` | L2510-2531 → `_flow['recall_goal_failures']` | 発火（ただし帰属は場面遮蔽の可能性で誤計上の疑い、§3） |
| `roster_read_due['アルマムーン']` | `_request_recall` L2363-2372 + 終端側 L2501-2509（#5129） | 予約は 2 度登録される設計 |
| fingerprint `0b1f418d2b3f507c` | `_fingerprint(12, kind, [kind])`（`month_audit.py` L931 → L3105-3107）、章+kind のみ | 式から確定（実行再計算は環境拒否で未実施） |
| `auto_file` / confidence high | `_build_camp_incident` `auto_file=True`（L932） | 契約どおり |

## 2. 直前決定列が示す機構（issue JSON `decisions.before` より）

```
14:44:20  camp_enter          (map)     野営へカーソル・きかんを開く
14:44:20  camp_fort_sortie    (text)    砦の中からバジルを選びしゅつげき
14:44:21  y_jump_open RECALL  (map_target) → アルマムーン
14:44:25  y_jump_confirm      (world_map)
14:44:26.03 camp_recall_requested (map_target)
14:44:26.17 camp_recall_input_sent  (screen=unknown)   ← journal で物理送信を確認
14:44:26.60 event_text_advance      (text)  イベント会話が同一文面のまま待機 → Aで進める
14:44:26.72 camp_recall_unconfirmed (text)  ← 月クローズ時刻と同一 tick
14:44:26.72 camp_recall_goal_failed (text)
```

- 同月の先行帰還（14:44:02-08、マリボー系・非砦）は `map` でピッカーが
  閉じ `camp_recall_dispatched` まで受理。月内 `camp_fort_sortie`:5 /
  `dispatched`:6 — 砦経路も通常は受理される。
- バジルのみ送信直後に `text` 場面へ取り込まれた。`event_text_advance`
  （`orchestrator.py` L177-216, L2466-2471）は concert/event/dialogue
  phase の同一文面 3 観測で A を送る汎用機構で、帰還の await_dispatch を
  認識しない。この A は場面へ効き、ピッカーには効かない。
- 終端観測（14:44:26.72）= `month_summary.last_at` = `closed_at`。
  観測レート（1461回/186.4秒 ≈ 0.13秒/観測）から steps=6 は約0.7秒 —
  月末場面（「こんげつは……」系、phase 'concert'。`orchestrator.py`
  L2061-2064 の実測記述）がちょうど割り込んだ形と時刻が一致する。
  無関係イベント文面の可能性も残るが、どちらでも検証窓が死ぬ構造は同じ。

## 3. 同型家族との切り分け

| issue | 形 | 機構 | 本件との関係 |
|---|---|---|---|
| #5248 / #5250 | `input_sent=False` → `dispatch_unconfirmed` | 送信 receipt 鎖の喪失（送信前） | **別機構**。記録形欠陥は v1435 系で修正済み、本件は新形（stage/steps/status 全出し） |
| #5306 | `arrival_unconfirmed` ch10 デーメーテール、砦→ボス | 送信後の受理未観測 + due 消費経路欠落 | 同じ終端形。ただし #5306 は消費経路（#5141 未マージ）が焦点、本件は**窓を食う場面遮蔽**が直接証拠付き。宛先も本件は owned 本城 |
| #5434 | `camp_recall_failed` ch12 キール | failed 層（fort_status / camp_recall_limit） | 同一ラン・同章の帰還完了層の残件。stage が違い別件 |
| #5452 | 防衛戦闘で dest/y_route のモード喪失 → view モード `castle_menu` 誤入力 | 戦闘中断の復帰欠陥 | 「中断で未確認」の親戚だが別機構（場面/text 遮蔽 vs 戦闘モード喪失）。#5452 は現行でも未修正と報告済み |
| #5429/#5430/#5431 | `camp_abandoned` ch12 同一ラン | (d) 判定不能 | 帰還完了層の残件として関連。本件の未確認保持が将来の放置起票に繋がりうる面は同じ |

## 4. v1469 / v1470 / v1471 / v1477 / v1481 / 現行のカバー範囲

- **v1469（#5424、ランと同バージョン）**: 帰還Yジャンプ未解決セルで
  brain 落下＋Gカーソル停止の修正＋自動起票。本件の Y ジャンプは
  アルマムーンへ正常解決しており、修正の効果外・修正後の発火である点のみ
  一致。
- **v1470（#5432）**: 第4話四季王サイト待機の上限。ch12 本件と無関係。
- **#5141（`975090bcb`、v1471 系でマージ済み — VERIFY-5306 時点では未
  マージだった「欠けたピース」）**: `routes.roster_read_due`（章共通の
  due 抽出＋stale 掃除、`routes.py` L204-222）と `interim_candidates`
  の due 城 `roster_probe` 起票（`replan.py` L1861-1890+）は現行 main に
  存在する。宛先 `アルマムーン` は ch12 本城＝owned で `c in owned`
  フィルタを通るため、**現行 main なら到着確認（完全名簿読み →
  `_observe_garrison` → due pop）の後続経路が機能しうる**。ただしこれは
  発火後の片付けであり、`arrival_unconfirmed` 終端そのものと
  `camp_recall_goal_failed` の誤計上は防がない。またランは v1469 で、
  発火時点では消費経路が存在しなかった（VERIFY-5306 と同じ穴の時期）。
- **v1477（#5442）**: `task_recall` 導入・`release_for_task`（古い退却
  再確認の乗っ取り防止）＋免除カウント消失修正。対象は別欠陥。
  `task_recall` フラグ自体は v1469 に無いが、本件の砦経路は月次タスクの
  将軍別処理列の直後に走っており、#5442 の guard（`camp_recheck.py`
  L1024-1028 の `task_recall` 例外）は本件の失敗点に触れない。
- **v1481（#5450）**: `camp_watch_hint` 配置修正＋`task_recall` の goal
  未解決フォールバック（`orders.py` L3074-3084）。本件は goal が
  アルマムーンへ解決済みで未解決形ではない。
- **v1483（#5451）**: ch12 ボス拠点集結。無関係。
- **現行 main（`acda6c36d`、v1487 系）**: `git diff 7db52e963..HEAD` で
  `flows/recall.py` の変更は `_site_king_down`/`_boss_move_goal`（ch4
  四季王リダイレクト）のみ、`orchestrator.py` も `event_text_advance` /
  `_recall_after_scene` / await_dispatch 例外に変更なし。
  **場面遮蔽のまま未確認終端する機構は現行でもそのまま**であり、
  `interrupted` の post-send 刻印不能、task/砦経路の月末ゲート欠落も
  同じ。再発コメント（ch12 23-1、同一 fingerprint）は機構が生きている
  傍証。

## 5. 残件（read-only では確定不能、判定には影響しない）

一意確定にはラン成果物（VM 側 `hanjuku_decisions.jsonl` /
`hanjuku_events.jsonl` / `hanjuku_frames/`）が必要:

- **送信 A の着地点**: 14:44:26.03〜26.17 のフレームで、A が
  `map_target` ピッカーを確定したか、既に被り始めた場面/text を進めたか。
  前者なら誤計上系、後者なら誤入力系の欠陥が確定する。
- **`text` の正体**: 月末場面（concert phase）か無関係イベント文面か。
  前者なら月末ゲート欠落が直接因として確定できる。
- **`world.garrison` のバジル**: 実測名簿由来か簿記（`_garrison_move`）
  由来か、砦入り前からの陈旧値か。月末スナップショット単体では
  今回の dispatch の到着証拠にできない。
- **再発 23-1 シュガー件**: 同じく場面遮蔽の時期か（同じ直前決定型なら
  機構の再現性を補強）。
- `roster_read_due['アルマムーン']` が当月内に偶発的な完全読みで pop
  され、バジルの着否が事後確認されたか。

## 6. 修正候補（実装はしない、検証の結論として）

1. **await_dispatch の場面耐性**: `picker_closed` の判定を `map` 復帰に
   限定したまま、text/場面の連続観測では steps を消費しない（例:
   `screen.kind in ('text','concert','event')` では hold して場面が
   明けてから窓を再開）— または `_recall_after_scene` の await_dispatch
   除外を、「input_sent 済み」のみに狭めて再検討（未送信のまま場面に
   入った場合は再開対象にできる）。
2. **送信後の割り込み刻印**: `_mark_recall_interrupted` の
   `input_sent` ゲート（L448）を、post-send の場面遮蔽を `interrupted`
   とは別フラグ（例 `scene_covered`）で残せるよう見直し、
   `post_month_recall_requeued`（`post_month_status.py` L2518-2535）の
   無料再試行を送信後の場面喪失にも効かせるか判断する。
3. **月次タスク/砦経路への月末ゲート**: `_month_end_soon`（margin 200
   tick）を `post_month_status` の task_recall 起票（L2365-2390）と砦
   しゅつげき開始にも適用する — margin 内に始めた帰還は検証不能が
   構造的に確定している。
4. **`camp_recall_goal_failed` の帰属**: `arrival_unconfirmed` のうち
   場面/text で窓が潰れた終端を「宛先拒否」として計上しない（終端時の
   直近 screen 列を metric に残して監査側・学習側で判別できるように）。
5. **（A が場面に食われた場合）誤入力の防止**: 送信後に非 picker 画面を
   検出したら、ピッカーが残っている可能性を考慮して dispatch を
   未確定のまま安全に仕切り直す経路（二重送信を避ける設計要）。

## 付録: issue 事実 → コード対応表

| issue の値 | 対応箇所（全て `acda6c36d` 上の行番号） |
|---|---|
| 決定 `camp_recall_unconfirmed` / status | `recall.py` L2415-2418, L2498-2500 |
| stage `await_dispatch` / steps 6 | `recall.py` L2356, L2390, L2405 |
| `picker_closed` = map 限定 | `recall.py` L2403-2404 |
| `input_sent` receipt 照合 | `recall.py` L2391-2402 |
| `interrupted` 刻印不能（post-send / await_dispatch） | `recall.py` L448, L1460-1470 |
| 場面による recall 再開の await_dispatch 除外 | `orchestrator.py` L2057-2079（`_recall_after_scene` L941-981） |
| `event_text_advance`（phase 限定・同一文面3観測） | `orchestrator.py` L134, L177-216, L2466-2471 |
| 月末ゲートが新規フィールド野営のみ | `recall.py` L1241（`_month_end_soon` L472-490） |
| `camp_recall_goal_failed` 計上 | `recall.py` L2510-2531 |
| `roster_read_due` 予約（要求時＋終端時） | `recall.py` L2363-2372, L2501-2509 |
| 砦しゅつげき経路 | `recall.py` L2186-2309（FORT_STAGES / `_fort_step`）、`camp_fort_sortie` 記録 L2294 |
| reason_code 決定的写像 | `month_audit.py` L768-771 |
| fingerprint 式 | `month_audit.py` L3105-3107（呼出 L931） |
| incident_id 式 | `month_audit.py` L926-929 |
| `アルマムーン` = ch12 本城 | `hanjuku_chart.py` `HOME_CASTLES[12]` |
| #5141 の消費経路（現行のみ・ラン時点では無し） | `routes.py` L204-222, `replan.py` L1861-1890+（commit `975090bcb`） |
| `release_for_task` / `task_recall`（v1477、ラン時点では無し） | `camp_recheck.py` L427-446, `post_month_status.py` L2363-2377 |
| 設計契約（6 観測・未確認保持） | `docs/operations/hanjuku-camp-recall-20261001.md` |
| 受理後の到着確認の設計 | `docs/operations/hanjuku-recall-dispatched-20261122.md` |

記録: issue #5437 への結論コメントは検品ゲートが行うため投稿しない。
本レーンはコード変更・マージを行わない（read-only 検証のみ）。
