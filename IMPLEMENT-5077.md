# IMPLEMENT-5077 — [半熟英雄] ボスフェーズでボス戦闘ゼロ: ch1 クイーン

ブランチ: `fix/hanjuku-5077-boss-dispatch`（`origin/main` `16bb5b431` から作成、push なし）
BOT_VERSION: `hanjuku-chart-v1375-boss-dispatch-guarantee`
（v1374 は open PR #5078「rich-merchant-entry」が使用中のため v1375 を採用）
検証結論: VERIFY-5077.md — (a) 実欠陥（ボスフェーズ 600 秒・5,622 観測で開始可能な
move 注文 2 件が一度も `order_start` に届かなかった出撃ディスパッチ停滞）。

## 変更内容

### 1. ボスフェーズ中の出撃ディスパッチ保証（VERIFY-5077 §6-1）

**`src/docich/hanjuku/strategy/orders.py`**（`next_order` 直前に追加）

- `ORDER_DISPATCH_STARVE_TICKS = 60` — pending 注文が `order_start` なしに
  待てる観測数の上限（約 90 秒）。
- `boss_only_phase(mem)` — `month_audit._boss_phase` のエンジン側双子。
  非ボス城をすべて保有＋ボス城が 1 つ以上残存のときのみ真。
  `owned = _owned − lost` で監査と同じ計算。ch9 はボスが城テーブル外なので
  残存チェックは構造的に真となり、実質「回廊全城保有」＝`recall.boss_phase` と一致。
- `_dispatch_pending(mem)` — pending（または `_boss_retry_due` のボスリトライ）の
  注文 step を返す軽量近似。`next_order` の全フィルタは複製せず、
  「発射ブロックを一度実行する価値があるか」の判定だけに使う。
- `order_dispatch_starved(mem)` — ボスフェーズ中に pending 注文が
  `ORDER_DISPATCH_STARVE_TICKS` を超えて待つと真。最初の待ち観測で
  `order_dispatch_since` をシードし、エピソード（`since`）ごとに 1 回だけ
  `order_dispatch_starved` を `_record`（`wait`・`pending` 先頭6件を記録）。
  pending なし・`next_order` が None・`order_start` 記録のいずれかで待ちは解除。

**`src/docich/hanjuku/flows/map.py`**（`map_step`）

- `select_focus`（定期 SELECT リフォーカス）: 間隔経過かつ
  `not order_dispatch_starved(mem)` のときだけ発火。飢餓中は `select_focus_tick` を
  更新しないため、飢餓が続く限り毎フレーム譲歩する。
- `world_map_open`（全体マップ確認の Y）: 同様に `order_dispatch_starved` が
  真なら早期 return せず、直下のディスパッチブロックへフォールスルー。
  `world_map_due` は消費しないので、飢餓が続く間は要求が残る。
- ディスパッチブロック内:
  - `next_order` 系の解決で `order is None` → `order_dispatch_since` を除去
    （「開始可能な注文が無い」＝待ちの解除。pending だが開始不能な注文は
    次のチェックで `since` が再シードされ、上限ごとに 1 回だけ譲歩が再発する）。
  - `order` 確定時 → `order_dispatch_since` を setdefault でシード。
  - `order_start` 記録時 → `order_dispatch_since` を除去（待ち終了）。

**`src/docich/hanjuku/flows/recall.py`**（`_camp_recall_step`）

- `state is None`（帰還未開始）かつ map フレームで、`order_dispatch_starved` が
  真なら `None` を返し、新規の野営帰還・帰還ハントの開始を譲歩。
  飛行中の帰還（state dict 存在）は一切変えない——途中のメニュー所有は維持。
  `camp_arrival_check`（受領照合の読み取り専用ブックキーピング）は引き続き先頭で実行。

設計上のポイント:

- 「`order is None` かつ startable 注文が存在」の判定を、発射ブロック到達前に
  重い `next_order` を回さず `_dispatch_pending`（pending/boss-retry の粗い近似）で
  行う。開始不能な注文が残るだけなら、譲歩してブロックを走らせても
  `next_order → None` でカウンタが即解除され、上限ごとの周期確認に留まるため
  調査系・帰還系を恒久的に枯渇させない。
- 保証の上限は `STARVE(60) + ORDER_START_BACKOFF(12) + SORTIE_RETRY_DEFER(~100)`
  観測で有界。防御戦闘・月次など別 owner のフレームは譲歩対象外だが、
  `order_dispatch_starved` 記録がその枯渇を可視化する（VERIFY §6-1 の記録要件）。
- `order_dispatch_since` / `order_dispatch_starved_at` は `regions.py` に
  PLAN/chapter キーとして宣言（`order_start_tick` と同じ扱い）。

### 2. 未読本城の roster probe ギャップ（VERIFY-5077 §6-2）

