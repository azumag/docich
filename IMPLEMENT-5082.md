# IMPLEMENT-5082 — [半熟英雄] ボスフェーズでボス戦闘ゼロ: 全城実測・need=2 不在の「最後の1将軍」例外

ブランチ: `fix/5082-boss-last-general`（PR #5309、Muse 経由で push 済み）
BOT_VERSION: `hanjuku-chart-v1456-boss-last-general`
（#5367 の v1455 まで main で使用済みのため
v1456 に再採番。version.py・allowlist・固定値テストを一括更新済み）

## 背景（issue #5082）

#5077 と同系統の別サブケース。g1190 chapter 1 month 9-10 で、全8城が実測済み・
各1将軍、未読の保有城なし。`need=2`（防衛1＋予備1）を満たす城が無く、
奪還・攻撃・移動のいずれも source を持てず、`strategy/replan.py` が
`chart_interim_hold` をループ。ボス城宛の出撃注文が生成されず
`order_start=0` のまま `boss_phase_no_battle` が約600秒継続した。
PR #5095 (v1375) の `source_probe` は未読城向けのため、全実測のこの局面では
発火しない。

## 設計（DSH 採用方針どおり: fail-open・bounded）

ボスフェーズ限定の「最後の1将軍」例外。発火条件（すべて必須）:

1. ボスフェーズ active かつ `boss_cell` が readings ≥ 2（追跡対象確定。
   ch9 は `MOVING_BOSS_TICKS` の鮮度も要求）
2. `chart_interim_hold` が #5095 と同じ上限 `ORDER_DISPATCH_STARVE_TICKS`
   （60観測 ≈ 90秒）継続
3. 未読の保有城が 0（probe 経路枯渇。probe > 例外の優先順位を維持）
4. 全実測城が `need=2` 未満（通常の source 選択が空）

## 変更内容

### 1. `src/docich/hanjuku/strategy/replan.py`

- `_stable_interim_step(state, rid, order)` — `_mechanical_interim` 内の
  暫定 step 名付けロジックを関数化（例外注文と通常経路で共有）。
- `_boss_last_general_besieged(mem)` — 例外が空にしてはいけない包囲中の城。
  live `plan.attack`（side=defense）と `defense_seen`（
  `corridor.DEFENSE_WINDOW_TICKS` の同一窓）を合成。
- `_boss_last_general_ready(mem)` — 上記4条件を検査し
  `(owned, free, reading)` を返す。どれか欠ければ None（=例外不発）。
- `_boss_last_general_source(...)` — source 選定。敵・ボス cell から
  最遠を優先、同点は将軍の戦力で決壊。包囲中の城は除外。本城
  （アルマムーン）は最後の手段（他に候補が無い場合のみ、記録に
  `source=home` を明記）。将軍名は `_order_hero_alternatives` の
  deploy 実選択に合わせ、実際に開始可能な注文にする。
- `_boss_last_general_order(mem, state, rid)` — エピソード管理と起票。
  - `boss_last_general_since` をシードし、`ORDER_DISPATCH_STARVE_TICKS`
    未満では従来どおり hold。
  - `boss_last_general_used` でエピソードあたり1件にラッチ。発行済み
    sortie が `_sorties` に残り将軍が帰還/損失した時点でエピソードを
    閉じ、bounded wait を再アーム。発射しなかった注文はエピソードを
    ラッチしたまま（拒否された出撃の連発はしない）。
  - 発行時に `boss_last_general_order` を `_record`（理由に
    「全城実測済みで need=2 を満たす城が無いため、ボス追跡のために
    最後の1将軍を出す」を明記）。
- `_mechanical_interim` — `interim_candidates` が空のとき、hold 記録の
  前に `_boss_last_general_order` を試行。発行すれば `interim_order` に
  格納して終了。未発射の例外注文は `busy` 判定直後に前提条件を再検査し、
  崩れていればスロットを解放（例: 発射前に source が2名態勢へ回復）。

### 2. `src/docich/hanjuku/strategy/orders.py`

