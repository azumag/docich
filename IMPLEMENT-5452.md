# IMPLEMENT-5452 — 目的地操舵中の戦闘中断でいどうモード喪失を扱う

docich issue #5452。検証記録 VERIFY-5452.md（PR #5462、結論 (a) 実欠陥）の
機構に従い実装した。

## 欠陥（再掲）

防衛戦闘が「いどう→全体マップ(Y)」の目的地選択を中断したあと、`dest`
ステージの再開ロジックが戦闘後の通常フィールド（`map`）を「いどうモード
継続中のフィールドカーソル」と区別せず Y ジャンプを再開した。いどう
モードは戦闘で死んでいるため開いた全体マップは view モードであり、
その世界図での自城 A は目的地指定ではなく城メニュー（`castle_menu`）を
開き、続く確定 A が城メニュー内へ入って帰還は発射されなかった。
終端は `(野営, 城)` ペア拒否として `camp_recall_goal_failed` に誤計上
されていた。

## 実施内容

### 1. 目的地操舵中の戦闘中断に復帰保護（`_mark_recall_battle` 拡張）

- `MOVE_MODE_STAGES = ('dest','y_route','relay','hero_focus','hero_open')`
  を新設し、`_mark_recall_battle` の `battle_return` 付与対象へ追加。
  `to_camp`/`menu` は従来どおりカーソル位置リセット（`_battle_return_reset`）
  のみで、move-mode 群は別経路を取る。
- 新規 `_recall_move_mode_lost`：戦闘でいどうモードが死んだ前提で、
  その場の受領・再開を一切せず帰還をやり直す中断処理。
  - `mem['y_jump']` が step=RECALL なら除去、`_reset_boss_jump` で
    `y_jumps['RECALL:target']`/`y_jumped['RECALL']` と `y_jumped_res` を消去
    （`observe_sortie_transition` が `forget_target_placement` で行う
    受領破棄と同機構）。
  - `recall` を破棄し `mem['uncertain']`、短い復帰 hold（`FORT_RETRY_TICKS`、
    boss 系は `BOSS_RETRY_TICKS`、hero は従来どおり hold 無し）。
  - `camp_recall_interrupted`（`resulting_event='move_mode_lost'`）を記録し、
    `camp_battle_interrupt` 監査シグナルにも載る。
  - メニュー/ピッカー/全体マップ系画面では B で閉じる。
- `battle_return` 消費ロジック（`_camp_recall_step`）:
  - `map`：move-mode ステージでは `_recall_move_mode_lost`（Y 再開しない）。
  - `map_target` + `dest`：ピッカー自体は戦闘を生き延びうるため、受領を
    消して通常 dest 処理へフォールスルーし、新規ジャンプでマーカーを置き
    直す（受領による盲目確定をしない）。
  - `world_map` / 他ステージの `map_target`：中止。
  - その他の画面：次の判定可能な画面までマーカー保持（従来と同じ）。

### 2. dest ステージの `map` 扱いを守る

- 上記 `battle_return` 分岐で、戦闘を経た `map` を「いどう継続中の
  フィールドカーソル」とみなさない（`hero_focus` の `map`→`y_route`
  自動遷移、dest の `map`→goal 解決→`_start_y_jump` も同じ穴だった）。
- `map.py world_map_step`：戦闘後の `world_map` は view/picker を区別
  できないため、`battle_return` 中の move-mode ステージでは帰還の
  `y_jump`（step=RECALL）を捨てて mode-lost 中止。他フローのジャンプが
  所有する world_map には B を送らない。

### 3. 誤入力の防止（`castle_menu` へ A を送らない）

- `_y_route_step`：`y_jumped['RECALL']` の受領が出ていても、確定 A は
  `('map','map_target','world_map')` のみで発射。`castle_menu`/
  `general_list`/`castle_info`/`main_menu` や camp/fort メニューは
  mode-loss として `_recall_move_mode_lost`（B で閉じる）。その他
  （unknown/text 等）は hold。
