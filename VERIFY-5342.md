# VERIFY-5342 — [半熟英雄] ボスフェーズでボス戦闘ゼロ: ch11 エッグママ

- 対象: docich issue #5342 / fingerprint `e6546af0d5fe2fe7` / occurrence `ca57d46b3c5ec95e1ea6`
- ラン: `g1190-ch1v9` / v1447。ボスフェーズ: 月 18-5 → 18-6、600.2秒、観測 2715回。
- 再発: 監視 workflow のコメント 12:57 JST、ch11 18-5、`boss_phase_no_battle`、occurrence `d20b5e9823ae0e6a607d` — 同一フェーズ、別月。
- 検証範囲: read-only（本ファイルの作成以外の編集なし。実装・push・PR・issue コメント・本番 SSH なし）
- 検証ベース: `origin/main` = `362e65713`（v1453、`version.py:7`）。行番号はこのコミット基準。
- 検品ゲート注記: セッションはスペイン語で VERIFY を作成したため、検品ゲートで日本語へ書き直した（内容・結論は変更なし）。セッションは `gh` が使えず issue 本文は依頼記載の要点のみ使用。VERIFY-5339.md（PR #5363）は未マージのためセッションは読めなかったが、ゲートで PR #5363 の件名から結論 (d) を確認済み。

## 結論: (a) 実欠陥 — #5276 とは別機構。#5276 の修正は ch11 をカバーしない

ボスフェーズでは通常城はすべて奪取済みで、残る攻撃目標はボスのみである。13 回の
非 `move` 出撃（`_departures` は `purpose=='move'` を除外、`month_audit.py:2844-2846`）
はほぼ確実にボスへの波であり、そのどれもが戦闘に入らなかった（ボス戦闘 0、
ボス HP 未観測、行軍 >3000 ticks `stuck_march`）。これは ch11 の構造的な欠陥と一致する。

**`_boss_wave_pick` が ch11 の gateway を無視している。** ch11 のマップは hub
タタールを介してのみ結ばれた前後 2 つの半分に分かれている。前半の城
（アルマムーン/チリベット/タプロベイン/ジャンセニア/ゴンダル/マランマ）から
後半の目標へ出した波は「出て戻る」だけで到達しない（g1190 2026-11-17 で実測、
`hanjuku_chart.py:1280-1292`）。ボス オヴァリウムは後半にある（ピクセル原典の
flag `(58,21)`、`src/docich/hanjuku_map_sources.py:112`。後半の城は y≤43、
同ファイル :84-85）。

発信元の gateway 制限は `_interim_source` にしかない（`replan.py:81-86`：
`target in gate['back'] → owned = {hub} ∪ back`）。しかも `gate['back']` に
`'ボス'` は含まれていない（`hanjuku_chart.py:1285-1292`）。さらに波の経路は
`_interim_source` を通らない。目標ループはボス目標に対して `_boss_wave_pick`
を呼ぶ（`replan.py:1319-1323`）。`_boss_wave_pick`（`replan.py:1169-`）は
**全** `owned` を、空き将軍 ≥2・卵/距離順で走査し（`replan.py:1229-1267`）、
gateway フィルタも `march_blocked` も見ない。hub/back の空き将軍が <2 人なら、
波は前半の城から mint される → 行軍開始 → 通行不能地形 → 野営/帰還/戦闘 →
`stuck_march` + `battle_grind` + ボス戦闘 0。

### #5276（ch9 ハデス）との異同

- **#5276 は修正済みで本件を説明しない。** `boss_companion_required` の無制限
  保留には step 共有の上限がある。`BOSS_COMPANION_WAIT_TICKS=200` +
  `_boss_companion_wait_expired`（`src/docich/hanjuku/strategy/orders.py:704-737`）
  が general_list（`deploy.py:2383`）、card_select/sortie_confirm
  （`deploy.py:2505`）、map_target（`map.py:2831`）に適用され、
  `BOSS_COMPANION_SOURCE_WAIT_TICKS=200` +
  `_boss_companion_source_wait_expired`（`orders.py:740-770`）が `_ready` ゲート
  （`orders.py:816-822`）に適用される。上限後は主人公単独の安全確認付き出撃
  （`deploy.py:2387-2411`）へ落ちるか、注文を failed とする。PR #5310
  （v1443）と PR #5308（v1446）がマージ済みで、本ランは v1447 のためこれらの
  上限はすでに有効だった。
