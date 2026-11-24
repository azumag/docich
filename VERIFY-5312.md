# VERIFY-5312 — [半熟英雄] キャンプ帰還キャンセル: ch10 将軍不明

- 判定: **(b) 設計どおり、または既存の修正でカバー済み**（owner の Draft 修正 PR #5315 / v1445 が本件の観測経路をカバー。残留課題あり、§6 参照）
- 検証方法: read-only。issue 本文（依頼文の要点）+ 現行 `origin/main`（01d44e161）のコード + `origin/fix-5312-dome-camp-event-v1445`（PR #5315 相当ブランチ）の差分で判定。
- **gh 未使用**: 本環境では `gh` の起動確認・issue 参照が実行できなかった（コマンド不許可）。issue 本文は依頼文の記載のみを根拠にした。生 incident JSON は未取得のため、screen kind 等の実測値はコードと PR テストからの推論で補完せず、確度を明記した。
- **テスト未実施**: `pytest` 実行が本環境で許可されなかったため、関連テスト（`tests/test_hanjuku_dome_camp.py`、`tests/test_hanjuku_concert_text_advance.py`）は未実行。ソース精読による静的確認のみ。

## 1. 観測経路（現行 main のコード確認）

issue の記録と一致する経路は `src/docich/hanjuku/flows/recall.py` の dome 専用の bounded close:

- `recall.py:1729-1742` — `stage == 'menu'` かつ `camp_kind == 'dome'` で、`is_camp_menu` でも `is_fort_menu`（`recall.py:1698-1699` で先に `enter_fort` へ分岐）でも `map` でもない画面が `DOME_SCREEN_WAIT = 6`（`recall.py:33`）観測続くと:
  - `_flow(mem).pop('recall')`、`mem['uncertain'] = True`、`_arm_recall_skip(mem, state)`（base=None → 既定 `RECALL_SKIP_TICKS = 400` 観測の camp-scoped クールダウン、`recall.py:24`, `:268-299`）
  - `_record(mem, 'camp_group_screen', resulting_event='unclassified_screen_closed', observed_metric={'camp','screen': screen.kind,'text': (screen.text or '')[:80]}, …)` → `[pad('b')]`
- issue の「結果 cancelled / 決定 camp_group_screen / stage 不明 / steps None / status unclassified_screen_closed」はこの record と一致（`observed_metric` に stage/steps を含めない設計のため「不明/None」表示になる）。
- audit 側: `month_audit.py:733-793`（`CAMP_TERMINAL` と `_camp_event`）で `camp_group_screen`+`unclassified_screen_closed` → `kind=camp_recall_cancelled / outcome=cancelled`、`reason_code=camp_unclassified_screen`（`:763-764`）。ドキュメント済みの incident 種別（`docs/operations/hanjuku-month-audit.md:74`）。

直前の決定列（camp_enter → camp_fort_sortie バラクーダ → y_jump RECALL ボスへ）は、**同一 dome [131,113] に対する先行する別 recall flow** が fort route（`enter_fort` `recall.py:2172-2176` → `_fort_step` `:2199-2259` → `camp_fort_sortie` `:2244` → picker → `_request_recall` `:2298` → `await_dispatch` → dispatched `:2430`）で 1 名将軍の帰還 dispatch を完了したことを示す。fort route は 1 recall につき将軍 1 人を送る設計であり、dispatch 後 flow は `_flow(mem).pop('recall')` で終了（`:2395`）する。**本 incident はその後に再検出された同一 dome への 2 回目の recall** が、menu stage で測定済み砦メニュー以外の画面に出くわしてキャンセルされたもの。「既に dispatch 済みの帰還がキャンセルされた」のではなく、「残り将軍を出す次の帰還が中断された」と読むのが正しい。

## 2. 実欠陥か設計どおりか

- **機構は設計どおり**: コメント「The dome opens the measured 砦 menu above; anything else on it is unmeasured. Keep its frame and leave it.」（`recall.py:1730-1731`）の通り、未測定画面を記録して有界に B で閉じる保守的 fallback。テストでも意図が固定されている（`tests/test_hanjuku_dome_camp.py:70-85`、未実行だがソース確認）。
- **しかし発生原因は実際の協調ギャップ**: menu stage での待機中、`orchestrator.py:2448` の汎用 `event_text_advance`（`orchestrator.py:176-215`、concert/event/dialogue phase の kind=`text` が 3 観測不変で A 送出）は recall が `[]` を返す間も発火しうる（`not actions or typing`）。recall 側の `menu_other` は `text` 画面を「その他画面」として無条件カウントするため、汎用ハンドラが既に処理できるイベント通知文（例: 被攻撃通知）が出ただけで 6 観測後に recall が放棄され、camp が 400 観測（実効 ~10 分）スキップされる。これは「未分類画面」を見逃すための機構が、**分類可能な既知のイベント文にも発火した**ケースであり、issue は誤検知ではなく実挙動の検出。
- 画面種別について: issue 本文には screen kind が出ていない（§5 の投影欠損）。ただし PR #5315 ブランチの再現テストが `target=[131,113]`・`chapter=10`・`text='せめこまれました'` と本 incident と同じ値を使っており、観測画面は防御系イベントの `text` 画面だったと強く示唆される（推論、incident JSON 未取得のため断定はしない）。

## 3. #5306 / #5311 との異同