- `_camp_recall_step`：move-mode ステージ中に `castle_menu` 系が出たら
  即 mode-lost。camp/fort メニューは `いどう` 選択直後の閉じフェード
  があり得るため `steps > 2` のみ mode-lost とする。
- dispatch 終端の B 対象に `castle_menu` 系を追加（誤って開いた城メニューを
  畳む）。

### 4. `camp_recall_goal_failed` の誤帰属を直す

- `_recall_dispatch_step` が観測した screen を `dispatch_screens`（直近 6）
  に記録。終端時 `castle_menu` 系を含めば `mode_lost` として：
  - `recall_goal_failures` への (camp, castle) ペア計上と
    `camp_recall_goal_failed` 記録を抑止し、代わりに
    `camp_recall_mode_lost`（`resulting_event='move_mode_lost'`）を記録。
  - `terminal_metric` に `mode_lost` / `recent_screens` を残す。
  - `recall_verification['interrupted']` を mode_lost でも立て、
    `recall_skip` も interrupted 相当の短い hold にする。
- `month_audit._camp_event`：`mode_lost` / `recent_screens` を
  camp incident へ転写し、監査・学習側が mode 喪失終端を判別できるようにした。
  `camp_recall_unconfirmed` の incident 自体は従来どおり（検証で
  「真の検出」だったため）。

## 変更ファイル

- `src/docich/hanjuku/flows/recall.py`
  - `MOVE_MODE_STAGES` 追加、`_mark_recall_battle` 拡張、
    `_recall_move_mode_lost` 新設、`battle_return` 消費ロジック改修、
    move-mode 中の castle/camp メニューガード、`_y_route_step` の確定 A
    画面制限、`_recall_dispatch_step` の `dispatch_screens`/`mode_lost`/
    ペア誤計上抑止・終端 B 拡張。
- `src/docich/hanjuku/flows/map.py`
  - `MOVE_MODE_STAGES` を import、`world_map_step` 冒頭に post-battle
    world_map の mode-lost ガード（RECALL ジャンプ破棄＋再開抑止）。
- `src/docich/hanjuku/orchestrator.py`
  - `_recall_dialog_wanted`：move-mode ステージ中の `castle_menu`/
    `general_list`/`castle_info`/`main_menu` も帰還フロー所有とし、誤って
    開いたメニューを帰還自身が B で閉じて再開できるようにした（従来は
    所有外でステップ上限まで停滞した）。
- `src/docich/hanjuku/month_audit.py`
  - `_camp_event` に `mode_lost`/`recent_screens` 転写を追加。
- `src/docich/hanjuku/version.py`
  - `hanjuku-chart-v1496-recall-battle-mode-lost`（検品ゲートで付け直し。実装時は v1492 だったが、main が v1493・PR #5508 が v1494・PR #5514 が v1495 のため、空き番号 v1496 へ変更）。
- `ops/vm_actions/collect_diagnostics.py`
  - allowlist へ同文字列を追加（末尾カンマ）。
- `ops/vm_actions/tests/test_collect_diagnostics.py`
  - サンプル一覧へ同文字列を追加。
- `tests/test_hanjuku_recall_battle_mode_loss.py`（新規）
  - MOVE_MODE_STAGES 各ステージの戦闘 → `battle_return` 付与、
    戦闘後 `map` で Y/A を出さず mode-lost 中止（recall 破棄・短い hold・
    `camp_recall_interrupted`/`move_mode_lost` 記録）、RECALL `y_jump`/
    `y_jumped` 破棄、`dest`+`map_target` 生存時の受領消去→新規ジャンプ、
    `castle_menu` 中の dest/y_route が mode-lost+B、非ピッカー画面での
    確定 A 抑止、`world_map_step` の post-battle ガード、
    castle_menu 終端で `camp_recall_goal_failed` 非計上・
    `camp_recall_mode_lost` 記録・`mode_lost`/`recent_screens` metric、
    `map_target` 終端の従来ペア計上維持、`to_camp` の従来 reset 維持。

## テスト結果

- ローカル実行は行えていない。2 セッション連続で `python3 -m pytest`
  `pytest` `gh` を含む exec 呼び出しが環境の承認で拒否された
  （2026-11-25）。変更は読み返しによる静的レビューのみで確認済み。
