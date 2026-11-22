# IMPLEMENT-5105/5108 — [半熟英雄] 帰還で「きかん」を選択 / キャンプ帰還失敗: ch10 将軍不明 (v1389)

- Issues: azumag/docich#5105 (`recall_kikan_selected`)、azumag/docich#5108 (`camp_recall_failed`, ch10、将軍不明)
- Base: origin/main dbb5d9dcc (v1386)
- バージョン: `hanjuku-chart-v1389-idou-y-route-recall-hold`
  - 採番根拠（2026-11-22 コミット前確認）: main は v1386。worktree 調査で
    v1387 は docich-5118 (`sortie-measured-kit`、PR #5128) と
    docich-5086-5089 (`recall-preempt-sortie-gate`) が、v1388 は
    docich-5120 (`boss-month-phase-guard`、#5120 ガード別worktree) が使用中。
    v1389 以降を主張する worktree は無く、v1389 を採用。
- 前提方針: オーナー 2026-11-19「きかん禁止・いどう→Y」

## 原因

- **#5105**: v1362 の修正は通常帰還を `きかん` の全島ピッカーへ戻したが、
  オーナー方針は「きかん禁止・いどう→Y」。`きかん` 選択自体が
  `recall_kikan_selected` (month_audit) として記録される。
- **#5108**: 帰還が進行中に戦闘・無関係な text 画面が前面に出ると、
  `dest` などのステージが 90 観測の操作予算 (`RECALL_LIMIT`) を
  操作不能フレームで消費し、`camp_recall_aborted` (≒ `camp_recall_failed`)
  で帰還が死んだ。ch10 の月次タスク帰還が `wait_recall` 経由で
  全画面を `_camp_recall_step` に通すため特に顕在化した。

## 修正

- `flows/recall.py`:
  - `menu` ステージの `choice` を無条件 `いどう` に変更 (v1362 の
    `きかん` フォールバック廃止)。boss / boss_move / gather /
    move_visible / relay / fort の既存ルートは不変。
  - `dest` の `map` 分岐: boss / boss_move / fort / relay は従来どおり
    中断 (`_hold_after_picker`)。通常帰還は goal が解決できれば
    `stage='y_route'` → 既存 `_y_route_step()` (`RECALL:target` 予算、
    `_start_y_jump` 経路そのまま) へ委譲、解決できなければ
    `map_y_route=True` を立てて Y で全体マップを開く
    (5 観測以内の再押しは抑制)。新規ジャンプ実装は作らない。
  - `_y_route_step` の受領不能フォールバックは `hero` のみ `hero_open`
    (SELECT センタリング) へ。通常帰還は SELECT が誤った将軍を開くため
    abort + `_arm_recall_skip()`。
  - `RECALL_HOLD_TICK_LIMIT = 600` と `_recall_hold_or_abort()` を追加。
    戦闘系 kind・`mem['battle']`・操作不能な text (野営/砦メニューと
    ステータスパネル以外、`camp_recheck.py` と同じ除外条件) で
    `steps` を進めず `hold_since` を記録し、画面は実所有者へ返す
    (`None`)。再開可能画面で `hold_since` をクリア。600 tick 超過時のみ
    既存 abort (`camp_recall_aborted`) + `_arm_recall_skip()`。
    `RECALL_LIMIT=90` は不変。
  - `y_jumped` / `y_jumps` / `y_jumped_res` の RECALL リセット
    (`#4890`・`_reset_boss_jump`) は既存処理を維持。
- `flows/map.py`:
  - `world_map_step` に `recall['map_y_route']` 分岐を追加。旗が読めるまで
    待ち、`_picker_goal()` + `_goal_after_failures()` で自軍城を確定して
    `stage='y_route'` → `_y_route_step()` へ委譲（`_picker_y_route` と同じ
    `_y_route_step` → `_start_y_jump` 経路。`_picker_y_route` は world_map
    で `camp_world` を要求するため、旗確定直後の委譲は `_y_route_step`
    直接呼び）。goal 不能時は首都在城 `roster_read_due` (+180 tick) +
    B 撤退 + `_arm_recall_skip()` (`_map_y_route_give_up()`)。
    リングピッカー確定へは戻らない。
  - `picker_observations >= RECALL_LIMIT` でも `map_y_route` の recall は
    `_picker_fallback` (リング確定) ではなく同じ撤退経路を取る。
  - `world_map_step` 先頭で `recall['hold_since']` をクリア (view 復帰)。
  - `relay_from_picker` は `map_y_route` 分岐より先に評価されるため、
    ピッカーの ▲ で後半キャンプと判明した場合は既存どおり中継セルへ
    再照準される（崖越え送信の抑止を維持）。
- `flows/post_month_status.py`: 月次タスクの帰還生成時に
  `_observe_recall_origin()` と `_camp_world_cell()` で `origin` /
  `camp_world` を付与 (SELECT センタリング後のカーソルがまだテント上
  にある間に計測) し `_reset_boss_jump()` で前回 receipt を消去。
  `_recall_goal` が月次タスク帰還でも目的地を解決できるようにする。

## 変更テスト

- `tests/test_hanjuku_camp_recall_ch2.py`: v1362 期待を全面反転。
  全章 `いどう` 確定、dest→map→y_route、map_y_route→world_map→
  旗で goal 確定、goal 不能時の B+roster_read_due+skip、flag 未読の待機。
- `tests/test_hanjuku_recall_picker_y_route.py`: メニュー選択が
  `いどう` であることの追加固定。
- `tests/test_hanjuku_month_audit.py`: 実フローの `camp_recall_cursor`
  記録が `recall_kikan_selected` を出さない回帰テスト (detector 本体不変)。
- `tests/test_hanjuku_recall_hold.py` (新規): #5108。text 91観測で
  abort しない・戦闘 hold・600 tick で abort+skip・再開画面で
  `hold_since` クリア・`RECALL_LIMIT==90` 固定。
- `test_hanjuku_chart_bot.py` / `test_hanjuku_camp_receipts.py` /
  `test_hanjuku_recall_month_scene.py` / `test_hanjuku_return_status_close.py` /
  `test_hanjuku_chapter9_camp_boss_redirect.py` / `test_hanjuku_boss_camp_remarch.py` /
  `test_hanjuku_retreat_recheck.py` / `test_hanjuku_visible_skip_blocked.py`:
  通常帰還のメニュー歩行を `きかん` (row3 へ down) から `いどう`
  (row0 へ up) へ更新。boss 系・relay・fort は不変。

## 検証

- 対象テスト: 上記変更テストファイル群 + `test_hanjuku_fort_camp.py` /
  `test_hanjuku_recall_relay_4473.py` / `test_hanjuku_recall_dest_jump.py` /
  `test_hanjuku_off_chart.py` / `test_hanjuku_camp_proof_binding.py` /
  `test_hanjuku_picker_tempo.py` / `test_hanjuku_menu_cursor_tempo.py` 等の
  帰還経路を通る既存テスト。
- 実行結果: **未実行**。本環境 (docich-5105-5108 worktree 上のエージェント) では
  `python3` / `pytest` / `gh` の起動がツール承認レイヤで拒否され、
  テストランナーを実行できなかった（計6回試行）。CI の hanjuku-suite での
  確認が前提となる。静的レビューで確認済みの点:
  - `_y_route_step` / `_picker_y_route` / `_start_y_jump` 既存経路の再利用
    （dest→`stage='y_route'`→`_y_route_step`、world_map `map_y_route` 分岐→
    `_picker_goal`+`_goal_after_failures`→`_y_route_step`）。新規ジャンプ実装なし。
  - `RECALL_LIMIT == 90` 不変、`month_audit.py` / `test` 以外の detector 不変、
    `y_jumped`/`y_jumps`/`y_jumped_res` の RECALL リセット (`_reset_boss_jump`) 維持。
  - `hold_since` は `state` が存在する全経路の合流点 (`_camp_recall_step` の
    stage dispatch 直前) でクリア。`state is None` 側の early return は
    リセット対象を持たないため残留しない。`world_map_step` 先頭でもクリア。
  - `_recall_goal` の解決性: `_owned` は captured∪home_castle を返すため、
    新テストの前提（ch2 で captured=['アルマムーン'] → 単一城で goal 解決、
    2城+位置情報なし → 未解決）は chart.home_castle(2)='アルマムーン' と整合。
  - camp_recheck.py の `interrupt()`/`_step_flow` は通常帰還（retreat_recheck
    /anonymous_recheck 無し）を `existing` へ通すため、#5108 hold は実経路で
    到達する。named/recheck 経路の defer 動作は不変。

## 残課題 / 注意

- `month_audit.py` の `camp_recall_failed` / `recall_kikan_selected`
  detector は不変 (issue 通知の責務)。`きかん` を選ぶ経路が無くなったため
  `recall_kikan_selected` は発火しない想定。
- `docs/operations/hanjuku-month-audit.md` の detector 表記は不変。
- `recall.py` 1703-1714 行: `if visible is not None: ... elif visible is not None:`
  の dead branch (`camp_recall_relay_over_visible` 未発火) は main 由来の
  既存問題であり本PRでは未対処。動作上は `_relay_target` が同じ結論を
  出すため中継ルートは維持される。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読。
  GitHub の open PR 一覧は本環境から取得できないが、ローカル worktree
  全数調査で v1389 が他の採番と衝突しないことを確認済み（v1388 は
  docich-5120 が使用するため本ブランチは v1389 へ付け替えた）。
- 前セッションが rate limit で中断した部分実装を引き継ぎ完了させた。
  独立レビューは未実施（本ドキュメントの検証節は静的レビューに基づく）。
