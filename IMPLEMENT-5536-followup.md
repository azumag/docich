# IMPLEMENT-5536-followup — egg_attempted 二元フラグと有界再試行の不整合

- Issue: azumag/docich#5536「[半熟英雄] 卵を残して敗北: ch8 キッシュ」
- 調査対象: コメント 7190414700（訂正結論 (d)）で指摘された実装不整合が実欠陥かどうか
- 基準 HEAD: `62dca40deec9ecbc22e33494748ba94431228d8c`（origin/main、detached HEAD）
- 作業日: 2026-11-26
- handoff: `DOCICH_HANDOFF_PATH` を確認する `printenv` 実行が拒否されたため、正本は未読扱い。GitHub issue と現行コードを根拠に実施した。
- 制約遵守: push・PR 作成・issue コメント投稿・Slack 投稿・本番 VM 操作は未実施。VERIFY-5536.md の再作成はしていない。

## 結論: **(a) 実欠陥**

`_egg_before_retreat` と `_early_resource_rescue_needed` は、卵選択が使用確定にならなかった場合に

1. 通常上限 `EGG_ATTEMPT_LIMIT = 2` までの再試行
2. 直前試行時より HP が低下した場合の `EGG_ATTEMPT_HARD_LIMIT = 4` までの有界追加再試行

を許可する設計だった。しかし実際に開いた戦闘メニューを処理する `_survival_menu` は、白い `たまごをつかう` 行が `labels` に存在しても `rescue['egg_attempted']` が一度 True になると卵枝へ進まなかった。

したがって、外側の判定が「再試行してよい」と判断してメニューを開いても、内側のメニュー処理が卵を選べず、切り札もなければ `battle_survival_unavailable` / `rescue['exhausted']` へ落ちる経路が実在した。これは設計意図と実装が矛盾する実欠陥である。

### 設計意図の裏付け

