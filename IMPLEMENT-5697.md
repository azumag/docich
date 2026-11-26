# IMPLEMENT-5697 — [半熟英雄] キャンプ帰還失敗: ch10 将軍不明

## 対象

- Issue: azumag/docich#5697「[半熟英雄] キャンプ帰還失敗: ch10 将軍不明」
- 検証記録: `/home/hatch/work/docich-5697/VERIFY-5697.md`、結論 (a) 実欠陥
- base: `origin/main` `54c9c9073`（v1562）を前提とする worktree `fix/5697-camp-recall`
- 新しい hot-load marker: `hanjuku-chart-v1565-camp-recall-dome-text-hold`

## 実装内容

### 1. 月次 task の指名将軍を砦一覧へ伝える

`post_month_status.py` が月次 `recall` / `boss_move` task 用の `flow.recall` を作る際、指名された `general` と同じ名前を `fort_actor` にも入れるようにした。砦に到達したとき、砦一覧は従来どおり `fort_actor` を最優先で選択できる。

加えて `recall.py` の砦一覧では、`fort_actor` が空のまま残った task-owned recall だけ、`general` を選択候補へ fallback するようにした。条件を `task_recall is True` に限定しているため、hero recall・anonymous recall・camp_recheck 経路へこの fallback は適用されない。

### 2. dome の text 例外を menu/fort 画面待ちへ限定する

従来は `camp_kind == 'dome'` なら stage に関係なく無関係な text 画面も recall が所有し、`steps` を消費していた。今回、dome の text 例外を次の stage に限定した。

- `menu`
- `fort_menu`
- `fort_list`
- `fort_status`

これらは、dome 決定後の未分類画面を `DOME_SCREEN_WAIT` で待つ段階、または砦メニュー・一覧・ステータスといった text パネルを読む段階である。

一方、`dest`・`y_route` など後段の move-mode stage では、無関係な text を既存の hold 経路へ返す。したがって戦闘後の長いイベント文などが出ても、recall の 90 観測予算を消費せず、`hold_since` による既存の有界 hold だけが進む。`await_dispatch` / `await_dispatch_stale` の既存扱いは変更していない。

### 3. stage `dest` の abort metric への安全な投影（採用）

検証記録 §8 の検討項目は、既存の incident 投影項目だけを使う最小範囲で採用した。`_abort_camp_recall` が stage `dest` で発火した場合、type と長さを確認したうえで次を decision metric へ入れる。

- `camp`: `state['target']` の 2 要素整数セル
- `target_cell`: 確認済みなら同じく 2 要素整数セル
- `castle`: `state['goal']` の文字列を 48 文字までに制限したもの（既存 incident schema の `castle` 項目へ投影される）
- `general`: 選択した砦メンバーなど、現在の対象将軍文字列を 48 文字までに制限したもの

`month_audit` 側の schema は広げていない。自由形式 text・frame・内部 object は投影しない。既存の投影経路では `camp_event.camp/general/castle` が埋まるため、stage `dest` の repeat cooldown key も従来の null 同士ではなく実際の camp/general/castle 単位へ分かれる。fingerprint の入力である chapter/kind は変更しない。

## 変更ファイル

- `src/docich/hanjuku/flows/post_month_status.py`
  - 月次 task の recall state に `fort_actor` を追加
- `src/docich/hanjuku/flows/recall.py`
  - `DOME_TEXT_OWNED_STAGES` を追加
  - dome の text 例外を menu/fort stage へ限定
  - task-owned recall だけ砦一覧で `general` fallback
  - stage `dest` abort の bounded metric 投影を追加
- `src/docich/hanjuku/version.py`
  - `BOT_VERSION` を v1565 へ更新
- `ops/vm_actions/collect_diagnostics.py`
  - hot-load version allowlist に v1565 を追加
- `ops/vm_actions/tests/test_collect_diagnostics.py`
  - 固定値テストへ v1565 を追加
- `tests/test_hanjuku_post_month_status.py`
  - 月次 task が `fort_actor` を残すことを確認
- `tests/test_hanjuku_fort_camp.py`
  - 砦一覧の指名選択、dome/dest の text hold、dest abort metric の回帰テストを追加

## 追加した回帰テスト

- `test_recall_task_starts_the_named_return_on_the_selected_camp`
  - 月次 task の recall state が `general` と `fort_actor` の両方に指名将軍を保持することを確認する
- `test_a_monthly_task_recall_without_fort_actor_still_selects_its_named_general`
  - 旧来の state 形状（`fort_actor` なし）でも、task-owned recall は砦一覧で指名将軍へカーソルを動かすことを確認する
- `test_a_non_task_recall_does_not_use_general_as_the_fort_selection_fallback`
  - task-owned でない recall は `general` fallback を使わず、従来どおり砦一覧の現在行を選ぶことを確認する
- `test_dome_menu_text_still_belongs_to_the_dome_screen_wait`
  - stage `menu` の dome では、従来どおり text が `DOME_SCREEN_WAIT` の所有に残ることを確認する
- `test_dome_dest_text_is_held_without_spending_the_recall_budget`
  - `camp_kind == 'dome'`・stage `dest`・無関係 text・`steps == RECALL_LIMIT` でも、text は real owner へ返され、`steps` が増えず abort しないことを確認する
- `test_dest_abort_projects_camp_goal_and_the_selected_fort_member`
  - stage `dest` で上限 abort した metric に、camp・target_cell・goal 由来の castle・選択将軍が残ることを確認する

## 検証

- `git diff --check`: 成功（exit 0）
- pytest: この環境には無い前提のため未実行。CI の hanjuku-suite で確認すること
- `python3 -m compileall`: 今回の VM 置き換え後環境では Python 起動を伴う実行が拒否され、変更ファイル限定・`PYTHONPYCACHEPREFIX=/tmp` 隔離の `python` fallback も拒否された。そのため **今回の追加変更後の compileall は未実施**である。前セッションでは既存変更分の compileall が通っていた旨の引き継ぎがあったが、本ドキュメントでは今回分を成功扱いにしない

## 残件

- CI の hanjuku-suite で関連テストを確認すること
- 検品ゲートまたは実行可能な環境で、変更ファイルの `compileall` を再確認すること
- `VERIFY-5697.md` に残っていた fingerprint / occurrence raw の sha256 数値照合は実装対象外。検品ゲート側で再計算すること
- git commit・push・PR 作成、issue/Slack 投稿、本番 VM 操作は本 worktree では未実施

## 補足

- 非公開正本 `handoff.md` の参照は、この環境では `DOCICH_HANDOFF_PATH` 確認自体が拒否されたため未読扱いとする。内容は推測していない
- 機密情報・実値トークン・鍵・パスワードは本ドキュメントおよび変更ファイルへ記載していない
