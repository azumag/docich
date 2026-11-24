# 半熟英雄: キッシュの野営帰還が成立しない (2026-11-25)

## 事象

ライブ `g1342-1fe0734c` の ch4。キッシュは 7-11 (tick 55227) から
四季王城(北東) の野営に留まり、月次監査の `camp_abandoned` が
#5421 として起票されていた。毎月の帰還タスクは開始されるが、野営メニュー
の途中で終了し、将軍は移動しない。

実測（`hanjuku_decisions.jsonl`, 2026-11-24 23:41 JST）:

| 時刻 | できごと |
| --- | --- |
| 23:41:24 | 野営 [130,124] で `camp_enter` |
| 23:41:25 | 野営メニュー（実カーソルは いどう） |
| 23:41:27 | A（実際は ステータス側へ） |
| 23:41:28 | B + `retreat_recheck_unconfirmed`（キッシュ、予算上限） |
| 23:41:30 | `post_month_task_result {"task":"boss_move"}`（移動なしで消費） |

## 原因

1. **免除月数のリセット**: `boss_camp_exempt` は 8-5 に 6 あったが、8-6 の
   `arm` が「前月の戦闘で location が field のまま残った」キャッシュを読み、
   野営なしとしてカウントを破棄していた。同じ月の後続スキャンで野営を
   再観測しても、当時の実装は `boss_camp_exempt_scope` が同じ月であることを
   理由に再カウントしなかった。その結果、3ヶ月を超えても常に 0 から数え直し、
   本来の「帰還（recall home）」ではなく毎月 `boss_move` が選ばれ続けた。
2. **古い退却再確認が帰還タスクを乗っ取る**: `retreat_rechecks` には
   7-11 のままのキッシュ行が残り、attempts=3（予算上限）だった。
   `camp_recheck._step_flow` は月次タスクの野営メニューも匿名再確認へ
   変換し、上限到達済みのため B で閉じてタスクを消費していた。

## 修正

- `flows/post_month_status.py`
  - 免除月数のカウントを (将軍, 月) 単位で冪等化（`_count_boss_camps`）。
    古いキャッシュは「野営を追加」できても「解消」はできない。解消の
    削除は完了済みの fresh scan だけが行う。
  - 月次スキャン完了時に fresh な status で再カウントしてからタスクを
    組み直す。
  - カウントが失われていても、同run・同章の退却再確認行が免除窓より
    古ければ、その観測月数でシードする。
  - 月次監査の `camp_watch`（完全な名簿から数えた連続野営月数）を bot が
    観測開始時に bounded hint (`camp_watch_hint`) として渡し、失われた
    カウントを補完する。hint は run 同一性が一致する場合だけ採用する。
  - 月次タスクの帰還状態に `task_recall: True` を付け、同将軍の古い
    再確認行を `camp_recheck.release_for_task` で破棄する。
- `flows/camp_recheck.py`
  - `release_for_task(mem, actor)`: 明示的な月次タスクが所有する将軍の
    古い再確認行を削除する。
  - `_step_flow`: `task_recall` の野営メニューは匿名再確認へ変換しない
    （recall.py の いどう→Y 経路へ渡す）。通常の野営帰還は従来どおり
    再確認を通る。

## 検証

- `tests/test_hanjuku_boss_camp_months.py`（新規）
  - 古い field 行で6ヶ月のカウントが消えないこと
  - 同じ月に arm と fresh scan で二重カウントしないこと
  - fresh scan が解消済みの野営だけを削除すること
  - 失われたカウントを古い再確認行からシードし、`recall` タスクになること
  - `task_recall` が再確認へ変換されず、通常帰還は変換されること
  - `release_for_task` が古い行を削除すること
- `tests/test_hanjuku_boss_camp_remarch.py`: 月次タスクが `task_recall` を
  持ち、古い再確認行を解放すること。
- `tests/test_hanjuku_bot_exit_diagnostic.py`: 監査 hint が同一 run のときだけ
  policy state に付き、異なる run では古い hint を破棄すること。
- 関連: `test_hanjuku_post_month_status.py`, `test_hanjuku_camp_proof_binding.py`,
  `test_hanjuku_retreat_recheck.py`, 帰還系テスト。

## 追補（v1478）

配備後の実測で次の2点を確認し、追補した。

- 監査 hint は `state['camp_watch_hint']` に置いていたが、`decide` が
  `state['policy']` だけを渡すため policy から見えていなかった。hint を
  `policy['camp_watch_hint']` に置く。
- 月次タスクの帰還で位置が測定できず、自軍城が複数ある場合
  `_recall_goal` が None を返し、ピッカーの fallback が野営地点で確定して
  「移動なし」を dispatched として記録していた（キッシュ 01:11）。
  `task_recall` のときは、主人公の城が一意でなければ章の本城（所有城）へ
  フォールバックする。通常の野営帰還の保守的な None は変えない。
## 未確認・残件

- 配備後の実機で、次の月次タスクが `recall（帰還）` になり、キッシュが
  城へ戻ることは実観測で確認する。テスト成功を実機成立とは扱わない。
- 免除カウントを失った別の将軍が、再確認行を持たない場合はシードできない。
  その場合は fresh scan の観測から数え直す。

## 実装メタデータ

- Hot-load marker: `hanjuku-chart-v1477-boss-camp-recall`
- Diagnostics collector の固定 allowlist と marker contract test も同じ値へ同期する。
- 免除カウントの追加キー `boss_camp_counted` は (将軍 → 最後に数えた月) の
  小さな辞書で、run 同一性リセットの対象に含まれる。
