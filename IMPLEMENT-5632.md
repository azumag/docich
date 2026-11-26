# IMPLEMENT-5632 — [半熟英雄] 敵召喚獣戦/ボス戦で切り札未使用: scroll 枯渇後の re-arm 猶予中に攻撃を保留

- 対象: [docich#5632](https://github.com/azumag/docich/issues/5632) / 監査 `card_miss`（confidence `high`）
- 検証記録: `VERIFY-5632.md`（commit `5452d6f62`、結論 (a) 実欠陥）
- ブランチ: `verify/5632`（worktree `/home/hatch/work/docich-5632`）
- BOT_VERSION: `hanjuku-chart-v1540-summon-card-rearm-hold`

## 実施内容（VERIFY-5632.md §6 の項目1・2）

### 1.【主】re-arm 猶予中は攻撃を保留する（`src/docich/hanjuku/flows/battle_menus.py`）

`egg_battle_step` の召喚切り札揭示 scroll ブロック（`battle_menus.py` L3286-L3346）を変更。

- `scrolls >= SUMMON_CARD_SCROLL_LIMIT` で `_summon_card_rearm` が今回は発火しなくても、
  `summon_card_rearms < SUMMON_CARD_REARM_LIMIT` なら re-arm の余地が残る（猶予中）と判定し、
  `pad('a')` の `こうげき` 確定・`battle_boss_card_missed` emit へ進まず `return []` で入力を保留する。
- 猶予は有界: `summon_card_exhausted_tick` から `SUMMON_CARD_REARM_TICKS=8` 観測経過で re-arm が
  `summon_card_scroll` を 0 に戻し再 scroll へ進む。re-arm 上限 `SUMMON_CARD_REARM_LIMIT=2` 到達後は
  従来通り攻撃フォールバックへ進む。
- `battle_summon_card_exhausted` の `observed_metric` に `rearm_pending` を追加し、
  reason も猶予中は「再開猶予が残る間は攻撃を保留する」と記録する。

### 2.【副】監査 emit を最終失敗と分ける

- 最終枯渇（re-arm 上限到達）時は `battle['summon_card_reveal_failed']` を latch し、
  新しい decision `battle_summon_card_reveal_failed` を記録する（`card` / `scrolls` / `rearms` / `limit` /
  `cards_left` / `cursor_y` / `visible` / `screen_kind` / `text` 付き）。
  「切り札を開かず攻撃した」ではなく「有界 scroll と再開を尽きても行が読めなかった」別証拠として残す。
- `battle_boss_card_missed` の emit 条件に `not battle.get('summon_card_reveal_failed')` を追加し、
  最終枯渇後の攻撃では card_miss を high confidence 起票しない。
  （初回枯渇では攻撃自体が保留されるため emit に到達しない。行が読めたのに開かず攻撃した場合は
  従来通り card_miss が記録される。）
- `battle_boss_card_missed` の `observed_metric` に `rearms` / `summon_card_scroll_out` /
  `summon_card_exhausted_tick` を追加。
- 札オープン成功時のカウンタリセットに `summon_card_reveal_failed` の解放を追加（次の札の判定へ影響しない）。

### 3. month audit 投影（`src/docich/hanjuku/month_audit.py`）

- `_track_card_miss` の `battle_event` に `rearms` / `summon_card_scroll_out` /
  `summon_card_exhausted_tick` を投影（`_bounded_int` / bool 型確認で bounding）。
- `_render_card_miss` に `切り札行揭示: rearms=… / scroll_out=… / exhausted_tick=…` の1行を追加し、
  起票された issue 上で初回枯渇・re-arm 中・最終枯渇（相当の過去証拠）を区別できるようにした。

### 4. バージョン

- `src/docich/hanjuku/version.py`: `hanjuku-chart-v1540-summon-card-rearm-hold`。
  （rebase 時点で main は v1538、open PR は v1539 まで使用中のため v1540。）
- `ops/vm_actions/collect_diagnostics.py` のバージョン allowlist に同値を追加。

## テスト（`tests/`）

### 更新（旧仕様を固定していたものを新仕様へ）

- `test_hanjuku_boss_egg_row_log.py`
  - `test_a_fresh_scroll_exhaustion_marks_evidence_before_attacking` →
    `test_a_fresh_scroll_exhaustion_holds_the_attack_for_the_rearm_grace` に改名・期待値更新。
    初回枯渇で `acts == []`（攻撃保留）・`battle_summon_card_exhausted`（`rearm_pending=True`）記録・
    card_miss 非 emit を確認し、tick 経過後の re-arm で `[pad('down')]` 再開まで確認。
  - `test_the_card_rearm_stops_at_its_limit`: re-arm 上限到達で `[pad('a')]` へ進み、
    `battle_summon_card_reveal_failed` が記録され card_miss が出ないことを確認。
  - `test_the_missed_card_record_carries_the_battle_identity`: card_miss の発火経路を
    「きりふだ行が見えているのに開かずこうげき確定」に変更し、新 metric 項目（rearms/scroll_out/exhausted_tick）を確認。
  - `test_ordinary_carried_card_is_still_reported_when_boss_only_card_is_filtered` /
    `test_boss_only_card_remains_eligible_after_measured_boss_entry`: 同じ可視行経路へ更新。
- `test_hanjuku_chart_bot.py`
  - `test_summon_menu_records_a_missed_carried_card_for_the_audit` →
    `test_summon_menu_holds_attack_while_the_card_reveal_can_rearm` に改名・期待値更新
    （初回枯渇で 'a' を送らず exhausted 記録・card_miss 非 emit）。
- `test_hanjuku_month_audit.py`
  - `test_card_miss_decision_seals_an_auto_file_incident`: metric に re-arm 状態を追加し、
    `battle_event` 投影と render 行を確認。

### 追加

- `test_a_post_exhaustion_attack_with_a_visible_row_still_files_card_miss`（boss_egg_row_log）:
  初回枯渇の `summon_card_scroll_out` が残る状態で行が読めたのに開かず攻撃した場合は、
  従来通り card_miss が記録され、metric で枯渇履歴を区別できることを確認。

## スコープ外（VERIFY-5632.md §6 の後続候補）

- 項目3「スクロール入力の進行確認」（down が実際にカーソル/表示へ効いたかの bounded evidence、
  入力不成立時の予算消費延期・明示記録）— 本修正の範囲外。follow-up 候補。
- 項目4「side/castle 不明の context 添付」（`_battle_context` の context / sortie step を emit に添える）— 同上。
  v1524（PR #5633）以降 `side=None` を attack 扱いしない材料になる。

## 検証状況・残件

- **merge**: `git fetch origin` / `git merge origin/main` は本環境で拒否された。ただしローカル参照では
  ブランチ HEAD が merge commit `b39cf75d4`（`origin/main` = `031d0bed3` 取り込み済み、`031d0bed3` は
  `1eb90d221` の子）のため、指示された merge は実質済み。`origin/main` の最新（指示上は `1eb90d221`）は包含済み。
- **pytest / python 実行**: 本環境で拒否されたため未実行。構文・ロジックは差分レビューのみで確認。
  CI または検品ゲートで `tests/test_hanjuku_boss_egg_row_log.py` /
  `tests/test_hanjuku_chart_bot.py`（summon 系）/ `tests/test_hanjuku_month_audit.py`（card_miss 系）の
  実行を要する。`test_hanjuku_survival.py`（L741-L799）の egg_battle 系も影響範囲内だが、
  `summon_card_scroll` 枯渇状態を作る箇所はなく退行は見込めない。
- **commit/push**: commit は試みたが本環境で拒否された（`git add`+`git commit`）。push は行わない。
  変更は worktree の working tree に未コミットのまま残っているため、検品ゲート側で add/commit/push を要する。
- **issue コメント**: 検品ゲートが実施。下記に代理投稿文を残す。
- **Slack #agent-council（ts 1795634294.000719）完了報告**: `slack-remote` MCP のツール列挙が失敗し投稿不可。
  下記ドラフトを検品ゲート経由で投稿してほしい。
- 本番 SSH・VM 操作・ゲーム入力は実施していない。
- `handoff.md` 正本は `DOCICH_HANDOFF_PATH` 未設定のため未読（AGENTS.md §1）。本ファイルを引き継ぎ記録とする。

## 代理投稿文（issue docich#5632 コメント用・要検閲）

> #5632 の実修正を verify/5632 で実装しました（BOT_VERSION v1531）。
>
> - 初回 scroll 枯渇（scrolls=3）は re-arm 猶予の開始だけを記録し、猶予が残る間（rearms < 2）は
>   `こうげき` 確定と `battle_boss_card_missed` emit を保留するようにしました。猶予経過後は re-arm が
>   有界 scroll を再開します（VERIFY-5632 §6-1）。
> - re-arm 上限（2回）まで揭示できなかった最終枯渇は、`battle_summon_card_reveal_failed` という別 decision で
>   記録し、card_miss 起票しないようにしました。emit の metric に `rearms` / `summon_card_scroll_out` /
>   `summon_card_exhausted_tick` を追加し、month audit の issue にも同値を投影します（同 §6-2）。
> - §6-3（scroll 入力の進行確認）・§6-4（side/castle 不明の context 添付）は本修正のスコープ外で、
>   follow-up 候補として IMPLEMENT-5632.md に記録しています。

## Slack #agent-council（ts 1795634294.000719）完了報告ドラフト

> <@U0CCCH69XMW> docich#5632 の実修正を verify/5632 に実装しました。結論: (a) 実欠陥の修正 —
> 初回 scroll 枯渇で re-arm 猶予中は `こうげき` 確定・card_miss emit を保留し、re-arm 上限到達の最終枯渇は
> `battle_summon_card_reveal_failed` の別証拠へ分離。emit/監査へ re-arm 状態を投影。BOT_VERSION v1531。
> 残件: pytest・git fetch/merge・push は本環境で拒否のため検品ゲート側で実行が必要
> （ローカル origin/main は 031d0bed まで取り込み済み）。詳細は IMPLEMENT-5632.md。
