# 半熟英雄: 第4話クリアと、章末の次章で旧章の注文が走る窓の修正（v530、2026-11-09）

## 第4話クリア（実測）

- 03:57〜04:01 の南東の四季王戦（ヘパイストス 49 / スプリミョーネ 60）は
  `battle_result` が未分類のまま終わり、04:01:53 に章末のセーブ確認
  （「章末のセーブ確認を実画面で読んだため進行し、直後の先発隊で次章を確定する」）
  が出た。04:02 に先発隊3人を確定して出発。
- 04:05 に `chapter 5` を実測。第4話の `season_sites`・`captured`・`orders` は
  章替わりでリセットされた。**第4話はクリア**。

## 症状（章末の窓）

先発隊の確定（04:02:24）から次章の観測（04:05）までの間、旧章の注文が残り、
04:04:24 に第4話の `4-B2 四季王（南東）` へ出撃を開始した。ゲームは既に第5話の
先発隊後なので、この出撃は無駄になる。

## 原因

章替わりのリセット（`campaign._enter_chapter`）は「新しい章を実測した時」にしか
走らない。先発隊は章の間にしか出ないのに、その時点では旧章の `orders` /
`chart_plan` / `sorties` が残っていた。

## 修正（v530 `hanjuku-chart-v530-chapter-scope-clear`）

- `strategy/campaign.py`: リセット対象のキーを `CHAPTER_SCOPE_KEYS` へ定数化し
  `_reset_chapter_scope` に切り出した。新しい `finish_chapter(mem)` は
  実行状態だけを落とし、`chapter` の値は推測せず据え置く
  （`chapter_transition_pending` と `chapter_scope_cleared` を記録）。
- `flows/starter.py`: 先発隊の出発を実測した時点で `finish_chapter` を呼ぶ。
- `version.py`: `hanjuku-chart-v530-chapter-scope-clear`、診断許可リストへ追加。

## 確認

- `tests/test_hanjuku_chapter_scope_cleared.py`（2件）: `finish_chapter` が
  `orders`/`chart_plan`/`sorties`/`season_sites` を落として `chapter` を保つ、
  先発隊の出発で `chapter_scope_cleared` が記録される。
- 章・先発隊・チャート関連 414件 passed。
- 本番反映後: 次の章替わりで、先発隊の出発後に旧章の `order_start` が出ないことを
  実測する。第5話の進行（オトラント攻撃など）は別途継続観察する。
