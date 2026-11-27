# IMPLEMENT-5676 — [半熟英雄] 切り札を残して敗北: ch7 キッシュ

対象: azumag/docich#5676。検証記録 `/home/hatch/work/docich-5676/VERIFY-5676.md` の結論 (a) 実欠陥に従い、選択済み・消費未確認の切り札 evidence を落とす残余経路を修正した。

## 実装内容

1. `selected_interrupted` の一般化
   - `src/docich/hanjuku/judgment/battle.py` に `_release_interrupted_card_flow()` を追加した。`card_flow` の札が `cards_selected` に既にある場合だけ `_card_use_unclassified(..., selected_interrupted=True)` へ回し、開札前・選択前の flow は従来どおり台帳へ載せずに閉じる。
   - 同処理を以下の破棄分岐へ適用した。
     - `src/docich/hanjuku/flows/battle_menus.py`: 敵召喚画面への置換（`summon_card_resume` 保存部）
     - 同: `battle_hero_retreat_cancel_card`
     - 同: one-hit / HP1 早期クローズ（battle menu 側と card list 側）
     - `src/docich/hanjuku/flows/battle.py`: `_card_gate_close`
     - 同: `battle_end`（`stage == 'announce'` 限定を、`cards_selected` 内に flow の札がある場合へ拡張）
   - 敵召喚置換では、選択済み flow を未選択の再開意図 `summon_card_resume` へ巻き戻さず、消費未確認として解放する。
2. announce retry の残余 copy の監査 evidence 化
   - `battle_card_announce_retry` 時に `_note_card_spend_attempt()` を呼び、`cards_spend_retry` / `cards_spend_attempts` へ retry 系譜を残す。
   - `month_audit._battle_resource_event()` の `_remaining_cards_are_unconfirmed()` へ `cards_spend_retry` を evidence group として渡す。
   - `cards_spend_retry` を battle event へ投影し、issue 本文にも「消費未確認の再選択」として表示する。
3. バージョン
   - `BOT_VERSION = hanjuku-chart-v1559-selected-card-interruption`
   - `ops/vm_actions/collect_diagnostics.py` の allowlist と `ops/vm_actions/tests/test_collect_diagnostics.py` の固定値テストへ追加した。

## 追加・更新した回帰テスト

- `tests/test_hanjuku_card_announce_retry.py`
  - announce retry が `cards_spend_retry` / `cards_spend_attempts` へ積まれること。
  - 選択→announce retry→再選択→実測確定で `cards_selected` 2件・`cards_used` 1件の残余 copy が残る場合に、`cards_unconfirmed_only=True` から confidence medium へ降格すること。
- `tests/test_hanjuku_card_list_transition.py`
  - 選択→announce retry→`stage='list'` のまま戦闘終了した flow が `cards_unclassified` へ残り、medium へ降格すること。
  - 開札前・選択前の flow は戦闘終了でも `cards_unclassified` を汚染しない対照。
- `tests/test_hanjuku_boss_egg_row_log.py`
  - 選択済み・announce retry 後の list flow が敵召喚画面へ置換されたとき、`summon_card_resume` へ戻さず `selected_interrupted` 付きで未分類台帳へ残ること。
- `tests/test_hanjuku_card_interrupted_release.py`（新規）
  - `_card_gate_close`、one-hit/HP1 クローズ、退却キャンセルで選択済み flow が未分類台帳へ残ること。
  - `_card_gate_close` の選択前対照が台帳非汚染であること。

## 検証結果

- `git diff --check`: 成功（出力なし）。
- 関連テスト実行を次のコマンドで試行したが、この環境では Python / pytest の実行が拒否され、未実行のままである。

```sh
python3 -m pytest tests/test_hanjuku_card_announce_retry.py tests/test_hanjuku_card_list_transition.py tests/test_hanjuku_card_interrupted_release.py tests/test_hanjuku_boss_egg_row_log.py tests/test_hanjuku_month_audit.py ops/vm_actions/tests/test_collect_diagnostics.py -q
```

## 環境上の制約と残件

- `git fetch origin main` は環境側で拒否された。既存の `origin/main` 追跡参照は `fad137d43`（v1551）だが、`git rebase origin/main` / `git merge --ff-only origin/main` も拒否されたため、ブランチ metadata は `origin/main` より 1 件遅れたままである。変更は working tree 上のファイルへ行った。
- `gh pr list` も拒否されたため open PR のバージョン確認は GitHub へ問い合わせできなかった。代わりにローカル worktree 内の `docich*/src/docich/hanjuku/version.py` を検索し、`v1559` の使用が無いことを確認した。現行 `origin/main` が v1557 のため v1559 を使用した。
- `DOCICH_HANDOFF_PATH` の確認コマンドも拒否されたため、handoff 正本は未読として扱う。
- Python / pytest 実行が拒否されているため、テストは追加済みだが実行確認は残件である。
- `git add` も拒否されたためローカルコミットは作成できず、コミットハッシュは無い。変更は working tree のファイルとして残している。
- 本番 VM への SSH・秘密情報の記録・push・PR 作成は行っていない。
