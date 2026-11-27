# IMPLEMENT-5679 — camp_recall_unconfirmed ch9 オオキクの修正実装

- 対象: azumag/docich#5679
- 作業ブランチ: `fix/5679`（base: 現行 main `9e2d2418c` / v1554）
- 参照: `/home/hatch/work/docich-5679/VERIFY-5679.md`（commit `cff1f1419`）§6〜§8
- handoff: `DOCICH_HANDOFF_PATH` 参照確認コマンドが環境で拒否されたため、正本は未読・未更新として扱った。

## 実装内容

1. proven-unsent resend を新規 request として扱うようにした。
   - resend 成功時は従来の `request_trace` / `input_sent` / `picker_closed` に加え、旧要求の `scene_covered` / `scene_covered_tick` / `scene_pending` を state から外す。
   - 同時に flow 側の古い `recall_interrupted` も外す。
   - これにより、scene 中断後に補送した A の receipt が届いても、scene 前の中断 marker が新しい receipt へ引き継がれない。
2. receipt 後の interruption 昇格条件を限定した。
   - `scene_covered` へ昇格するのは `interrupted_tick >= state['requested_tick']` の場合だけにした。
   - resend より前の tick が、resend 後の新しい request に適用されないようにした。
3. scene 後の picker 同一性を確認できない場合は resend しないようにした。
   - 同一性確認は、現在画面が `map_target` で、元要求の `target_cell` と現在の `screen.marker` が `RECALL_CONFIRM_PX` 以内にある場合に限る。
   - 確認できない場合は A を送らず、`camp_recall_dispatch_resend_rejected`（`picker_identity_unconfirmed`）を記録し、stale 待機を明示的な `dispatch_unconfirmed` / interrupted 終端へ進める。
   - これは案2の全面的な採用ではなく、同一 picker と確認できる `map_target` 経路では従来どおり一度だけ resend する。確認できない場合だけ保守的に終端する。
4. バージョンを v1561 に更新した。
   - `BOT_VERSION = 'hanjuku-chart-v1561-camp-resend-scene-reset'`
   - diagnostics の bot_version allowlist と固定値テストへ同じ文字列を追加した。

## 変更ファイル

- `src/docich/hanjuku/flows/recall.py`
  - `_resend_picker_identity_confirmed` を追加。
  - `_recall_dispatch_step` の receipt 昇格条件、resend 時の状態初期化、picker 同一性不明時の終端を実装。
- `tests/test_hanjuku_camp_receipts.py`
  - scene 中断後の同一 picker への resend が新規 request として扱われ、picker close で `dispatched` になる回帰テストを追加。
  - `target_cell` が無い場合、および marker がずれている場合に resend せず `dispatch_unconfirmed` / interrupted 終端になる回帰テストを追加。
- `tests/test_hanjuku_recall_scene_resume.py`
  - 既存 fixture に実際の request 状態へ近い `requested_tick` を追加。
  - request より古い interruption が receipt 後の `scene_covered` 昇格へ使われない回帰テストを追加。
- `src/docich/hanjuku/version.py`
  - v1561 へ更新。
- `ops/vm_actions/collect_diagnostics.py`
  - v1561 の bot_version を allowlist へ追加。
- `ops/vm_actions/tests/test_collect_diagnostics.py`
  - v1561 の固定値テストを追加。

## テスト

実行を試みたコマンド:

```sh
python3 -m pytest -q tests/test_hanjuku_camp_receipts.py tests/test_hanjuku_recall_scene_resume.py tests/test_hanjuku_recall_month_scene.py
```

結果: **未実行**。この環境では pytest 実行ツール呼び出しが拒否されたため、テストプロセスは開始されていない。依頼条件に従い再実行はしていない。

実施できた静的確認:

- `git diff --check`: 成功（出力なし）。
- 既存関連テストとの整合は実行ではなく静的照合のみ。特に `test_receipt_arriving_after_scene_marker_does_not_charge_the_destination` が要求どおり `interrupted_tick >= requested_tick` の正例となるよう、fixture の `requested_tick` を現実の状態に合わせた。

## 残件・制約

- picker 同一性の確認は `map_target` の `target_cell` / `screen.marker` がある場合に限る。`world_map` では parser が picker marker を保持しないうえ、`_recall_dispatch_step` が frame を受け取らないため、現行のデータ経路では同じ判定ができない。`target_cell` を持たない request についても同様である。これらは scene 中断後には resend せず、明示的な unconfirmed / interrupted 終端になる。
- 上記の保守的終端は、picker 同一性を確認できる `map_target` 経路の resend を残したうえでの限定適用である。案2への黙った切り替えではないが、`world_map` まで同じ回復率にするには、frame または picker cursor の同一性証拠を dispatch wait へ渡す別途設計が必要である。
- pytest は未実行のため、CI または実行可能な環境での回帰確認が必要。
- 独立レビューは未実施。自己レビューと静的確認のみ。
- push・PR 作成・issue/Slack 投稿・本番 VM 操作は行っていない。
