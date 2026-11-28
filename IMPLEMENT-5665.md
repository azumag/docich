# docich#5665 修正実装記録 — camp_recall_unconfirmed（第三亜型）

検証結論 (a) 実欠陥（VERIFY-5665.md、commit b407e00bb）に対する修正。
同月内・無中断で、帰還 A の receipt 証明鎖が 6 観測窓内で機会枯渇したパターン。

## 変更内容（VERIFY-5665.md §6 の 3 点すべて）
1. **送信有無を receipt 独立に記録する**：`action_sent`（`hanjuku_run.py`）が送信時に
   `hanjuku_inputs_sent.jsonl` へ同じ payload をミラーする。密な events journal が
   書込バーストで該当行を蒸発させても、疎な入力専用ミラーが残る。`_journal_pad_inputs`
   （`bot.py`）は両 journal を走査し、`{'count', 'later_inputs'}` を返す。
   `later_inputs` は sender 側で後続入力が記録されたのに当該 A が無いこと＝
   未送信の積極的証明。journal が読めない場合は None（unknown）で再送しない。
2. **64KiB 証明窓の書込量非依存化**：ミラーにより、対象 request の `planned_at`
   以降の行が events tail から蒸発しても照合できる。`hanjuku_inputs_sent` は
   `hanjuku_log_retention.py` の保持・剪定対象にも追加した。
3. **stale stage への降格**：6 観測で receipt が得られなければ `await_dispatch_stale`
   へ降格（`recall.py`、定数 `DISPATCH_STALE_STEP_LIMIT=12` / `DISPATCH_STALE_TICK_LIMIT=400` /
   `DISPATCH_RESEND_MAX=1`）。stale 待機は所有画面を通常所有者へ返しつつ証明を再試行する。
   遅れて receipt が届けば新しい観測窓で受理確認を再開する。未送信が証明されれば
   開いたピッカーへ A を一度だけ補送する。いずれの bound も `camp_recall_unconfirmed`
   として終端する。cliff escape は旧来の終端を維持する（A は field move で補送は危険）。

## 段階の配管
- `await_dispatch_stale` を `camp_recheck.py`（`_ALLOWED_KINDS` / `_owner_shape` /
  未知前景ガード）、`map.py`（`observe_sortie_transition` / `world_map_step`）、
  `orchestrator.py`（`_recall_dialog_wanted` / `_recall_after_scene`）で
  `await_dispatch` と同扱いにした。
- 新規決定名 `camp_recall_dispatch_stale` / `camp_recall_dispatch_resumed` /
  `camp_recall_dispatch_resent` を `bot.py` の `INPUT_CONTEXT_DECISIONS` と
  persist 対象へ追加した。

## テスト
- `tests/test_hanjuku_camp_receipts.py`：30 passed（新規 2 件を含む）。
  6 観測での stale 降格→12 stale steps での有界終端、遅延 receipt での再開、
  未送信証明での一度だけの補送→再降格→有界終端を固定。
- 関連 camp recall 系 7 ファイル：220 passed。
- バージョン：v1546（PR #5667 のマージで main が v1545 になったため renumber）。

## 残件
- 未送信 vs 送信済み未証明の切り分けはラン成果物が必要だったが、本修正により
  送信時ミラーと `later_inputs` で終端時に区別可能になった（`unsent_proven` /
  `stale_steps` / `resends` を終端 metric に記録）。
- `card_scroll_receipt` / `departure_input_receipt` も `later_inputs` を返すように
  なったが、現状その値を消費するのは recall 経路のみ。
