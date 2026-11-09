# 半熟英雄: 月次の将軍確認で「同じ月に読んだ在城将軍」を再利用する（v577、2026-11-09）

## 症状（実測）

g1163 第7話 2026-11-09 18:19:30 の月次処理は **約7分半**。うち
18:21:38〜18:26:27 の **約5分**が将軍別の状態確認（`post_month_roster_marker_*` と
`house_egg_seen` の往復、27将軍分）でした。

## 原因

`_house_freshly_read` は「同じ月に読んだ」読みでも **tick が300以内**のものしか
再利用していませんでした。月次の一巡は2000 tick 以上かかるため、最初に読んだ将軍は
再利用されず、ほぼ全員を読み直していました。

状態が変わるのは「戦闘に参加した」「移動が完了した」場合で、戦闘参加は
`battle_end` が `dirty_status` で印を付けます。

## 修正（v577 `hanjuku-chart-v577-post-month-clean-reuse`）

`_house_freshly_read` の再利用条件を広げました。

- **同じ月・城内（`location == castle`）・`location_observed` が偽でない・tick が読めている・
  その将軍が dirty でない** → tick の古さに関わらず再利用。
- 城内の読みは戦闘以外で変わらないため。**行軍・野営・フィールドの読みは従来どおり
  tick 300 以内のみ**（到着で戦闘なしに変わり得るため）。
- tick が読めない読み（`'x'` 等）は従来どおり再利用しない。

## 確認

- `tests/test_hanjuku_post_month_reuse_clean.py`（新規3件）: 城内の古い同一月読みは再利用／
  dirty は読み直す／前月の読みは再利用しない。
- 既存 `test_hanjuku_post_month_status.py` の
  「行軍は再利用しない」「tick 不明は再利用しない」も維持（88件 passed）。
- 本番反映後: 月次処理の所要時間（`post_month_sequence_finished` までの秒数）が
  短くなることを実測する。
