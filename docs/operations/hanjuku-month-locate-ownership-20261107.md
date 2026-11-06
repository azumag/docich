# 半熟英雄: 月次在城確認が所有未更新で同じ再試行を繰り返す（v498、2026-11-07）

## 症状

g1139、49年2月の月次ステータス確認で、ポワソンの城が確定できず、
「将軍一覧で SELECT → 城メニュー → 待機 → B」を3回繰り返した。
待機中は入力が出ず、1回あたり約35秒を消費した。最終的にポワソンは
`general_location_unknown` に入り、以後の計画から外れた。

## 実測（本番 g1139、JST）

- 00:03:38 `attack_observed` ポワソン（ジュメルズへの のりこみ）。
- 00:03:52 `battle_entry_expired` ポワソン。
- 00:04:10 将軍一覧の選択三角が ジュメルズ に対応
  （`post_month_roster_marker_mapped` reads=2、distance 36.2）。
  同じ走査の個別ステータスは「しろのなかにいます」。
- 00:04:11 `post_month_roster_castle_rejected`。Yマップ由来の所有一覧は
  まだ ジュメルズ=敵（直近の `world_map_owners` は 23:57:58）。
- 00:05:24 `post_month_status_complete` のタスクが
  `[repair ヘスティア, repair アテナ, locate ポワソン, repair ブカティーニ]`。
- 00:06:32 / 00:07:55 / 00:08:34 `post_month_castle_location_unverified`
  （attempt 1/2/3、いずれも resulting_event `castle_status_not_read`）。
- 00:06:20 の A の後、00:06:32 の B まで入力は無い。画面は
  `castle_menu` のまま（城ステータス画面がメニュー背後に残り、手は非表示）。
- 00:08:34 `post_month_task_result` ポワソン `castle_status_not_read`。
- 00:09:03 `world_map_owners` で ジュメルズ=own、`castle_owned_observed`。
- 翌月 00:27 の月次走査では ジュメルズ が own になっており、三角から
  `post_month_location_confirmed` で確定した（locate は発生していない）。

入力の内訳（`hanjuku_events.previous.jsonl`、attempt 1）:
05:58:24 `x` → メインメニューで `down`×2・`a` → 将軍一覧で `down`×5・`select`
→ 06:13:48 `a`（城メニュー）
→ 06:16:47 `down` → 06:20:53 `a` → 06:32:49/06:34:15 `b`。

## 原因

`_assign` は `_owned`（`captured`）に無い城を在城として採用しない。ポワソンが
00:03 に取った ジュメルズ は、Yマップの更新が 00:09 まで来ていなかったため
敵扱いのままだった。

城名自体は2経路で観測できていた。

1. 将軍一覧の選択三角が ジュメルズ の城旗の上（`_marker` の distance 36.2）。
2. locate が SELECT 後に開いた城ステータス画面（`map_registration._label` が読む
   `CASTLE_STATUS` 行）。分類は `castle_menu` のまま、`screen.hand` は None。

どちらも `_assign` が所有一覧を理由に拒否し、locate 側は「城名が読めない」と
誤って記録して `LOCATE_RETRIES` まで再試行した。城ステータス画面は開いたまま
手カーソルが消えるため、待機中は次の入力も出ない。

## 変更（v498）

- `flows/post_month_status.py`
  - 三角または城ステータス画面で城名が読めたが所有一覧が未更新のとき、
    `world.castle_location_pending[name]` と status 行の `castle_pending` に
    城名を残す（`post_month_castle_pending_ownership`）。在城一覧（garrison）へは
    所有確認まで入れない。
  - `_tasks` は `castle_pending` を持つ将軍に locate を積まない。
  - 翌月の `arm` で所有が確認済みなら `_assign` して pending を解消する
    （SELECT のやり直しをしない）。
  - `post_month_castle_location_unverified` に画面種別と text 先頭120字を残す。
- `version.py` v498、診断 allowlist 追加。
- 回帰テストと本doc。

## 変更しないこと

- 所有が確認できていない城を在城一覧（garrison）や計画へ入れない。
- 城名を読めない画面（メニュー見出しだけ）から城を推定しない。
- 既存の帰還（recall）・卵修理（house）・計画（next_order）の予算と所有者。

## 検証

- `tests/test_hanjuku_post_month_status.py` 49件。
- 関連 465件（house / map_discovery / castle_routes / off_chart /
  recruit_roster / castle_jump）成功、1 skip。

## 残件

- pending の昇格は翌月の `arm` 時。同月内は `general_location_unknown` のまま
  計画から外れる（変更前と同じ）。Yマップ更新時に即時昇格させるのは別変更。
- 記録が残る範囲では locate の `castle_verified` 成功例が無い。今回追加した
  `text` / `screen` で、次に失敗したときは画面の実内容を確認できる。
- 本番反映は未実施（PR と CI の後）。
