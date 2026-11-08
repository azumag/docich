# 半熟英雄: 章またぎ将軍の待ちキューを在籍名簿で確認する（v519、2026-11-08）

## 症状

章をまたいだ直後、前章から引き継げる将軍は1か月に3人までで、残りは
「いまこちらにむかっております」の待ちキューに入る。月次ステータス確認が
この全員を毎月個別に開き直していた。

## 実測（g1153、2026-11-08 15:19 JST）

- `post_month_status_last.statuses` に24人が
  `status_text="いまこちらにむかっております"`、`location="unknown"` で並んでいた。
- コードの `MARCH` は「いまこちらに**むかっています**」で、実画面の
  「いまこちらにむかって**おります**」と一致していなかった。そのため
  `_location` が `unknown` を返し、`arm` が毎月 `stale` に入れ、
  `_complete_scan` も `unknown` を許さないため月次確認が完了しなかった
  （`failed="roster_end_unconfirmed"`）。
- 到着済みのポワソン・タピオカ・キールはアルマムーンにいた。先発隊は
  エピィ・ヘパイストス・トレビス。トレビスは `general_location_unknown`。

## 変更（v519 `hanjuku-chart-v519-carry-manifest`）

### 1. 待ちキューを既知の状態にする

- `post_month_status.CARRY` に実文言「いまこちらにむかっております」を追加し、
  `_location` が `'carry'` を返す。旧表記は `MARCH` のまま残す。
- `house.CARRY_LOCATIONS` と `general_status` の `carry_waiting` を追加し、
  `location_observed` を立てる。
- `_complete_scan` は `unknown` 拒否を維持しつつ `carry` を既知として許可。
- `arm` は `carry` を毎月 `stale` にしない。初回の完全scanで
  `carry_tracking`（initial/remaining）を作り、以後は在籍名簿で確認する。
- `_carry_eligible` は、城に居る行に加えて「field + carry_waiting +
  location_observed」の待ち行も月境界で引き継ぐ。

### 2. 主人公の城の在籍マニフェスト

- `carry` が1人もいなければ何も開かない。
- 目的地は主人公の城（`_hero_castle`、なければ主人公本人の `castle` 行）。
- 城ステータスは現行リーダーが5行まででスクロール処理がないため、住人が
  多い場合は **しゅつげき名簿** を使う。既存の `_present_generals` /
  `_current` と同じ読みで、下入力のwrap/stall/limitまで歩く。Aは押さない。
- 名簿に載っている既知の将軍は `location='castle'`、`castle=目的地`、
  `castle_evidence='hero_castle_manifest'` にし、`dirty` から `location` を外す。
  `carry` だった名前は到着としてキューから除く。
- 完全な名簿に載っていない `carry` は待ち継続。不在を退去とは扱わない。
- 名簿にだけ居る未知の名前（新規加入・OCRの部分名）は、この経路では行を
  作らない。通常の名簿証明に委ねる。
- マニフェスト後、`location` だけが dirty だった将軍はroster巡回でAを押さない。
  `hp`/`egg` が dirty な将軍だけAを1回押し、三角読みとSELECT locateを省略する。
- マニフェストだけの月はroster walkをせず `_finish` し、`general_statuses` と
  `post_month_status_last` を更新する。`post_month_status_last.scope` が
  当月になるため、既存のhouse field surveyは重複しない。

### 3. 安全期限とフォールバック

- 初回のcarry数Nから `ceil(N/3)` ヶ月＋2を超えても残る場合は、一度だけ
  残り全員の個別状態を再確認する（`carry_overdue_recheck`）。
- マニフェストが読めなかった月は、待ちキューから最大3人を巡回で確認する
  （`carry_fallback_window`）。全員を開き直さない。
- 主人公が行軍中などで目的地が不明なら、その月はマニフェストをスキップして
  待ちを保持する。

## 変更しないこと

- 未確認の所在地を城として扱わない。`carry` を城に丸めない。
- 名簿に載っていないことを退去・死亡の証拠にしない。
- 章またぎ以外のフィールド行軍（`しんぐんちゅうです`）の扱い。
- 既存の帰還・修理・計画の予算と所有権。

## 検証

- `tests/test_hanjuku_post_month_status.py`: 76 passed（新規9件）。
  - 実文言が `carry` になり、`general_status` が `carry_waiting` を返す。
  - carryが居ても毎月の個別Aを開かず、主人公の城のマニフェストをarmする。
  - 目的地が不明ならcache再利用で入力を出さない。
  - `_complete_scan` がcarryを既知として完了し、trackingを作る。
  - マニフェストが到着者をcastleにし、載っていないcarryを待ちのまま残す。
  - しゅつげき名簿のwalkで全員を読み、個別statusなしで完了する。
  - manifestでlocationを充足した将軍は、HP dirtyのとき三角読みをしない。
  - 待ち行が月境界を越えられる。
- 関連: house / survey_cache / chart_bot / off_chart / arrival_status_probe /
  recall_arrival_status / fort_camp / hotload / no_input_gate /
  month_status_single_read の713+47件が成功。
- `tests/test_hanjuku_diagnostics.py` は本変更前からの `tmp_path` がmoduleに
  なる環境要因で43 errors（今回の退行ではない）。

## 未確認・残件

- 実ランで章またぎ待ちのマニフェストが自然に発火し、到着3人を一度で反映する
  ことは未確認。次の章替わりで実測する。
- 城ステータスのスクロール可否は実画面で未確認。現状は5行までしか読めない
  ため、しゅつげき名簿を主経路にしている。城ステータスがスクロールできると
  確認できたら、住人が5人以下のときの近道として使える。
- しゅつげき名簿が傷病・卵持ちなど一部の住人を出さない場合、その将軍は
  待ちのまま残る。overdue再確認で拾うが、名簿の完全性は実測が必要。
- 2026-11-08 19:26 の g1153 で、`manifest_satisfied` を `set` で持ったため
  `hanjuku_bot.json` の書き込みが全観測で失敗し、決定済み入力が送られず
  `input_stalled` でコーナーが終了した。v523
  （[`hanjuku-manifest-json-state-20261108.md`](./hanjuku-manifest-json-state-20261108.md)）
  で list 化と副作用の隔離を入れた。実ランの再確認は未実施。