- **機構が違う。** #5276 では注文が `pending` のまま発射されなかった（発射≠0 は
  ferry）。本件ではボスへの出撃は発射されている（order_launched 12）が軍が
  到達しない。欠陥は**下流**、すなわち分断されたマップでの出撃元城の選択にある。
- ch9 には `boss_gather` / corridor があり、道の最後の城から出すことを強制する
  （`replan.py:90-98`、`:1188-1194`）。ch11 にはボスに対する hub/back への
  gather がない。
- #5276 の監査欠陥（`battle_start` が `most_common(30)` で切り捨て）は修正済み。
  `battle_starts` は rows を直接数えている（`month_audit.py:620-622`）。

### 監査のノイズ信号（ゲームの欠陥ではない）

- `boss_pursuit_idle`: `boss_pursuit.step()` は `chapter != 9` のすべてで
  `None` を返す（`src/docich/hanjuku/flows/boss_pursuit.py:523-524`）。追跡は
  ch9 専用で設計どおりだが、監査は常にこの信号を付けている
  （`month_audit.py:632-633`）。ch11 では期待される状態であり、診断的ではない。
- `order_start_without_launch` と `launch_confirmation_missing`:
  `_build_boss_incident` は `departures=0` をハードコードして
  `_signals(context, counts, 0, 0, battle_starts)` を呼ぶ
  （`month_audit.py:634`）。`:619` で `departures=13` を計算しているにもかかわらず
  である。`order_start>0` / `sortie_launch_unverified>0` なら必ず発火する。
- `boss_cell_missing` は出ていない。ボス cell は実測されている
  （`_apply_skull_boss` は chapter 0, 4 以外の全章で走る、
  `src/docich/hanjuku/os_layer/world.py:705-706`）。

### #5339（ch11 18-5、`boss_month_zero_sortie`、(d) 判定不能）との関係

- 同一フェーズ。`_build_boss_incident` は `current.started_at` からの全行を
  読む（`month_audit.py:615-617`）ため、18-5 → 18-6 の ≥600秒をカバーする。
  12:57 JST の 18-5 での再発とも整合する。
- `boss_month_zero_sortie` は `_boss_departures`（target が `boss_sites` か
  `purpose=='boss'`、`month_audit.py:2856-2879`）を使うが、#5342 の 13 回の
  「発射」は汎用の `_departures`（非 move の全 target）である。18-5 でボス
  出撃が 0 回だったなら、13 回は 18-6 に集中したか、非ボスである。issue の
  決定行がなければ mint と dispatch を区別できない — #5339 が (d) で示した
  のと同じ限界である。VERIFY-5339.md（PR #5363）の結論は (d) 判定不能で、
  決定行の不足を理由としている（ゲートで PR #5363 の件名から確認）。

### 確実性の注記

- issue の `decisions.before` 行は読めなかった。「出撃 ≈ ボスへの波」という
  推論は、フェーズが 600秒続き、lost がなければ retake を説明する出撃はない
  ことから成り立つ。絶対的な確認には 12 回の `order_launched` の
  `source`/`target`（`hanjuku_decisions.jsonl`、runtime 側にあり本 worktree
  からは読めない）が必要である。

## 判定詳細

- **(a) 実欠陥**: 600秒のボスフェーズでボス戦闘 0 は設計ではない。ボス波に
  gateway 制限がないという具体的な gap があり、シグネチャを正確に再現する。
- **(b) ではない**: #5276 の修正（#5310/#5308）は v1447 で有効済みだが別層
  （代役待ち）であり、波の出撃元選択には触れていない。
- **(c) ではない**: 検出は正しい。ノイズなのは補助信号 2 件だけである。
- 監査の副欠陥: ch9 以外での `boss_pursuit_idle` と、`_build_boss_incident`
  での `departures=0` ハードコード。

## §6 修正案（実装はしない）

1. `replan.py:_boss_wave_pick` — gateway フィルタを適用する。
   `chart.gateway(chapter)` が存在すれば、候補の発信元を `{hub} ∪ back` に
   制限する（`_interim_source:81-86` と同じ規則）。または `gate['back']` /
   `boss_reachable_sources(chapter)` を拡張してボス城を含め、両側で使う。
