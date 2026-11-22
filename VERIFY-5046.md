# VERIFY-5046 — [半熟英雄] 卵回復サブ画面が数量確認後に終了せず month_sub_abort→abandoned

- Issue: azumag/docich#5046。`month_sub_open（たまごのかいふく）→ egg_recover_select
  → quantity_confirm_accept → month_sub_abort → month_sub_abort_text_advance
  → month_sub_abandoned → recruit_deferred_egg → month_done`（支払い結果が未確認のまま）
- 作業 base: origin/main 3a01ae711（worktree、commit/push なし）
- handoff: `DOCICH_HANDOFF_PATH` 未設定のため正本未読。コードとリポジトリ内の事実を根拠に調査
- DSH 重複確認: worktree に VERIFY-5046.md 等の成果物なし。gh は承認されず issue
  スレッドは未確認のため、DSH の結論は取得できず。本調査は独立に実施

## 結論: 欠陥（2箇所）。修正実装（v1369）

## 原因

v1124（de512ee3d、#4573）で導入された購入確認の早期受理が、卵回復フローの
**自身の見積もり確認画面を横取り**する。

### 1. 横取りの仕組み

- `orchestrator._is_quantity_confirm`（orchestrator.py:544）は
  `kind in ('shop_quantity','shop_quantity_prompt','month_menu')` かつ
  本文に「よろしいでっか」＋「うむッ」「いかんッ」を含む画面を、月一サブ画面
  フローより先に `quantity_confirm_accept` → `pad('a')` で受理する
  （orchestrator.py:1565-1574）。動機は g1190 63-5 の兵士数量確認の数分停止で、
  対象は商人系の数量購入だった。
- しかし卵回復の**全回復見積もり**「NこでNGになりまんな! よろしでっか?」は
  「なりまんな」を含むため `classify_text`（os_layer/screen.py:1467）で
  `shop_quantity` に分類され、同じ手（うむッ初期位置）と文言を備える
  → 条件に完全一致し、`_egg_recovery_step` が処理する前に orchestrator が
  A を送信する。
- 主人公単独・将軍個別の定額見積もり（「50Gかかりますがよろしいですかな」）は
  「よろしいでっか」を含まないため影響を受けない。**全回復経路だけ**が壊れる。

### 2. 横取りが生む崩壊列

`_egg_recovery_step` の見積もり分岐（flows/egg_recovery.py:213-287）が
走らないため、次が全て欠ける:

- `sub['stage']` が 'recovering' にならず `quoted_cost`・`confirm_identity`・
  `confirm_actor`・`confirm_chapter` が未設定。`egg_recover_confirm` も記録されない。
- 所持金・見積もり整合（`quoted_cost == count×50`、gold ≥ 見積+修理予約+留保）の
  **検証も拒否も通らず、支払いAが無検証で送信される**。
- 儀式フレームの押し切り上限が `EGG_RITUAL_LIMIT=64` ではなく
  `MONTH_SUB_LIMIT=8` のまま（month.py:1800-1802）。回復儀式の詠唱・会話は
  8観測を超えるため `month_sub_abort` → B では閉じない儀式へ
  `month_sub_abort_text_advance` → `MONTH_SUB_ABANDON_BACKS=20` で
  `month_sub_abandoned`。
- 支払いフェード・返却一覧の「購入済み」ガード（egg_recovery.py:153,166,358）は
  全て `stage=='recovering'` 前提で不発。返却一覧に戻った場合は
  `stage != 'recovering'` の分岐（:288）でぜんかいふくを**再選択し二重支払い**
  しうる（本事象は abort が先に立ったため未発火だが同根の欠陥）。
- 終端記録が `shop['egg'] = 'skipped'`（month.py:1817-1818）。支払いAを
  送った可能性のある取引を「未実施」と記録し、`_prepare_recruit_egg_check`
  （economy.py:573）が同月に再 pending 化 → 再購入しうる。

結果として issue 記載どおり「数量確認後にサブ画面が終了せず
month_sub_abort→abandoned、支払い結果が未確認のまま」となる。

### 3. #5045（#4980/#4989 残余ギャップ）との関係

- `skipped` は終端扱いなので v1363（#4980）の解放条件では hold が外れ、
  `deferred_egg` → cards 解放へ進む。一方で「支払いA送信済み」を skipped と
  記録するのは嘘であり、`unverified` として残すべき終端。
- `unverified` の卵で保留が続く問題（`recruit_deferred_egg` の当月終端化）は
  IMPLEMENT-4980 の検品ゲート修正で「支払い状態が不明なまま築城へ進む二重支出
  リスクより小さい」として DSH・owner 判断へ回された既知の残件（#5045 側）。
  本修正はその「未確認」を正しく記録する側であり、#5045 の永久保留そのものは
  解消しない（意図的に維持: 未確認支払いは再試行も完了扱いもしない設計凍結）。

## 変更（v1369 で実装済み）

- `orchestrator._decide_impl`: 追跡中の `month_sub` が `kind=='egg'` の間は
  `quantity_confirm_accept` を発火せず、`month_sub_step` → `_egg_recovery_step`
  へ見積もり画面を渡す（兵士数量確認＝month_sub 非保持は従来どおり受理）。
- `flows/month.py` の `month_sub_abandoned`: `kind=='egg'` かつ
  `stage=='recovering'`（＝見積もり受理A送信済み）は `skipped` ではなく
  `unverified` を記録。支払い未確認の終端を `skipped` と偽らず、同月の再購入を
  防ぐ（`unverified` は「再試行も完了扱いもしない」既存語彙）。
  `observed_metric.shop_status` に採用した終端を記録。
- バージョン: `hanjuku-chart-v1369-egg-quote-owned`
  （version.py、collect_diagnostics.py 許可リスト）。
- 回帰テスト: `tests/test_hanjuku_quantity_confirm.py`（卵サブが自身の
  見積もりを所有し費用を束縛すること／拒否できること／他サブ・無所有者では
  従来受理が残ること）、`tests/test_hanjuku_month_sub_abandon.py`
  （recovering 放棄→unverified、未確認放棄→skipped 維持）。

## 検証

- ローカル pytest / python3 実行は exec が拒否され未実施。コードトレースで確認:
  - 全回復選択後の `shop_quantity` 見積もりは guard により `month_sub_step` へ
    （kind≠'month_menu' で到達、`recruit_input_guard`/`_paid_recruit_candidate`
    は kind='egg' で早期 None）。
  - `_egg_recovery_step` の見積もり分岐で `menu_to('うむッ!')` → 'here'
    → quoted_cost/stage/confirm_* を束縛して A（egg_recover_confirm）。
  - 以後の儀式は `stage=='recovering'` で `EGG_RITUAL_LIMIT=64`、返却一覧は
    既存の B クローズ経路、月メニュー復帰で `_finish_month_sub` が
    gold 差分で `done` を照合（g719 の既存テスト群と同じ設計経路）。
  - 見積もり未受理（stage≠'recovering'）での放棄は従来どおり 'skipped'。
    `payment_interrupted` の卵サブは month.py:1703 の早期Bループ側が
    処理するため abandon 分岐には到達しない（条件に含めていない）。
- 未実施のため CI で `test_hanjuku_quantity_confirm.py`・
  `test_hanjuku_month_sub_abandon.py`・周辺（`test_hanjuku_recovery_list_return`、
  `test_hanjuku_partial_egg_recovery`、`test_hanjuku_ritual_*`、
  `test_hanjuku_month_status_single_read`）の回帰確認が必要。