- v726 (`0f1b7f08b`) の記録では、`egg_attempted` が恒久フラグで「その戦闘では二度と卵を試さない」ことを問題として、`EGG_ATTEMPT_LIMIT = 2` による有限再試行を意図していた。
- v1398 (`02844c7fc`) は、使用未確認でも HP 減少後は再試行するオーナー決定 (#5143/#5144) を `egg_attempt_hp` と `EGG_ATTEMPT_HARD_LIMIT = 4` に反映した。
- ところが両変更とも `_egg_before_retreat` 側の許可と `egg_attempts` 加算を整えただけで、`_survival_menu` の卵枝条件 `not rescue['egg_attempted']` を更新していなかった。

## 衝突する制御フロー

### 外側: 再試行を許可する

修正前の `_egg_before_retreat`（`src/docich/hanjuku/judgment/battle.py`）は、以下の条件なら False ではなく再試行許可を返した。

- 卵台帳が正で、卵0と確定していない
- `egg_attempts < 2`、または
- `egg_attempts < 4` かつ現在 HP が `egg_attempt_hp` より低下
- `rescue['exhausted']` がなく、opens / pending_opens の予算内
- 召喚獣戦・イベント決闘・召喚クラス敵などの除外条件に非該当

`_early_resource_rescue_needed` も同じ上限計算を複製していた。

### 内側: 卵枝を外し続ける

修正前の `_survival_menu`（`src/docich/hanjuku/flows/battle_menus.py`）は、卵選択可否を次で決めていた。

```python
egg_ready = ('たまごをつかう' in labels and not rescue['egg_attempted']
             and not _own_egg_empty(mem, cur))
```

このため、外側が再試行を許可しても、

- 1回目の卵選択が A 確定まで進んだが召喚へ移行しなかった
- その後も白い `たまごをつかう` 行が読める
- `egg_attempts` 上はまだ再試行できる
- HP が低下したため hard-limit 側の再試行条件も満たす

という実戦条件でも、`egg_attempted=True` だけで卵枝が外れた。切り札がない・使い切った状況では `battle_survival_unavailable` に進み、`rescue['exhausted']` を立てて白兵へ戻る。これが「卵が残っている・HP が低下している・退却/生存判断が絡む場面で、卵枝が外され続ける経路」である。

## 既存テストの扱い

- `tests/test_hanjuku_combat_strength.py`
  - `test_the_egg_menu_gets_one_bounded_retry` は外側 `_egg_before_retreat` の許可だけを検証していた。
  - `test_a_failed_egg_selection_retries_after_the_general_loses_more_hp` も HP 低下時の外側許可だけを検証していた。
  - どちらも、再許可後に開いた `_survival_menu` が実際に卵を再選択するかは未検証だった。今回 `test_the_hp_drop_retry_can_reselect_the_visible_egg_in_the_menu` を追加した。
- `tests/test_hanjuku_survival.py::test_no_safe_card_returns_to_parent_menu_and_uses_egg`
  - 1回目の卵選択失敗後、次のメニュー観測で B に進む旧挙動を事実上契約化していた。
  - v726 の「bounded second selection」と矛盾するため、2回目の卵選択を期待する内容へ更新した。
- `tests/test_hanjuku_egg_timing.py::test_hp3_defense_uses_a_real_egg_without_an_unmeasured_card_scout`
  - 最終状態だけを見るテストだったが、コメントが旧来の「second egg selection なし」を前提にしていた。
  - 有界2回選択後に打ち切る現在仕様へ合わせ、`egg_attempts == 2` を明示した。
- `tests/test_hanjuku_egg_before_retreat.py::test_failed_or_bounded_rescue_does_not_suppress_retreat_forever`
  - カウンタなしの旧 `egg_attempted` 状態を使っている。後方互換で「カウンタなし二元フラグは再試行しない」ことを守るため変更不要。
- `tests/test_hanjuku_early_resource_rescue.py::test_early_egg_retry_shares_the_existing_hard_limit_and_requires_new_hp_loss`
  - early rescue 側の上限計算を検証している。今回共通化しても契約は変えない。

## 修正内容

### `src/docich/hanjuku/judgment/battle.py`

- `_egg_attempt_spent(rescue, hp)` を追加し、卵選択予算の判定を1か所に集約した。
  - 欠損時は 0 回として扱う
  - 型不正・負数は fail-closed で hard limit 到達扱い
  - 通常上限は `EGG_ATTEMPT_LIMIT`
  - `egg_attempt_hp` より HP が低下していれば `EGG_ATTEMPT_HARD_LIMIT` まで許可
  - カウンタを持たない旧来の `egg_attempted` だけの状態は従来どおり使用済み扱い
- `_egg_before_retreat` と `_early_resource_rescue_needed` の重複していた上限計算をこの helper へ統一した。

### `src/docich/hanjuku/flows/battle_menus.py`

- `_survival_menu` の卵枝判定を `not rescue['egg_attempted']` から `_egg_attempt_spent(rescue, ally_hp)` へ変更した。
- 通常分岐だけでなく critical / dying / egg_priority 優先分岐にも同じ `egg_ready` を使う。
- 卵を選択するたびに `egg_wait = 0` を初期化し、再選択ごとに従来の3観測フェード待ちが働くようにした。

### `src/docich/hanjuku/version.py`

- 行動変更のため `BOT_VERSION` を `hanjuku-chart-v1561-egg-attempt-retry` へ更新した。

### テスト変更

- `tests/test_hanjuku_combat_strength.py`
  - HP 低下による hard-limit 再試行が、実際に開いた menu で白い卵行を再選択することを追加。
  - `egg_attempts` が 2 → 3、`egg_attempt_hp` が現在 HP へ更新されること、`egg_wait` が初期化されることを確認する。
- `tests/test_hanjuku_survival.py`
  - 1回目失敗後に bounded second selection が行われ、その後初めて B へ進む期待へ更新。
- `tests/test_hanjuku_egg_timing.py`
  - bounded second selection 後に打ち切られることを `egg_attempts == 2` で明示。

## 影響範囲

変更は通常戦闘の救済メニューにおける卵選択可否に限定される。

- 白い `たまごをつかう` 行が実際に読める場合だけ再選択する。灰色行・OCR 脱落を卵あり扱いしない。
- `EGG_ATTEMPT_LIMIT` / `EGG_ATTEMPT_HARD_LIMIT` の有限性は維持する。
- 旧状態（`egg_attempted` だけで `egg_attempts` がない）は無限再試行へ開放しない。
- 卵残数台帳の減算規則、監査 incident の投影、`battle_loss_with_resources` の起票条件は変更していない。
- `battle_survival_critical_recheck`、`_general_egg_rescue_recheck` など、未観測メニュー向けの別経路は今回の最小修正の対象外であり、従来条件を維持している。

## 検証結果

### 実行できた検証

- `git diff --check` — 成功（exit 0）
- `git status` / `git diff` / `git show` / `git log` による静的差分確認 — 実施済み
- 対象コード・既存テストの静的照合 — 実施済み

### 実行できなかった検証

以下のコマンドは、この環境ではユーザー承認段階で拒否され、実行できなかった。

- `python3 -m pytest -q tests/test_hanjuku_combat_strength.py tests/test_hanjuku_survival.py tests/test_hanjuku_egg_before_retreat.py tests/test_hanjuku_early_resource_rescue.py`
- `python3 -m pytest -q tests/test_hanjuku_combat_strength.py`
- `pytest -q tests/test_hanjuku_combat_strength.py`
- `python3 --version` / `python --version` / `uv --version`

したがって pytest の pass/fail は未確認である。テストは追加・更新済みだが、「緑」とは記録しない。CI または Python 実行可能な検品環境で、少なくとも次を実行する必要がある。

```bash
python3 -m pytest -q \
  tests/test_hanjuku_combat_strength.py \
  tests/test_hanjuku_survival.py \
  tests/test_hanjuku_egg_timing.py \
  tests/test_hanjuku_egg_before_retreat.py \
  tests/test_hanjuku_early_resource_rescue.py
```

## 残件

1. Python 実行可能な環境で上記関連テストを実行し、必要なら失敗を修正する。
2. FIX PR 化はゲート（メイン Muse）が行う。こちらでは push・PR 作成をしていない。
3. issue #5536 occurrence 自体の (d) 判定は変更しない。本修正は、occurrence の事実認定ではなく、現行 main に存在した実装不整合の修正である。
4. 訂正コメントにあった改善候補のうち、`battle_survival_unavailable` / `battle_menu_rescue_exhausted` / `battle_survival_select` の `labels` や選択行を bounded に incident へ投影する監査側改善は未実施。今回の最小修正範囲外。