明示フラグ `order['boss_last_general'] is True` の注文だけに、次の
one-general 拒否を個別解除（通常注文の `need=2`・予備ガードは不変）:

- `_ready` — corridor/通常章の「source が1将軍なら拒否」ゲート
- `_reserve_source_guard` — 最後の守り将軍を出さないガード
- `_source_spare_missing` — pre-flight の最後の守り将軍チェック

### 3. `src/docich/hanjuku/state/regions.py`

- `boss_last_general_since` — PLAN 領域・chapter lifetime・
  `migrated: False`（エピソード開始 tick）
- `boss_last_general_used` — 同上（発行済み例外注文のラッチ）

### 4. `src/docich/hanjuku_commentary.py`

- `PLANNED`・`SPOKEN` に `boss_last_general_order` を追加。
- `compose` で `boss_last_general:{step}` のラベルと説明文
  （全城実測・予備なし・最後の1将軍をボス追跡へ出す旨）を返す。

### 5. バージョン・allowlist

- `src/docich/hanjuku/version.py`: `hanjuku-chart-v1456-boss-last-general`
- `ops/vm_actions/collect_diagnostics.py`: allowlist に同文字列を追加
- `ops/vm_actions/tests/test_collect_diagnostics.py`: fixed-values リストに
  同文字列を追加

### 6. テスト

`tests/test_hanjuku_boss_last_general.py`（新規 10 件）:

- 発火: hold が上限を超えた時点でボス向け例外注文が起票され、
  `boss_last_general_order` が記録される
- 上限内では従来どおり `chart_interim_hold` のまま
- `boss_cell` readings < 2 ではエピソード自体をアームしない
- 未読城が1つでもあれば `source_probe` が優先（probe > 例外）
- 実測で2名態勢があれば通常のボス波が出て例外は発火しない
- ボスフェーズ外（失城あり）では発火しない
- 包囲中（`defense_seen` 窓内）の城は空にせず次点の城を選ぶ
- 本城は最後の手段（候補が本城だけのとき選ばれ `source=home` を記録）
- 1エピソード1件（発射前失敗・行軍中は再発行しない。sortie 終結で
  エピソードを閉じ、bounded wait 再アーム後に次の例外が出る）
- `_source_spare_missing`/`_reserve_source_guard` がフラグ付き例外だけ
  解放されること、発射前に前提が崩れたスロットが解放されること

## 検証結果

この実行環境では `python3` / `pytest` 系コマンドの実行が承認されず、
ローカルでのテスト実行は未実施。実施した検証は以下:

- `git diff` による全変更のレビュー（replan.py / orders.py / regions.py /
  commentary / version / allowlist / 診断テスト / 新規テスト）
- 実装が参照するシンボル（`ORDER_DISPATCH_STARVE_TICKS`, `boss_only_phase`,
  `_order_hero_alternatives`, `_strength`, `_known_residents`, `_sorties`,
  `MOVING_BOSS_CHAPTER`, `MOVING_BOSS_TICKS`, `DEFENSE_WINDOW_TICKS`,
  `RETIRED_ORDER_STATES`, `chart.SPEEDRUN_CHART_ENV` 等）の存在確認
- 新規 state キーが `migrated: False` で宣言され、フラット読み書きと
  整合することを確認

関連テスト（`test_hanjuku_boss_last_general.py`、interim/dispatch 系、
`test_collect_diagnostics.py` の fixed-values テスト）の実行は
CI / 検品ゲートに委譲する。

## スコープ外（指示どおり未実施）

- `need=2` の全般的緩和（通常攻撃・移動への適用）
- #5120 の検出器ガードのバンドル
- `month_audit.py` 側の変更（`post_month_status_unverified` は例外を
  ブロックしない＝現行どおり、戦闘側の survival/retreat ガードも不変）

## 残件・注意

- BOT_VERSION は v1456 に確定（#5310 の共有 receipt との共存回帰テストを追加済み）。
- push・PR 更新は Muse が代行。rebase 後 HEAD で DSH レビュー・CI 確認を行う。
