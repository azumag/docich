# VERIFY-5452 — [半熟英雄] キャンプ帰還未確認: ch4 アルマムーン

対象: docich#5452（`bug` / `auto-generated` の監査自動起票）
ラン `g1342-1fe0734c` / generation `1342` /
`hanjuku-chart-v1478-site-nearest-source` / 月 `ch4 8-12`（観測 399回 / 631.9秒）
fingerprint `b8b3fd004c5b06a6` / occurrence(incident_id) `7d7abb367804928ac4e9`
created_at `1795537787.7720428`（2026-11-24 16:29:47Z）

判定: **(a) 実欠陥** — 監査の `camp_recall_unconfirmed` 自体は正しい検出
（真の未ディスパッチ）。実欠陥はその下層で、防衛戦闘が「いどう→全体マップ(Y)」の
目的地選択を中断したあと、`dest` ステージの再開ロジックが戦闘後の通常フィールド
（`map`）を「いどうモード継続中のフィールドカーソル」と区別せず Y ジャンプを再開
したため、view モードの全体マップで自城へ A を送り、城メニュー（`castle_menu`）を
開くだけで帰還は発射されなかった。現行 main（v1482）でも未修正で、PR #5442 /
#5450 のカバー範囲外。

調査基盤: `/home/ubuntu/repos/docich`（`origin/main` = `f8793965f` =
`hanjuku-chart-v1482-departure-request-ownership`）。
read-only 検証: 本番 SSH なし / ラン成果物 `hanjuku_decisions.jsonl`・
`hanjuku_events.jsonl`・`hanjuku_frames/` は VM 側で直接参照せず / 秘密情報なし /
コード変更なし（成果物は本ファイルの追加のみ）。
根拠は gh issue 本文・コメント、`origin/main` のコード、git history、
同型 issue・PR・過去の運用記録（docs/operations/）。
`handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読。

---

## 結論

**(a) 実欠陥。** 根拠は次の 4 点。

1. **発火は契約どおりであり、帰還は実際に発射されていない（誤検知ではない）。**
   `recall.py` `_recall_dispatch_step`（L2369-2513）の受理条件は
   `input_sent && picker_closed && !interrupted`（L2395）。`picker_closed` は
   「送信 A のあと画面が `map` へ戻る」こと（L2383-2384、受理＝ピッカーが
   フィールドへ閉じる、docs/operations/hanjuku-recall-dispatched-20261122.md）。
   本件は送信後の実画面が **`castle_menu` で 6 ステップ経過**し `map` に戻らず、
   `picker_closed=False` のまま `arrival_unconfirmed` で終端。`castle_menu` は
   「しゅつげき ＋ ステータス」を持つ城メニュー（`os_layer/screen.py` L1563-1572）
   であり、移動ディスパッチでは決して開かない画面（§2）。よって帰還入力は
   発射先ピッカーへ届かず、未確認保持は正しい。

2. **欠陥機構: 防衛戦闘で いどうモードが死んだまま dest が復帰する。**
   - 直前決定列（issue 本文・JSON `decisions.before`）:
     `camp_enter`(16:28:32) → `camp_recall_cursor`(16:28:33, A で いどう確定,
     stage='dest') → `y_jump_open RECALL→アルマムーン`(16:28:35, 目的地ピッカー系
     の Y) → **`defense_observed`(16:28:44) → 戦闘**(〜16:29:08) →
     `post_month_battle_resume` → `camp_recall_y_route`(16:29:08) →
     `y_jump_open` → `y_jump_confirm`(16:29:37, A) →
     `camp_recall_requested`(16:29:38, A) → `camp_recall_input_sent`(16:29:39,
     screen=**`castle_menu`**) → `camp_recall_unconfirmed`(16:29:47,
     screen=`castle_menu`) → `camp_recall_goal_failed`。
   - `_mark_recall_battle`（L453-465）が `battle_return` を立てるのは
     **`('to_camp','menu')` だけ**で、目的地操舵中の `dest` / `y_route` は
     対象外。`recall_interrupted`（L434-450）も `await_dispatch` 限定。つまり
     目的地ピッカー層の戦闘中断はどちらの復帰保護も受けない。
   - 戦闘後の `dest` ステージは `screen.kind == 'map'` をそのまま
     「いどうが開いたフィールドカーソル」と扱い（L1887-1914、
     `camp_recall_y_route` の理由文そのまま）Y → `world_map` → 自城で A を送る。
     いどうモードが既に失われていると、この世界図は view モードであり、
     自城への A は移動先指定ではなく **城メニューを開く**（地図上で自城へ
     カーソルが乗った状態での A は城メニューを開く契約: `flows/map.py`
     L2023-2025「Aで城メニューを開き実ステータスで出撃元を照合する」）。
   - `castle_menu` が 16:29:39〜47 に観測された事実は、2 発目の A
     （`camp_recall_requested`）が帰還確定ではなく城メニュー系画面へ入ったことを
     意味する（view A が自城へ着地しフィールド A が城メニューを開いた、または
     view モード A が直接城メニューを開いた、のいずれか — どちらでも
     ディスパッチは成立しない）。

3. **同型家族との異同を照合した。**
   - #5248 / #5250 / #5252（v1419 世代、closed）: `input_sent: False`、
     stage 不明・steps None — 「送信すら未確認」の旧形。本件は
     `input_receipt_confirmed: True` / `stage: await_dispatch` / `steps: 6` の
     新形（v1350 の受理分離後）で、機構が異なる。
   - #5437（v1469, ch12 バジル）: 砦の `camp_fort_sortie`（しゅつげき帰還）で、
     1 回目は `camp_recall_dispatched` まで受理、2 回目は `event_text_advance`
     の会話横取りで未確認。「中断で未確認」の親戚だが、砦しゅつげき経路かつ
     会話横取りという別機構。本件は通常野営の いどう→Y で、**戦闘中断後の
     モード喪失**という固有の欠陥。
   - いずれの家族も本件の「戦闘が dest/y_route を潰す」失敗はカバーしない。

4. **PR #5442 / #5450 のカバー範囲外。**
   - #5442（v1475 系）: ボス野営の免除月数カウント消失と、古い退却再確認が
     月次帰還タスクを乗っ取る問題の修正（`task_recall` 導入）。本件の失敗点
     （戦闘中断後のモード照合）には触れない。
   - #5450（v1478 フォローアップ）: `camp_watch_hint` を `policy` 直下へ配置
     し直す修正と、`task_recall` の `_recall_goal` が None を返さない本城
     フォールバック。本件は goal（アルマムーン）が解決済みで Y ジャンプまで
     到達しており、goal 未解決形ではない。
   - さらに `git log --since=2026-11-24` では `flows/recall.py` /
     `os_layer/navigation.py` / `flows/map.py` に本件以降の変更が無く、
     現行 main（v1482）でも同じ受理条件・同じ dest 復帰ロジックのまま。

以上から (a) を採用する。監査が「未確認」を正しく挙げた一方で、下層の
帰還実行が戦闘中断によって破壊され、しかも `camp_recall_goal_failed` が
「(野営, 城) ペアの拒否」として誤帰属する（実際はモード喪失）ため、
「次回は別の城を試す」回転では同一機構の再失敗を止められない。

## 1. issue 提示値 → コード対応（根拠の照合）

| issue の値 | 対応箇所（`origin/main` = v1482） | 検証結果 |
|---|---|---|
| `resulting_event: arrival_unconfirmed` | `recall.py` L2395-2398: `input_sent` 真・`picker_closed` 偽 → `arrival_unconfirmed` | 一致 |
| `input_receipt_confirmed: True` / `input_sent: true` | L2371-2382: journal `_recall_inputs` が `request_trace`・`a_inputs` を一致させ送信を確認 | 一致（**送信は実在**。ただし送信先画面はピッカーではなく `castle_menu`） |
| `picker_closed: False` | L2383-2384: `input_sent && screen.kind=='map'` でのみ立つ | 一致（`castle_menu` では立たない） |
| `stage: await_dispatch` / `steps: 6` | L2336 / L2385: 6 ステップ or picker_closed で終端 | 一致 |
| `camp_recall_unconfirmed` + `camp_recall_goal_failed` | L2475-2511: 未受理終端で 2 決定、失敗ペアを `recall_goal_failures` に加算 | 一致 |
| 理由 `camp_arrival_unconfirmed` | L2475-2477 の文言と一致 | 一致 |
| 帰還先 `アルマムーン` / 野営 `[127,124]` | `camp_recall_y_route` → `y_jump_open/confirm` の target と一致 | 一致 |
| `roster_read_due` 予約 | L2481-2489: `arrival_unconfirmed` でも目的地名簿読みを予約（#5127） | コード上は到着確認の再読経路あり |

## 2. なぜ `castle_menu` が「未ディスパッチ」を証明するか

- 受理（dispatched）の実測契約（v1350 記録 doc）: 「送信 A の直後に行き先
  ピッカーが閉じてフィールドマップへ戻る」→ `camp_recall_dispatched`。
  本件は送信後に `castle_menu` が開き続け、`map` へは戻らなかった。
- `castle_menu` = 「しゅつげき」「ステータス」を含む城のメニュー
  （`screen.py` L1563-1572）。野営メニュー（いどう/ステータス/キャンプ/
  きかん）や砦メニュー（しゅつげき/こうかん、ステータス無し）とは別種で、
  移動発射の副作用として開く画面ではない。
- よって「A が城メニューを開いた」= その時点の UI は目的地ピッカーでは
  なかった。いどうモードが生きていれば、世界図での A は目的地確定として
  受理され `map` に戻る（dispatched 系の実測どおり）。`castle_menu` の出現は
  戦闘でピッカー層が死に、view モード世界図/フィールド上で自城を操作した
  ことを示す。

## 3. 残存リスク（(a) の実害）

- 帰還が実行されないため、野営将軍は残り続ける（`camp_abandoned` 系の将来
  起票につながりうる）。
- `camp_recall_goal_failed` が `(野営[127,124], アルマムーン)` を失敗ペアとして
  記録したが、原因は目的地の拒否ではなくモード喪失のため、次回は別城へ回る
  回復機構が機構上の真因を直さない。
- `camp_recall_requested` の A が `castle_menu` へ入った（誤入力）。画面は
  `castle_menu` に留まったため今回はしゅつげき等の実行には至らなかったが、
  メニュー上の誤 A は本来送るべきでない入力。
- 16:37 の再発コメント（occurrence `8aa6ecbc3be5cd4f71e4`、ch4 9-1、
  同 `arrival_unconfirmed`）があり、単発ではない。

## 4. 他の判定を採らない理由

- **(b) でない**: `camp_recall_unconfirmed` の「検出」自体は設計どおりだが、
  検出対象の下層イベントは「帰還実行がモード喪失で毎回失敗しうる」実バグ。
  既存修正（#5442/#5450）は免除カウント・task_recheck 乗っ取り・hint 配置・
  goal 未解決フォールバックの範囲で、本件の失敗点に触れない。
- **(c) でない**: 帰還 A は実画面の確認経路を通り送信され、受理画面（`map`
  復帰）が無かった。unconfirmed は正しい。
- **(d) でない**: 決定列と画面種別（`castle_menu`）だけで「発射されていない」
  ことと「戦闘中断が dest/y_route を潰す」機構がコード上確定でき、
  現行 main に修正が無いことも git history で確認済み。

## 5. 残件（read-only では確定不能な補足論点）

- ラン成果物 `hanjuku_frames/` があれば、16:29:08〜37 の世界図が view モードか
  目的地ピッカーか、`castle_menu` の手前の画面遷移を確定できる（結論は変えない）。
- `hanjuku_events.jsonl` の `input_sent` 行の座標・画面証拠があれば、2 発目の A が
  フィールド自城と view モード世界図のどちらへ入ったか確定できる。
- 9-1 の再発 occurrence（`8aa6ecbc3be5cd4f71e4`）の直前決定列: 同様に防衛戦闘が
  介在していたか（介在していれば本機構の再現性を補強）。

記録: issue #5452 へ結論コメント、agent-council の #5452 スレッドへ完了報告。
本レーンはコード変更・マージを行わない（read-only 検証のみ）。

## 付録: 判定シグネチャ

- event / kind: `camp_recall_unconfirmed`（`reason_code: camp_arrival_unconfirmed`）
- signals: `camp_recall_unconfirmed`, `camp_arrival_unconfirmed`
- flow.recall_verification: `{stage: 'await_dispatch', status: 'arrival_unconfirmed', steps: 6}`
- confidence: `high`
- incident_id: `7d7abb367804928ac4e9` / fingerprint: `b8b3fd004c5b06a6`
- 結論: **(a) 実欠陥**（防衛戦闘後の dest 復帰が いどうモード喪失を検出できず、
  view モード世界図 + 城メニューへ入力を送り帰還が未発射。現行 v1482 で未修正、
  #5442/#5450 の対象外）
