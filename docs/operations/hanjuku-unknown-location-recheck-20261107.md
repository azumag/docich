# 半熟英雄: 行軍追跡が消えた将軍が月次ステータス確認で再確認されない（v508、2026-11-07）

## 症状

行軍記録が期限切れ（追跡消失）した将軍は `general_location_unknown` に入り、
以後の出撃計画から外れる。設計は「次の月次の本人ステータス確認で所在を
再確認する」（`orders._queue_overdue_location_recheck`）だが、実際には
次の月替わりで**その将軍のステータスが開かれず**、`post_month_status_reused`
（`unchanged_cached`）のまま月が進むことがあった。

## 実測

### 本番（g1139、JST）

- 2026-11-06 23:40:42 `sortie_location_recheck_queued` セルリアク
  （行軍記録の期限切れ `sortie_record_expired`）。
- 以後 23:46（49-1）・00:03（49-2）・00:27（49-3）の
  `post_month_status_armed` の `reopen` にセルリアクは一度も入らず、
  00:53 のラン終了まで `general_location_unknown` に残った。
- セルリアクは 23:17 の完了走査の名簿にいない（それ以前に離脱）。
  すなわち「名簿にいない名前」が再確認待ちとして永久に残っていた。

### 再現（コード）

確定済みキャッシュ（全員が自軍城の `castle` 行、`dirty` 空）に対し
`general_location_unknown = ['ゼウス']` だけを与えて `arm` すると、
走査も再確認も起らず `post_month_status_last.result == 'unchanged_cached'`
になる（修正前）。

## 原因

1. `arm` の再確認対象（`stale`）は `general_statuses.dirty` とキャッシュ行の
   `location` だけで決まっていた。`general_location_unknown` は参照されない。
   そのため、dirty が失われた／付かなかった将軍は、キャッシュ行が
   「自軍城の castle」のままだと何ヶ月でも再読されない。
2. 行軍の期限切れは月次走査の**実行中**にも起こる。`_queue_overdue_location_recheck`
   はその場で `general_statuses.dirty` を立てるが、走査完了時の
   `_complete_scan` は `dirty: {}` でキャッシュを丸ごと置き換えるため、
   その依頼は消える。次の月は dirty が無く、1. により再確認されない。
3. 離脱した将軍の掃除（`_complete_scan` の `missing` ループ）は
   `general_location_unknown` を**フラットキー**で読んでいた。同キーは
   world 領域へ移行済みのためこの読みは常に空振りし、名簿から消えた将軍が
   再確認待ちに残り続けた（セルリアク）。さらに「キャッシュに一度も
   載っていない名前」は `missing` に入らないため、同じ走査でも掃除されない。

## 変更（v508）

- `flows/post_month_status.py`
  - `arm`: `general_location_unknown` を再確認の要求として扱い、
    確定済み名簿（`statuses`）にいる名前を `stale` に加える
    （dirty の有無に依存しない）。名簿にも加入待ちにもいない名前は
    `post_month_unknown_location_pruned` を記録して再確認対象から外す。
  - `post_month_status_armed` の `observed_metric` に `unknown_location` を追加。
  - `_complete_scan`: 離脱将軍の掃除で `general_location_unknown` を
    `_world(mem)` から読む（フラットキー依存を解消）。完了走査の名簿に
    いない未知所在名も同じ membership proof で掃除する。
- `version.py` を `hanjuku-chart-v508-unknown-location-recheck` へ更新し、
  `ops/vm_actions/collect_diagnostics.py` の `bot_version` 許可集合へ追加。
- 回帰テスト5件（`tests/test_hanjuku_post_month_status.py`）。

## 変更しないこと

- 行軍中（`しんぐんちゅうです`）の将軍を在城扱いしない。所在は到着後の
  本人ステータスと SELECT→城ステータスの実表示で確定する。
- 未確認の所在地を城として扱わない。`_complete_scan` の membership proof
  （先頭確認・dirtyなし・location unknownなし・人数不一致なし）は緩めない。
- 月次走査中に記録された他の dirty（hp / egg）を保持する変更は入れない
  （行軍追跡の再確認は `general_location_unknown` 経由で成立するため）。

## 検証

- `tests/test_hanjuku_post_month_status.py` 61件成功（新規5件を含む）。
  - 追跡消失の将軍が翌月の個別ステータス読み→SELECT locate に進む
  - 走査中に積まれた再確認依頼が走査完了で消えても翌月に再確認される
  - 名簿にいない未知所在名が `arm` と完了走査の両方で掃除される
  - 加入待ちの将軍は掃除されない
- 関連 864件（off_chart / spent_sortie_expiry / release_sortie_cleanup /
  unlocated_successor / house / recruit_roster / survey_cache /
  arrival_status_probe / state_regions / castle_jump / chart_bot）成功。
- `tests/test_hanjuku_diagnostics.py` は本変更前から `tmp_path` が
  module として渡る環境要因で43件エラー（今回の退行ではない）。

## 本番反映（2026-11-07）

- PR #3708 → main `5c98aafe`（CI: `ci-required` / hanjuku-suite / 各regression 成功）。
- 稼働中の g1153 は hot-load で
  `hanjuku-chart-v508-unknown-location-recheck` を判断ログに出し、
  `post_month_status_armed.observed_metric.unknown_location` を記録している。
  観測イベントの `bot_version` は agent プロセス起動時の値のままなので、
  次回の通常再起動までは v507 と表示される（判断は v508）。
- 初回の自動 deploy は、実行中に production checkout の HEAD を切り替えたため
  `rollback_incomplete` で停止した。`deployment_intent` を除去し、
  `games/soviet_now` を gitlink（`4b497120`）へ fast-forward して再 deploy し、
  gateway `status` が `configured`（sha `5c98aafe`、ops brief matched）に戻った。
  deploy 中の checkout 操作は自動 rebaseline が途中の HEAD を baseline に
  採用し得るため行わない（follow-up Issue #3709）。

## 残件

- 行軍中と読めた将軍は所在不明のまま翌月も再読する（到着まで計画外）。
  到着後の確定は従来どおり月次の locate に依存する。
- 月次走査中の hp / egg dirty の取りこぼし（原因2の一般形）は未対応。
  必要になったら同様にキャッシュの dirty を引き継ぐ変更を別途行う。