2. ch11 のボスフェーズに hub/back への gather を追加する（ch9 の corridor
   の `boss_gather`（`replan.py:90-98`）と等価）。将軍を hub へ集め、波が
   到達可能な発信元から出るようにする。
3. 監査: `boss_pursuit_idle` は追跡を使う章（`MOVING_BOSS_CHAPTERS` / ch9）
   のときだけ出す。`month_audit.py:634` の `_signals` へは実測の
   `departures` を渡す。
4. テスト: `tests/test_hanjuku_ch11_gateway.py` にボス波のケースを追加する
   （全城奪取済み、back/hub の空き将軍 <2、前半に spare あり）。選択される
   発信元が hub/back であり、前半の城から出ないことを確認する。

## テスト（検品ゲートで実行）

- 現行 main（362e65713、v1453）で `tests/test_hanjuku_ch11_gateway.py` +
  `tests/test_hanjuku_boss_companion_wait_bound.py` を実行し、**52 passed**。
  `test_hanjuku_ch11_gateway.py` は `_interim_source` / recalls をカバーし、
  ボス波の `_boss_wave_pick` は未カバー。`test_hanjuku_boss_companion_wait_bound.py`
  は #5310 の上限をカバーする。
- セッションは本環境で pytest が拒否されたため未実行だった。

## 検証で使った根拠（抜粋・origin/main = `362e65713` の行番号）

- `src/docich/hanjuku/strategy/replan.py`: `_interim_source` L81-86、
  `boss_gather` L90-98、`:1188-1194`、`_boss_wave_pick` L1169-（走査 L1229-1267）、
  目標ループ L1319-1323
- `src/docich/hanjuku_chart.py`: `GATEWAYS` ch11 L1280-1292
- `src/docich/hanjuku_map_sources.py`: ch11 城座標 L84-85、ボス site `(58,21)` L112
- `src/docich/hanjuku/strategy/orders.py`: `BOSS_COMPANION_WAIT_TICKS` L704、
  `BOSS_COMPANION_SOURCE_WAIT_TICKS` L740
- `src/docich/hanjuku/flows/deploy.py`: companion wait 適用 L2383・L2505
- `src/docich/hanjuku/flows/map.py`: companion wait 適用 L2831
- `src/docich/hanjuku/flows/boss_pursuit.py`: `step()` の ch9 専用 L523-524
- `src/docich/hanjuku/os_layer/world.py`: `_apply_skull_boss` L705-706
- `src/docich/hanjuku/month_audit.py`: `_build_boss_incident` L615-617、
  `battle_starts` L620-622、`boss_pursuit_idle` L632-633、`_signals` L634、
  `_departures` L2844-2846、`_boss_departures` L2856-2879
- 関連コミット: PR #5310（v1443）、PR #5308（v1446）
- 関連ブランチ: `verify/5342-boss-phase-no-battle`（本検証）

## 手順上の注記

- read-only 検証。コード編集・本番 VM 操作・push・PR 作成なし。
- `gh` 不可のため issue/PR 状態は依頼記載の要点＋ローカル git 参照のみ。
  VERIFY-5339.md（PR #5363）は未マージのため本検証では読めていない。
  → **検品ゲートで PR #5363 の件名から (d) 判定不能を確認済み。**
- セッションは本環境で `python3`/`pytest` の起動を拒否されたためテスト未実行。
  → **検品ゲートで現行 main（362e65713）で 52件 green。**
- セッションは VERIFY をスペイン語で作成した。**検品ゲートで日本語へ書き直し、**
  **ファイルパスを正しいものへ訂正した**（`boss_pursuit.py` は `flows/`、
  `orders.py` は `strategy/`、`hanjuku_map_sources.py` は `src/docich/` 直下、
  `world.py` は `os_layer/`）。`_boss_wave_pick` の定義行は L1169（セッションは
  走査部分の L1229-1267 を引用）。`month_audit.py:523-531` の引用はゲートで
  確認できなかったため、該当の推論からは行番号を外した。
- `git add` / `git commit` はセッション環境が拒否したため、本ファイルは
  `verify/5342-boss-phase-no-battle` の worktree ルートに**未コミット**で置いて
  ある（VERIFY-5313 と同じ制約パターン）。
  → **検品ゲートで origin/main（362e65713）へ merge --ff-only したうえで
  コミット・push・PR 化まで完了。**
