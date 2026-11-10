# 半熟英雄: 名簿上限まで列挙できたら末尾未確認でも月次を続行する（v580、2026-11-09）

## 症状（実測）

月次処理が **`complete: False`（`roster_end_unconfirmed`）** で終わる月が繰り返し出ています。

| 時刻 | names | end_stall | phase_reads | 状態 |
|---|---|---|---|---|
| 19:00:09 | 32 | 13 | 13 | selected ビシソワーズ |
| 19:17:33 | 32 | 13 | 13 | selected パプリカ |

将軍一覧は**上限32人**まで列挙できているのに、末尾の判定（スクロール端の画素）が
ライブ解像度 299x224 では読めず、最後の行での `down` も無変化のため失敗扱いになり、
**将軍別の確認（dirty の解消）へ進めませんでした**。

## 修正（v580 `hanjuku-chart-v580-roster-end-assumed`）

`_roster_end_assumable(state)` を追加し、`roster_end_unconfirmed` に落ちる直前に判定します。

- **先頭（どうし）から確認済み** かつ **列挙数が上限32（`MAX_SCAN`）以上**なら、
  名簿は全員列挙できたとみなし、`post_month_roster_end_assumed` を記録して
  **将軍別タスク（`process`）へ進みます**。
- `complete` は True にしません（末尾の証明が無いため、**不在将軍の除籍はしない**）。
- 上限に届いていない場合は従来どおり失敗（`roster_end_unconfirmed`）。

## 確認

- `tests/test_hanjuku_post_month_reuse_clean.py` に1件追加: 32人＋先頭確認で成立／31人や
  先頭未確認では成立しない。
- 月次関連 90件 passed。
- 本番反映後: 次の月替わりで `post_month_roster_end_assumed` が出て、
  `complete: False` で終わる月が減り、dirty 将軍の確認が実行されることを実測する。
