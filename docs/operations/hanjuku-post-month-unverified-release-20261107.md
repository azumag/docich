# 月次ステータス確認の未確認終了後にフィールドへ戻す（v505、2026-11-07）

## 症状

`post_month_status_unverified` が記録された後、月次ステータスの持ち主
（`post_month_status`）が `stage=blocked` のままフィールドを保持し、入力が
送られない状態が続いた。`post_month_status_unverified` は次の4経路で出る。

- `observation_budget`: 1回の月次処理で観測上限 `MAX_STEPS`（900）を超えた
- `roster_end_unconfirmed`: 将軍一覧の末尾を確認できなかった
- `roster_top_unconfirmed`: 将軍一覧の先頭を確認できなかった
- `status_unreadable`: 将軍個別ステータスを読めなかった

従来の `blocked` は map でも `[]` を返し続け、次のいずれかが起きるまで
所有を手放さなかった。

- 戦闘が発生して `resume_after_battle` が `blocked` をやり直す
- SELECT reanchor で `reenter_after_reanchor` が戻す
- 月が変わり scope が変わる

戦闘も reanchor も起きないと、その月の残りを無入力で待ち続ける。AGENTS.md §12
（障害対応は復旧を優先）に反する停止経路だった。

## 変更

- `step` の先頭で `stage == 'blocked'` を処理し、`_finish` で
  `post_month_status_last`（`complete` / `failed`）に失敗を記録して所有を手放す。
  - map / text はその場で `None` を返し、通常の出撃計画・会話送りへ戻す。
  - 城メニュー等（`STRAY_MENU_KINDS`）は B を1回だけ送って閉じ、次の観測から
    通常処理に任せる。
- `blocked` を城メニュー後退の対象集合から外す（先頭で処理するため）。
- `_finish` のログに `failed` を含め、失敗終了の理由を「未確認情報を城の所在地と
  して扱わず、通常の出撃計画へ制御を返す」に分ける。
- `BOT_VERSION` を `hanjuku-chart-v505-post-month-blocked-release` へ更新し、
  `ops/vm_actions/collect_diagnostics.py` の `bot_version` 許可集合へ追加する。

未確認の将軍状態は `general_statuses` へ書かない（従来どおり）。所有を手放しても
「未確認の所在地を城として扱う」ことはしない。`post_month_status_last` の scope が
当月になるため、`house.py` の月次フィールド調査は同じ月に重ねて走らない。

## 検証

- `tests/test_hanjuku_post_month_status.py`
  - `status_unreadable` 後に map が `None` を返し、`post_month_status` が消え、
    `post_month_status_last.failed` が残る
  - `blocked` の map / 会話画面が `None` を返して解放される
  - `observation_budget` の翌観測で map が解放される
  - `blocked` の城メニューは B で閉じてから解放される
- `tests/test_hanjuku_castle_jump.py`、`tests/test_hanjuku_chart_bot.py`、
  `tests/test_hanjuku_spent_sortie_expiry.py`、`tests/test_hanjuku_arrival_status_probe.py`

## 未確認・残件

- 本番で `blocked` からフィールド復帰することを実測する。デプロイ後の次回
  月次確認で `post_month_status_unverified` が出た場合、`post_month_sequence_finished`
  （`failed` 付き）が続き、同じ観測列で通常の map 入力が再開することを確認する。
- 今回の調査で、別の停止として将軍トレード完了台詞で `trade_guard` が
  `held` のまま解放されない事象を確認した（g1139 / g1145 の終端は
  `screen_stalled`）。`post_month_status` とは独立した問題として切り分ける。