**`src/docich/hanjuku/strategy/replan.py`**（`_boss_wave_pick`、arrival probe ブロックの直後）

`no_source`（全実測城が `need=2` 未満＝`choices` 空）かつ 1 つでも実測城が
あるとき、`garrison` キー欠落（未読）の保有城へ `source_probe`（
`MOVE_ANY_GENERAL`）を起票して在城リストを実測しに行く。
`roster_read_due`・`arrival_probe_due` に依らない。直前の probe 開始 tick
（`arrival_probe_tick` + `PROBE_INTERVAL`）で再発行をスロットル。
従来の「全城未読」フォールバック（`not any(garrison.get(c) ...)`）は
新分岐がカバーしない範囲（全城未読・空リストのみ）としてそのまま残す。

本件の構造（前線 6 城×各 1 将軍・本城アルマムーン未読）はまさにこの空隙で、
起票条件をどれも満たさず証拠取得経路が無かった。probe は出撃メニューで
実一覧を読み（`_observe_garrison`）、2 名態勢があれば次の計画から
`need=2` を満たす出撃元としてボス波が合法に起票される。
probe 注文自体は `_ready`/`_source_list_open`/`_source_spare_missing` の
既存 probe 例外（`source_probe=True` + `PROBE_GENERAL`）で開始可能。
ボス城向け `source_probe` は既存の `boss_companion_required` 有界ホールドで
実一覧が不十分でも暴走しない。

### 3. 監査側

VERIFY-5077 §6-3 どおり変更不要（`no_source`・`boss_pursuit_idle` 等の
シグナルは正しい）。`order_dispatch_starved` 記録は新しい観測ポイントとして
今後の監査で「計画不足」と「dispatch 停滞」の読み分けを補強する。

### 4. バージョン・allowlist

- `src/docich/hanjuku/version.py`: `hanjuku-chart-v1375-boss-dispatch-guarantee`
- `ops/vm_actions/collect_diagnostics.py`: allowlist に同文字列を追加（trailing comma あり）
- `ops/vm_actions/tests/test_collect_diagnostics.py`: fixed-values リストに同文字列を追加

### 5. テスト

- `tests/test_hanjuku_boss_dispatch_starved.py`（新規 10 件）:
  `boss_only_phase` の境界、starved 判定・記録の 1 エピソード 1 回性、
  pending なし/フェーズ外/上限内での非発火、world_map 調査の譲歩と
  `order_start` 到達・`order_dispatch_since` 解除、未 startable 時の待ち解除、
  select_focus 譲歩、新規 camp recall 譲歩（飛行中は不変）。
- `tests/test_hanjuku_boss_probe_source.py`: 旧仕様テスト
  `test_an_unread_castle_is_not_probed_while_other_generals_are_known` を
  新仕様（実測城が need=2 を満たせないとき未読城を probe）に更新し、
  cooldown スロットルの 2 テストを追加。
- `tests/test_hanjuku_boss_wave_solo.py`:
  `test_the_home_castle_alone_does_not_send_a_boss_wave` は
  `source_probe`（読み取り probe＝波ではない）を除外して「実波なし」を検証。

## 検証結果

実行コマンド（worktree `~/work/docich-5077`、`python3 -m pytest`）:

| 対象 | 結果 |
|---|---|
| `test_hanjuku_boss_dispatch_starved.py` + `test_hanjuku_boss_probe_source.py` | 18 passed |
| ボス波・暫定計画系 10 ファイル | 164 passed（うち 1 件は上記の通り期待値更新） |
| interim/staging/corridor/名簿系 20 ファイル | 360 passed |
| move/garrison/hero 系 20 ファイル | 257 passed |
| world map・SELECT・map 制御系 9 ファイル | 88 passed |
| camp recall・recheck 系 14 ファイル | 100 passed |
| month/menu audit・order tally・override 系 13 ファイル | 210 passed |
| map_unowned/map_events/boss_only_carry/no_input_gate + `test_collect_diagnostics.py` | 292 passed + 2 failed（下記・環境起因） |

既知の失敗（本変更と無関係・環境起因）:
`test_collect_diagnostics.py` の `test_an_inaccessible_docich_root_is_treated_as_absent`
×2 は、実行環境が root（uid 0）のため `chmod 0` のディレクトリを読めてしまい
「読めない」前提が成立しない。本変更は `_collect_broadcast_template` に無関係。

## 制約の内訳

- 別スクリーン owner（防御戦闘・月次・post_month_status・house・ジャンプ途中）の
  フレームは譲歩対象外——譲歩は「繰り返し前景ワーク」のみ（
  `world_map_open`・`select_focus`・新規 camp recall/帰還ハント）。
  これらに支配された枯渇は `order_dispatch_starved` 記録で可視化する設計。
- `order_dispatch_starved` はボスフェーズ外では一切発火しないため、
  通常局面の調査・帰還挙動は不変。
- push・commit・PR は実施していない。