- 静的検証で確認した対応:
  - `MOVE_MODE_STAGES` = `('dest','y_route','relay','hero_focus','hero_open')`
    は recall.py L60 に存在し、`_mark_recall_battle`（L477-480）の
    `battle_return` 付与対象へ追加済み。
  - `is_camp_menu`/`is_fort_menu`/`_reset_boss_jump`/`_arm_recall_skip`/
    `BOSS_RETRY_TICKS`/`FORT_RETRY_TICKS` はすべて recall.py 内で
    import または定義済み。`map.py` の `_flow` は L37 で import 済み。
  - `_y_route_step` の確定 A 画面制限（L1153-1164）と
    `_finish_hero_recall`→`_request_recall`（`camp_recall_requested`）
    の呼出関係を確認。
  - `_camp_recall_step` の `battle_return` 消費（L1553-1592）、
    move-mode 中メニューガード（L1593-1606）、`_recall_dispatch_step`
    の `dispatch_screens`/`mode_lost`/ペア計上抑止（L2562-2738）を確認。
  - `world_map_step` の post-battle ガード（map.py L569-583）、
    `_recall_dialog_wanted` の castle_menu 系回送（orchestrator.py
    L934-940）、`month_audit._camp_event` の `mode_lost`/`recent_screens`
    転写（L845-850）を確認。
  - `recall_verification` の消費側（post_month_status.py L1563/L2500、
    camp_recheck.py）は `.get()` 参照のみのため `dispatch_screens` 追加で
    形状検証を壊さない。`_REGION_KEY['flow']='flow'` とテストの
    `mem['flow']` 構築は整合。
  - 新規テストの想定経路（battle→`battle_return`、post-battle `map`→
    `[]`+recall 破棄、`map_target`+`dest` 生存時の受領消去→`_start_y_jump`、
    `castle_menu` 終端の mode_lost 計上）を呼出経路ごと追跡済み。
- 採番: `version.py` は `hanjuku-chart-v1496-recall-battle-mode-lost`（検品ゲートで v1492 から付け直し）。
  ローカル main の最新は v1491（commit 231ec8fbd）。open PR の採番は
  `gh` が拒否されるため未確認 — 検品ゲートで衝突有無を確認のこと。
- `DOCICH_HANDOFF_PATH` 未設定のため handoff 正本は未読（AGENTS.md §1）。
- 実行すべき対象（CI または検品側で実施のこと）:

```
python3 -m pytest tests/test_hanjuku_recall_battle_mode_loss.py \
  tests/test_hanjuku_recall_hold.py tests/test_hanjuku_recall_interrupt.py \
  tests/test_hanjuku_recall_dest_jump.py tests/test_hanjuku_camp_observation.py \
  tests/test_hanjuku_camp_recheck*.py tests/test_hanjuku_camp_*.py \
  tests/test_hanjuku_map_*.py tests/test_hanjuku_month_audit*.py \
  ops/vm_actions/tests/test_collect_diagnostics.py -x -q
```

## 残件・既知の注意点

- `dest`+`map_target` 生存時は受領消去後に通常 dest 処理へ継続する設計。
  「ピッカー復帰不可＝中止」へ寄せる判断もあり得るが、ソーティ側の
  `battle_interrupt` が受領だけ消して再駆動する機構に合わせた。
- `hero_focus`/`hero_open` の post-battle `map` も mode-lost 中止
  （hero は skip 無しで自然に再検出される）。
- `menu`/`to_camp` の `battle_return` 動作・`await_dispatch` の
  `recall_interrupted` 動作・`picker_closed`/`dispatched` 受理判定は不変。
- `_recall_move_mode_lost` は `camp_recall_interrupted` を記録するため
  `camp_battle_interrupt` シグナルに加算される。mode_lost 固有の件数が
  欲しければ `resulting_event='move_mode_lost'` と
  `camp_recall_mode_lost`（終端）で分離可能。
- push・PR は未実施（検品ゲートで Muse が実施）。
