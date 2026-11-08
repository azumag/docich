# 半熟英雄: こわれた将軍のボス至近野営を月次帰還へ戻す 2026-11-07

## 症状

キャンプ状態の将軍が月次ステータス確認で帰還シーケンスに載らず、野営のまま
残っている。実測例は g1153（2026-11-07、1章2年12の月）のヴィーナス。

- 本人ステータス: `キャンプしています`、HP 3/82、卵こわれ、けっかい野営。
- `post_month_status_armed.reopen` には入るが、2-11 / 2-12 の
  `post_month_status_complete.tasks` はどちらも空で、ヴィーナスの recall が無い。
- `boss_camp_exempt = {'ヴィーナス': 1}`。roster triangle の
  `castle_candidate = けっかい` がボス城名のため、#3512 の
  「ボス城至近の野営は月次帰還から除外」に該当していた。
- 将軍自身の EXPL では `goals` に `kind = repair` が積まれているが、修理タスクは
  `_tasks` で `location == 'castle'` の将軍だけに積まれる。野営のままでは
  帰還も修理も始まらない。

## 原因

#3512 / #3505 の除外は「いまボス攻撃を続けている将軍」を守るためのものだが、
卵こわれは修理タスクの合図であり、その修理は在城確認後の次回月次パスでしか
開始されない。それでも `castle_candidate` がボス城名というだけで3か月
除外され続け、4か月目にようやく recall になるため、それまで野営に固定される。

## 変更

- `flows/post_month_status.py`
  - `_boss_camp_holds_siege(info)` を追加。ボス城至近の野営でも
    `broken is True` の将軍は「攻撃継続中」と扱わない。broken 未読
    (`None`) は従来どおり除外し、不明な証拠で帰還を強制しない。
  - `_tasks` はこの判定を使い、こわれたボス至近野営を `recall` タスクへ戻す。
  - `arm` の `boss_camp_exempt` 月数加算も同じ判定に揃え、こわれた将軍を
    除外月数へ数えない（翌月以降も帰還対象のまま）。
- `version.py` を `hanjuku-chart-v510-broken-boss-camp-recall` へ。
- 診断 `bot_version` 許可集合へ v510 を追加。

## 変更しないこと

- 健全な将軍のボス城至近野営は従来どおり月次帰還の対象外（#3505 / #3512）。
- 除外の 3 か月上限（`BOSS_CAMP_EXEMPT_MONTHS`）と、月替わり1回だけ加算する
  スコープは不変。
- 月次ステータスの個別確認 → 帰還 → 修理の順序、未確認の所在地を城として
  扱わない方針は不変。

## 検証

- `tests/test_hanjuku_post_month_status.py`: 64 passed（新規3件を含む）。
  - こわれたボス至近野営は `_tasks` で recall になる。
  - 月次 `arm` はキャッシュ済みでも recall を積み、`boss_camp_exempt` に数えない。
  - recall タスクが実際に `recall` state / `wait_recall` を開始する。
- camp / recall / fort / chart bot の関連テスト: 859 passed（154.74s）。
- `test_hanjuku_hotload` / `test_hanjuku_overwhelming_egg_conservation` /
  `test_hanjuku_post_month_status` / `collect_diagnostics`: 338 passed。
  `collect_diagnostics` の4件（huge telemetry / infra pid / recent_events / redact）は
  同じ base 11b2393e の clean worktree でも同じ4件が失敗し、本変更と無関係の
  実行環境依存と確認した。

## 未確認・残件

- デプロイ後の実ランで、次の月次ステータス確認がヴィーナスの recall を積み、
  実際に帰還・修理へ進むことは未確認。
- こわれていない低HP将軍（卵は無事）を帰還させるかの判断は本変更の対象外。