同じキャンプ帰還 family だが terminal・stage・機構がすべて異なり、別経路の別 incident と確認:

| issue | decision | 発火箇所 | 機構 |
|---|---|---|---|
| #5306 arrival_unconfirmed | `camp_recall_unconfirmed`（status=`arrival_unconfirmed`） | `recall.py:2355-2368, 2448-2450`（`await_dispatch` stage、input_sent 済み・picker_closed 未確認） | dispatch 後の受理/到着未確認 terminal |
| #5311 abort/上限 | `camp_recall_aborted` | `recall.py:1500-1525`（`steps > RECALL_LIMIT = 90`） | 90 観測の操作予算使い切り abort |
| #5312 cancelled | `camp_group_screen` / `unclassified_screen_closed` | `recall.py:1729-1742`（`menu` stage + `camp_kind='dome'`） | dome 限定の未測定画面 bounded close |

いずれも `camp_*` 終端は `month_audit.py:_camp_event` に集約されるが、発火コードパスは独立しており同一根因の重複ではない。

## 4. PR #5315（v1445 Draft）のカバー確認

`origin/fix-5312-dome-camp-event-v1445`（`git diff origin/main...` で確認、+237/-2、主要差分 `orchestrator.py` +67 行）:

- 追加 `_dome_camp_menu_text_owned(recall, recall_dialog, kind)` — recall が `stage=='menu'` かつ `camp_kind=='dome'` の `text` 画面を「dome recall の所有する文面」として認識（当初 `('text','unknown')` → コミット `9008b444e` で `kind=='text'` に narrowed）。
- `event_text_advance_step`（新規ラッパ）— 汎用イベント A が発火した際、dome recall が条件に合えば **`menu_other = 0` に一度だけ戻し** `event_text_advance_restarted = True` を立てる（`orchestrator.py` diff 内、`:2504-2513` 付近で `dome_recall` として接続）。2 度目以降の A はリセットしない＝bounded を維持。
- 効果: 一過性のイベント文（汎用 A が 1 回進めれば消える文面）が dome menu wait を殺さなくなる。文が消えた後に map/砦メニューが現れれば、既存の reopen 経路（`recall.py:1743-1771`）か fort menu 検出で recall が継続する。
- **本件経路との関係**: 観測画面が `text` 種別であれば（§2 の推論通りなら）、本件の発火条件そのものをカバーする。ただし注意点:
  - **多ページ/持続するイベント文にはキャンセルが依然発火する**。PR 自身のテスト `test_dome_unclassified_text_is_held_then_closed_after_bounded_wait` が「text が残れば bounded close は起きる」ことを断言しており、incident 抑制ではなく緩和。
  - **`text` 以外の kind（`unknown`、他メニュー種別）の未分類画面はカバー外**。`_dome_camp_menu_text_owned` は `kind=='text'` のみ。
  - Draft 状態で未マージ（検証時点の `origin/main` には未取込み）。

## 5. camp_unclassified_screen の記録 → 分類・学習経路

- **設計として学習経路はある**: `_record` が `observed_metric.screen`（画面 kind）と `observed_metric.text`（文言先頭 80 文字）を保持し（`recall.py:1738-1741`）、`hanjuku_decisions.jsonl` の行には `observed_metric` 全体と top-level `screen_kind` が残る（`brains/hanjuku/bot.py:244-248`）。incident 化 → issue 起票 → 人/owner が画面を測定して分類を追加する運用で、実際に本件から PR #5315 が生まれている（放置されていない）。
- **ただし投影欠損あり（残留課題）**:
  - `month_audit.py:827-828`: `camp_event.screen` は `metric.get('screen_kind') or record.get('screen_kind')` で top-level に救われるが、`screen_text` は `metric.get('screen_text')` を読み、record 側は `text` キーで書くため **常に None**（キー不一致）。
  - `month_audit.py:3814-3819`: issue 本文の「最終画面/文言」ブロックは `fort_menu_observations` 等が non-None のときだけ描画されるが、`camp_group_screen` record は fort_* を一切含まないため **本 incident 種別では画面情報が issue 本文に一切出ない**。issue #5312 に画面種別が無いのはこのため。
  - 結果として issue だけでは「どの未分類画面か」を判断できず、後続の分類作業は runtime jsonl の参照を要する。`text` → `screen_text` のキー不一致と、fort 限定描画条件の拡張は別途の小さな欠陥として残る。

## 6. 結論と残件

- **結論: (b)**。キャンセル機構自体は設計どおり（記録→有界 B 閉じ→scoped skip）。発生の直接因（dome menu wait 中の既知イベント `text` 画面への未協調）は owner の修正 PR #5315（v1445、Draft）が本 incident の観測値（[131,113]、ch10、`せめこまれました` 想定）を再現するテスト付きでカバーしている。誤検知でも判定不能でもない。
- 残件（本 issue の判定には影響しないが記録する）:
  1. PR #5315 は未マージの Draft。マージ後も、`text` が持続する場合や `text` 以外の kind の未分類画面では `unclassified_screen_closed` は再発しうる（設計の意図通りの保守動作）。
  2. `month_audit.py:827-828` の `text`/`screen_text` キー不一致と `:3814-3819` の fort 限定描画条件により、この incident 種別の issue 本文から画面証拠が欠落する。分類・学習ループを issue だけで完結させるなら別途修正が必要（本検証は実装しない）。
